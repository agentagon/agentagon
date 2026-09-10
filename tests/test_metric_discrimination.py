"""Preparation must distinguish valid behaviors, not merely accept numeric output."""

import copy
import json

import pytest
from support.evaluation import draft, review_for

from agentagon.core.records import AuditError
from agentagon.experiments import inspection, preparation, runners


def metric_draft(application, specification, *, repetitions=1):
    specification.update(repetitions=repetitions, seeds=list(range(repetitions)))
    specification["checks"][0]["preflight"] = True
    started, plan = draft(application, specification)
    plan["metric_cases"] = []
    for name, latency, quality in [("better", 80, 0.9), ("worse", 100, 0.7)]:
        source = application.state / f"{name}.json"
        source.write_text(json.dumps({"latency": latency, "quality": quality, "variant": name}))
        plan["metric_cases"].append(
            {
                "id": name,
                "description": f"Known correct {name} behavior",
                "mutations": [
                    {"source": str(source.relative_to(application.root)), "path": "app.json"}
                ],
            }
        )
    plan["metric_comparisons"] = [
        {"metric": "latency", "better": "better", "worse": "worse", "min_delta": 10},
        {"metric": "quality", "better": "better", "worse": "baseline", "min_delta": 0.1},
    ]
    return started, plan


def test_correct_cases_distinguish_min_and_max_objectives_and_freeze(application, specification):
    started, plan = metric_draft(application, specification, repetitions=3)
    checked = preparation.check(application, started["evaluation_id"], plan)
    assert checked["state"] == "awaiting_review"
    record = checked["checks"][-1]
    assert checked["usage"]["trials"] == 12
    assert len(record["metric_comparisons"]) == 2
    assert all(c["passed"] for c in record["metric_comparisons"])
    assert record["metric_comparisons"][0]["improvement"] == 20
    assert record["metric_comparisons"][1]["better_value"] == 0.9
    assert record["metric_comparisons"][1]["improvement"] == 0.1
    projected = inspection.evaluation_summary(checked)
    assert projected["metric_comparisons"] == record["metric_comparisons"]
    assert {t["case_id"]: t["kind"] for t in projected["trials"]} == {
        "baseline": "baseline",
        "low-quality": "negative",
        "better": "metric",
        "worse": "metric",
    }
    assert "mutations" not in json.dumps(projected)
    # Negative controls still execute the full benchmark despite opted-in preflights.
    negative = next(t for t in record["trials"] if t["case_id"] == "low-quality")
    assert negative["outcome"]["checks"][0]["exit_code"] == 1
    result = application.read_artifact(negative["artifact"])
    assert [c["role"] for c in result["results"]] == ["check", "benchmark"]
    assert result["benchmark_output"]
    again = preparation.check(application, started["evaluation_id"], plan)
    assert again["checks"][-1] == record
    assert again["usage"]["trials"] == 12
    frozen = preparation.freeze(application, started["evaluation_id"], review_for(again))
    assert frozen["state"] == "frozen"
    assert frozen["package"]["plan"]["metric_comparisons"] == plan["metric_comparisons"]


@pytest.mark.parametrize(
    "failure", ["constant", "constant-default-delta", "reversed", "insufficient", "incorrect"]
)
def test_plausible_metrics_cannot_hide_a_bad_comparison(application, specification, failure):
    started, plan = metric_draft(application, specification)
    if failure == "constant-default-delta":
        del plan["metric_comparisons"][0]["min_delta"]
        failure = "constant"
    if failure in ("constant", "reversed", "incorrect"):
        source = application.root / plan["metric_cases"][0]["mutations"][0]["source"]
        source.write_text(
            json.dumps(
                {
                    "latency": 100
                    if failure == "constant"
                    else 120
                    if failure == "reversed"
                    else 80,
                    "quality": 0.1 if failure == "incorrect" else 0.9,
                    "variant": "bad-comparison",
                }
            )
        )
    else:
        plan["metric_comparisons"][0]["min_delta"] = 21
    checked = preparation.check(application, started["evaluation_id"], plan)
    assert checked["state"] == "check_failed"
    record = checked["checks"][-1]
    if failure == "incorrect":
        assert "correctness checks" in record["metric_comparison_error"]
    else:
        assert not record["metric_comparisons"][0]["passed"]
    with pytest.raises(AuditError, match="passing baseline"):
        preparation.freeze(application, started["evaluation_id"], review_for(checked))


@pytest.mark.parametrize(
    "failure",
    [
        "unknown-metric",
        "negative-ref",
        "self",
        "protected",
        "baseline-fails",
        "missing-cases",
        "negative-delta",
        "infinite-delta",
    ],
)
def test_invalid_comparison_plan_spends_no_trials(application, specification, failure):
    started, plan = metric_draft(application, specification)
    comparison = plan["metric_comparisons"][0]
    if failure == "unknown-metric":
        comparison["metric"] = "unknown"
    elif failure == "negative-ref":
        comparison["worse"] = "low-quality"
    elif failure == "self":
        comparison["worse"] = "better"
    elif failure == "protected":
        plan["metric_cases"][0]["mutations"][0]["path"] = "checks.py"
    elif failure == "baseline-fails":
        plan["spec"]["checks"][0]["baseline_expected"] = "fail"
    elif failure == "missing-cases":
        del plan["metric_cases"]
    else:
        comparison["min_delta"] = -1 if failure == "negative-delta" else float("inf")
    with pytest.raises(AuditError):
        preparation.check(application, started["evaluation_id"], plan)
    assert preparation.load(application, started["evaluation_id"])["usage"]["trials"] == 0


def test_metric_trial_resume_keeps_reservation_and_comparisons(
    application, specification, monkeypatch
):
    started, plan = metric_draft(application, specification)
    execute = runners.execute
    interrupted = False

    def interrupt_once(profile, source, attempt, request):
        nonlocal interrupted
        if json.loads((source / "app.json").read_text())["variant"] == "better" and not interrupted:
            interrupted = True
            return {"state": "interrupted", "finalized": False}
        return execute(profile, source, attempt, request)

    monkeypatch.setattr(runners, "execute", interrupt_once)
    pending = preparation.check(application, started["evaluation_id"], plan)
    assert pending["state"] == "interrupted" and pending["usage"]["trials"] == 3
    reserved = pending["checks"][-1]["trials"][-1]["trial_id"]
    resumed = preparation.check(application, started["evaluation_id"], plan)
    assert resumed["state"] == "awaiting_review" and resumed["usage"]["trials"] == 4
    assert resumed["checks"][-1]["trials"][2]["trial_id"] == reserved
    assert all(c["passed"] for c in resumed["checks"][-1]["metric_comparisons"])


def test_freeze_recomputes_comparison_from_observations(application, specification, monkeypatch):
    started, plan = metric_draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    trial = next(t for t in checked["checks"][-1]["trials"] if t["case_id"] == "better")
    read = application.read_artifact

    def changed_observation(artifact):
        result = copy.deepcopy(read(artifact))
        if artifact == trial["artifact"]:
            output = json.loads(result["benchmark_output"])
            output["metrics"]["latency"] = 200
            result["benchmark_output"] = json.dumps(output)
        return result

    monkeypatch.setattr(application, "read_artifact", changed_observation)
    with pytest.raises(AuditError, match="metric comparison evidence changed"):
        preparation.freeze(application, started["evaluation_id"], review_for(checked))
