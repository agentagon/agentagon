"""Native optimizer proposals execute in the existing bounded application engine."""

import json

import pytest
from support.experiments import executions, passing_review
from support.optimizer import candidate_text, proposal_response
from test_scoring import definition

from agentagon.core.records import AuditError
from agentagon.experiments import engine, optimize_run
from agentagon.experiments.store import load_run


def _start(
    application, specification, optimizer="gepa", target=None, repetitions=1, max_trials=None
):
    specification["scoring"] = definition()
    if target is not None:
        specification["scoring"]["target"] = target
    specification["repetitions"] = repetitions
    specification["seeds"] = list(range(repetitions))
    started = engine.start(application, specification, "local")
    optimize_run.configure(
        application,
        started["run_id"],
        host="fake-codex",
        model="fake-model",
        optimizer=optimizer,
        max_trials=max_trials,
    )
    return started["run_id"]


def _host(request):
    if request["role"] == "review":
        return {
            "review": passing_review({"review_template": request["payload"]["review_template"]})
        }
    proposal = json.loads(candidate_text(request))
    app = json.loads(proposal["files"]["app.json"])
    app["quality"] += 0.01
    app["latency"] -= 1
    app["variant"] = f"optimized-{app['quality']}"
    proposal["files"]["app.json"] = json.dumps(app)
    return proposal_response(request, json.dumps(proposal))


@pytest.mark.parametrize("optimizer", ["gepa", "omni", "autoresearch", "meta_harness"])
def test_application_optimizer_keeps_baseline_pending_then_verifies_winner(
    application, specification, optimizer
):
    run_id = _start(application, specification, optimizer)
    pending = optimize_run.advance(application, run_id)
    assert pending["state"] == "host_pending"
    assert pending["pending"][0]["role"] == "review"
    assert len(executions()) == 1
    result = optimize_run.advance(application, run_id, host_handler=_host)
    assert result["state"] == "completed", result
    winner = result["selection"]["winner"]
    assert winner is not None
    data = load_run(application, run_id)
    selected = data["candidates"][winner["candidate_id"]]
    assert selected["verification_of"]
    assert selected["budget_stage"] == "verification"
    assert selected["state"] == "verified"
    assert (
        selected["source_digest"]
        == data["candidates"][selected["verification_of"]]["source_digest"]
    )
    assert len(executions()) == sum(op["units"] for op in result["budget"]["operations"].values())
    before = len(executions())
    assert optimize_run.advance(application, run_id, host_handler=_host)["state"] == "completed"
    assert len(executions()) == before


def test_application_proposal_cannot_touch_evaluator(application, specification):
    run_id = _start(application, specification)

    def invalid_host(request):
        if request["role"] == "review":
            return _host(request)
        return proposal_response(request, json.dumps({"files": {"benchmark.py": "print('fake')"}}))

    result = optimize_run.advance(application, run_id, host_handler=invalid_host)
    assert result["state"] == "completed"
    assert result["selection"]["retained_baseline"]
    assert len(executions()) == 1
    data = load_run(application, run_id)
    assert len(data["candidates"]) == 1
    failed = [op for op in result["budget"]["operations"].values() if op["status"] == "failed"]
    assert "protected" in failed[-1]["result"]["error"]


@pytest.mark.parametrize("max_trials", [19, 20])
def test_repeated_trials_leave_room_for_every_omni_stage(application, specification, max_trials):
    from agentagon.experiments.budget import BudgetLedger

    run_id = _start(application, specification, "omni", repetitions=3, max_trials=max_trials)
    assert optimize_run.advance(application, run_id)["state"] == "host_pending"
    stages = []

    def host(request):
        response = _host(request)
        if request["role"] == "proposal":
            stages.append(request["payload"]["stage"])
            proposal = json.loads(
                response.get("candidate")
                or response["text"].removeprefix("```\n").removesuffix("\n```")
            )
            app = json.loads(proposal["files"]["app.json"])
            app["variant"] = request["request_id"]
            proposal["files"]["app.json"] = json.dumps(app)
            response = proposal_response(request, json.dumps(proposal))
        return response

    result = optimize_run.advance(application, run_id, host_handler=host)
    assert result["state"] == "completed", result
    assert set(stages) == {"gepa", "autoresearch", "meta_harness", "refinement"}
    assert result["selection"]["winner"]
    assert BudgetLedger.spent(result["budget"], "optimization") == 12
    assert BudgetLedger.spent(result["budget"], "verification") == 3
    assert len(executions()) == BudgetLedger.spent(result["budget"]) == 18
    assert optimize_run.advance(application, run_id, host_handler=host)["state"] == "completed"
    assert len(executions()) == 18


def test_configure_requires_frozen_agreed_scoring(application, specification):
    started = engine.start(application, specification, "local")
    with pytest.raises(AuditError, match="agree on scoring"):
        optimize_run.configure(application, started["run_id"], host="fake", model="fake")


def test_target_stops_search_after_independent_final_verification(application, specification):
    run_id = _start(application, specification, optimizer="omni", target=0.62)
    result = optimize_run.advance(application, run_id, host_handler=_host)
    assert result["state"] == "completed"
    assert result["selection"]["winner"]["score"]["target_reached"]
    assert len(executions()) == 3  # Baseline, target proposal, independent final trial.


def test_application_review_pending_replays_without_duplicate_trial(application, specification):
    from agentagon.experiments.host_bridge import HostBridge

    run_id = _start(application, specification, target=0.62)
    first = optimize_run.advance(application, run_id)
    bridge = HostBridge(application, run_id)
    baseline_review = first["pending"][0]
    bridge.start(baseline_review["request_id"])
    bridge.reply(
        baseline_review["request_id"],
        _host(baseline_review),
        host=baseline_review["host"],
        model=baseline_review["model"],
        binding_digest=baseline_review["binding_digest"],
    )
    pending = optimize_run.advance(application, run_id)
    proposal = next(r for r in pending["pending"] if r["role"] == "proposal")
    bridge.start(proposal["request_id"])
    bridge.reply(
        proposal["request_id"],
        _host(proposal),
        host=proposal["host"],
        model=proposal["model"],
        binding_digest=proposal["binding_digest"],
    )
    pending = optimize_run.advance(application, run_id)
    assert any(r["role"] == "review" for r in pending["pending"])
    before = len(executions())
    assert optimize_run.advance(application, run_id)["state"] == "host_pending"
    assert len(executions()) == before
    result = optimize_run.advance(application, run_id, host_handler=_host)
    assert result["state"] == "completed"
    assert result["selection"]["winner"]


def test_saved_intent_budget_and_scoring_are_reused(application, specification):
    from agentagon.experiments import journeys
    from agentagon.experiments.budget import BudgetLedger

    specification["scoring"] = definition()
    intent = journeys.save(
        application,
        {
            "version": 1,
            "goal": "Improve quality",
            "accepted_by": "test-user",
            "scoring": definition(),
            "discovery": {
                "status": "usable",
                "paths": ["benchmark.py"],
                "evidence": ["existing evaluator inspected"],
                "creation_authorized": False,
            },
            "budget": {"max_trials": 20, "max_elapsed_seconds": 300, "trial_timeout_seconds": 5},
        },
    )
    started = engine.start(application, specification, "local")
    with pytest.raises(AuditError, match="expand"):
        optimize_run.configure(
            application,
            started["run_id"],
            intent_id=intent["intent_id"],
            host="fake",
            model="fake",
            max_trials=21,
        )
    configured = optimize_run.configure(
        application, started["run_id"], intent_id=intent["intent_id"], host="fake", model="fake"
    )
    assert configured["budget"]["limits"]["max_trials"] == 20
    assert load_run(application, started["run_id"])["intent_id"] == intent["intent_id"]
    assert load_run(application, started["run_id"])["limits"]["trial_timeout_seconds"] == 5
    assert (
        BudgetLedger(application, started["run_id"]).snapshot()["started_at"]
        <= configured["budget"]["started_at"]
    )


def test_final_verification_recovers_mapping_before_candidate_flag_crash(
    application, specification, monkeypatch
):
    run_id = _start(application, specification, target=0.62)
    original_save = optimize_run.save_run

    def interrupt(workspace, data):
        if any(c.get("verification_of") for c in data["candidates"].values()):
            raise RuntimeError("process stopped before verification flag persisted")
        original_save(workspace, data)

    monkeypatch.setattr(optimize_run, "save_run", interrupt)
    with pytest.raises(RuntimeError, match="verification flag"):
        optimize_run.advance(application, run_id, host_handler=_host)
    assert len(executions()) == 2
    monkeypatch.setattr(optimize_run, "save_run", original_save)
    result = optimize_run.advance(application, run_id, host_handler=_host)
    assert result["state"] == "completed"
    assert result["selection"]["winner"]
    assert len(executions()) == 3


def test_failed_target_verification_continues_search_with_remaining_budget(
    application, specification
):
    run_id = _start(application, specification, target=0.62)
    rejected = []

    def host(request):
        response = _host(request)
        if request["role"] == "review":
            candidate = load_run(application, run_id)["candidates"][
                request["payload"]["candidate_id"]
            ]
            if candidate.get("verification_of") and not rejected:
                response["review"]["verdict"] = "reject"
                response["review"]["rationale"] = (
                    "Independent review found a problem; try another candidate."
                )
                rejected.append(candidate["candidate_id"])
        return response

    result = optimize_run.advance(application, run_id, host_handler=host)
    assert result["state"] == "completed"
    assert result["selection"]["winner"]["score"]["target_reached"]
    assert len(rejected) == 1 and len(executions()) == 5
    assert load_run(application, run_id)["candidates"][rejected[0]]["state"] == "rejected"


def test_process_driver_lock_prevents_concurrent_application_advances(application, specification):
    import fcntl

    from agentagon.experiments.budget import BudgetLedger

    run_id = _start(application, specification)
    with (BudgetLedger(application, run_id).directory / "application.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(AuditError, match="already advancing"):
            optimize_run.advance(application, run_id, host_handler=_host)
    assert executions() == []
