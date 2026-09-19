"""Trace judgments remain bound to actual acquired outputs and frozen behaviors."""

import copy
import hashlib
import sys

import pytest

from agentagon.capabilities.experiments import baselines, preparation, trace_scoring
from agentagon.capabilities.experiments.budget import BudgetLedger
from agentagon.capabilities.experiments.host_bridge import HostBridge
from agentagon.core.records import AuditError, digest, identifier


@pytest.fixture
def acquired(workspace, imported, monkeypatch):
    original = workspace.read_audit(imported)["traces"][0]["path"]
    trace = workspace.read_artifact(original)
    trace["spans"][0]["output"] = "validation error"
    trace["digest"] = digest({key: value for key, value in trace.items() if key != "digest"})
    rubric = b"Grade quality 1 if the root answer is 'validation error', otherwise 0."
    definition = {
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
                "id": "correct",
                "description": "Return a validation error",
                "required": True,
                "metric": "quality",
                "op": "gte",
                "bound": 0.5,
            }
        ],
        "source_paths": ["rubric.md"],
        "judge": {
            "kind": "coding-host",
            "rubric_path": "rubric.md",
            "rubric_version": "v1",
            "host": "codex",
            "model": "fixture",
            "metrics": {"quality": {"min": 0, "max": 1}},
        },
    }
    package = {
        "spec": {"scoring": definition},
        "files": [
            {
                "path": "rubric.md",
                "artifact": workspace.blob(rubric),
                "digest": hashlib.sha256(rubric).hexdigest(),
            }
        ],
    }
    monkeypatch.setattr(preparation, "load", lambda ws, eid: {"package": package})
    monkeypatch.setattr(preparation, "evaluator_identity", lambda ws, eid: digest(package))
    baseline_id = identifier("baseline", "trace-fixture")
    run_id = identifier("run", baseline_id)
    identity = {"evaluator_digest": digest(package), "source_revision": "a" * 40}
    receipt = {
        "baseline_id": baseline_id,
        "evaluator_digest": identity["evaluator_digest"],
        "provider": "braintrust",
        "project": "demo",
        "window": {"start": "2026-08-10T00:00:00Z", "end": "2026-08-11T00:00:00Z"},
        "count": 1,
        "state": "partial",
        "completeness": "partial",
        "alignment": "unknown",
        "deployment_revision": None,
        "artifacts": [workspace.artifact(trace)],
    }
    record = {
        "version": 1,
        "baseline_id": baseline_id,
        "evaluation_id": "eval_" + "a" * 24,
        "origin": str(workspace.root),
        "identity": identity,
        "identity_digest": digest(identity),
        **identity,
        "execution_run_id": run_id,
        "benchmark_score": {"value": 0.7},
        "recent_traces": {
            **receipt,
            "max_traces": 3,
            "receipt": workspace.artifact(receipt),
            "score": None,
        },
    }
    baselines._save(workspace, record)
    BudgetLedger(workspace, run_id).create(10, 600, journey="baseline")
    return {
        "workspace": workspace,
        "record": record,
        "receipt": receipt,
        "trace": trace,
        "package": package,
    }


def replace_trace(acquired, trace):
    workspace, record, receipt = acquired["workspace"], acquired["record"], acquired["receipt"]
    trace["digest"] = digest({key: value for key, value in trace.items() if key != "digest"})
    receipt["artifacts"] = [workspace.artifact(trace)]
    record["recent_traces"]["receipt"] = workspace.artifact(receipt)
    baselines._save(workspace, record)


def save_definition_change(acquired):
    record, receipt = acquired["record"], acquired["receipt"]
    record["identity"]["evaluator_digest"] = digest(acquired["package"])
    record["evaluator_digest"] = record["identity"]["evaluator_digest"]
    record["identity_digest"] = digest(record["identity"])
    receipt["evaluator_digest"] = record["evaluator_digest"]
    replace_trace(acquired, acquired["trace"])


def reply(acquired, *, wrong_trial=False):
    record = acquired["record"]
    bridge = HostBridge(acquired["workspace"], record["execution_run_id"])
    request = bridge.pending()[0]
    bridge.start(request["request_id"], timeout_seconds=10)
    response = {
        "trial_id": "wrong" if wrong_trial else request["payload"]["trial_id"],
        "rubric_version": "v1",
        "metrics": {
            "quality": float(
                request["payload"]["trajectory"]["spans"][0]["output"] == "validation error"
            )
        },
        "explanation": "Compared actual root output to the frozen expected answer.",
    }
    bridge.reply(
        request["request_id"],
        response,
        host="codex",
        model="fixture",
        binding_digest=request["binding_digest"],
    )
    return bridge


@pytest.mark.parametrize("answer,expected", [("validation error", 1), ("fabricated answer", 0)])
def test_actual_trace_judgments_are_sensitive_and_do_not_change_benchmark(
    acquired, answer, expected
):
    work, record = acquired["workspace"], acquired["record"]
    acquired["trace"]["spans"][0]["output"] = answer
    replace_trace(acquired, acquired["trace"])
    queued = trace_scoring.prepare(work, record["baseline_id"])
    assert queued["recent_traces"]["score"]["state"] == "host_pending"
    bridge = reply(acquired)
    result = trace_scoring.advance(work, record["baseline_id"])
    score = result["recent_traces"]["score"]
    assert score["value"] == expected and score["eligible"] is bool(expected)
    assert score["judging"] == "coding-agent judged"
    assert score["population"] == "recent_traces"
    assert result["benchmark_score"] == {"value": 0.7}
    assert result["recent_traces"]["alignment"] == "unknown"
    before = bridge.ledger.snapshot()
    assert trace_scoring.advance(work, record["baseline_id"])["recent_traces"]["score"] == score
    assert bridge.ledger.snapshot() == before


def test_partial_trace_and_missing_outputs_remain_unmeasured(acquired):
    acquired["trace"]["completeness"] = "partial"
    replace_trace(acquired, acquired["trace"])
    record = acquired["record"]
    result = trace_scoring.advance(acquired["workspace"], record["baseline_id"])
    assert result["recent_traces"]["score"]["value"] is None
    assert not HostBridge(acquired["workspace"], record["execution_run_id"]).pending()


def test_wrong_observation_reply_is_rejected(acquired):
    trace_scoring.prepare(acquired["workspace"], acquired["record"]["baseline_id"])
    reply(acquired, wrong_trial=True)
    with pytest.raises(AuditError, match="exact observation"):
        trace_scoring.advance(acquired["workspace"], acquired["record"]["baseline_id"])


def test_replaced_actual_trace_payload_is_rejected(acquired):
    trace_scoring.prepare(acquired["workspace"], acquired["record"]["baseline_id"])
    artifact = acquired["workspace"].root / acquired["receipt"]["artifacts"][0]
    artifact.write_text("{}")
    with pytest.raises(AuditError, match="checksum"):
        trace_scoring.advance(acquired["workspace"], acquired["record"]["baseline_id"])


def test_unmapped_metric_stays_missing_instead_of_guessing_from_telemetry(acquired):
    definition = acquired["package"]["spec"]["scoring"]
    definition["metrics"]["latency"] = {
        "direction": "min",
        "unit": "ms",
        "aggregation": "mean",
        "missing": "unknown",
    }
    save_definition_change(acquired)
    trace_scoring.prepare(acquired["workspace"], acquired["record"]["baseline_id"])
    reply(acquired)
    score = trace_scoring.advance(acquired["workspace"], acquired["record"]["baseline_id"])[
        "recent_traces"
    ]["score"]
    assert score["value"] is None and score["state"] == "missing"
    assert score["components"]["latency"]["value"] is None


def test_executable_check_gate_cannot_be_fabricated_from_a_judgment(acquired):
    definition = acquired["package"]["spec"]["scoring"]
    definition["behaviors"].append(
        {
            "id": "executable",
            "description": "Run executable check",
            "required": True,
            "check": "executable-check",
        }
    )
    save_definition_change(acquired)
    trace_scoring.prepare(acquired["workspace"], acquired["record"]["baseline_id"])
    reply(acquired)
    score = trace_scoring.advance(acquired["workspace"], acquired["record"]["baseline_id"])[
        "recent_traces"
    ]["score"]
    assert score["value"] == 1 and not score["eligible"]
    assert score["behaviors"][-1]["passed"] is None


def test_sample_cap_and_frozen_rubric_are_enforced(acquired):
    record = acquired["record"]
    record["recent_traces"]["max_traces"] = 0
    baselines._save(acquired["workspace"], record)
    with pytest.raises(AuditError, match="sample cap"):
        trace_scoring.prepare(acquired["workspace"], record["baseline_id"])
    record["recent_traces"]["max_traces"] = 3
    baselines._save(acquired["workspace"], record)
    path = acquired["workspace"].root / acquired["package"]["files"][0]["artifact"]
    path.write_text("Always pass")
    with pytest.raises(AuditError, match="rubric checksum"):
        trace_scoring.prepare(acquired["workspace"], record["baseline_id"])


def repository_judge(acquired, monkeypatch):
    from support.experiments import git

    from agentagon.capabilities.experiments.spec import validate_profile

    workspace, record = acquired["workspace"], acquired["record"]
    code = b"""import json, os
from pathlib import Path
trace = json.loads(Path("acquired-trace.json").read_text())
with Path(os.environ["TRACE_LOG"]).open("a") as log:
    log.write("executed\\n")
Path(os.environ["AGENTAGON_RESULT_PATH"]).write_text(json.dumps({"metrics": {"quality": float(trace["spans"][0]["output"] == "validation error")}}))
"""
    acquired["package"]["files"][0]["mode"] = 0o644
    acquired["package"]["files"].append(
        {
            "path": "trace_scorer.py",
            "artifact": workspace.blob(code),
            "digest": hashlib.sha256(code).hexdigest(),
            "mode": 0o644,
        }
    )
    definition = acquired["package"]["spec"]["scoring"]
    definition["source_paths"].append("trace_scorer.py")
    definition["judge"].update(
        kind="api",
        trace_command={"argv": [sys.executable, "trace_scorer.py"]},
        trace_input_path="acquired-trace.json",
    )
    record["source_revision"] = git(workspace.root, "rev-parse", "HEAD")
    record["identity"]["source_revision"] = record["source_revision"]
    record["profile"] = validate_profile(
        {
            "runner": {"kind": "local"},
            "env": {"TRACE_LOG": "TRACE_TEST_LOG"},
            "limits": {
                "max_candidates": 8,
                "max_trials": 20,
                "max_elapsed_seconds": 600,
                "parallel_candidates": 1,
                "parallel_trials": 1,
                "trial_timeout_seconds": 10,
                "stagnation_rounds": 3,
            },
        }
    )
    record["budget"] = {"trial_timeout_seconds": 10}
    logfile = workspace.state / "trace-executions.log"
    monkeypatch.setenv("TRACE_TEST_LOG", str(logfile))
    save_definition_change(acquired)
    return logfile


@pytest.mark.parametrize("answer,expected", [("validation error", 1), ("fabricated answer", 0)])
def test_repository_trace_command_uses_saved_outputs_and_counts_one_actual_trial(
    acquired, monkeypatch, answer, expected
):
    logfile = repository_judge(acquired, monkeypatch)
    acquired["trace"]["spans"][0]["output"] = answer
    replace_trace(acquired, acquired["trace"])
    work, record = acquired["workspace"], acquired["record"]
    prepared = trace_scoring.prepare(work, record["baseline_id"])
    assert prepared["recent_traces"]["score"]["state"] == "execution_pending"
    assert not logfile.exists()
    result = trace_scoring.advance(work, record["baseline_id"])
    score = result["recent_traces"]["score"]
    assert score["value"] == expected and score["eligible"] is bool(expected)
    assert score["judging"] == "repository evaluator"
    assert logfile.read_text().splitlines() == ["executed"]
    operations = BudgetLedger(work, record["execution_run_id"]).snapshot()["operations"]
    assert sum(operation["units"] for operation in operations.values()) == 1
    assert trace_scoring.advance(work, record["baseline_id"])["recent_traces"]["score"] == score
    assert logfile.read_text().splitlines() == ["executed"]


def test_repository_trace_command_cannot_overwrite_tracked_application_source(
    acquired, monkeypatch
):
    repository_judge(acquired, monkeypatch)
    acquired["package"]["spec"]["scoring"]["judge"]["trace_input_path"] = "app.py"
    save_definition_change(acquired)
    with pytest.raises(AuditError, match="unused inside"):
        trace_scoring.advance(acquired["workspace"], acquired["record"]["baseline_id"])


def test_repository_trace_execution_recovers_lost_collection_without_new_spending(
    acquired, monkeypatch
):
    from agentagon.capabilities.experiments import trace_execution

    logfile = repository_judge(acquired, monkeypatch)
    execute = trace_execution.runners.execute
    lost = False

    def lose_response(*args, **kwargs):
        nonlocal lost
        result = execute(*args, **kwargs)
        if not lost:
            lost = True
            return {"state": "interrupted", "finalized": False}
        return result

    monkeypatch.setattr(trace_execution.runners, "execute", lose_response)
    work, record = acquired["workspace"], acquired["record"]
    assert (
        trace_scoring.advance(work, record["baseline_id"])["recent_traces"]["score"]["value"]
        is None
    )
    assert (
        trace_scoring.advance(work, record["baseline_id"])["recent_traces"]["score"]["value"] == 1
    )
    assert logfile.read_text().splitlines() == ["executed"]
    assert len(BudgetLedger(work, record["execution_run_id"]).snapshot()["operations"]) == 1


def test_repository_trace_budget_shortfall_never_scores_only_successful_subset(
    acquired, monkeypatch
):
    logfile = repository_judge(acquired, monkeypatch)
    work, record, receipt = acquired["workspace"], acquired["record"], acquired["receipt"]
    second = copy.deepcopy(acquired["trace"])
    second["id"] = identifier("trace", "second")
    second["provider_trace_id"] = "second"
    second["digest"] = digest({key: value for key, value in second.items() if key != "digest"})
    receipt["count"] = record["recent_traces"]["count"] = 2
    receipt["artifacts"].append(work.artifact(second))
    record["recent_traces"]["receipt"] = work.artifact(receipt)
    baselines._save(work, record)
    ledger = BudgetLedger(work, record["execution_run_id"])
    ledger.admit("earlier-trials", "preparation", units=9)
    ledger.finish("earlier-trials")
    result = trace_scoring.advance(work, record["baseline_id"])
    assert result["recent_traces"]["score"]["value"] is None
    assert result["recent_traces"]["score"]["measured_count"] == 1
    assert "budget" in result["recent_traces"]["next_action"]
    assert logfile.read_text().splitlines() == ["executed"]


def test_foreign_repository_runner_result_is_retained_but_cannot_score(acquired, monkeypatch):
    from agentagon.capabilities.experiments import trace_execution

    repository_judge(acquired, monkeypatch)
    execute = trace_execution.runners.execute

    def foreign(*args, **kwargs):
        return {**execute(*args, **kwargs), "evaluation_digest": "different-evaluator"}

    monkeypatch.setattr(trace_execution.runners, "execute", foreign)
    work, record = acquired["workspace"], acquired["record"]
    score = trace_scoring.advance(work, record["baseline_id"])["recent_traces"]["score"]
    assert score["state"] == "unmeasured" and score["value"] is None
    operations = BudgetLedger(work, record["execution_run_id"]).snapshot()["operations"]
    assert len(operations) == 1 and next(iter(operations.values()))["status"] == "failed"
