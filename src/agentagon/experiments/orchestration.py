"""Durable work packets for the active host; this module never starts model processes."""

import copy

from agentagon.core.records import AuditError, digest, identifier, now, validate_record
from agentagon.experiments import checkouts, engine, evaluation, learning, search
from agentagon.experiments.spec import object_keys, path, text
from agentagon.experiments.store import load_run, locked, run_dir

MAXIMUM_SETTINGS = {
    "round_width": 64,
    "host_capacity": 64,
    "branch_depth": 100,
    "resource_slots": 1024,
    "scan_workers": 16,
    "ideation_passes": 10,
}
DEFAULT_SETTINGS = {
    "round_width": 1,
    "host_capacity": 1,
    "branch_depth": 1,
    "resource_slots": 1,
    "scan_workers": 1,
    "ideation_passes": 1,
}


def validate_settings(value: dict) -> dict:
    object_keys(value, set(MAXIMUM_SETTINGS), set(MAXIMUM_SETTINGS), "orchestration settings")
    for key, number in value.items():
        if (
            type(number) is not int
            or not (0 if key == "ideation_passes" else 1) <= number <= MAXIMUM_SETTINGS[key]
        ):
            raise AuditError(f"invalid orchestration bound: {key}")
    return copy.deepcopy(value)


def capacity(data: dict, host_capacity: int | None = None) -> int:
    settings = data["profile"].get("orchestration")
    if not settings:
        return 0
    occupied = sum(engine._unfinished(data, c) for c in data["candidates"].values())
    trial_commitments = sum(
        max(0, data["spec"]["repetitions"] - len(c["trials"]))
        for c in data["candidates"].values()
        if engine._unfinished(data, c)
    )
    remaining_trials = max(
        0, data["limits"]["max_trials"] - data["usage"]["trials"] - trial_commitments
    )
    return max(
        0,
        min(
            settings["round_width"],
            settings["host_capacity"] - occupied,
            (host_capacity or settings["host_capacity"]) - occupied,
            data["limits"]["parallel_candidates"] - occupied,
            settings["resource_slots"] // data["spec"].get("resources", {}).get("slots", 1)
            - occupied,
            data["limits"]["max_candidates"] - data["usage"]["candidates"],
            remaining_trials // data["spec"]["repetitions"],
        ),
    )


def update_round(data: dict, round_: dict) -> bool:
    """Project branch completion without allocating successors or changing old rounds."""
    for branch in round_["branches"]:
        tip = data["candidates"][branch["candidates"][-1]]
        live = any(t["state"] in {"running", "interrupted"} for t in tip["trials"])
        if branch.get("finished") and not live:
            continue
        reason = None
        if evaluation.invalidated(data, tip) and not live:
            reason = "invalidated"
        elif tip["state"] in {"failed", "rejected", "duplicate", "cancelled"} and not live:
            reason = tip["state"]
        elif tip["state"] == "verified":
            if len(branch["candidates"]) >= branch["depth_limit"]:
                reason = "depth allowance completed"
            elif (
                data["usage"]["candidates"] >= data["limits"]["max_candidates"]
                or data["limits"]["max_trials"] - data["usage"]["trials"]
                < data["spec"]["repetitions"]
            ):
                reason = "remaining candidate or trial budget cannot cover a descendant"
        if reason:
            branch.update(finished=True, reason=reason)
    return all(branch.get("finished") for branch in round_["branches"])


def stagnation_state(data: dict) -> str:
    settings = data.get("profile", {}).get("orchestration")
    if not settings:
        return "exhausted"
    completed = sum(bool(r.get("completed")) for r in data["rounds"])
    ideations = data.get("ideations", [])
    if ideations:
        last = ideations[-1]
        if not last["hypotheses"] and completed == last["after_rounds"]:
            return "exhausted"
        # A supported alternative gets exactly one further round, within unchanged budgets.
        if last["hypotheses"] and completed == last["after_rounds"]:
            return "active"
    if len(ideations) < settings["ideation_passes"]:
        return "awaiting_ideation"
    return "exhausted"


def _evidence(data: dict, candidate_ids: list[str]) -> set[str]:
    return {
        ref
        for cid in candidate_ids
        for ref in [
            f"candidate:{cid}",
            *[t["artifact"] for t in data["candidates"][cid]["trials"] if t.get("artifact")],
        ]
    }


def _brief(data: dict, value: dict, *, branch: dict | None = None) -> dict:
    value = copy.deepcopy(
        object_keys(
            value,
            {
                "parent_id",
                "hypothesis",
                "author",
                "editable_paths",
                "evidence",
                "depth_limit",
                "branch_id",
            },
            {"hypothesis", "author", "editable_paths", "evidence"},
            "candidate brief",
        )
    )
    text(value["hypothesis"], "candidate hypothesis")
    text(value["author"], "candidate author")
    if not isinstance(value["editable_paths"], list) or not value["editable_paths"]:
        raise AuditError("candidate brief needs explicit permitted edits")
    value["editable_paths"] = [path(p) for p in value["editable_paths"]]
    for scope in value["editable_paths"]:
        if not any(checkouts.under(scope, allowed) for allowed in data["spec"]["editable_paths"]):
            raise AuditError("brief edit scope exceeds the frozen application scope")
    references = value["evidence"]
    if (
        not isinstance(references, list)
        or not references
        or any(
            not isinstance(ref, str) or ref not in _evidence(data, list(data["candidates"]))
            for ref in references
        )
    ):
        raise AuditError("brief evidence must reference this run's candidates or trial artifacts")
    depth = value.setdefault("depth_limit", branch["depth_limit"] if branch else 1)
    if type(depth) is not int or not 1 <= depth <= data["profile"]["orchestration"]["branch_depth"]:
        raise AuditError("brief exceeds the saved branch-depth allowance")
    if branch:
        if depth != branch["depth_limit"] or branch.get("finished"):
            raise AuditError("branch is finished or its depth allowance changed")
        parent = branch["candidates"][-1]
        if value.get("parent_id") != parent or len(branch["candidates"]) >= depth:
            raise AuditError(
                "descendant must use the reviewed branch tip within its depth allowance"
            )
        if data["candidates"][parent]["state"] != "verified":
            raise AuditError(
                "measure and independently review the parent before expanding its branch"
            )
    elif "branch_id" in value:
        raise AuditError("new branches cannot claim an existing branch identity")
    return value


def reserve_round(workspace, run_id: str, request: dict) -> dict:
    validate_record("fix-round", request)
    request = copy.deepcopy(
        object_keys(
            request,
            {"operation_id", "host_id", "host_capacity", "branches", "round_id", "finish_branches"},
            {"operation_id", "host_id", "host_capacity"},
            "round reservation",
        )
    )
    for field in ("operation_id", "host_id"):
        text(request[field], field)
        if len(request[field]) > 200:
            raise AuditError("round identity exceeds 200 characters")
    if type(request["host_capacity"]) is not int or not 1 <= request["host_capacity"] <= 64:
        raise AuditError("host capacity must be between 1 and 64")
    branches, finishes = request.get("branches", []), request.get("finish_branches", [])
    if (
        not isinstance(branches, list)
        or not isinstance(finishes, list)
        or not branches
        and not finishes
    ):
        raise AuditError("a round must reserve branches or explicitly finish existing branches")
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        if "orchestration" not in data["profile"]:
            raise AuditError(
                "historical run has no orchestration contract; use its existing fix commands"
            )
        operations = data.setdefault("round_operations", [])
        existing = next(
            (op for op in operations if op["operation_id"] == request["operation_id"]), None
        )
        if existing:
            if existing["request"] != request:
                raise AuditError("round operation_id already belongs to another request")
            for cid in existing["candidate_ids"]:
                candidate = data["candidates"][cid]
                if candidate.get("creation_pending"):
                    engine._finish_creation(workspace, data, candidate)
                    engine._save(workspace, data)
            return {**copy.deepcopy(existing), "work": next_packet(workspace, run_id)}
        engine._scheduling(data)
        if data["candidates"][data["baseline_id"]]["state"] != "verified":
            raise AuditError(
                "finish baseline execution and independent review before reserving a round"
            )
        if any(op["state"] == "queued" for op in data.get("controls", [])):
            raise AuditError("acknowledge queued controls before reserving a round")
        round_ = next((r for r in data["rounds"] if r["round_id"] == request.get("round_id")), None)
        if request.get("round_id") and (
            not round_ or not round_.get("branches") or round_.get("completed")
        ):
            raise AuditError("round is unknown, historical, or already complete")
        if round_ and round_["host_id"] != request["host_id"]:
            raise AuditError("round belongs to another active host identity; resume that identity")
        if round_ is None:
            if finishes or any(not r.get("completed") for r in data["rounds"]):
                raise AuditError("finish the current round before reserving another")
            round_ = {
                "round_id": identifier("round", run_id, request["operation_id"]),
                "host_id": request["host_id"],
                "size": len(branches),
                "candidates": [],
                "branches": [],
                "starting_frontier": list(data["frontier"]),
            }
            data["rounds"].append(round_)
        known = {b["branch_id"]: b for b in round_["branches"]}
        for finish in finishes:
            object_keys(
                finish, {"branch_id", "reason"}, {"branch_id", "reason"}, "branch completion"
            )
            text(finish["reason"], "branch completion reason")
            branch = known.get(finish["branch_id"])
            if not branch:
                raise AuditError("completion must reference this round's branch")
            tip = data["candidates"][branch["candidates"][-1]]
            if any(t["state"] in {"running", "interrupted"} for t in tip["trials"]):
                raise AuditError("collect or cancel the live trial before finishing its branch")
            branch.update(finished=True, reason=finish["reason"])
            if tip["state"] not in engine.TERMINAL:
                tip["state"] = "cancelled"
        available = capacity(data, request["host_capacity"])
        if len(branches) > available:
            raise AuditError(
                f"round width exceeds available host, resource or execution budget capacity ({available})"
            )
        candidate_ids, seen = [], set()
        for value in branches:
            branch = known.get(value.get("branch_id")) if isinstance(value, dict) else None
            if known and branch is None:
                raise AuditError("descendants must identify an existing branch")
            brief = _brief(data, value, branch=branch)
            if branch and branch["branch_id"] in seen:
                raise AuditError("a round request may advance a branch only once")
            decision = search.choose_parent(data, brief.get("parent_id"))
            parent = data["candidates"][decision["chosen_parent"]]
            engine._verified_evidence(workspace, data, parent)
            number = data["usage"]["candidates"] + 1
            cid = identifier("candidate", run_id, number)
            if branch is None:
                branch = {
                    "branch_id": identifier("branch", round_["round_id"], len(round_["branches"])),
                    "depth_limit": brief["depth_limit"],
                    "candidates": [],
                }
                round_["branches"].append(branch)
            seen.add(branch["branch_id"])
            brief.update(
                parent_id=parent["candidate_id"],
                branch_id=branch["branch_id"],
                depth=len(branch["candidates"]) + 1,
            )
            candidate = engine._new_record(
                cid, parent["candidate_id"], brief["hypothesis"], brief["author"]
            )
            candidate.update(
                worktree=str(
                    (run_dir(workspace, run_id) / "candidates" / cid).relative_to(workspace.root)
                ),
                parent_revision=parent["source_revision"],
                creation_pending=True,
                operation_id=f"{request['operation_id']}:{len(candidate_ids)}",
                creation_request={
                    "parent_id": parent["candidate_id"],
                    "hypothesis": brief["hypothesis"],
                    "author": brief["author"],
                    "round_id": round_["round_id"],
                },
                brief=brief,
                round_id=round_["round_id"],
                search_decision_index=decision["index"],
                assignment={"state": "queued", "host_id": request["host_id"], "agent_id": None},
            )
            data["candidates"][cid] = candidate
            data["usage"]["candidates"] = number
            parent["last_expanded"] = number
            search.reserve(data, decision, cid, candidate["operation_id"])
            branch["candidates"].append(cid)
            round_["candidates"].append(cid)
            candidate_ids.append(cid)
        operation = {
            "operation_id": request["operation_id"],
            "request": request,
            "round_id": round_["round_id"],
            "candidate_ids": candidate_ids,
            "created_at": now(),
        }
        operations.append(operation)
        # Persist every identity and budget reservation before the first checkout or host dispatch.
        engine._save(workspace, data)
        for cid in candidate_ids:
            engine._finish_creation(workspace, data, data["candidates"][cid])
            engine._save(workspace, data)
    return {**copy.deepcopy(operation), "work": next_packet(workspace, run_id)}


def assign(workspace, run_id: str, candidate_id: str, host_id: str, agent_id: str) -> dict:
    text(agent_id, "host agent identity")
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        engine._scheduling(data)
        candidate = engine._candidate(data, candidate_id)
        assignment = candidate.get("assignment")
        if not assignment or assignment["host_id"] != host_id:
            raise AuditError("candidate assignment belongs to another host")
        if assignment["agent_id"] and assignment["agent_id"] != agent_id:
            raise AuditError(
                "candidate already has an assigned agent; resume its existing assignment"
            )
        assignment.update(state="assigned", agent_id=agent_id)
        engine._save(workspace, data)
    return copy.deepcopy(assignment)


def ideation_packet(workspace, data: dict) -> dict:
    candidates = sorted(
        data["candidates"], key=lambda cid: (data["candidates"][cid].get("created_at", ""), cid)
    )[-20:]
    packet = {
        "run_id": data["run_id"],
        "after_rounds": sum(bool(r.get("completed")) for r in data["rounds"]),
        "evidence": sorted(_evidence(data, candidates)),
        "code_paths": data["spec"]["editable_paths"],
        "hypotheses_tried": [
            {
                "candidate_id": cid,
                "hypothesis": data["candidates"][cid]["hypothesis"],
                "state": data["candidates"][cid]["state"],
                "checks": data["candidates"][cid].get("checks", []),
            }
            for cid in candidates
        ],
        "instruction": "Propose alternatives grounded in observed failures and approaches not yet tried. Retain unsuccessful hypotheses. No replacement measurements or changed gates.",
    }
    return {**packet, "packet_digest": digest(packet)}


def ideate(workspace, run_id: str, response: dict) -> dict:
    object_keys(
        response,
        {"packet_digest", "author", "hypotheses", "unsuccessful"},
        {"packet_digest", "author", "hypotheses", "unsuccessful"},
        "ideation response",
    )
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        if any(i.get("response") == response for i in data.get("ideations", [])):
            return next_packet(workspace, run_id)
        engine._update(data)
        if data["state"] != "awaiting_ideation":
            raise AuditError("run is not requesting a bounded ideation pass")
        packet = ideation_packet(workspace, data)
        if response["packet_digest"] != packet["packet_digest"]:
            raise AuditError("ideation response refers to stale evidence")
        text(response["author"], "hypothesis author")
        for key in ("hypotheses", "unsuccessful"):
            if not isinstance(response[key], list) or len(response[key]) > 10:
                raise AuditError(
                    "ideation may contain at most ten proposed or unsuccessful hypotheses"
                )
            for item in response[key]:
                object_keys(
                    item, {"hypothesis", "evidence"}, {"hypothesis", "evidence"}, "hypothesis"
                )
                text(item["hypothesis"], "hypothesis")
                if (
                    not isinstance(item["evidence"], list)
                    or not item["evidence"]
                    or any(ref not in packet["evidence"] for ref in item["evidence"])
                ):
                    raise AuditError("hypotheses must cite observed evidence")
        data.setdefault("ideations", []).append(
            {
                "after_rounds": packet["after_rounds"],
                "hypotheses": copy.deepcopy(response["hypotheses"]),
                "response": copy.deepcopy(response),
                "artifact": workspace.artifact(response),
                "at": now(),
            }
        )
        engine._save(workspace, data)
    return next_packet(workspace, run_id)


def next_packet(workspace, run_id: str, host_id: str | None = None) -> dict:
    """Read-only actionable packet. Time and polling revisions never count as progress."""
    data = load_run(workspace, run_id)
    engine._update(data)
    work = []
    cleanups = [
        c
        for c in data["candidates"].values()
        if c.get("cleanup_of") and not c.get("cleanup_outcome")
    ]
    if data["state"] == "stopped":
        reason = "Explicit stop; continuation requires user authorization"
    elif data.get("selected") and not cleanups:
        reason = (
            "A delivery candidate is selected; inspect or ship it within the user's authorization"
        )
    elif "orchestration" not in data["profile"]:
        reason = "Historical run: use status and its existing manual fix operations"
    else:
        reason = None
        for control in data.get("controls", []):
            if control["state"] in {"queued", "acknowledged"}:
                work.append(
                    {
                        "action": "acknowledge_control",
                        "operation_id": control["operation_id"],
                        "control": control["action"],
                    }
                )
        for cid, candidate in data["candidates"].items():
            if (
                evaluation.invalidated(data, candidate)
                or candidate["state"] in engine.TERMINAL
                or candidate["state"] == "cancelled"
                and candidate.get("brief")
            ):
                continue
            state = candidate["state"]
            action = {
                "editing": "author",
                "sealed": "execute",
                "interrupted": "reconcile",
                "cancelled": "reconcile",
                "running": "monitor",
                "awaiting_review": "independent_review",
            }.get(state)
            if candidate.get("creation_pending"):
                action = "recover_reservation"
            assignment = candidate.get("assignment")
            if assignment and host_id and assignment["host_id"] != host_id:
                action = "waiting_for_host"
            if action:
                work.append(
                    {
                        "action": action,
                        "candidate_id": cid,
                        "state": state,
                        "worktree": candidate["worktree"],
                        "brief": candidate.get("brief"),
                        "assignment": assignment,
                    }
                )
        for candidate in cleanups:
            if (
                candidate["state"] in engine.TERMINAL
                or candidate["state"] == "cancelled"
                or evaluation.invalidated(data, candidate)
            ):
                work.append(
                    {"action": "finalize_cleanup", "candidate_id": candidate["candidate_id"]}
                )
        pending_scan = learning.pending(data)
        if pending_scan.get("pending"):
            work.append({"action": "scan", "scan": pending_scan})
        for round_ in data["rounds"]:
            if round_.get("completed"):
                continue
            for branch in round_.get("branches", []):
                tip = data["candidates"][branch["candidates"][-1]]
                if not branch.get("finished") and tip["state"] == "verified":
                    work.append(
                        {
                            "action": "next_branch_hypothesis",
                            "round_id": round_["round_id"],
                            "branch": branch,
                            "parent_id": tip["candidate_id"],
                        }
                    )
        if data["state"] == "awaiting_ideation":
            work.append({"action": "ideate", "packet": ideation_packet(workspace, data)})
        elif data["state"] == "exhausted":
            work = [
                item
                for item in work
                if item["action"]
                in {"monitor", "reconcile", "independent_review", "scan", "finalize_cleanup"}
            ]
            reason = "Saved limits exhausted; inspect the frontier or request an explicit extension"
        elif not work and capacity(data) and search.eligible(data):
            work.append(
                {
                    "action": "reserve_round",
                    "capacity": capacity(data),
                    "parents": search.eligible(data),
                }
            )
    progress = {
        "state": data["state"],
        "candidates": [
            {
                "id": cid,
                "state": c["state"],
                "source": c.get("source_digest"),
                "review": c.get("review_artifact"),
                "assignment": c.get("assignment"),
                "trials": [
                    {"id": t["trial_id"], "state": t["state"], "artifact": t.get("artifact")}
                    for t in c["trials"]
                ],
            }
            for cid, c in data["candidates"].items()
        ],
        "controls": [(c["operation_id"], c["state"]) for c in data.get("controls", [])],
        "scans": [(s["scan_id"], s["state"]) for s in data.get("scans", [])],
        "ideations": len(data.get("ideations", [])),
    }
    return {
        "run_id": run_id,
        "state": data["state"],
        "reason": reason,
        "work": work,
        "progress_digest": digest(progress),
        "capacity": capacity(data),
        "actionable": any(item["action"] not in {"monitor", "waiting_for_host"} for item in work),
        "lesson_context": learning.context(workspace, data),
    }
