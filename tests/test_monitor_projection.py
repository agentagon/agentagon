"""Monitor projections explain collection state without inventing production health."""

import pytest

from agentagon.domain.monitoring import monitor_projection
from agentagon.workflows.service import Application

AT = "2026-09-20T12:00:00+00:00"


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    application = Application(tmp_path / "state", execute=lambda *_: {})
    application.scheduler.close()
    project = application.register(str(root))["id"]
    yield application, project
    application.close()


def save_monitor(application, project, suffix, **changes):
    monitor = {
        "id": f"monitor_{suffix}",
        "agent_id": "agent_support",
        "enabled": True,
        "state": "ready",
        "series": 3,
        "operational_revision": 7,
        "interval_seconds": 3600,
        "next_due": "2026-09-20T13:00:00+00:00",
        "last_checked": "2026-09-20T11:00:00+00:00",
        "checkpoint": "2026-09-20T11:00:00+00:00",
        **changes,
    }
    return application.state.db.put_record(project, "monitors", monitor["id"], monitor)


def save_observation(application, project, monitor, suffix, *, complete=True, population=4):
    observation = {
        "id": f"observation_{suffix}",
        "monitor_id": monitor["id"],
        "agent_id": monitor["agent_id"],
        "series": monitor["series"],
        "window": {
            "start": "2026-09-20T10:00:00+00:00",
            "end": "2026-09-20T11:00:00+00:00",
            "gap": False,
        },
        "coverage": {"complete": complete, "invalid_spans": []},
        "limitations": [] if complete else ["Provider pagination was incomplete."],
        "metrics": [
            {
                "name": "Latency",
                "count": population,
                "population": population,
                "coverage": 1 if population else 0,
                "time_coverage_complete": complete,
            }
        ],
    }
    return application.state.db.put_record(project, "observations", observation["id"], observation)


def test_public_states_have_one_evidence_backed_resolution_action(app, monkeypatch):
    application, project = app
    tasks = {
        "task_collecting": {"state": "running", "updated_at": AT},
        "task_interrupted": {
            "state": "interrupted",
            "updated_at": AT,
            "next_action": "Resume or discard this observation.",
        },
    }
    monkeypatch.setattr(
        application.runtime,
        "get",
        lambda _project, task_id: {"id": task_id, **tasks[task_id]},
    )
    records = {
        "paused": save_monitor(application, project, "paused", enabled=False, state="paused"),
        "scheduled": save_monitor(application, project, "scheduled"),
        "collecting": save_monitor(
            application, project, "collecting", state="running", task_id="task_collecting"
        ),
        "credentials_needed": save_monitor(
            application,
            project,
            "credentials",
            enabled=False,
            state="needs_attention",
            error="Provider credential is unavailable.",
        ),
        "storage_limit_reached": save_monitor(
            application,
            project,
            "storage",
            enabled=False,
            state="needs_attention",
            error="Background evidence storage budget exhausted.",
        ),
        "interrupted_check": save_monitor(
            application,
            project,
            "interrupted",
            state="needs_attention",
            task_id="task_interrupted",
        ),
        "backing_off": save_monitor(
            application,
            project,
            "backoff",
            state="needs_attention",
            failures=2,
            error="Provider temporarily unavailable.",
        ),
        "stale": save_monitor(
            application,
            project,
            "stale",
            next_due="2026-09-20T10:00:00+00:00",
        ),
    }
    waiting = save_monitor(application, project, "waiting")
    save_observation(application, project, waiting, "waiting", population=0)
    records["waiting_for_traffic"] = waiting
    partial = save_monitor(application, project, "partial")
    save_observation(application, project, partial, "partial", complete=False, population=0)
    records["partial_coverage"] = partial

    actions = {
        "paused": "enable_monitoring",
        "scheduled": "analyze_now",
        "collecting": "inspect_check",
        "waiting_for_traffic": "review_trace_scope",
        "credentials_needed": "repair_credentials",
        "storage_limit_reached": "adjust_storage_budget",
        "interrupted_check": "resolve_interrupted_check",
        "partial_coverage": "inspect_coverage",
        "backing_off": "analyze_now",
        "stale": "analyze_now",
    }
    for expected, monitor in records.items():
        state = monitor_projection(application, project, monitor["id"], at=AT)["public_state"]
        assert state["code"] == expected
        assert state["resolution_action"] == actions[expected]
        assert state["label"] and state["reason"]


def test_partial_and_stale_states_take_precedence_over_zero_traffic(app):
    application, project = app
    monitor = save_monitor(application, project, "precedence")
    save_observation(application, project, monitor, "zero", complete=False, population=0)
    partial = monitor_projection(application, project, monitor["id"], at=AT)
    assert partial["coverage_summary"]["waiting_for_traffic"] is False
    assert partial["public_state"]["code"] == "partial_coverage"

    application.state.db.put_record(
        project,
        "monitors",
        monitor["id"],
        {
            **application.monitoring.get(project, monitor["id"]),
            "next_due": "2026-09-20T10:00:00+00:00",
        },
    )
    stale = monitor_projection(application, project, monitor["id"], at=AT)
    assert stale["public_state"]["code"] == "stale"
    assert stale["coverage_summary"]["state"] == "partial"


def test_overview_preserves_raw_monitor_and_adds_evidence_summary(app):
    application, project = app
    monitor = save_monitor(
        application,
        project,
        "overview",
        next_due="2999-01-01T00:00:00+00:00",
        custom_provider_field="retained",
    )
    observation = save_observation(application, project, monitor, "overview", complete=False)

    overview = application.monitoring.overview(project)
    projected = next(item for item in overview["monitors"] if item["id"] == monitor["id"])
    assert projected["state"] == "ready"
    assert projected["custom_provider_field"] == "retained"
    assert projected["public_state"]["code"] == "partial_coverage"
    assert projected["last_successful_window"] == {
        "observation_id": observation["id"],
        "series": 3,
        "coverage_complete": False,
        "start": "2026-09-20T10:00:00+00:00",
        "end": "2026-09-20T11:00:00+00:00",
        "gap": False,
    }
    assert projected["last_successful_checkpoint"] == "2026-09-20T11:00:00+00:00"
    assert projected["coverage_summary"]["measurements"][0]["population"] == 4
    assert projected["revision_summary"] == {
        "comparison_series": 3,
        "operational_revision": 7,
        "record_revision": monitor["revision"],
    }
    assert "healthy" not in projected and "recovered" not in projected
    assert overview["observations"][0]["id"] == observation["id"]
