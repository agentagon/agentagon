"""Focused repair uses the shared measured engine, without an optimizer search."""

from agentagon.capabilities.experiments import engine, suites
from agentagon.core.records import AuditError, digest


def review_handoff(workspace, job, handoff, run_id):
    from agentagon.capabilities.experiments.store import load_run
    from agentagon.workflows.procedures import _validate_limits, _validate_paths

    record = load_run(workspace, run_id)
    _validate_limits(job, record)
    _validate_paths(workspace, job, record)
    if record["created_at"] < job["created_at"] or job["workflow_ids"].get("run_id") not in (
        None,
        run_id,
    ):
        raise AuditError("review belongs to a different task")
    candidate = record["candidates"].get(handoff.get("candidate_id"))
    if not candidate or candidate["state"] != "awaiting_review":
        raise AuditError("candidate must have completed execution before review")
    if (
        candidate["candidate_id"] != record["baseline_id"]
        and candidate["author"] != job["session_id"]
    ):
        raise AuditError("candidate author must match the managed session")
    bound = {
        "kind": "candidate",
        "run_id": run_id,
        "workflow_run_id": run_id,
        "candidate_id": candidate["candidate_id"],
        "template": engine._review_template(record, candidate),
        "source": str(workspace.root / candidate["worktree"]),
    }
    return {**bound, "id": "review_" + digest(bound)[:24]}


def verify(workspace, job, record):
    from agentagon.workflows.procedures import _require_review_session

    if record.get("focused_repair", {}).get("task_id") != job["id"]:
        raise AuditError("repair requires its frozen task budget")
    if record.get("optimizer_configured"):
        raise AuditError("focused Fix must use direct candidate execution")
    if len(record["candidates"]) - 1 > 3:
        raise AuditError("focused Fix exceeded three repair attempts")
    selected = record.get("selected")
    if not selected:
        return False, record["state"], {}
    candidate = record["candidates"][selected["candidate_id"]]
    baseline = record["candidates"][record["baseline_id"]]
    if candidate["candidate_id"] == baseline["candidate_id"]:
        raise AuditError("retaining the reference does not verify a repair")
    for item in (baseline, candidate):
        engine._verified_evidence(workspace, record, item)
        _require_review_session(job, item["review"]["reviewer"], run_id=record["run_id"])
    # expected=fail records a successful reproduction, not a failed execution.
    reproduced = {
        c["id"] for c in baseline["checks"] if c.get("expected") == "fail" and c["passed"]
    }
    repaired = {c["id"] for c in candidate["checks"] if c.get("expected") == "pass" and c["passed"]}
    if not reproduced & repaired:
        raise AuditError(
            "Fix requires a failing reference regression check that passes on the repair"
        )
    if job["options"].get("suite_manifest"):
        if (
            not record.get("suite")
            or record["suite"].get("manifest_digest") != job["options"]["suite_manifest"]["digest"]
        ):
            raise AuditError("repair omitted the required regression suite")
        suites.verify_selection(workspace, record["run_id"], candidate["candidate_id"])
        for entry in suites.status(workspace, record["run_id"])["measurements"].values():
            from agentagon.capabilities.experiments.store import load_run

            child = load_run(workspace, entry["execution_run_id"])
            review = child["candidates"][child["baseline_id"]]["review"]
            _require_review_session(job, review["reviewer"], run_id=child["run_id"])
    receipt = {
        "version": 1,
        "provenance": "agentagon-engine",
        "verification_scope": "tested_revision",
        "run_id": record["run_id"],
        "candidate_id": candidate["candidate_id"],
        "source_revision": candidate["source_revision"],
        "source_digest": candidate["source_digest"],
        "evaluation_digest": record["evaluation_digest"],
        "regression_checks": sorted(reproduced & repaired),
        "review": candidate["review_artifact"],
        "production_recovery_verified": False,
    }
    reference = workspace.artifact(receipt)
    return (
        True,
        "complete",
        {
            "verification": reference,
            "source_revision": candidate["source_revision"],
            "branch": selected["branch"],
            "production_recovery_verified": False,
        },
    )


def configure(workspace, run_id, context):
    """Freeze a direct repair's ledger and regression suite before execution."""
    from datetime import datetime

    from agentagon.capabilities.experiments.budget import BudgetLedger
    from agentagon.capabilities.experiments.store import load_run, locked, save_run
    from agentagon.workflows.procedures import _validate_limits, _validate_paths

    if context.get("kind") != "fix" or not context.get("session_id"):
        raise AuditError("repair configuration requires a managed Fix context")
    data = load_run(workspace, run_id)
    _validate_limits(context, data)
    _validate_paths(workspace, context, data)
    if data.get("focused_repair"):
        if data["focused_repair"]["task_id"] != context["id"]:
            raise AuditError("repair belongs to another task")
        return data["focused_repair"]
    if len(data["candidates"]) != 1 or data["candidates"][data["baseline_id"]]["trials"]:
        raise AuditError("configure the repair before running its reference")
    manifest = context["options"].get("suite_manifest")
    if manifest:
        suites.bind(workspace, run_id, manifest, finalist_count=1)
        data = load_run(workspace, run_id)
    repeats = data["spec"]["repetitions"]
    from agentagon.capabilities.experiments import preparation

    evaluation_ids = {
        review["evaluation_id"]
        for review in context.get("review_tasks", {}).values()
        if review.get("evaluation_id")
    }
    if context.get("workflow_ids", {}).get("evaluation_id"):
        evaluation_ids.add(context["workflow_ids"]["evaluation_id"])
    preparation_trials = []
    for evaluation_id in sorted(evaluation_ids):
        evaluation = preparation.load(workspace, evaluation_id)
        preparation_trials.extend(
            (evaluation_id, trial) for check in evaluation["checks"] for trial in check["trials"]
        )
    ledger = BudgetLedger(workspace, run_id)
    ledger.create(
        data["limits"]["max_trials"],
        data["limits"]["max_elapsed_seconds"],
        baseline_trials=repeats + len(preparation_trials),
        verification_trials=repeats + data.get("suite", {}).get("verification_trials", 0),
        started_at=datetime.fromisoformat(context["created_at"]).timestamp(),
    )
    for evaluation_id, trial in preparation_trials:
        ledger.admit(
            trial["trial_id"],
            "preparation",
            binding={"evaluation_id": evaluation_id, "task_id": context["id"]},
        )
        ledger.finish(
            trial["trial_id"],
            status="completed" if trial["state"] == "passed" else "failed",
            result={"artifact": trial.get("artifact")},
        )
    frozen = {
        "task_id": context["id"],
        "host": context["agent"],
        "model": context.get("actual_model") or context["model"],
        "session_id": context["session_id"],
    }
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        data["focused_repair"] = frozen
        data["budget_id"] = run_id
        data["limits"]["max_candidates"] = min(3, data["limits"]["max_candidates"])
        save_run(workspace, data)
    return frozen


def validate(workspace, job, candidate, result):
    from agentagon.workflows.procedures import validate_run

    record = validate_run(workspace, job, candidate)
    complete, state, details = verify(workspace, job, record)
    result.update(details)
    return record, state, complete
