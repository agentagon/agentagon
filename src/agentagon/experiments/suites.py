"""Immutable, isolated final measurements across retained application focuses."""

import copy
import fcntl
import os
from contextlib import contextmanager

from agentagon.core.records import AuditError, digest, identifier, load_json, now
from agentagon.experiments import baselines, engine, evaluation, preparation, scoring
from agentagon.experiments.budget import BudgetLedger
from agentagon.experiments.host_bridge import HostBridge
from agentagon.experiments.spec import finite
from agentagon.experiments.store import load_run, save_run
from agentagon.experiments.store import locked as run_locked


def _path(workspace, run_id):
    return BudgetLedger(workspace, run_id).directory / "suite.json"


@contextmanager
def _locked(workspace, run_id):
    path = _path(workspace, run_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def _manifest(value):
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("missing"):
        raise AuditError("suite requires a complete versioned manifest")
    manifest = {k: copy.deepcopy(v) for k, v in value.items() if k not in {"digest", "missing"}}
    if value.get("digest") != digest(manifest):
        raise AuditError("suite manifest digest changed")
    members = manifest.get("members")
    if not isinstance(members, list) or not members:
        raise AuditError("suite requires at least one measured focus")
    ids = [m.get("focus_id") for m in members]
    if None in ids or len(ids) != len(set(ids)) or manifest.get("focus_id") not in ids:
        raise AuditError("suite requires unique focuses including its primary objective")
    return manifest


def _definition(workspace, member):
    evaluation_id = member["evaluation_id"]
    spec = preparation.fix_spec(workspace, evaluation_id, reuse=True)
    evaluator_digest = preparation.evaluator_identity(workspace, evaluation_id)
    if evaluator_digest != member["evaluator_digest"] or spec["metrics"] != member["metrics"]:
        raise AuditError("suite evaluator or metric definitions changed")
    baseline = baselines.status(workspace, member["baseline_id"])
    if (
        baseline["state"] != "completed"
        or baseline["evaluation_id"] != evaluation_id
        or baseline["evaluator_digest"] != evaluator_digest
        or not baseline.get("measurement")
        or baseline["profile_name"] != member["profile_name"]
    ):
        raise AuditError("every suite focus requires its completed compatible baseline")
    measured = baseline["measurement"]
    data = load_run(workspace, measured["execution_run_id"])
    candidate = data["candidates"][measured["candidate_id"]]
    engine._verified_evidence(workspace, data, candidate)
    if candidate["metrics"] != measured["metrics"]:
        raise AuditError("suite baseline metrics differ from retained execution evidence")
    primary = member["primary_metric"]
    if primary not in spec["metrics"]:
        raise AuditError("suite primary metric is not in its frozen evaluator")
    scoring_definition = spec.get("scoring", {})
    scoring_metric = scoring_definition.get(
        "primary" if scoring_definition.get("mode") == "primary" else "custom_metric"
    )
    if scoring_definition.get("mode") in {"primary", "custom"} and primary != scoring_metric:
        raise AuditError("suite primary metric differs from its frozen scoring objective")
    guards = member.get("guardrails", [])
    if not isinstance(guards, list):
        raise AuditError("suite guardrails must be a list")
    for guard in guards:
        if (
            set(guard) != {"metric", "op", "bound", "reference"}
            or guard["metric"] not in spec["metrics"]
            or guard["op"] not in {"gte", "lte"}
            or guard["reference"] not in {"absolute", "baseline_delta", "baseline_ratio"}
        ):
            raise AuditError("invalid suite guardrail")
        finite(guard["bound"])
    return {
        "spec": spec,
        "profile": data["profile"],
        "profile_digest": data["profile_digest"],
        "inputs_digest": data["inputs_digest"],
        "evaluator_digest": evaluator_digest,
        "baseline_metrics": copy.deepcopy(measured["metrics"]),
        "baseline_artifact": baseline["measurement_artifact"],
    }


def bind(workspace, run_id, manifest, *, finalist_count=3):
    """Freeze the entire accepted suite before any application proposal is made."""
    canonical = _manifest(manifest)
    if type(finalist_count) is not int or not 1 <= finalist_count <= 10:
        raise AuditError("finalist_count must be between 1 and 10")
    with _locked(workspace, run_id), run_locked(workspace, run_id):
        data = load_run(workspace, run_id)
        if data.get("suite"):
            existing = status(workspace, run_id)
            if existing["manifest"] != canonical or existing["finalist_count"] != finalist_count:
                raise AuditError("the run's measurement suite is already frozen")
            return existing
        if data.get("optimizer_configured") or len(data["candidates"]) != 1:
            raise AuditError(
                "bind the complete suite before configuring optimization or proposing candidates"
            )
        definitions = {m["focus_id"]: _definition(workspace, m) for m in canonical["members"]}
        active_spec = definitions[canonical["focus_id"]]["spec"]

        # Goal wording and issue links are context; executable definitions must match.
        def executable(spec):
            return {k: v for k, v in spec.items() if k not in {"goal", "issue_ids"}}

        if executable(data["spec"]) != executable(active_spec):
            raise AuditError("primary run must use the active focus's frozen evaluator")
        reserve = (
            2 * finalist_count * sum(item["spec"]["repetitions"] for item in definitions.values())
        )
        ledger = BudgetLedger(workspace, run_id)
        if ledger.path.exists():
            budget = ledger.snapshot()
            remaining = budget["allocations"]["verification"] - ledger.spent(budget, "verification")
            if remaining < reserve + finalist_count * data["spec"]["repetitions"]:
                raise AuditError(
                    "existing budget cannot protect the complete suite verification reserve"
                )
        state = {
            "version": 1,
            "run_id": run_id,
            "manifest": canonical,
            "manifest_digest": digest(canonical),
            "definitions": definitions,
            "verification_trials": reserve,
            "finalist_count": finalist_count,
            "trial_timeout_seconds": data["limits"]["trial_timeout_seconds"],
            "reference_source_revision": data["origin_revision"],
            "state": "bound",
            "active_candidate_id": None,
            "finalists": {},
            "created_at": now(),
        }
        immutable = {
            k: state[k]
            for k in (
                "manifest",
                "definitions",
                "reference_source_revision",
                "verification_trials",
                "finalist_count",
                "trial_timeout_seconds",
            )
        }
        artifact = workspace.artifact(immutable)
        state["definition_artifact"] = artifact
        workspace.write(_path(workspace, run_id), state)
        data["suite"] = {
            "manifest_digest": state["manifest_digest"],
            "definition_artifact": artifact,
            "verification_trials": reserve,
            "finalist_count": finalist_count,
        }
        save_run(workspace, data)
        return state


FINALIST_FIELDS = (
    "binding",
    "state",
    "measurements",
    "runtime_trial_timeout_seconds",
    "execution_binding_artifact",
    "result",
    "result_artifact",
    "next_action",
    "completed_at",
)


def _load(workspace, run_id):
    path = _path(workspace, run_id)
    if not path.exists():
        raise AuditError("run has no bound measurement suite")
    state = load_json(path)
    data = load_run(workspace, run_id)
    frozen = workspace.read_artifact(state["definition_artifact"])
    if (
        state["run_id"] != run_id
        or digest(state["manifest"]) != state["manifest_digest"]
        or any(state[k] != v for k, v in frozen.items())
        or data.get("suite")
        != {
            k: state[k]
            for k in (
                "manifest_digest",
                "definition_artifact",
                "verification_trials",
                "finalist_count",
            )
        }
        or len(state["finalists"]) > state["finalist_count"]
    ):
        raise AuditError("bound suite identity changed")
    for candidate_id, finalist in state["finalists"].items():
        if finalist["binding"]["candidate_id"] != candidate_id:
            raise AuditError("suite finalist identity changed")
        if finalist.get("result_artifact") and workspace.read_artifact(
            finalist["result_artifact"]
        ) != finalist.get("result"):
            raise AuditError("retained suite result changed")
        if workspace.read_artifact(finalist["execution_binding_artifact"]) != {
            "binding": finalist["binding"],
            "trial_timeout_seconds": finalist["runtime_trial_timeout_seconds"],
        }:
            raise AuditError("suite finalist or execution limits changed")
    return state


def _view(state, candidate_id):
    return {**state, **state["finalists"][candidate_id]}


def _save_finalist(workspace, state):
    stored = _load(workspace, state["run_id"])
    candidate_id = state["binding"]["candidate_id"]
    stored["active_candidate_id"] = candidate_id
    stored["finalists"][candidate_id] = {k: state[k] for k in FINALIST_FIELDS if k in state}
    workspace.write(_path(workspace, state["run_id"]), stored)


def status(workspace, run_id):
    state = _load(workspace, run_id)
    chosen = (load_run(workspace, run_id).get("selected") or {}).get("candidate_id")
    chosen = chosen or state["active_candidate_id"]
    result = _view(state, chosen) if chosen else {**state, "binding": None}
    # Review authorization and application completion must see every child, including
    # unsuccessful finalists. Individual result verification uses its own view.
    result["measurements"] = {
        f"{candidate_id}:{key}": entry
        for candidate_id, finalist in state["finalists"].items()
        for key, entry in finalist["measurements"].items()
    }
    return result


def _measurement(workspace, state, member, role, host, host_handler):
    primary = state["run_id"]
    definition = state["definitions"][member["focus_id"]]
    source = (
        state["reference_source_revision"]
        if role == "reference"
        else state["binding"]["source_revision"]
    )
    child_id = identifier(
        "run",
        primary,
        state["binding"]["candidate_id"],
        state["manifest_digest"],
        member["focus_id"],
        role,
        source,
    )
    key = member["focus_id"] + ":" + role
    entry = state["measurements"].setdefault(
        key, {"execution_run_id": child_id, "role": role, "focus_id": member["focus_id"]}
    )
    if entry["execution_run_id"] != child_id:
        raise AuditError("suite measurement identity changed")
    _save_finalist(workspace, state)
    started = engine.start(
        workspace,
        definition["spec"],
        member["profile_name"],
        run_id=child_id,
        source_revision=source,
        execution_profile=definition["profile"],
        budget_id=primary,
        measurement_role=role,
    )
    with run_locked(workspace, child_id):
        data = load_run(workspace, child_id)
        data["suite_owner"] = primary
        data["suite_digest"] = state["manifest_digest"]
        data["evaluator_id"] = member["evaluation_id"]
        data["evaluator_digest"] = member["evaluator_digest"]
        data["limits"]["trial_timeout_seconds"] = min(
            definition["profile"]["limits"]["trial_timeout_seconds"],
            state["runtime_trial_timeout_seconds"],
        )
        data["candidates"][data["baseline_id"]]["budget_stage"] = "verification"
        save_run(workspace, data)
    result = engine.run(workspace, child_id)
    bridge = HostBridge(workspace, primary)
    if result["candidate"]["state"] == "awaiting_grading":
        for request in bridge.pending():
            if (
                request["role"] == "judging"
                and request["payload"].get("execution_run_id") == child_id
            ):
                bridge.fulfill(request, host_handler)
        result = engine.run(workspace, child_id)
    if result["candidate"]["state"] == "awaiting_review":
        candidate = result["candidate"]
        request = bridge.request(
            f"suite-review:{child_id}",
            source=candidate["source_digest"],
            evaluator=result["review_template"]["evaluation_digest"],
            role="review",
            scope=[candidate["worktree"], *result["review_template"]["evidence"]],
            host=host["host"],
            model=host["model"],
            stage="verification",
            payload={
                "execution_run_id": child_id,
                "primary_run_id": primary,
                "suite_digest": state["manifest_digest"],
                "candidate_id": started["candidate_id"],
                "source_revision": candidate["source_revision"],
                "review_template": result["review_template"],
            },
        )
        request = bridge.fulfill(request, host_handler)
        if request["state"] == "completed":
            if request.get("deadline_exceeded"):
                raise AuditError("suite review exceeded the accepted verification budget")
            reply = request["response"]
            result = engine.run(workspace, child_id, review=reply.get("review", reply))
        else:
            entry["host_request_id"] = request["request_id"]
    entry["state"] = result["candidate"]["state"]
    entry["candidate_id"] = started["candidate_id"]
    _save_finalist(workspace, state)
    return entry


def _observed(workspace, state, member, role):
    entry = state["measurements"].get(member["focus_id"] + ":" + role)
    if not entry or entry.get("state") != "verified":
        raise AuditError("suite has missing or unverified measurements")
    definition = state["definitions"][member["focus_id"]]
    data = load_run(workspace, entry["execution_run_id"])
    candidate = data["candidates"][entry["candidate_id"]]
    source = (
        state["reference_source_revision"]
        if role == "reference"
        else state["binding"]["source_revision"]
    )
    if (
        data.get("suite_owner") != state["run_id"]
        or data.get("suite_digest") != state["manifest_digest"]
        or data["spec"] != definition["spec"]
        or data["profile_digest"] != definition["profile_digest"]
        or data["inputs_digest"] != definition["inputs_digest"]
        or digest(data["frozen"]) != definition["inputs_digest"]
        or data["origin_revision"] != source
        or data.get("measurement_role") != role
        or data.get("budget_id") != state["run_id"]
        or candidate.get("budget_stage") != "verification"
        or data["limits"]["trial_timeout_seconds"]
        != min(
            definition["profile"]["limits"]["trial_timeout_seconds"],
            state["runtime_trial_timeout_seconds"],
        )
    ):
        raise AuditError("suite execution changed its source, evaluator, profile, or budget")
    engine._verified_evidence(workspace, data, candidate)
    return data, candidate


def _result(workspace, state):
    rows = []
    for member in state["manifest"]["members"]:
        definition = state["definitions"][member["focus_id"]]
        accepted = workspace.read_artifact(definition["baseline_artifact"])
        accepted_run = load_run(workspace, accepted["execution_run_id"])
        accepted_candidate = accepted_run["candidates"][accepted["candidate_id"]]
        engine._verified_evidence(workspace, accepted_run, accepted_candidate)
        if (
            accepted["metrics"] != definition["baseline_metrics"]
            or accepted_candidate["metrics"] != definition["baseline_metrics"]
        ):
            raise AuditError("accepted suite baseline no longer matches retained evidence")
        _, reference = _observed(workspace, state, member, "reference")
        data, finalist = _observed(workspace, state, member, "finalist")
        spec = definition["spec"]
        checks = [
            {**c, "passed": c["passed"] and c["expected"] == "pass"} for c in finalist["checks"]
        ]
        constraints = evaluation.constraints(spec, finalist["metrics"], reference["metrics"])
        guard_spec = {"constraints": member.get("guardrails", [])}
        guards = [
            {**g, "baseline": name}
            for name, metrics in (
                ("accepted", definition["baseline_metrics"]),
                ("paired_reference", reference["metrics"]),
            )
            for g in evaluation.constraints(guard_spec, finalist["metrics"], metrics)
        ]
        score = (
            scoring.summarize(spec["scoring"], [t["metrics"] for t in finalist["trials"]], checks)
            if spec.get("scoring")
            else None
        )
        primary = member["focus_id"] == state["manifest"]["focus_id"]
        metric = member["primary_metric"]
        improved = (
            finalist["metrics"][metric] < reference["metrics"][metric]
            if spec["metrics"][metric]["direction"] == "min"
            else finalist["metrics"][metric] > reference["metrics"][metric]
        )
        target_passed = (
            not primary
            or spec.get("scoring", {}).get("target") is None
            or bool(score and score["target_reached"])
        )
        passed = (
            all(c["passed"] for c in checks + constraints + guards)
            and (score is None or score["eligible"])
            and (not primary or improved)
            and target_passed
        )
        rows.append(
            {
                "focus_id": member["focus_id"],
                "name": member["name"],
                "evaluation_id": member["evaluation_id"],
                "evaluator_digest": member["evaluator_digest"],
                "profile_digest": definition["profile_digest"],
                "reference": reference["metrics"],
                "finalist": finalist["metrics"],
                "primary": primary,
                "checks": checks,
                "constraints": constraints,
                "guardrails": guards,
                "score": score,
                "improved": improved,
                "target_passed": target_passed,
                "passed": passed,
                "executions": {
                    role: state["measurements"][member["focus_id"] + ":" + role]["execution_run_id"]
                    for role in ("reference", "finalist")
                },
            }
        )
    return {
        "manifest_digest": state["manifest_digest"],
        "binding": state["binding"],
        "passed": all(r["passed"] for r in rows),
        "members": rows,
    }


def advance(workspace, run_id, *, candidate_id=None, host_handler=None):
    with _locked(workspace, run_id):
        stored = _load(workspace, run_id)
        data = load_run(workspace, run_id)
        candidate_id = (
            candidate_id
            or stored["active_candidate_id"]
            or (data.get("selected") or {}).get("candidate_id")
        )
        if candidate_id not in data["candidates"] or candidate_id == data["baseline_id"]:
            raise AuditError("suite verification requires an independently reviewed finalist")
        candidate = data["candidates"][candidate_id]
        engine._verified_evidence(workspace, data, candidate)
        if not candidate.get("verification_of"):
            raise AuditError("suite requires fresh primary final verification")
        binding = {
            "run_id": run_id,
            "manifest_digest": stored["manifest_digest"],
            "candidate_id": candidate_id,
            "source_revision": candidate["source_revision"],
            "source_digest": candidate["source_digest"],
        }
        config_path = BudgetLedger(workspace, run_id).directory / "application-optimizer.json"
        if not config_path.exists():
            raise AuditError(
                "configure the shared optimizer budget and host before running the suite"
            )
        from agentagon.experiments import optimize_run

        host = optimize_run.status(workspace, run_id)["config"]
        if candidate_id not in stored["finalists"]:
            if len(stored["finalists"]) >= stored["finalist_count"]:
                raise AuditError("all frozen finalist verification slots have been used")
            timeout = min(stored["trial_timeout_seconds"], data["limits"]["trial_timeout_seconds"])
            stored["finalists"][candidate_id] = {
                "binding": binding,
                "state": "running",
                "measurements": {},
                "runtime_trial_timeout_seconds": timeout,
                "execution_binding_artifact": workspace.artifact(
                    {
                        "binding": binding,
                        "trial_timeout_seconds": timeout,
                    }
                ),
            }
            stored["active_candidate_id"] = candidate_id
            workspace.write(_path(workspace, run_id), stored)
        state = _view(stored, candidate_id)
        if state["binding"] != binding:
            raise AuditError("suite finalist source changed")
        if state["state"] in {"completed", "failed"}:
            return state
        state["state"] = "running"
        _save_finalist(workspace, state)
        for member in state["manifest"]["members"]:
            for role in ("reference", "finalist"):
                try:
                    entry = _measurement(workspace, state, member, role, host, host_handler)
                except AuditError as exc:
                    state.update(state="blocked", next_action=str(exc))
                    _save_finalist(workspace, state)
                    return state
                if entry["state"] != "verified":
                    state["state"] = (
                        "failed"
                        if entry["state"] in {"failed", "rejected", "cancelled"}
                        else "host_pending"
                    )
                    state["next_action"] = (
                        "Inspect retained measurements."
                        if state["state"] == "failed"
                        else "Complete bound grading or independent review, then run the suite again."
                    )
                    _save_finalist(workspace, state)
                    return state
        state["result"] = _result(workspace, state)
        state["result_artifact"] = workspace.artifact(state["result"])
        state["state"] = "completed" if state["result"]["passed"] else "failed"
        state.pop("next_action", None)
        if state["state"] == "failed":
            state["next_action"] = (
                "Required checks or guardrails failed. The original baseline is retained."
            )
        state["completed_at"] = now()
        _save_finalist(workspace, state)
        return state


def verify_completed(workspace, run_id, manifest):
    """Recheck the exact selected finalist against every required evaluator."""
    state = _load(workspace, run_id)
    if state["manifest"] != _manifest(manifest):
        raise AuditError("completed suite differs from the accepted focus manifest")
    selected = load_run(workspace, run_id).get("selected") or {}
    result = verify_selection(workspace, run_id, selected.get("candidate_id"))
    finalist = state["finalists"][selected["candidate_id"]]
    return {"state": "completed", **result, "artifact": finalist["result_artifact"]}


def verify_selection(workspace, run_id, candidate_id):
    stored = _load(workspace, run_id)
    if candidate_id not in stored["finalists"]:
        raise AuditError("selected candidate differs from the suite's verified source")
    state = _view(stored, candidate_id)
    if state["state"] != "completed" or not state.get("result", {}).get("passed"):
        raise AuditError("complete suite verification is missing or a required guardrail failed")
    data = load_run(workspace, run_id)
    candidate = data["candidates"].get(candidate_id)
    if not candidate or any(
        candidate.get(k) != state["binding"][k]
        for k in ("candidate_id", "source_revision", "source_digest")
    ):
        raise AuditError("selected candidate differs from the suite's verified source")
    engine._verified_evidence(workspace, data, candidate)
    if _result(workspace, state) != state["result"]:
        raise AuditError("suite result no longer matches independent execution evidence")
    return state["result"]


def verify_outcome(workspace, run_id, manifest):
    """Validate all terminal finalist attempts, preserving failed evidence."""
    stored = _load(workspace, run_id)
    if stored["manifest"] != _manifest(manifest):
        raise AuditError("suite differs from the accepted focus manifest")
    data = load_run(workspace, run_id)
    outcomes = {}
    for candidate_id in stored["finalists"]:
        state = _view(stored, candidate_id)
        if state["state"] not in {"completed", "failed"}:
            raise AuditError("required suite measurements or independent reviews are unfinished")
        candidate = data["candidates"].get(candidate_id)
        if not candidate or candidate["source_revision"] != state["binding"]["source_revision"]:
            raise AuditError("suite refers to a changed finalist")
        engine._verified_evidence(workspace, data, candidate)
        if state.get("result"):
            if _result(workspace, state) != state["result"]:
                raise AuditError("suite no longer matches its retained observations")
            outcomes[candidate_id] = {
                "state": state["state"],
                **state["result"],
                "artifact": state["result_artifact"],
            }
            continue
        failures = []
        for entry in state["measurements"].values():
            child = load_run(workspace, entry["execution_run_id"])
            measured = child["candidates"][child["baseline_id"]]
            if (
                child.get("suite_owner") != run_id
                or child.get("suite_digest") != state["manifest_digest"]
            ):
                raise AuditError("failed execution is not bound to this suite")
            if measured["state"] in {"failed", "rejected", "cancelled"}:
                failures.append({"execution_run_id": child["run_id"], "state": measured["state"]})
        if not failures:
            raise AuditError("failed suite has no retained failed execution")
        outcomes[candidate_id] = {"state": "failed", "passed": False, "failures": failures}
    if data.get("selected"):
        return {**verify_completed(workspace, run_id, manifest), "finalists": outcomes}
    if not outcomes:
        return {
            "state": "not_measured",
            "passed": False,
            "manifest_digest": stored["manifest_digest"],
            "limitations": [
                "No qualifying finalist; the original baseline is retained. Full-suite improvement is not established."
            ],
            "finalists": {},
        }
    if any(outcome["state"] == "completed" for outcome in outcomes.values()):
        raise AuditError("a verified suite finalist still needs selection")
    latest = outcomes[stored["active_candidate_id"]]
    return {**latest, "manifest_digest": stored["manifest_digest"], "finalists": outcomes}


def review_execution(workspace, owner, request):
    """Authorize a child review without allowing it to impersonate its primary run."""
    state = status(workspace, owner)
    payload = request["payload"]
    child = payload.get("execution_run_id")
    if (
        payload.get("primary_run_id") != owner
        or payload.get("suite_digest") != state["manifest_digest"]
    ):
        raise AuditError("review is not bound to this measurement suite")
    if child not in {v["execution_run_id"] for v in state["measurements"].values()}:
        raise AuditError("review child does not belong to this measurement suite")
    data = load_run(workspace, child)
    candidate = data["candidates"].get(payload.get("candidate_id"))
    if (
        not candidate
        or data.get("suite_owner") != owner
        or payload.get("review_template") != engine._review_template(data, candidate)
        or request["source"] != candidate["source_digest"]
        or request["evaluator"] != data["evaluation_digest"]
    ):
        raise AuditError("suite review binding does not match the child execution")
    return child
