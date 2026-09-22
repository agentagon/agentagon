"""Validate bounded search, independent finalists and frozen regression gates."""

import copy

from agentagon.capabilities.experiments import engine, optimize_run
from agentagon.capabilities.experiments.store import load_run
from agentagon.core.records import AuditError


def validate(workspace, job, candidate, result):
    from agentagon.workflows.procedures import (
        _require_review_session,
        _validate_limits,
        validate_run,
    )

    record = validate_run(workspace, job, candidate)
    state = record["state"]
    complete = False
    if record.get("optimizer_configured"):
        optimized = optimize_run.status(workspace, candidate)
        if optimized["config"]["optimizer"] != job["options"].get("engine", "omni"):
            raise AuditError("fix run uses a different optimizer than requested")
        if optimized["config"].get("finalist_count") != job["options"].get("finalist_count", 3):
            raise AuditError("fix run uses a different final candidate count than requested")
        if optimized["config"]["host_concurrency"] > job.get(
            "host_slots", job["options"].get("host_concurrency", 1)
        ):
            raise AuditError("fix run exceeds its allocated coding-agent capacity")
        if optimized["config"].get("source_background", "") != job["options"].get(
            "optimization_background", ""
        ):
            raise AuditError("fix run changed the accepted optimization context")
        _validate_limits(job, {"limits": optimized["budget"]["limits"]})
        state = optimized["state"]
        selection = optimized.get("selection")
        complete = state == "completed" and isinstance(selection, dict)
        if complete:
            # Completion belongs to the coordinator, not the engine's active state.
            # Validate retained final evidence without selecting or mutating anything.
            chosen = ([selection["winner"]] if selection.get("winner") else []) + selection.get(
                "alternatives", []
            )
            if not chosen and not selection.get("retained_baseline"):
                raise AuditError("completed optimizer has no verified outcome")
            candidate_ids = [item["candidate_id"] for item in chosen] or [record["baseline_id"]]
            for candidate_id in candidate_ids:
                engine._verified_evidence(workspace, record, record["candidates"][candidate_id])
                reviewer = record["candidates"][candidate_id]["review"]["reviewer"]
                _require_review_session(job, reviewer, run_id=candidate)
            if (
                selection.get("winner")
                and (record.get("selected") or {}).get("candidate_id")
                != selection["winner"]["candidate_id"]
            ):
                raise AuditError("optimizer result differs from its saved selection")
            if job["options"].get("suite_manifest"):
                from agentagon.capabilities.experiments import suites

                verified = suites.verify_outcome(
                    workspace, candidate, job["options"]["suite_manifest"]
                )
                for entry in suites.status(workspace, candidate)["measurements"].values():
                    run_id = entry["execution_run_id"]
                    child = load_run(workspace, run_id)
                    review = child["candidates"][child["baseline_id"]].get("review")
                    if review:
                        _require_review_session(job, review["reviewer"], run_id=run_id)
                result["suite"] = copy.deepcopy(verified)
                if not verified["passed"]:
                    state = "complete_with_limits"
            result["comparison"] = copy.deepcopy(selection)
        elif state == "budget_exhausted":
            result["needs_input"] = (
                "Budget exhausted. Inspect retained evidence and start a new task to authorize more work."
            )
    return record, state, complete
