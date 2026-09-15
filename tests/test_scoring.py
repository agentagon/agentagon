"""Agreed scoring preserves gates, unknown measurements and original units."""

import copy

import pytest

from agentagon.core.records import AuditError
from agentagon.experiments import scoring


def definition():
    return {
        "version": 1,
        "mode": "weighted",
        "metrics": {
            "quality": {
                "direction": "max",
                "unit": "fraction",
                "aggregation": "mean",
                "missing": "unknown",
                "weight": 2,
            },
            "latency": {
                "direction": "min",
                "unit": "ms",
                "aggregation": "median",
                "missing": "unknown",
                "weight": 1,
                "scale": 100,
            },
        },
        "behaviors": [
            {
                "id": "correct",
                "description": "Answers are correct",
                "required": True,
                "metric": "quality",
                "op": "gte",
                "bound": 0.6,
            }
        ],
        "source_paths": ["benchmark.py"],
    }


def test_higher_score_never_compensates_for_failed_required_behavior():
    score = scoring.summarize(definition(), [{"quality": 0.5, "latency": 1}])
    assert score["state"] == "measured" and score["value"] == 0.99
    assert not score["eligible"] and score["behaviors"][0]["passed"] is False


def test_missing_values_and_failures_are_not_numeric_zero():
    score = scoring.summarize(definition(), [{"quality": 0.9}])
    assert score["state"] == "missing" and score["value"] is None and not score["eligible"]
    d = definition()
    d["metrics"]["latency"]["missing"] = "fail"
    assert scoring.summarize(d, [{"quality": 0.9}])["state"] == "failed"
    with pytest.raises(AuditError, match="finite"):
        scoring.summarize(d, [{"quality": float("nan"), "latency": 100}])


def test_aggregation_direction_and_primary_target_use_agreed_units():
    d = definition()
    d.update(mode="primary", primary="latency", target=80)
    score = scoring.summarize(d, [{"quality": 0.8, "latency": 100}, {"quality": 1, "latency": 40}])
    assert score["components"]["quality"]["value"] == 0.9
    assert score["components"]["latency"]["value"] == 70
    assert score["value"] == -0.7 and score["target_reached"]


def test_custom_function_is_a_frozen_runner_metric_and_not_executed_by_host():
    d = definition()
    d.update(mode="custom", custom_metric="quality")
    assert scoring.summarize(d, [{"quality": 0.8, "latency": 10}])["value"] == 0.8
    invalid = copy.deepcopy(d)
    invalid["custom_metric"] = "unmeasured"
    with pytest.raises(AuditError, match="declared"):
        scoring.validate(invalid)


def test_selection_rejects_tampered_cached_trial_score(application, specification):
    from support.experiments import baseline, propose, verify

    from agentagon.experiments import engine
    from agentagon.experiments.store import load_run, save_run

    specification["scoring"] = definition()
    original = baseline(application, specification)
    candidate, _ = propose(application, original["run_id"], quality=0.9)
    verify(application, original["run_id"], candidate["candidate_id"])
    data = load_run(application, original["run_id"])
    data["candidates"][candidate["candidate_id"]]["trials"][0]["metrics"]["quality"] = 100
    save_run(application, data)
    with pytest.raises(AuditError, match="recorded metrics"):
        engine.select_best(application, original["run_id"])


def test_late_execution_retains_evidence_but_cannot_establish_score(
    application, specification, monkeypatch
):
    import time
    from datetime import datetime

    from agentagon.experiments import engine, runners
    from agentagon.experiments.budget import BudgetLedger

    budget_id = "run_" + "d" * 24
    ledger = BudgetLedger(application, budget_id)
    ledger.create(10, 100)
    started = engine.start(application, specification, "local", budget_id=budget_id)
    execute = runners.execute
    clock = time.time

    def delayed_collection(*args, **kwargs):
        result = execute(*args, **kwargs)
        monkeypatch.setattr("agentagon.experiments.budget.time.time", lambda: clock() + 101)
        return result

    monkeypatch.setattr(runners, "execute", delayed_collection)
    measured = engine.run(application, started["run_id"])
    candidate = measured["candidate"]
    assert candidate["state"] == "failed"
    trial = candidate["trials"][0]
    admission = ledger.snapshot()["operations"][trial["trial_id"]]
    assert trial["artifact"] and admission["deadline_exceeded"]
    # ISO timestamps retain microseconds; the ledger's epoch float can be finer.
    assert (
        datetime.fromisoformat(trial["request"]["deadline_at"]).timestamp()
        <= admission["deadline"] + 1e-6
    )
    assert "budget deadline" in trial["error"]
