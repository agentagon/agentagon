"""Native-host grading judges actual saved outputs without replacing runner checks."""

import json

import pytest
from support.evaluation import draft, review_for
from support.experiments import executions, git, passing_review

from agentagon.capabilities.experiments import delivery, engine, grading, preparation
from agentagon.capabilities.experiments.budget import BudgetLedger
from agentagon.capabilities.experiments.host_bridge import HostBridge
from agentagon.capabilities.experiments.store import load_run
from agentagon.core.records import AuditError, identifier

BENCHMARK = """import json, os
from pathlib import Path
app = json.loads(Path('app.json').read_text())
with Path(os.environ['EXECUTION_LOG']).open('a') as stream:
    stream.write(json.dumps({'variant': app['variant']}) + '\\n')
Path(os.environ['AGENTAGON_RESULT_PATH']).write_text(json.dumps({
    'metrics': {'latency': app['latency']},
    'outputs': [{'answer': app.get('answer', 'validation error'),
                 'tool_calls': app.get('tool_calls', [])}]
}))
"""


def start(
    application,
    specification,
    *,
    answer="validation error",
    tool_calls=(),
    crash=False,
    prepare_only=False,
):
    app_path = application.root / "app.json"
    app = json.loads(app_path.read_text())
    app.update(answer=answer, tool_calls=list(tool_calls))
    app_path.write_text(json.dumps(app))
    (application.root / "benchmark.py").write_text(
        "raise RuntimeError('application unavailable')\n" if crash else BENCHMARK
    )
    (application.root / "rubric.md").write_text(
        "For an unknown tool, grade quality 1 only if the answer is exactly "
        "'validation error' and tool_calls is empty. Otherwise grade quality 0.\n"
    )
    git(application.root, "add", ".")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Add grounded output-grading fixture",
    )
    specification.update(
        repetitions=1,
        seeds=[0],
        evaluation_paths=["benchmark.py", "checks.py", "rubric.md"],
        scoring={
            "version": 1,
            "mode": "primary",
            "primary": "quality",
            "metrics": {
                "quality": {
                    "direction": "max",
                    "unit": "fraction",
                    "aggregation": "mean",
                    "missing": "unknown",
                }
            },
            "behaviors": [
                {
                    "id": "unknown-tool",
                    "description": "Unknown tools produce an error without invocation",
                    "required": True,
                    "metric": "quality",
                    "op": "gte",
                    "bound": 1,
                    "rubric": "rubric.md",
                }
            ],
            "source_paths": ["benchmark.py", "rubric.md"],
            "judge": {
                "kind": "coding-host",
                "rubric_path": "rubric.md",
                "rubric_version": "unknown-tool-v1",
                "host": "test-native-host",
                "model": "deterministic-fixture",
                "metrics": {"quality": {"min": 0, "max": 1}},
            },
        },
    )
    if prepare_only:
        return None
    budget_id = identifier("budget", str(application.root))
    ledger = BudgetLedger(application, budget_id)
    ledger.create(10, 600)
    return engine.start(application, specification, "local", budget_id=budget_id)


def pending(application, specification, **kwargs):
    started = start(application, specification, **kwargs)
    result = engine.run(application, started["run_id"])
    assert result["candidate"]["state"] == "awaiting_grading", result["candidate"]
    data = load_run(application, started["run_id"])
    candidate = data["candidates"][data["baseline_id"]]
    trial = candidate["trials"][0]
    bridge = HostBridge(application, data["budget_id"])
    request = bridge.pending()[0]
    return data, candidate, trial, bridge, request


def judgment(request, **changes):
    output = request["payload"]["outputs"][0]
    value = int(output["answer"] == "validation error" and output["tool_calls"] == [])
    response = {
        "trial_id": request["payload"]["trial_id"],
        "rubric_version": request["payload"]["rubric_version"],
        "metrics": {"quality": value},
        "explanation": (
            "The saved output rejects the unknown tool without invocation."
            if value
            else "The saved output fails the rubric's error/no-invocation requirement."
        ),
    }
    return {**response, **changes}


def reply(bridge, request, response):
    bridge.start(request["request_id"])
    return bridge.reply(
        request["request_id"],
        response,
        host=request["host"],
        model=request["model"],
        binding_digest=request["binding_digest"],
    )


@pytest.mark.parametrize(
    ("answer", "tool_calls", "expected"),
    [
        ("validation error", [], 1),
        ("success", [], 0),
        ("validation error", ["unknown-tool"], 0),
    ],
)
def test_known_judgments_match_actual_outputs_and_the_frozen_rubric(
    application, specification, answer, tool_calls, expected
):
    data, candidate, trial, bridge, request = pending(
        application, specification, answer=answer, tool_calls=tool_calls
    )
    assert request["role"] == "judging"
    assert request["payload"]["outputs"] == [{"answer": answer, "tool_calls": tool_calls}]
    assert "Otherwise grade quality 0" in request["payload"]["rubric"]
    reply(bridge, request, judgment(request))
    assert grading.resume(application, data, candidate)
    assert trial["state"] == "completed"
    assert trial["metrics"] == {"latency": 100, "quality": expected}
    observation = application.read_artifact(trial["grading"]["observation"])
    assert observation["label"] == "coding-agent judged"
    assert observation["host"] == "test-native-host"
    assert observation["model"] == "deterministic-fixture"
    assert observation["response"]["explanation"]
    assert observation["evidence"] == trial["artifact"]


def test_resume_waits_without_reexecution_and_review_uses_the_bound_judgment(
    application, specification
):
    data, _, trial, bridge, request = pending(application, specification)
    for _ in range(2):
        result = engine.run(application, data["run_id"])
        assert result["candidate"]["state"] == "awaiting_grading"
    assert len(executions()) == 1
    assert bridge.ledger.spent(bridge.ledger.snapshot()) == 1
    reply(bridge, request, judgment(request))
    measured = engine.run(application, data["run_id"])
    assert measured["candidate"]["state"] == "awaiting_review"
    assert measured["candidate"]["metrics"]["quality"] == 1
    verified = engine.run(application, data["run_id"], review=passing_review(measured))
    assert verified["candidate"]["state"] == "verified"
    assert len(executions()) == 1
    assert bridge.ledger.spent(bridge.ledger.snapshot()) == 1
    replay = reply(bridge, request, judgment(request))
    assert replay["request_id"] == trial["grading"]["request_id"]
    assert len(bridge.snapshot()["requests"]) == 1


def test_failed_application_cannot_be_replaced_with_host_grading(application, specification):
    started = start(application, specification, crash=True)
    result = engine.run(application, started["run_id"])
    assert result["candidate"]["state"] == "failed"
    data = load_run(application, started["run_id"])
    assert HostBridge(application, data["budget_id"]).snapshot()["requests"] == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"trial_id": "foreign-trial"},
        {"rubric_version": "unfrozen-version"},
        {"explanation": " "},
        {"metrics": {"quality": 2}},
        {"metrics": {"quality": True}},
        {"metrics": {"latency": 0, "quality": 1}},
    ],
)
def test_unbound_or_invalid_host_scores_never_become_measurements(
    application, specification, changes
):
    data, candidate, _, bridge, request = pending(application, specification)
    reply(bridge, request, judgment(request, **changes))
    with pytest.raises(AuditError):
        grading.resume(application, data, candidate)
    assert candidate["trials"][0]["state"] == "awaiting_grading"


def test_host_provenance_and_retained_output_integrity_are_required(application, specification):
    data, candidate, trial, bridge, request = pending(application, specification)
    bridge.start(request["request_id"])
    with pytest.raises(AuditError, match="provenance"):
        bridge.reply(
            request["request_id"],
            judgment(request),
            host=request["host"],
            model="different-model",
            binding_digest=request["binding_digest"],
        )
    reply(bridge, request, judgment(request))
    grading.resume(application, data, candidate)
    original = application.read_artifact(trial["artifact"])
    original["benchmark_output"] = json.dumps(
        {"metrics": {"latency": 100}, "outputs": [{"answer": "fabricated", "tool_calls": []}]}
    )
    trial["artifact"] = application.artifact(original)
    with pytest.raises(AuditError, match="no longer matches"):
        grading.observed(application, data, candidate, trial, {"latency": 100})


def test_saved_grading_observation_is_immutable(application, specification):
    data, candidate, trial, bridge, request = pending(application, specification)
    reply(bridge, request, judgment(request))
    grading.resume(application, data, candidate)
    observation = application.read_artifact(trial["grading"]["observation"])
    observation["response"]["metrics"]["quality"] = 0
    trial["grading"]["observation"] = application.artifact(observation)
    with pytest.raises(AuditError, match="observation changed"):
        grading.observed(application, data, candidate, trial, {"latency": 100})


def test_missing_saved_binding_rejects_grading_as_invalid_evidence(application, specification):
    data, candidate, trial, bridge, request = pending(application, specification)
    reply(bridge, request, judgment(request))
    saved = bridge.snapshot()
    del saved["requests"][request["request_id"]]["binding_digest"]
    application.write(bridge.path, saved)
    with pytest.raises(AuditError, match="no longer matches"):
        grading.observed(application, data, candidate, trial, {"latency": 100})


def preparation_pending(application, specification, *, compare=False):
    start(application, specification, prepare_only=True)
    started, plan = draft(application, specification)
    checks = application.root / started["worktree"] / "checks.py"
    checks.write_text(checks.read_text() + "\n# Reviewed evaluator version\n")
    wrong = application.state / "wrong-behavior.json"
    wrong.write_text(
        json.dumps(
            {
                "latency": 100,
                "quality": 0.1,
                "variant": "incorrect",
                "answer": "success",
                "tool_calls": ["unknown-tool"],
            }
        )
    )
    if compare:
        lower = application.state / "lower-score.json"
        lower.write_text(
            json.dumps({"latency": 100, "quality": 0.8, "answer": "success", "variant": "lower"})
        )
        plan["metric_cases"] = [
            {
                "id": "lower-score",
                "description": "Valid execution with a worse judged answer",
                "mutations": [
                    {"path": "app.json", "source": str(lower.relative_to(application.root))}
                ],
            }
        ]
        plan["metric_comparisons"] = [
            {"metric": "quality", "better": "baseline", "worse": "lower-score", "min_delta": 0.5}
        ]
    pending = preparation.check(application, started["evaluation_id"], plan)
    assert pending["state"] == "awaiting_grading", pending
    return started["evaluation_id"], plan, pending


def test_preparation_grades_real_baseline_and_negative_outputs_then_freezes(
    application, specification
):
    evaluation_id, plan, waiting = preparation_pending(application, specification)
    bridge = HostBridge(application, evaluation_id)
    assert len(executions()) == 1
    again = preparation.check(application, evaluation_id, plan)
    assert again["current_validation"] == waiting["current_validation"]
    assert again["state"] == "awaiting_grading"
    assert len(executions()) == 1
    first = bridge.pending()[0]
    assert first["payload"]["evaluation_id"] == evaluation_id
    assert first["payload"]["validation_id"] == waiting["current_validation"]
    reply(bridge, first, judgment(first))
    second_wait = preparation.check(application, evaluation_id, plan)
    assert second_wait["state"] == "awaiting_grading"
    assert len(executions()) == 2
    second = bridge.pending()[0]
    assert second["source"] != first["source"]
    assert judgment(second)["metrics"]["quality"] == 0
    reply(bridge, second, judgment(second))
    checked = preparation.check(application, evaluation_id, plan)
    assert checked["state"] == "awaiting_review", checked
    trials = checked["checks"][-1]["trials"]
    assert [t["outcome"]["metrics"]["quality"] for t in trials] == [1, 0]
    assert trials[1]["outcome"]["checks"][0]["exit_code"] == 1
    assert all(
        t["grading"]["observation"] in checked["checks"][-1]["review_template"]["evidence"]
        for t in trials
    )
    frozen = preparation.freeze(application, evaluation_id, review_for(checked))
    assert frozen["state"] == "frozen"
    assert len(executions()) == 2
    assert bridge.ledger.spent(bridge.ledger.snapshot()) == 2


def test_preparation_freeze_rechecks_grading_reply_binding(application, specification):
    evaluation_id, plan, _ = preparation_pending(application, specification)
    bridge = HostBridge(application, evaluation_id)
    while bridge.pending():
        request = bridge.pending()[0]
        reply(bridge, request, judgment(request))
        checked = preparation.check(application, evaluation_id, plan)
    assert checked["state"] == "awaiting_review"
    state = bridge.snapshot()
    first = next(r for r in state["requests"].values() if r["response"]["metrics"]["quality"] == 1)
    first["response"]["metrics"]["quality"] = 0
    application.write(bridge.path, state)
    with pytest.raises(AuditError, match="admitted host-work evidence"):
        preparation.freeze(application, evaluation_id, review_for(checked))


@pytest.mark.parametrize("compare", [False, True])
def test_evaluation_delivery_rechecks_host_grades(application, specification, compare):
    evaluation_id, plan, _ = preparation_pending(application, specification, compare=compare)
    bridge = HostBridge(application, evaluation_id)
    while bridge.pending():
        request = bridge.pending()[0]
        reply(bridge, request, judgment(request))
        checked = preparation.check(application, evaluation_id, plan)
    assert checked["state"] == "awaiting_review", checked
    preparation.freeze(application, evaluation_id, review_for(checked))
    assert delivery.deliver(application, evaluation_id=evaluation_id)["state"] == "prepared"
    trial = checked["checks"][-1]["trials"][0]
    (application.root / trial["grading"]["observation"]).write_text("{}")
    with pytest.raises(AuditError, match="checksum|observation changed"):
        delivery.deliver(application, evaluation_id=evaluation_id)


def test_candidate_delivery_includes_validated_host_metrics(application, specification):
    from pathlib import Path

    from support.experiments import propose

    start(application, specification, prepare_only=True)
    specification["scoring"]["primary"] = "latency"
    specification["scoring"]["metrics"]["latency"] = {
        "direction": "min",
        "unit": "ms",
        "aggregation": "mean",
        "missing": "unknown",
    }
    budget_id = identifier("budget", str(application.root))
    BudgetLedger(application, budget_id).create(10, 600)
    started = engine.start(application, specification, "local", budget_id=budget_id)
    bridge = HostBridge(application, budget_id)
    run_id = started["run_id"]

    def verify_candidate(candidate_id=None):
        engine.run(application, run_id, candidate_id)
        request = bridge.pending()[0]
        reply(bridge, request, judgment(request))
        measured = engine.run(application, run_id, candidate_id)
        engine.run(application, run_id, candidate_id, review=passing_review(measured))

    verify_candidate()
    created, _ = propose(application, run_id, latency=80)
    candidate_id = created["candidate_id"]
    verify_candidate(candidate_id)
    engine.select(application, run_id, candidate_id)
    shipped = delivery.ship(application, run_id)
    summary = json.loads(Path(shipped["artifacts"]["summary"]).read_text())
    assert summary["metrics"]["quality"]["candidate"] == 1
    assert summary["metrics"]["quality"]["min"] == summary["metrics"]["quality"]["max"] == 1
    assert summary["metrics"]["latency"]["candidate"] == 80
    assert summary["candidate_score"]["score"] > summary["baseline_score"]["score"]


def test_preparation_execution_failure_never_requests_judgment(application, specification):
    start(application, specification, crash=True, prepare_only=True)
    started, plan = draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    assert checked["state"] == "check_failed"
    assert not HostBridge(application, started["evaluation_id"]).pending()


def test_late_grading_response_is_retained_but_not_admitted(
    application, specification, monkeypatch
):
    data, candidate, _, bridge, request = pending(application, specification)
    claimed = bridge.start(request["request_id"])
    monkeypatch.setattr(
        "agentagon.capabilities.experiments.budget.time.time", lambda: claimed["deadline"] + 1
    )
    completed = reply(bridge, request, judgment(request))
    assert completed["deadline_exceeded"]
    assert completed["response"]["metrics"]["quality"] == 1
    with pytest.raises(AuditError, match="exceeded its admitted time budget"):
        grading.resume(application, data, candidate)


def test_preparation_propagates_trial_deadline_and_rejects_late_execution(
    application, specification, monkeypatch
):
    from datetime import datetime

    from agentagon.capabilities.experiments import runners

    start(application, specification, prepare_only=True)
    started, plan = draft(application, specification)
    execute = runners.execute

    def delayed_collection(profile, source, attempt, request):
        ledger = BudgetLedger(application, started["evaluation_id"])
        admitted = ledger.snapshot()["operations"][request["attempt_id"]]
        assert (
            datetime.fromisoformat(request["deadline_at"]).timestamp()
            <= admitted["deadline"] + 0.000001
        )
        result = execute(profile, source, attempt, request)
        budget = ledger.snapshot()
        expired = budget["started_at"] + budget["limits"]["max_elapsed_seconds"] + 1
        monkeypatch.setattr("agentagon.capabilities.experiments.budget.time.time", lambda: expired)
        return result

    monkeypatch.setattr(runners, "execute", delayed_collection)
    with pytest.raises(AuditError, match="time budget exhausted"):
        preparation.check(application, started["evaluation_id"], plan)
    saved = preparation.load(application, started["evaluation_id"])
    trial = saved["checks"][-1]["trials"][0]
    assert trial["state"] == "failed"
    assert "exceeded its admitted deadline" in trial["error"]
    assert trial["artifact"]
    assert not HostBridge(application, started["evaluation_id"]).pending()
