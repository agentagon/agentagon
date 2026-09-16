"""Real isolated trials establish cross-focus gates before any selection or delivery."""

import copy
import json

import pytest
from support.evaluation import draft, review_for
from support.experiments import executions, passing_review, propose, verify
from support.optimizer import candidate_text, proposal_response
from test_baselines import complete
from test_journeys import intent_definition
from test_scoring import definition

from agentagon.core.records import AuditError, digest
from agentagon.experiments import (
    baselines,
    delivery,
    engine,
    journeys,
    optimize_run,
    preparation,
    suites,
)
from agentagon.experiments.budget import BudgetLedger
from agentagon.experiments.host_bridge import HostBridge, HostWorkPending
from agentagon.experiments.store import load_run, save_run


def _suite(application, specification, timeout=None):
    members = []
    for primary in ("quality", "latency"):
        spec = copy.deepcopy(specification)
        spec.update(repetitions=1, seeds=[0])
        spec["scoring"] = {**definition(), "mode": "primary", "primary": primary}
        if primary == "latency":
            spec["scoring"]["target"] = 80
        started, plan = draft(application, spec)
        checked = preparation.check(application, started["evaluation_id"], plan)
        frozen = preparation.freeze(application, started["evaluation_id"], review_for(checked))
        baseline = complete(application, baselines.start(application, frozen["evaluation_id"]))
        members.append(
            {
                "focus_id": "focus_" + ("a" if primary == "quality" else "b") * 24,
                "focus_version": 2,
                "name": primary.title(),
                "evaluation_id": frozen["evaluation_id"],
                "evaluator_digest": preparation.evaluator_identity(
                    application, frozen["evaluation_id"]
                ),
                "baseline_id": baseline["baseline_id"],
                "primary_metric": primary,
                "metrics": frozen["package"]["spec"]["metrics"],
                "guardrails": [
                    {
                        "metric": primary,
                        "op": "gte" if primary == "quality" else "lte",
                        "bound": 0,
                        "reference": "baseline_delta",
                    }
                ],
                "profile_name": "local",
            }
        )
    manifest = {
        "version": 1,
        "agent_id": "agent_" + "a" * 24,
        "focus_id": members[-1]["focus_id"],
        "members": members,
    }
    manifest.update(digest=digest(manifest), missing=[])
    profile = copy.deepcopy(baseline["profile"])
    if timeout is not None:
        profile["limits"]["trial_timeout_seconds"] = timeout
    run = engine.start(
        application,
        preparation.fix_spec(application, members[-1]["evaluation_id"]),
        "local",
        execution_profile=profile,
    )
    suites.bind(application, run["run_id"], manifest)
    return run["run_id"], manifest


def _host(quality=0.9):
    def respond(request):
        if request["role"] == "review":
            return {
                "review": passing_review({"review_template": request["payload"]["review_template"]})
            }
        proposal = json.loads(candidate_text(request))
        proposal["files"]["app.json"] = json.dumps(
            {"latency": 80, "quality": quality, "variant": "finalist"}
        )
        return proposal_response(request, json.dumps(proposal))

    return respond


@pytest.mark.parametrize("quality,passed", [(0.7, False), (0.9, True)])
def test_latency_improvement_must_preserve_earlier_quality_focus(
    application, specification, quality, passed
):
    run_id, manifest = _suite(application, specification, timeout=2)
    optimize_run.configure(
        application, run_id, host="fake", model="fake", optimizer="gepa", max_trials=10
    )
    before = len(executions())
    result = optimize_run.advance(application, run_id, host_handler=_host(quality))
    assert result["state"] == "completed", result
    state = suites.status(application, run_id)
    assert state["state"] == ("completed" if passed else "failed"), state
    assert state["result"]["passed"] is passed
    quality_row, latency_row = state["result"]["members"]
    assert latency_row["passed"] and latency_row["improved"]
    assert quality_row["passed"] is passed
    assert (
        len(executions()) - before == 7
    )  # Primary baseline/proposal/final, plus two isolated pairs.
    assert BudgetLedger.spent(result["budget"], "verification") == 5
    assert len({entry["execution_run_id"] for entry in state["measurements"].values()}) == 4
    for entry in state["measurements"].values():
        child = load_run(application, entry["execution_run_id"])
        assert child["limits"]["trial_timeout_seconds"] == 2
        assert child["profile"]["limits"]["trial_timeout_seconds"] == 10
        assert all(
            trial["request"]["timeout_seconds"] <= 2
            for trial in child["candidates"][child["baseline_id"]]["trials"]
        )
    assert result["selection"]["alternatives"] == []
    if passed:
        verified = suites.verify_completed(application, run_id, manifest)
        assert verified["passed"]
        package = delivery.deliver(application, run_id=run_id)
        assert package["state"] == "prepared"
        assert suites.verify_outcome(application, run_id, manifest)["passed"]
        candidate = load_run(application, run_id)["candidates"][state["binding"]["candidate_id"]]
        with pytest.raises(AuditError, match="differs from the suite"):
            engine.select(application, run_id, candidate["verification_of"])
    else:
        assert result["selection"]["retained_baseline"]
        assert load_run(application, run_id)["selected"] is None
        with pytest.raises(AuditError, match="guardrail failed"):
            engine.select(application, run_id, state["binding"]["candidate_id"])
        assert suites.verify_outcome(application, run_id, manifest)["state"] == "failed"
    count = len(executions())
    assert (
        optimize_run.advance(application, run_id, host_handler=_host(quality))["state"]
        == "completed"
    )
    assert len(executions()) == count


def test_suite_review_pause_resumes_exact_child_without_repeating_trial(application, specification):
    run_id, manifest = _suite(application, specification)
    accepted = intent_definition()
    accepted["scoring"] = load_run(application, run_id)["spec"]["scoring"]
    accepted["budget"].update(max_trials=10, trial_timeout_seconds=2)
    intent = journeys.save(application, accepted)
    optimize_run.configure(
        application, run_id, host="fake", model="fake", intent_id=intent["intent_id"]
    )

    def pause_child(request):
        if request["payload"].get("execution_run_id"):
            raise HostWorkPending(request["request_id"])
        return _host()(request)

    first = optimize_run.advance(application, run_id, host_handler=pause_child)
    assert first["state"] == "verification"
    request = next(r for r in first["pending"] if r["payload"].get("execution_run_id"))
    child_id = suites.review_execution(application, run_id, request)
    assert child_id != run_id and request["payload"]["review_template"]["run_id"] == child_id
    tampered = copy.deepcopy(request)
    tampered["payload"]["execution_run_id"] = run_id
    with pytest.raises(AuditError, match="child"):
        suites.review_execution(application, run_id, tampered)
    count = len(executions())
    # The host claim remains pending in the bridge until a reply is durably recorded.
    assert suites.advance(application, run_id)["state"] == "host_pending"
    assert len(executions()) == count
    bridge = HostBridge(application, run_id)
    bridge.reply(
        request["request_id"],
        _host()(request),
        host="fake",
        model="fake",
        binding_digest=request["binding_digest"],
    )
    done = optimize_run.advance(application, run_id, host_handler=_host())
    assert done["selection"]["winner"]
    assert len(executions()) - count == 3
    assert suites.verify_completed(application, run_id, manifest)["passed"]
    state = suites.status(application, run_id)
    assert state["trial_timeout_seconds"] == 10
    assert state["runtime_trial_timeout_seconds"] == 2
    assert "next_action" not in state
    child = load_run(application, child_id)
    child["candidates"][child["baseline_id"]]["metrics"]["quality"] = 100
    save_run(application, child)
    with pytest.raises(AuditError, match="execution evidence"):
        suites.verify_completed(application, run_id, manifest)
    assert state["result_artifact"]


def test_suite_cannot_bind_late_or_spend_unreserved_final_trials(application, specification):
    run_id, manifest = _suite(application, specification)
    with pytest.raises(AuditError, match="total budget"):
        optimize_run.configure(application, run_id, host="fake", model="fake", max_trials=5)
    assert not BudgetLedger(application, run_id).path.exists()
    invalid = copy.deepcopy(manifest["members"][0])
    invalid["primary_metric"] = "latency"
    with pytest.raises(AuditError, match="frozen scoring objective"):
        suites._definition(application, invalid)
    optimize_run.configure(application, run_id, host="fake", model="fake", max_trials=10)
    altered = copy.deepcopy(manifest)
    altered["members"][0]["guardrails"][0]["bound"] = -1
    altered["digest"] = digest({k: v for k, v in altered.items() if k not in {"digest", "missing"}})
    with pytest.raises(AuditError, match="already frozen"):
        suites.bind(application, run_id, altered)
    unbound = engine.start(
        application,
        preparation.fix_spec(application, manifest["members"][-1]["evaluation_id"]),
        "local",
    )
    optimize_run.configure(application, unbound["run_id"], host="fake", model="fake")
    with pytest.raises(AuditError, match="before configuring"):
        suites.bind(application, unbound["run_id"], manifest)


def test_final_holdout_input_never_enters_optimizer_feedback(application, specification):
    snapshot = application.state / "holdout.json"
    snapshot.write_text(json.dumps({"dataset_partition": "final_holdout", "rows": []}))
    specification["inputs"] = [
        {"source": str(snapshot.relative_to(application.root)), "path": "cases.json"}
    ]
    specification["scoring"] = definition()
    run = engine.start(application, specification, "local")
    with pytest.raises(AuditError, match="holdout inputs"):
        optimize_run.configure(application, run["run_id"], host="fake", model="fake")
    assert not BudgetLedger(application, run["run_id"]).path.exists()


def test_isolated_finalist_requires_repaired_checks_instead_of_baseline_reproduction(
    application, specification
):
    check = application.state / "required-quality.py"
    check.write_text(
        "import json\nfrom pathlib import Path\nassert json.loads(Path('app.json').read_text())['quality'] >= 0.9\n"
    )
    specification.update(repetitions=1, seeds=[0])
    specification["checks"][0]["baseline_expected"] = "fail"
    specification["overlays"] = [
        {"source": str(check.relative_to(application.root)), "path": "checks.py", "deliver": False}
    ]
    run = engine.start(application, specification, "local", measurement_role="reference")
    original = verify(application, run["run_id"], run["candidate_id"])
    assert original["candidate"]["checks"][0]["expected"] == "fail"
    fixed, _ = propose(application, run["run_id"], quality=0.95)
    measured = verify(application, run["run_id"], fixed["candidate_id"])
    isolated = engine.start(
        application,
        specification,
        "local",
        source_revision=measured["candidate"]["source_revision"],
        measurement_role="finalist",
    )
    accepted = verify(application, isolated["run_id"], isolated["candidate_id"])
    assert accepted["candidate"]["state"] == "verified"
    assert accepted["candidate"]["checks"][0]["expected"] == "pass"
