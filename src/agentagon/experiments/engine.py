"""Five resumable operations for agent-driven, measured local improvements.

The host supplies patches and reviews. Only runner observations establish checks
and metrics; neither the review contract nor issue updates can supply outcomes.
"""

import copy
import fcntl
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from agentagon.core.records import AuditError, digest, identifier, now, validate_record
from agentagon.experiments import checkouts, evaluation, runners, search
from agentagon.experiments.spec import validate_limits, validate_spec
from agentagon.experiments.store import list_runs, load_run, locked, run_dir, save_run
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace

TERMINAL = {"verified", "rejected", "failed", "duplicate"}
ACTIVE = {"editing", "sealed", "running", "interrupted", "cancelled", "awaiting_review"}
REVIEW_ASSESSMENTS = {"patch", "evaluator_integrity", "issue_relevance", "measurements"}


def _unfinished(data: dict, candidate: dict) -> bool:
    if candidate.get("brief") and candidate["state"] == "cancelled":
        return any(t["state"] in {"running", "interrupted"} for t in candidate["trials"])
    if evaluation.invalidated(data, candidate):
        return any(t["state"] in {"running", "interrupted"} for t in candidate["trials"])
    return candidate["state"] not in TERMINAL


def _elapsed(data: dict) -> float:
    elapsed = (
        datetime.fromisoformat(now()) - datetime.fromisoformat(data["created_at"])
    ).total_seconds()
    return max(data["usage"]["elapsed_seconds"], elapsed, 0)


def _update(data: dict) -> None:
    data["usage"]["elapsed_seconds"] = _elapsed(data)
    data["cleanup_pending"] = any(
        t.get("cleanup_pending") for c in data["candidates"].values() for t in c["trials"]
    )
    data["frontier"] = evaluation.frontier(data)
    history = {data["baseline_id"]: data["candidates"][data["baseline_id"]]}
    previous_vectors = _frontier_vectors(data, history)
    for round_ in data["rounds"]:
        history.update({cid: data["candidates"][cid] for cid in round_["candidates"]})
        if round_.get("completed"):
            previous_vectors = _frontier_vectors(data, history)
            continue
        members = [data["candidates"][cid] for cid in round_["candidates"]]
        if "branches" in round_:
            from agentagon.experiments.orchestration import update_round

            ready = update_round(data, round_)
        else:
            ready = len(members) >= round_["size"] and not any(
                _unfinished(data, c) for c in members
            )
        if not ready:
            break
        round_["completed"] = True
        round_["frontier"] = evaluation.frontier({**data, "candidates": history})
        current_vectors = _frontier_vectors(data, history)
        if current_vectors == previous_vectors:
            data["stagnation"] += 1
        else:
            data["stagnation"] = 0
        previous_vectors = current_vectors
    if data["state"] != "stopped":
        exhausted = data["usage"]["elapsed_seconds"] >= data["limits"]["max_elapsed_seconds"]
        # A reserved candidate may still finish after the allocation bound is met.
        no_pending = not any(_unfinished(data, c) for c in data["candidates"].values())
        exhausted |= no_pending and (
            data["usage"]["candidates"] >= data["limits"]["max_candidates"]
            or data["usage"]["trials"] >= data["limits"]["max_trials"]
        )
        data["state"] = "exhausted" if exhausted else "active"
        cleanup_active = any(
            c.get("cleanup_of") and _unfinished(data, c) for c in data["candidates"].values()
        )
        if (
            not exhausted
            and not cleanup_active
            and data["stagnation"] >= data["limits"]["stagnation_rounds"]
        ):
            from agentagon.experiments.orchestration import stagnation_state

            data["state"] = stagnation_state(data)


def _frontier_vectors(data: dict, candidates: dict) -> set[tuple]:
    names = sorted(data["spec"]["metrics"])
    return {
        tuple(candidates[cid]["metrics"][name] for name in names)
        for cid in evaluation.frontier({**data, "candidates": candidates})
    }


def _save(workspace: Workspace, data: dict) -> None:
    _update(data)
    save_run(workspace, data)


def _candidate(data: dict, candidate_id: str | None) -> dict:
    candidate_id = candidate_id or data["baseline_id"]
    if candidate_id not in data["candidates"]:
        raise AuditError("candidate does not belong to this run")
    return data["candidates"][candidate_id]


def _owned(workspace: Workspace, data: dict, relative: str) -> Path:
    path = workspace.checked(workspace.root / relative)
    if not path.resolve().is_relative_to(run_dir(workspace, data["run_id"]).resolve()):
        raise AuditError("execution resource does not belong to this run")
    return path


def _new_record(candidate_id: str, parent: str | None, hypothesis: str, author: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "parent_id": parent,
        "hypothesis": hypothesis,
        "author": author,
        "created_at": now(),
        "state": "editing",
        "trials": [],
        "metrics": {},
        "variation": {},
        "task_metrics": {},
        "task_variation": {},
        "constraints": [],
        "checks": [],
        "review": None,
        "feasible": False,
        "last_expanded": 0,
    }


def _freeze(workspace: Workspace, spec: dict) -> list[dict]:
    entries = []
    for kind in ("overlays", "inputs"):
        for entry in spec[kind]:
            source = checkouts.checked_file(workspace.root, entry["source"])
            content = source.read_bytes()
            entries.append(
                {
                    "kind": kind,
                    "path": entry["path"],
                    "deliver": entry.get("deliver", False),
                    "artifact": workspace.blob(content, ".input"),
                    "digest": hashlib.sha256(content).hexdigest(),
                    "mode": source.stat().st_mode & 0o777,
                }
            )
    return entries


def start(
    workspace: Workspace,
    spec: dict,
    profile_name: str,
    goal: str | None = None,
    issue_ids: list[str] | None = None,
) -> dict:
    workspace.require_initialized()
    spec = copy.deepcopy(spec)
    if not isinstance(spec, dict):
        raise AuditError("evaluation specification must be an object")
    if goal is not None:
        spec["goal"] = goal
    if issue_ids:
        spec["issue_ids"] = list(dict.fromkeys(spec.get("issue_ids", []) + issue_ids))
    validate_record("fix-spec", spec)
    profile = Config().profile(workspace.root, profile_name)
    from agentagon.experiments.evidence import DEFAULT_LIMITS

    profile.setdefault("evidence", dict(DEFAULT_LIMITS))
    from agentagon.experiments.orchestration import DEFAULT_SETTINGS

    profile.setdefault("orchestration", dict(DEFAULT_SETTINGS))
    if "repetitions" not in spec and "repetitions" in profile:
        spec["repetitions"] = profile["repetitions"]
    spec = validate_spec(spec)
    if spec.get("resources", {}).get("slots", 1) > profile["orchestration"]["resource_slots"]:
        raise AuditError("benchmark resource requirements exceed the saved resource capacity")
    policy = search.validate_policy(profile.get("search", {}), spec)
    revision = checkouts.clean_revision(workspace.root)
    from agentagon.storage.issues import list_issues

    known = {issue["issue_id"] for issue in list_issues(workspace)}
    if set(spec["issue_ids"]) - known:
        raise AuditError("fix specification references unknown saved issues")
    if profile["limits"]["max_trials"] < spec["repetitions"]:
        raise AuditError("trial limit must cover every baseline repetition")
    run_id = identifier("run", str(workspace.root), revision, uuid.uuid4().hex)
    baseline_id = identifier("candidate", run_id, "baseline")
    directory = run_dir(workspace, run_id)
    with locked(workspace, run_id):
        frozen = _freeze(workspace, spec)
        baseline_path = directory / "candidates" / baseline_id
        checkouts.create(workspace.root, baseline_path, revision)
        checkouts.copy_frozen(workspace.root, baseline_path, [e for e in frozen if e["deliver"]])
        baseline_revision = checkouts.snapshot(
            baseline_path, revision, "Add frozen regression checks"
        )
        checkouts.retain(workspace.root, run_id, baseline_id, baseline_revision)
        # Frozen deliverable tests are part of every candidate parent, not editable patches.
        for scope in spec["evaluation_paths"]:
            exists = (baseline_path / scope).exists() or any(
                checkouts.under(e["path"], scope) for e in frozen
            )
            if not exists:
                raise AuditError("declared evaluation paths must exist in the frozen evaluation")
        baseline = _new_record(
            baseline_id,
            None,
            "Measure the original baseline and reproduce targeted defects",
            "baseline",
        )
        baseline.update(
            {
                "state": "sealed",
                "source_revision": baseline_revision,
                "source_digest": checkouts.tree(workspace.root, baseline_revision),
                "worktree": str(baseline_path.relative_to(workspace.root)),
            }
        )
        data = {
            "version": 1,
            "run_id": run_id,
            "origin": str(workspace.root),
            "created_at": now(),
            "goal": spec["goal"],
            "issue_ids": spec["issue_ids"],
            "spec": spec,
            "evaluation_digest": digest(spec),
            "profile_name": profile_name,
            "profile": profile,
            "profile_digest": digest(profile),
            "limits": copy.deepcopy(profile["limits"]),
            "origin_revision": revision,
            "baseline_revision": baseline_revision,
            "baseline_id": baseline_id,
            "frozen": frozen,
            "inputs_digest": digest(frozen),
            "state": "active",
            "usage": {"candidates": 0, "trials": 0, "elapsed_seconds": 0},
            "candidates": {baseline_id: baseline},
            "frontier": [],
            "rounds": [],
            "stagnation": 0,
            "selected": None,
            "selections": [],
            "continuations": [],
            "cleanup_pending": False,
        }
        search.set_policy(data, policy)
        _save(workspace, data)
    return status(workspace, run_id, baseline_id)


def _scheduling(data: dict) -> None:
    _update(data)
    if data["state"] in {"stopped", "exhausted", "awaiting_ideation"}:
        raise AuditError(
            "run is stopped, exhausted, or awaiting ideation; inspect fix next and continue only within authorized limits"
        )


def new(
    workspace: Workspace,
    run_id: str,
    parent_id: str | None = None,
    hypothesis: str = "",
    author: str = "unknown",
    round_id: str | None = None,
    operation_id: str | None = None,
    cleanup_of: str | None = None,
) -> dict:
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise AuditError("candidate requires a concrete hypothesis")
    if not isinstance(author, str) or not author.strip():
        raise AuditError("candidate requires an author identity")
    if operation_id is not None and (
        not isinstance(operation_id, str)
        or not operation_id.strip()
        or len(operation_id) > 200
        or "\x00" in operation_id
    ):
        raise AuditError("candidate operation_id must be nonempty text of at most 200 characters")
    creation_request = {
        "parent_id": parent_id,
        "hypothesis": hypothesis,
        "author": author,
        "round_id": round_id,
    }
    if cleanup_of is not None:
        creation_request["cleanup_of"] = cleanup_of
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        if operation_id is not None:
            existing = next(
                (c for c in data["candidates"].values() if c.get("operation_id") == operation_id),
                None,
            )
            if existing is not None:
                if existing["creation_request"] != creation_request:
                    raise AuditError("candidate operation_id already belongs to another request")
                if existing.get("creation_pending"):
                    _finish_creation(workspace, data, existing)
                    _save(workspace, data)
                return status(workspace, run_id, existing["candidate_id"])
        if cleanup_of is not None:
            _update(data)
            if (
                data["state"] == "stopped"
                or _elapsed(data) >= data["limits"]["max_elapsed_seconds"]
            ):
                raise AuditError("cleanup requires an active run within its elapsed-time limit")
            if (data.get("selected") or {}).get(
                "candidate_id"
            ) != cleanup_of or parent_id != cleanup_of:
                raise AuditError(
                    "cleanup must preserve the user's selected candidate as its parent"
                )
            if any(_unfinished(data, c) for c in data["candidates"].values()):
                raise AuditError("finish pending candidate work before starting shipping cleanup")
            if data["limits"]["max_trials"] - data["usage"]["trials"] < data["spec"]["repetitions"]:
                raise AuditError("cleanup trial budget exhausted; extend limits explicitly")
        else:
            _scheduling(data)
        if any(r.get("branches") and not r.get("completed") for r in data["rounds"]):
            raise AuditError(
                "a coordinated round is active; use fix round to advance or finish its branches"
            )
        baseline = _candidate(data, None)
        if baseline["state"] != "verified":
            raise AuditError(
                "finish baseline execution and its independent review before proposing candidates"
            )
        if data["usage"]["candidates"] >= data["limits"]["max_candidates"]:
            raise AuditError("candidate budget exhausted; extend limits explicitly")
        pending = [
            c
            for c in data["candidates"].values()
            if c["state"] in ACTIVE and not evaluation.invalidated(data, c)
        ]
        if len(pending) >= data["limits"]["parallel_candidates"]:
            raise AuditError(
                "parallel candidate capacity is occupied; complete pending candidates first"
            )
        decision = search.choose_parent(data, parent_id)
        parent_id = decision["chosen_parent"]
        parent = _candidate(data, parent_id)
        _verified_evidence(workspace, data, parent)
        number = data["usage"]["candidates"] + 1
        candidate_id = identifier("candidate", run_id, number)
        destination = run_dir(workspace, run_id) / "candidates" / candidate_id
        candidate = _new_record(candidate_id, parent_id, hypothesis, author)
        candidate["worktree"] = str(destination.relative_to(workspace.root))
        candidate["parent_revision"] = parent["source_revision"]
        candidate["operation_id"] = operation_id
        candidate["creation_request"] = creation_request
        candidate["creation_pending"] = True
        candidate["search_decision_index"] = decision["index"]
        if cleanup_of is not None:
            candidate["cleanup_of"] = cleanup_of
            candidate["cleanup_original_selection"] = copy.deepcopy(data["selected"])
        round_ = next(
            (
                r
                for r in data["rounds"]
                if not r.get("completed") and len(r["candidates"]) < r["size"]
            ),
            None,
        )
        if round_ is None:
            round_ = {
                "round_id": round_id or f"round_{len(data['rounds']) + 1:06d}",
                "size": (
                    min(
                        data["profile"]["orchestration"]["round_width"],
                        data["limits"]["parallel_candidates"],
                        data["profile"]["orchestration"]["host_capacity"],
                        data["profile"]["orchestration"]["resource_slots"]
                        // data["spec"].get("resources", {}).get("slots", 1),
                        data["limits"]["max_candidates"] - data["usage"]["candidates"],
                    )
                    if "orchestration" in data["profile"]
                    else len(decision["selection_pool"])
                ),
                "candidates": [],
                "starting_frontier": list(data["frontier"]),
            }
            if any(r["round_id"] == round_["round_id"] for r in data["rounds"]):
                raise AuditError("round ID is already complete or fully allocated")
            data["rounds"].append(round_)
        elif round_id is not None and round_id != round_["round_id"]:
            raise AuditError("finish allocating the current round before beginning another")
        candidate["round_id"] = round_["round_id"]
        round_["candidates"].append(candidate_id)
        parent["last_expanded"] = number
        data["usage"]["candidates"] = number
        data["candidates"][candidate_id] = candidate
        search.reserve(data, decision, candidate_id, operation_id)
        # The parent, budget slot and operation identity survive even a failed Git creation.
        _save(workspace, data)
        _finish_creation(workspace, data, candidate)
        _save(workspace, data)
    return status(workspace, run_id, candidate_id)


def _finish_creation(workspace: Workspace, data: dict, candidate: dict) -> None:
    if evaluation.invalidated(data, candidate):
        raise AuditError("invalidated candidates cannot create or resume work")
    if data["state"] == "stopped":
        raise AuditError("run was stopped; use fix run --continue explicitly")
    destination = _owned(workspace, data, candidate["worktree"])
    checkouts.create(workspace.root, destination, candidate["parent_revision"])
    candidate["creation_pending"] = False


@contextmanager
def _driver(workspace: Workspace, run_id: str, candidate_id: str):
    path = run_dir(workspace, run_id) / (candidate_id + ".lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AuditError(
                "candidate already has an active fix run invocation; inspect status"
            ) from exc
        yield


def _continue(data: dict, continuation: bool, limits: dict | None) -> None:
    if limits is not None:
        if not continuation:
            raise AuditError("limit changes require --continue")
        extended = validate_limits(limits)
        if any(extended[k] < data["limits"][k] for k in extended):
            raise AuditError("continuation may only extend limits; consumed budgets cannot reset")
        lifetime = data["profile"]["runner"].get("sandbox_timeout_seconds")
        if (
            data["profile"]["runner"]["kind"] == "e2b"
            and lifetime is not None
            and lifetime < extended["trial_timeout_seconds"] + 30
        ):
            raise AuditError(
                "extended trial timeout exceeds the frozen sandbox lifetime; use a new profile and run"
            )
        if (
            extended["parallel_trials"] > 1
            and data["profile"]["runner"]["kind"] != "e2b"
            and not data["profile"]["runner"].get("independent_capacity")
        ):
            raise AuditError(
                "parallel shared-host measurements require a new independent-capacity profile and run"
            )
        data["limits"] = extended
    if continuation:
        data["continuations"].append(
            {
                "at": now(),
                "usage": copy.deepcopy(data["usage"]),
                "limits": copy.deepcopy(data["limits"]),
            }
        )
        data["state"] = "active"
        data["stagnation"] = 0
        for candidate in data["candidates"].values():
            if candidate["state"] == "cancelled":
                candidate["state"] = "sealed"


def _seal(workspace: Workspace, data: dict, candidate: dict) -> None:
    if candidate["state"] != "editing":
        return
    path = _owned(workspace, data, candidate["worktree"])
    revision = checkouts.snapshot(path, candidate["parent_revision"], candidate["hypothesis"])
    checkouts.validate_scope(workspace.root, data["baseline_revision"], revision, data["spec"])
    if candidate.get("brief"):
        for changed in checkouts.changes(workspace.root, candidate["parent_revision"], revision):
            if not any(
                checkouts.under(changed, scope) for scope in candidate["brief"]["editable_paths"]
            ):
                raise AuditError("candidate changed files outside its assigned brief")
    candidate["source_revision"] = revision
    candidate["source_digest"] = checkouts.tree(workspace.root, revision)
    checkouts.retain(workspace.root, data["run_id"], candidate["candidate_id"], revision)
    # A duplicate is evidence of a proposal, not a new measured candidate.
    existing = next(
        (
            c
            for c in data["candidates"].values()
            if c["candidate_id"] != candidate["candidate_id"]
            and c.get("source_digest") == candidate["source_digest"]
        ),
        None,
    )
    if existing and not candidate.get("cleanup_of"):
        candidate["state"] = "duplicate"
        candidate["duplicate_of"] = existing["candidate_id"]
    else:
        candidate["state"] = "sealed"


def _request(data: dict, candidate: dict, trial_id: str, repetition: int) -> dict:
    remaining = data["limits"]["max_elapsed_seconds"] - _elapsed(data)
    if remaining <= 0:
        raise AuditError("elapsed-time budget exhausted; extend limits explicitly")
    return {
        "attempt_id": trial_id,
        **(
            {
                "run_id": data["run_id"],
                "candidate_id": candidate["candidate_id"],
                "evidence_limits": data["profile"]["evidence"],
            }
            if "evidence" in data["profile"]
            else {}
        ),
        "source_digest": candidate["source_digest"],
        "evaluation_digest": data["evaluation_digest"],
        "inputs_digest": data["inputs_digest"],
        "profile_digest": data["profile_digest"],
        "seed": data["spec"]["seeds"][repetition],
        "timeout_seconds": min(data["limits"]["trial_timeout_seconds"], remaining),
        "deadline_at": (
            datetime.fromisoformat(data["created_at"])
            + timedelta(seconds=data["limits"]["max_elapsed_seconds"])
        ).isoformat(),
        "commands": evaluation.commands(
            data["profile"], data["spec"], baseline=candidate["candidate_id"] == data["baseline_id"]
        ),
    }


def _reserve(workspace: Workspace, data: dict, candidate: dict) -> dict | None:
    if evaluation.invalidated(data, candidate):
        raise AuditError("invalidated candidates cannot start or resume execution")
    if candidate["trials"] and candidate["trials"][-1]["state"] in {"running", "interrupted"}:
        return candidate["trials"][-1]
    completed = _completed(candidate)
    if len(completed) == data["spec"]["repetitions"]:
        return None
    _scheduling(data)
    if data["usage"]["trials"] >= data["limits"]["max_trials"]:
        data["state"] = "exhausted"
        raise AuditError("trial budget exhausted; extend limits explicitly")
    active = sum(
        t["state"] in {"running", "interrupted"}
        for c in data["candidates"].values()
        for t in c["trials"]
    )
    if active >= data["limits"]["parallel_trials"]:
        raise AuditError("measurement capacity is occupied; reconcile or finish the existing trial")
    repetition = len(completed)
    trial_id = identifier(
        "trial", data["run_id"], candidate["candidate_id"], len(candidate["trials"])
    )
    attempt_dir = run_dir(workspace, data["run_id"]) / "attempts" / trial_id
    source = run_dir(workspace, data["run_id"]) / "execution" / trial_id
    checkouts.create(workspace.root, source, candidate["source_revision"])
    checkouts.copy_frozen(workspace.root, source, data["frozen"])
    trial = {
        "trial_id": trial_id,
        "state": "running",
        "repetition": repetition,
        "attempt_dir": str(attempt_dir.relative_to(workspace.root)),
        "source_path": str(source.relative_to(workspace.root)),
        "manifest": checkouts.source_manifest(source),
        "request": _request(data, candidate, trial_id, repetition),
        "started_at": now(),
        "artifact": None,
        "cleanup_pending": False,
    }
    candidate["trials"].append(trial)
    candidate["state"] = "running"
    data["usage"]["trials"] += 1
    return trial


def _validate_result(
    data: dict, candidate: dict, trial: dict, result: dict
) -> tuple[dict, list[dict], dict]:
    index = candidate["trials"].index(trial)
    expected_id = identifier("trial", data["run_id"], candidate["candidate_id"], index)
    request = trial["request"]
    if (
        trial["trial_id"] != expected_id
        or request["attempt_id"] != expected_id
        or request["source_digest"] != candidate["source_digest"]
        or any(
            request[key] != data[key]
            for key in ("evaluation_digest", "inputs_digest", "profile_digest")
        )
        or request["commands"]
        != evaluation.commands(
            data["profile"], data["spec"], baseline=candidate["candidate_id"] == data["baseline_id"]
        )
        or type(trial["repetition"]) is not int
        or not 0 <= trial["repetition"] < data["spec"]["repetitions"]
        or request["seed"] != data["spec"]["seeds"][trial["repetition"]]
    ):
        raise AuditError("trial request does not match this candidate's frozen execution contract")
    for key in (
        "attempt_id",
        "source_digest",
        "evaluation_digest",
        "inputs_digest",
        "profile_digest",
    ):
        if result.get(key) != trial["request"][key]:
            raise AuditError("trial result is foreign or has changed source/evaluation identity")
    if result.get("state") == "preflight_failed":
        raise AuditError("preflight check failed; remaining commands and benchmark were skipped")
    if result.get("state") != "completed":
        raise AuditError(
            "trial did not complete; setup failures and crashed checks do not establish reproduction"
        )
    if result.get("source_manifest_before") != trial["manifest"]:
        raise AuditError("runner staged a different evaluation/source identity")
    after = result.get("source_manifest_after", {})
    if any(after.get(name) != value for name, value in trial["manifest"].items()):
        raise AuditError("evaluation modified sealed source or frozen evaluator inputs")
    commands = result.get("results", [])
    if not isinstance(commands, list) or len(commands) != len(trial["request"]["commands"]):
        raise AuditError("required evaluation commands did not all complete")
    checks = []
    definitions = {c["id"]: c for c in data["spec"]["checks"]}
    for expected, actual in zip(trial["request"]["commands"], commands, strict=True):
        if (
            actual.get("id") != expected["id"]
            or actual.get("role") != expected["role"]
            or type(actual.get("exit_code")) is not int
            or actual.get("timed_out")
        ):
            raise AuditError("invalid command outcomes in trial")
        code = actual["exit_code"]
        if expected["role"] != "check":
            if code != 0:
                raise AuditError("setup or benchmark failed; metrics cannot be admitted")
        else:
            definition = definitions[expected["id"]]
            wanted = (
                definition["baseline_expected"]
                if candidate["candidate_id"] == data["baseline_id"]
                else "pass"
            )
            # Deliberate assertion failures use 1. Usage, collection and signal failures
            # must not establish reproduction (for example, pytest exits 2 through 5).
            passed = code == 0 if wanted == "pass" else code == 1
            checks.append(
                {
                    "id": definition["id"],
                    "passed": passed,
                    "expected": wanted,
                    "exit_code": code,
                    "issue_ids": definition["issue_ids"],
                }
            )
    try:
        output = json.loads(result.get("benchmark_output") or "", parse_constant=lambda _: None)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AuditError("benchmark must write structured JSON to AGENTAGON_RESULT_PATH") from exc
    if not isinstance(output, dict) or not isinstance(output.get("metrics"), dict):
        raise AuditError("benchmark result requires named numeric metrics")
    # Validate every reported metric, including extra values, before retaining measurements.
    from agentagon.experiments.spec import finite

    metrics = {name: finite(value) for name, value in output["metrics"].items()}
    if set(data["spec"]["metrics"]) - set(metrics):
        raise AuditError("benchmark omitted required metrics")
    tasks = evaluation.task_metrics(data["spec"], output)
    runtime = result.get("runtime_identity")
    if not isinstance(runtime, dict) or any(
        not isinstance(runtime.get(key), str) or not runtime[key]
        for key in ("platform", "machine", "os_release", "python", "python_version")
    ):
        raise AuditError("trial omitted the actual runner runtime identity")
    if data.get("runtime_identity") is not None and runtime != data["runtime_identity"]:
        raise AuditError("runner runtime identity changed; begin a new run")
    data.setdefault("runtime_identity", runtime)
    return metrics, checks, tasks


def _collect(workspace: Workspace, data: dict, candidate: dict, trial: dict, result: dict) -> None:
    trial["artifact"] = workspace.artifact(result)
    observations = trial.setdefault("observations", [])
    if trial["artifact"] not in observations:
        observations.append(trial["artifact"])
    trial["cleanup_pending"] = bool(result.get("cleanup_pending"))
    trial["ended_at"] = now()
    if result.get("state") == "interrupted" and not result.get("finalized"):
        trial["state"] = candidate["state"] = "interrupted"
        trial["error"] = (
            "Execution outcome is inconclusive; reconcile this same attempt with fix run"
        )
        return
    try:
        trial["metrics"], trial["checks"], trial["task_metrics"] = _validate_result(
            data, candidate, trial, result
        )
    except AuditError as exc:
        trial["state"] = candidate["state"] = "failed"
        trial["error"] = str(exc)
        if result.get("state") == "cancelled":
            trial["state"] = candidate["state"] = "cancelled"
    else:
        trial["state"] = "completed"
        candidate["state"] = "sealed"
    source = _owned(workspace, data, trial["source_path"])
    if not checkouts.remove(workspace.root, source):
        trial["cleanup_pending"] = True


def _completed(candidate: dict) -> list[dict]:
    return [t for t in candidate["trials"] if t["state"] == "completed"]


def _summarize(data: dict, candidate: dict) -> None:
    completed = _completed(candidate)
    if len(completed) != data["spec"]["repetitions"]:
        return
    if [t["repetition"] for t in completed] != list(range(data["spec"]["repetitions"])):
        raise AuditError("required repetitions contain duplicates or missing seeds")
    candidate["metrics"], candidate["variation"] = evaluation.aggregate(
        data["spec"], [t["metrics"] for t in completed]
    )
    candidate["task_metrics"], candidate["task_variation"] = evaluation.aggregate_tasks(
        data["spec"], [t.get("task_metrics", {}) for t in completed]
    )
    baseline = (
        candidate["metrics"]
        if candidate["candidate_id"] == data["baseline_id"]
        else _candidate(data, None)["metrics"]
    )
    candidate["constraints"] = evaluation.constraints(data["spec"], candidate["metrics"], baseline)
    candidate["checks"] = [
        {**c, "passed": all(t["checks"][i]["passed"] for t in completed)}
        for i, c in enumerate(completed[0]["checks"])
    ]
    candidate["machine_passed"] = all(c["passed"] for c in candidate["checks"])
    # Baseline reproduction can be verified even when the existing product is infeasible.
    candidate["feasible"] = (
        candidate["machine_passed"]
        and all(c["passed"] for c in candidate["constraints"])
        and all(c["expected"] == "pass" for c in candidate["checks"])
    )
    candidate["state"] = "awaiting_review"


def _review_template(data: dict, candidate: dict) -> dict:
    return {
        "version": 1,
        "run_id": data["run_id"],
        "candidate_id": candidate["candidate_id"],
        "source_digest": candidate["source_digest"],
        "evaluation_digest": data["evaluation_digest"],
        "inputs_digest": data["inputs_digest"],
        "trial_ids": [t["trial_id"] for t in candidate["trials"]],
        "reviewer": "",
        "verdict": "pending",
        "rationale": "",
        "evidence": [t["artifact"] for t in candidate["trials"] if t.get("artifact")],
        "assessments": dict.fromkeys(sorted(REVIEW_ASSESSMENTS), False),
    }


def _accept_review(workspace: Workspace, data: dict, candidate: dict, review: dict) -> None:
    if evaluation.invalidated(data, candidate):
        raise AuditError("invalidated candidates cannot be verified")
    validate_record("fix-review", review)
    expected = _review_template(data, candidate)
    if any(
        review[k] != expected[k]
        for k in (
            "version",
            "run_id",
            "candidate_id",
            "source_digest",
            "evaluation_digest",
            "inputs_digest",
            "trial_ids",
        )
    ):
        raise AuditError("review is stale or belongs to different source, evaluation or trials")
    if review["reviewer"] == candidate["author"]:
        raise AuditError("a separate host review pass must identify a different reviewer")
    if not review["evidence"] or set(review["evidence"]) - set(expected["evidence"]):
        raise AuditError("review must cite this candidate's retained engine evidence")
    if candidate.get("review") and candidate["review"] != review:
        raise AuditError("completed reviews are immutable; propose a new candidate")
    if candidate["state"] != "awaiting_review" and not candidate.get("review"):
        raise AuditError("complete all required execution before submitting a review")
    # Re-read every checksummed observation; handwritten receipts never establish metrics.
    task_samples = []
    for trial in _completed(candidate):
        metrics, checks, tasks = _validate_result(
            data, candidate, trial, workspace.read_artifact(trial["artifact"])
        )
        if (
            metrics != trial["metrics"]
            or checks != trial["checks"]
            or tasks != trial.get("task_metrics", {})
        ):
            raise AuditError("recorded measurement does not match retained execution evidence")
        task_samples.append(tasks)
    tasks, task_variation = evaluation.aggregate_tasks(data["spec"], task_samples)
    if tasks != candidate.get("task_metrics", {}) or task_variation != candidate.get(
        "task_variation", {}
    ):
        raise AuditError("task summary does not match retained execution evidence")
    if candidate.get("review"):
        if candidate["state"] == "verified":
            _verified_evidence(workspace, data, candidate)
        return
    candidate["review"] = copy.deepcopy(review)
    candidate["review_artifact"] = workspace.artifact(review)
    passing = (
        review["verdict"] == "pass"
        and all(review["assessments"].values())
        and candidate["machine_passed"]
    )
    if candidate["candidate_id"] != data["baseline_id"]:
        passing &= candidate["feasible"]
    candidate["state"] = "verified" if passing else "rejected"


def _retry_cleanup(workspace: Workspace, run_id: str) -> None:
    data = load_run(workspace, run_id)
    for candidate in data["candidates"].values():
        for trial in candidate["trials"]:
            if not trial.get("cleanup_pending") or trial["state"] in {"running", "interrupted"}:
                continue
            try:
                result = runners.cleanup(
                    data["profile"], _owned(workspace, data, trial["attempt_dir"])
                )
                trial["cleanup_pending"] = bool(result.get("cleanup_pending"))
                if not checkouts.remove(
                    workspace.root, _owned(workspace, data, trial["source_path"])
                ):
                    trial["cleanup_pending"] = True
            except (AuditError, OSError, ValueError):
                trial["cleanup_pending"] = True
            with locked(workspace, run_id):
                current = load_run(workspace, run_id)
                current_trial = next(
                    t
                    for t in current["candidates"][candidate["candidate_id"]]["trials"]
                    if t["trial_id"] == trial["trial_id"]
                )
                current_trial["cleanup_pending"] = trial["cleanup_pending"]
                _save(workspace, current)


def run(
    workspace: Workspace,
    run_id: str,
    candidate_id: str | None = None,
    review: dict | None = None,
    continue_run: bool = False,
    limits: dict | None = None,
) -> dict:
    data = load_run(workspace, run_id)
    candidate_id = _candidate(data, candidate_id)["candidate_id"]
    with _driver(workspace, run_id, candidate_id):
        _retry_cleanup(workspace, run_id)
        with locked(workspace, run_id):
            data = load_run(workspace, run_id)
            _continue(data, continue_run, limits)
            candidate = _candidate(data, candidate_id)
            if evaluation.invalidated(data, candidate):
                raise AuditError("invalidated candidates cannot start or resume execution")
            if digest(data["frozen"]) != data["inputs_digest"]:
                raise AuditError("frozen evaluation inputs changed")
            if review is not None:
                _accept_review(workspace, data, candidate, review)
                _save(workspace, data)
                return status(workspace, run_id, candidate_id)
            if candidate["state"] in TERMINAL or candidate["state"] == "awaiting_review":
                if candidate["state"] == "verified":
                    _verified_evidence(workspace, data, candidate)
                _save(workspace, data)
                return status(workspace, run_id, candidate_id)
            if data["state"] == "stopped":
                raise AuditError("run was stopped; use fix run --continue explicitly")
            if candidate.get("creation_pending"):
                _finish_creation(workspace, data, candidate)
                _save(workspace, data)
                return status(workspace, run_id, candidate_id)
            _seal(workspace, data, candidate)
            _save(workspace, data)
        while True:
            with locked(workspace, run_id):
                data = load_run(workspace, run_id)
                candidate = _candidate(data, candidate_id)
                if (
                    candidate["state"] in TERMINAL
                    or data["state"] == "stopped"
                    or evaluation.invalidated(data, candidate)
                ):
                    break
                trial = _reserve(workspace, data, candidate)
                if trial is None:
                    _summarize(data, candidate)
                    _save(workspace, data)
                    break
                _save(workspace, data)
                profile = copy.deepcopy(data["profile"])
                source = _owned(workspace, data, trial["source_path"])
                attempt_dir = _owned(workspace, data, trial["attempt_dir"])
                request = copy.deepcopy(trial["request"])
            try:
                result = runners.execute(profile, source, attempt_dir, request)
            except KeyboardInterrupt:
                stop(workspace, run_id)
                raise AuditError(
                    "fix interrupted; owned execution cancelled and evidence retained"
                ) from None
            except (AuditError, OSError) as exc:
                with locked(workspace, run_id):
                    data = load_run(workspace, run_id)
                    current = _candidate(data, candidate_id)
                    current["state"] = current["trials"][-1]["state"] = "interrupted"
                    _save(workspace, data)
                raise AuditError(
                    "execution interrupted; reconcile the same attempt with fix run"
                ) from exc
            with locked(workspace, run_id):
                data = load_run(workspace, run_id)
                candidate = _candidate(data, candidate_id)
                trial = candidate["trials"][-1]
                _collect(workspace, data, candidate, trial, result)
                _save(workspace, data)
                if candidate["state"] in {"failed", "interrupted", "cancelled"}:
                    break
    return status(workspace, run_id, candidate_id)


def _cancel_attempts(workspace: Workspace, data: dict, pending: list[tuple]) -> None:
    run_id = data["run_id"]
    for candidate_id, trial_id, relative, request in pending:
        result = runners.cancel(data["profile"], _owned(workspace, data, relative), request=request)
        with locked(workspace, run_id):
            data = load_run(workspace, run_id)
            candidate = _candidate(data, candidate_id)
            trial = next(t for t in candidate["trials"] if t["trial_id"] == trial_id)
            if trial["state"] in {"running", "interrupted"}:
                _collect(workspace, data, candidate, trial, result)
            _save(workspace, data)
    _retry_cleanup(workspace, run_id)


def cancel_invalidated(workspace: Workspace, run_id: str) -> dict:
    """Cancel only invalidated owned attempts; unconfirmed attempts remain retryable."""
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        if evaluation.invalidated(data, _candidate(data, None)):
            data["state"] = "stopped"
            data.setdefault("stopped_at", now())
            _save(workspace, data)
        pending = [
            (c["candidate_id"], t["trial_id"], t["attempt_dir"], t["request"])
            for c in data["candidates"].values()
            if evaluation.invalidated(data, c)
            for t in c["trials"]
            if t["state"] in {"running", "interrupted"}
        ]
    _cancel_attempts(workspace, data, pending)
    result = status(workspace, run_id)
    current = load_run(workspace, run_id)
    result["invalidated_active_attempts"] = [
        {"candidate_id": c["candidate_id"], "trial_id": t["trial_id"], "state": t["state"]}
        for c in current["candidates"].values()
        if evaluation.invalidated(current, c)
        for t in c["trials"]
        if t["state"] in {"running", "interrupted"}
    ]
    return result


def stop(workspace: Workspace, run_id: str) -> dict:
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        data["state"] = "stopped"
        data.setdefault("stopped_at", now())
        pending = [
            (c["candidate_id"], t["trial_id"], t["attempt_dir"], t["request"])
            for c in data["candidates"].values()
            for t in c["trials"]
            if t["state"] in {"running", "interrupted"}
        ]
        _save(workspace, data)
    _cancel_attempts(workspace, data, pending)
    return status(workspace, run_id)


def _verified_evidence(workspace: Workspace, data: dict, candidate: dict) -> None:
    if evaluation.invalidated(data, candidate):
        raise AuditError("candidate or an ancestor has been invalidated")
    if candidate["state"] != "verified" or not candidate.get("review_artifact"):
        raise AuditError("candidate has no engine-backed passing review")
    if workspace.read_artifact(candidate["review_artifact"]) != candidate["review"]:
        raise AuditError("review evidence changed")
    review = candidate["review"]
    validate_record("fix-review", review)
    template = _review_template(data, candidate)
    if (
        review["verdict"] != "pass"
        or not all(review["assessments"].values())
        or review["reviewer"] == candidate["author"]
        or any(
            review[k] != template[k]
            for k in (
                "version",
                "run_id",
                "candidate_id",
                "source_digest",
                "evaluation_digest",
                "inputs_digest",
                "trial_ids",
            )
        )
        or not set(review["evidence"]).issubset(template["evidence"])
    ):
        raise AuditError("candidate is missing a passing review bound to its actual trials")
    if checkouts.tree(workspace.root, candidate["source_revision"]) != candidate["source_digest"]:
        raise AuditError("candidate snapshot identity changed")
    metrics, all_checks, task_samples = [], [], []
    for trial in _completed(candidate):
        observed, checks, tasks = _validate_result(
            data, candidate, trial, workspace.read_artifact(trial["artifact"])
        )
        if tasks != trial.get("task_metrics", {}):
            raise AuditError("recorded tasks do not match retained execution evidence")
        metrics.append(observed)
        all_checks.extend(checks)
        task_samples.append(tasks)
    aggregated, _ = evaluation.aggregate(data["spec"], metrics)
    if aggregated != candidate["metrics"] or not all(c["passed"] for c in all_checks):
        raise AuditError("candidate verification no longer matches execution evidence")
    tasks, task_variation = evaluation.aggregate_tasks(data["spec"], task_samples)
    if tasks != candidate.get("task_metrics", {}) or task_variation != candidate.get(
        "task_variation", {}
    ):
        raise AuditError("task summary does not match retained execution evidence")
    if [t["repetition"] for t in _completed(candidate)] != list(range(data["spec"]["repetitions"])):
        raise AuditError("required repetitions are missing or repeated")
    if candidate["candidate_id"] != data["baseline_id"]:
        baseline = _candidate(data, None)
        _verified_evidence(workspace, data, baseline)
        if not all(
            c["passed"]
            for c in evaluation.constraints(data["spec"], aggregated, baseline["metrics"])
        ):
            raise AuditError("candidate no longer satisfies original-baseline constraints")


def _select_locked(workspace: Workspace, data: dict, candidate_id: str) -> None:
    """Select while the caller holds the run lock."""
    run_id = data["run_id"]
    candidate = _candidate(data, candidate_id)
    _verified_evidence(workspace, data, candidate)
    if candidate_id not in evaluation.frontier(data):
        raise AuditError("select a reviewed feasible candidate on the current Pareto frontier")
    branch = f"codex/ag-fix-{run_id}-{candidate_id}"
    existing = checkouts.git(workspace.root, "branch", "--list", branch, "--format=%(objectname)")
    if existing and existing != candidate["source_revision"]:
        raise AuditError(
            "delivery branch already exists with different source; it was not overwritten"
        )
    if not existing:
        checkouts.git(workspace.root, "branch", branch, candidate["source_revision"])
    selection = {
        "candidate_id": candidate_id,
        "branch": branch,
        "source_revision": candidate["source_revision"],
    }
    if data["selected"] != selection:
        data["selections"].append({**selection, "at": now()})
    data["selected"] = selection
    candidate["branch"] = branch
    _save(workspace, data)


def select(workspace: Workspace, run_id: str, candidate_id: str) -> dict:
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        _select_locked(workspace, data, candidate_id)
    return status(workspace, run_id, candidate_id)


def verify_origin(workspace: Workspace, run_id: str, candidate_id: str, issue_id: str) -> dict:
    """Return an engine receipt only for the exact clean, currently applied tree."""
    data = load_run(workspace, run_id)
    candidate = _candidate(data, candidate_id)
    _verified_evidence(workspace, data, candidate)
    revision = checkouts.clean_revision(workspace.root)
    if checkouts.tree(workspace.root, revision) != candidate["source_digest"]:
        raise AuditError(
            "current origin differs from the verified snapshot; start a run from this clean origin and execute/review it before resolution"
        )
    checks = [
        c
        for c in candidate["checks"]
        if issue_id in c["issue_ids"] and c["expected"] == "pass" and c["passed"]
    ]
    if issue_id not in data["issue_ids"] or not checks or not candidate["feasible"]:
        raise AuditError(
            "candidate does not provide passing engine acceptance checks for this issue"
        )
    return {
        "version": 1,
        "provenance": "agentagon-engine",
        "run_id": run_id,
        "candidate_id": candidate_id,
        "issue_id": issue_id,
        "origin_revision": revision,
        "source_digest": candidate["source_digest"],
        "evaluation_digest": data["evaluation_digest"],
        "inputs_digest": data["inputs_digest"],
        "profile_digest": data["profile_digest"],
        "trial_ids": [t["trial_id"] for t in candidate["trials"]],
        "artifacts": [t["artifact"] for t in candidate["trials"]],
        "review": candidate["review_artifact"],
    }


def status(
    workspace: Workspace, run_id: str | None = None, candidate_id: str | None = None
) -> dict:
    from agentagon.experiments import controls, learning
    from agentagon.reporting import build_fix_report

    if run_id is None:
        if candidate_id is not None:
            raise AuditError("candidate selection requires a run")
        runs = [build_fix_report(workspace, data) for data in list_runs(workspace)]
        return {"runs": runs, "latest_run_id": runs[0]["run_id"] if runs else None}
    data = load_run(workspace, run_id)
    _update(data)
    report = build_fix_report(workspace, data)
    report["search"] = copy.deepcopy(data.get("search", {}))
    report.update(controls.projection(data))
    report["scan_pending"] = learning.pending(data)
    report["lesson_context"] = learning.context(workspace, data)
    report["intelligence_receipts"] = copy.deepcopy(data.get("intelligence", []))
    directory = run_dir(workspace, run_id)
    report["artifacts"] = {
        "state": str(directory / "state.json"),
        "markdown": str(directory / "report.md"),
        "json": str(directory / "report.json"),
    }
    report["usage"]["elapsed_seconds"] = _elapsed(data)
    report["pending_actions"] = []
    chosen = [_candidate(data, candidate_id)] if candidate_id else list(data["candidates"].values())
    for candidate in chosen:
        entry = {
            "candidate_id": candidate["candidate_id"],
            "state": candidate["state"],
            "worktree": str(workspace.root / candidate["worktree"]),
        }
        if evaluation.invalidated(data, candidate):
            entry["action"] = (
                "candidate invalidated; retained history cannot be expanded or selected"
            )
        elif candidate.get("creation_pending"):
            entry["action"] = "fix run to recover the reserved candidate worktree before editing"
        elif candidate["state"] == "awaiting_review":
            entry["action"] = (
                "review exact sealed patch and engine evidence, then fix run --review-file"
            )
            entry["review_template"] = _review_template(data, candidate)
        elif candidate["state"] == "editing":
            entry["action"] = "edit candidate worktree, then fix run"
        elif candidate["state"] in {"sealed", "running", "interrupted"}:
            entry["action"] = "fix run to execute or reconcile this candidate"
        else:
            entry["action"] = "candidate complete"
        report["pending_actions"].append(entry)
    if candidate_id:
        report["candidate_id"] = candidate_id
        report["candidate"] = copy.deepcopy(_candidate(data, candidate_id))
        if report["candidate"]["state"] == "awaiting_review" and not evaluation.invalidated(
            data, report["candidate"]
        ):
            report["review_template"] = _review_template(data, _candidate(data, candidate_id))
    return report
