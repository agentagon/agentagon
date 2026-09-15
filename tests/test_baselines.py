"""Fresh branch-aware baseline jobs retain frozen definitions and previous evidence."""

import json

import pytest
from support.evaluation import draft, review_for
from support.experiments import executions, git, passing_review
from test_journeys import intent_definition
from test_scoring import definition

from agentagon.core.records import AuditError
from agentagon.experiments import baselines, journeys, preparation
from agentagon.experiments.budget import BudgetLedger
from agentagon.experiments.host_bridge import HostBridge


def frozen(application, specification):
    specification["scoring"] = definition()
    started, plan = draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    return preparation.freeze(application, started["evaluation_id"], review_for(checked))


def complete(application, baseline):
    waiting = baselines.advance(application, baseline["baseline_id"])
    assert waiting["state"] == "host_pending", waiting
    bridge = HostBridge(application, baseline["execution_run_id"])
    request = next(r for r in bridge.pending() if r["role"] == "review")
    reply = passing_review({"review_template": request["payload"]["review_template"]})
    bridge.start(request["request_id"])
    bridge.reply(
        request["request_id"],
        reply,
        host=request["host"],
        model=request["model"],
        binding_digest=request["binding_digest"],
    )
    done = baselines.advance(application, baseline["baseline_id"])
    assert done["state"] == "completed", done
    return done


def test_two_commits_share_evaluator_but_never_reuse_a_completed_measurement(
    application, specification
):
    evaluation = frozen(application, specification)
    first = baselines.start(application, evaluation["evaluation_id"], request_id="first")
    done = complete(application, first)
    count = len(executions())
    assert (
        baselines.start(application, evaluation["evaluation_id"], request_id="first")["baseline_id"]
        == first["baseline_id"]
    )
    assert (
        baselines.advance(application, first["baseline_id"])["measurement"] == done["measurement"]
    )
    assert len(executions()) == count
    git(application.root, "checkout", "-qb", "faster")
    app = application.root / "app.json"
    app.write_text(json.dumps({"latency": 80, "quality": 0.9, "variant": "faster"}))
    git(application.root, "add", "app.json")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Faster app",
    )
    second = baselines.rerun(application, first["baseline_id"])
    updated = complete(application, second)
    assert updated["evaluator_digest"] == done["evaluator_digest"]
    assert updated["source_revision"] != done["source_revision"]
    assert updated["branch"] == "faster"
    assert updated["execution_run_id"] != done["execution_run_id"]
    assert len(executions()) == count + 3
    assert len(baselines.list_baselines(application)) == 2
    assert journeys.invitation(application)["show"]
    assert not journeys.invitation(application)["show"]
    ledger = BudgetLedger(application, updated["execution_run_id"])
    assert ledger.spent(ledger.snapshot()) == 3


def test_evaluator_drift_is_blocked_without_losing_saved_baseline(application, specification):
    evaluation = frozen(application, specification)
    original = baselines.start(application, evaluation["evaluation_id"])
    (application.root / "checks.py").write_text("pass\n")
    git(application.root, "add", "checks.py")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Change eval",
    )
    blocked = baselines.rerun(application, original["baseline_id"])
    assert blocked["state"] == "blocked" and "compatibility" in blocked["pending_action"]
    assert len(baselines.list_baselines(application)) == 2


def test_trace_refresh_window_partial_acquisition_and_population_are_explicit(
    application, specification
):
    evaluation = frozen(application, specification)
    intent = intent_definition()
    intent["discovery"].update(status="usable", paths=["benchmark.py"])
    intent["acquisition"] = {
        "provider": "langfuse",
        "project": "sample",
        "lookback_seconds": 3600,
        "max_traces": 5,
        "filters": {},
        "authorized": True,
    }
    saved = journeys.save(application, intent, evaluation["evaluation_id"])
    base = baselines.start(application, evaluation["evaluation_id"], saved["intent_id"])
    waiting = baselines.advance(application, base["baseline_id"])
    traces = waiting["recent_traces"]
    assert traces["window"]["end"] == base["created_at"]
    assert any(
        r["role"] == "acquisition"
        for r in HostBridge(application, base["execution_run_id"]).pending()
    )
    receipt = {
        "baseline_id": base["baseline_id"],
        "evaluator_digest": base["evaluator_digest"],
        "provider": "langfuse",
        "project": "sample",
        "window": traces["window"],
        "state": "partial",
        "completeness": "partial",
        "count": 1,
        "alignment": "unknown",
        "deployment_revision": None,
        "artifacts": [application.artifact({"private_input": "secret"})],
    }
    attached = baselines.attach_traces(application, base["baseline_id"], receipt)
    projected = baselines.public_projection(attached)
    assert projected["recent_traces"]["score"]["value"] is None
    assert projected["recent_traces"]["population"] == "recent_traces"
    assert "secret" not in json.dumps(projected)
    with pytest.raises(AuditError, match="sample cap"):
        baselines.attach_traces(application, base["baseline_id"], {**receipt, "count": 6})


def test_pending_acquisition_replays_and_saved_execution_profile_survives_settings_changes(
    application, specification
):
    import sys

    from agentagon.storage.config import Config

    evaluation = frozen(application, specification)
    intent = intent_definition()
    intent["discovery"].update(status="usable", paths=["benchmark.py"])
    intent["acquisition"] = {
        "provider": "langfuse",
        "project": "sample",
        "lookback_seconds": 3600,
        "max_traces": 5,
        "filters": {},
        "authorized": True,
    }
    saved = journeys.save(application, intent, evaluation["evaluation_id"])
    initial = baselines.start(application, evaluation["evaluation_id"], saved["intent_id"])
    profile = Config().profile(application.root, "local")
    profile["setup"] = [[sys.executable, "-c", "raise RuntimeError('changed profile')"]]
    Config().update_profile("project", "local", profile, application.root)
    done = complete(application, initial)
    assert done["state"] == "completed"
    assert done["recent_traces"]["state"] == "pending"
    repeat = baselines.rerun(application, initial["baseline_id"])
    assert repeat["profile"]["setup"] == initial["profile"]["setup"]
    complete(application, repeat)


def test_provider_export_import_is_normalized_private_and_idempotent(
    application, specification, fixtures, tmp_path, monkeypatch
):
    evaluation = frozen(application, specification)
    monkeypatch.setattr(baselines, "now", lambda: "2026-08-11T00:00:00+00:00")
    baseline = baselines.start(application, evaluation["evaluation_id"])
    acquisition = tmp_path / "acquisition.json"
    acquisition.write_text(
        json.dumps(
            {
                "source": "braintrust",
                "project": "demo",
                "method": "fixture",
                "tool_version": "fixture-v1",
                "fetched_at": "2026-08-11T00:00:00Z",
                "completeness": "complete",
                "pagination_complete": True,
                "selected_trace_ids": ["root"],
                "failed_trace_ids": [],
            }
        )
    )
    imported = baselines.import_traces(
        application,
        baseline["baseline_id"],
        fixtures / "braintrust.json",
        acquisition,
        "braintrust",
        "demo",
    )
    recent = imported["recent_traces"]
    assert recent["state"] == "complete" and recent["count"] == 1
    assert recent["alignment"] == "unknown"
    assert recent["score"]["value"] is None
    receipt = application.read_artifact(recent["receipt"])
    trace = application.read_artifact(receipt["artifacts"][0])
    assert trace["contract_version"] == "1" and trace["source"] == "braintrust"
    assert trace["spans"] and trace["provider_trace_id"] == "root"
    assert "London" not in json.dumps(baselines.public_projection(imported))
    replay = baselines.import_traces(
        application, baseline["baseline_id"], fixtures / "braintrust.json", acquisition
    )
    assert replay["recent_traces"] == recent
    with pytest.raises(AuditError, match="immutable"):
        baselines.import_traces(application, baseline["baseline_id"], fixtures / "braintrust.json")
    fresh = baselines.rerun(application, baseline["baseline_id"])
    partial = baselines.import_traces(
        application,
        fresh["baseline_id"],
        fixtures / "braintrust.json",
        source="braintrust",
        project="demo",
    )
    assert partial["recent_traces"]["state"] == "partial"
    assert partial["recent_traces"]["completeness"] == "unknown"
    assert partial["recent_traces"]["score"]["eligible_count"] == 0
