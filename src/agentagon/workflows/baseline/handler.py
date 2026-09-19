"""Baseline preparation and independent measurement validation."""

import copy

from agentagon.capabilities.experiments import baselines
from agentagon.capabilities.experiments.store import load_run
from agentagon.core.records import AuditError
from agentagon.workflows.runtime import BUDGET_DEFAULTS


def prepare(workspace, job, save):
    options = job["options"]
    from agentagon.capabilities.experiments import journeys, preparation

    if job["workflow_ids"].get("baseline_id"):
        return
    if options.get("baseline_id"):
        previous = baselines.status(workspace, options["baseline_id"])
        evaluation_id = previous["evaluation_id"]
    elif options.get("evaluation_id"):
        evaluation_id = options["evaluation_id"]
    else:
        raise AuditError("select a frozen evaluation or a saved baseline")
    evaluator = preparation.load(workspace, evaluation_id)
    specification = evaluator.get("package", {}).get("spec", {})
    if not specification.get("scoring"):
        raise AuditError("selected evaluator needs an accepted scoring definition")
    definition = {
        "version": 1,
        "goal": job["goal"],
        "accepted_by": f"task:{job['id']}",
        "scoring": specification["scoring"],
        "discovery": {
            "status": "usable",
            "paths": specification["evaluation_paths"],
            "evidence": [f"User selected frozen evaluator {evaluation_id}"],
            "creation_authorized": False,
        },
        "budget": {key: options[key] for key in BUDGET_DEFAULTS},
        "host": {"name": job["agent"], "model": job["actual_model"]},
    }
    if options.get("baseline_id") and previous.get("intent_id"):
        prior = journeys.load(workspace, previous["intent_id"])["definition"]
        if prior.get("acquisition"):
            definition["acquisition"] = copy.deepcopy(prior["acquisition"])
    intent = journeys.save(workspace, definition, evaluation_id)
    result = baselines.start(
        workspace,
        evaluation_id,
        intent["intent_id"],
        profile_name=options["profile"],
        request_id=job["id"],
        execution_profile=job["execution_profile"],
    )
    job["workflow_ids"]["baseline_id"] = result["baseline_id"]
    save(job)


def validate(workspace, job, candidate, result):
    from agentagon.workflows.procedures import _require_review_session, _validate_limits

    record = baselines.status(workspace, candidate)
    _validate_limits(job, record)
    state = record["state"]
    complete = state == "completed" and bool(record.get("measurement_artifact"))
    if complete:
        run = load_run(workspace, record["execution_run_id"])
        from agentagon.memory.execution import verify_snapshot

        verify_snapshot(workspace, job, run)
        reviewer = run["candidates"][run["baseline_id"]]["review"]["reviewer"]
        _require_review_session(job, reviewer, run_id=run["run_id"])
    return record, state, complete
