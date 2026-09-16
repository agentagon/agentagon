"""Agent identity, focused evidence and retained measurement history."""

import copy
import uuid

import pytest
from test_baselines import complete, frozen
from test_webapp import project, running

from agentagon.core.records import AuditError
from agentagon.experiments import baselines
from agentagon.webapp import snapshots
from agentagon.webapp.service import Application


@pytest.fixture
def app(tmp_path):
    result = Application(tmp_path / "catalog", execute=lambda *_: {})
    yield result
    result.close()


def agent(app, saved, **values):
    return app.save_application_agent(
        saved["id"], {"name": "Support", "code_scopes": ["app.py"], **values}
    )


def focus(app, saved, item, category="correctness"):
    return app.catalog.save_focus(
        saved["id"], item["id"], {"category": category, "goal": f"Improve {category}"}
    )


def test_discovery_is_explicit_bounded_and_preserves_confirmed_identity(app, tmp_path):
    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    (root / "app.py").write_text(
        'support = Agent(name="Support")\nresearch = Agent(name="Research")\nraise RuntimeError("never execute discovery")\n'
    )
    assert app.application_agents(saved["id"]) == {"agents": []}
    discovered = app.catalog.discover(saved["id"])
    assert len(discovered["agents"]) == 2
    suggested = discovered["agents"][0]
    confirmed = app.save_application_agent(
        saved["id"], {"status": "confirmed", "name": "Customer support"}, suggested["id"]
    )
    again = app.catalog.discover(saved["id"])
    same = next(a for a in again["agents"] if a["id"] == confirmed["id"])
    assert same["name"] == "Customer support" and same["status"] == "confirmed"
    assert not (root / ".agentagon").exists()
    app2 = Application(app.state.directory, execute=lambda *_: {})
    try:
        assert app2.catalog.agent(saved["id"], confirmed["id"])["name"] == "Customer support"
    finally:
        app2.close()


def test_agents_and_focuses_do_not_cross_projects_or_agent_boundaries(app, tmp_path):
    one, two = project(app, tmp_path, "one"), project(app, tmp_path, "two")
    a = agent(app, one)
    b = agent(app, one, name="Research")
    f = focus(app, one, a)
    with pytest.raises(AuditError, match="not found"):
        app.catalog.agent(two["id"], a["id"])
    with pytest.raises(AuditError, match="not found"):
        app.catalog.focus(one["id"], b["id"], f["id"])
    with pytest.raises(AuditError, match="private"):
        agent(app, one, code_scopes=[".agentagon"])
    with pytest.raises(AuditError, match="confirm"):
        suggested = agent(app, one, name="Maybe", status="suggested")
        focus(app, one, suggested)


def test_saved_issue_focus_uses_real_occurrences_and_agent_scoped_audits(
    app, workspace, imported, fixtures
):
    from support.audit import finish
    from support.dashboard import make_audit

    from agentagon.operations import import_traces, start
    from agentagon.storage.issues import list_issues

    finish(workspace, imported)
    repeated = start(
        workspace,
        mode="traces",
        source="braintrust",
        project="demo",
        start_time="2026-08-10T00:00:00Z",
        end_time="2026-08-11T00:00:00Z",
        limit=1,
        scopes=[],
        host="test",
        model="fixture",
    )["audit_id"]
    import_traces(workspace, repeated, fixtures / "braintrust.json")
    finish(workspace, repeated)
    unrelated = make_audit(workspace)
    issue = list_issues(workspace)[0]
    assert issue["audit_ids"] == [imported, repeated]
    assert issue["latest_audit_id"] == repeated
    assert {o["audit_id"] for o in issue["occurrences"]} == {imported, repeated}

    saved = app.register(str(workspace.root))
    support, research, unused = [
        agent(app, saved, name=name) for name in ("Support", "Research", "Unused")
    ]
    for owner, audit_id in ((support, imported), (research, repeated)):
        job_id = "job_" + uuid.uuid4().hex[:24]
        app.state.db.put_record(
            saved["id"],
            "jobs",
            job_id,
            {
                "id": job_id,
                "kind": "audit",
                "state": "completed",
                "application_agent_id": owner["id"],
                "workflow_ids": {"audit_id": audit_id},
            },
        )
        projected = app.agent_overview(saved["id"], owner["id"])["issues"]
        assert len(projected) == 1
        assert projected[0]["audit_ids"] == [audit_id]
        assert projected[0]["latest_audit_id"] == audit_id
        source = {"kind": "issue", "issue_id": issue["issue_id"], "audit_id": audit_id}
        retained = app.catalog.save_focus(
            saved["id"], owner["id"], {"goal": "Handle weather timeouts", "source": source}
        )
        assert retained["source"] == source

    assert app.agent_overview(saved["id"], unused["id"])["issues"] == []
    source = {"kind": "issue", "issue_id": issue["issue_id"], "audit_id": repeated}
    with pytest.raises(AuditError, match="different application agent"):
        app.catalog.save_focus(
            saved["id"], support["id"], {"goal": "Handle timeouts", "source": source}
        )
    source["audit_id"] = unrelated
    with pytest.raises(AuditError, match="does not belong to that audit"):
        app.catalog.save_focus(
            saved["id"], support["id"], {"goal": "Handle timeouts", "source": source}
        )
    source.pop("audit_id")
    with pytest.raises(AuditError, match="different application agent"):
        app.catalog.save_focus(
            saved["id"], unused["id"], {"goal": "Handle timeouts", "source": source}
        )


def test_focused_audit_binds_dirty_local_code_and_missing_baseline_blocks_fix(
    app, tmp_path, monkeypatch
):
    saved = project(app, tmp_path)
    a = agent(app, saved)
    f = focus(app, saved, a)
    calls = []
    monkeypatch.setattr(
        app.jobs,
        "submit",
        lambda pid, payload, **kwargs: calls.append(copy.deepcopy(payload)) or payload,
    )
    request = {
        "operation_id": str(uuid.uuid4()),
        "kind": "audit",
        "application_agent_id": a["id"],
        "focus_id": f["id"],
        "options": {},
    }
    result = app.submit_job(saved["id"], request)
    assert result["options"]["code_scopes"] == ["app.py"]
    assert result["options"]["investigation_plan"]["focus_version"] == 1
    assert result["goal"] == f["goal"]
    with pytest.raises(AuditError, match="baselines"):
        app.submit_job(saved["id"], {**request, "kind": "fix"})
    with pytest.raises(AuditError, match="binding"):
        app.submit_job(saved["id"], {**request, "options": {"code_scopes": ["."]}})
    assert len(calls) == 1


def test_new_focus_retains_prior_measurements_and_requires_its_own_baseline(
    app, application, specification, monkeypatch
):
    specification["repetitions"] = 1
    evaluation = frozen(application, specification)
    baseline = complete(application, baselines.start(application, evaluation["evaluation_id"]))
    saved = app.register(str(application.root))
    a = app.save_application_agent(saved["id"], {"name": "Support", "code_scopes": ["app.json"]})
    correctness = focus(app, saved, a)
    attached = app.catalog.bind_measurement(
        saved["id"],
        a["id"],
        correctness["id"],
        {
            "evaluation_id": evaluation["evaluation_id"],
            "baseline_id": baseline["baseline_id"],
            "primary_metric": "quality",
        },
    )
    assert app.catalog.readiness(saved["id"], a["id"])["fix"]["ready"]
    latency = focus(app, saved, a, "latency")
    suite = app.catalog.suite(saved["id"], a["id"], latency["id"])
    assert [m["focus_id"] for m in suite["members"]] == [correctness["id"]]
    assert suite["missing"][0]["focus_id"] == latency["id"]
    assert not app.catalog.readiness(saved["id"], a["id"])["fix"]["ready"]
    app.catalog.bind_measurement(
        saved["id"],
        a["id"],
        latency["id"],
        {
            "evaluation_id": evaluation["evaluation_id"],
            "baseline_id": baseline["baseline_id"],
            "primary_metric": "latency",
        },
    )
    suite = app.catalog.suite(saved["id"], a["id"], latency["id"])
    assert len(suite["members"]) == 2 and not suite["missing"]
    quality_guard = next(m for m in suite["members"] if m["focus_id"] == correctness["id"])[
        "guardrails"
    ][0]
    assert quality_guard == {
        "metric": "quality",
        "op": "gte",
        "reference": "baseline_delta",
        "bound": 0,
    }
    assert (
        app.catalog.focus(saved["id"], a["id"], correctness["id"])["measurement"]
        == attached["measurement"]
    )
    history = app.catalog.metrics(saved["id"], a["id"])
    assert len(history["guardrails"]) == 2
    assert all(len(row["measurements"]) == 1 for row in history["metrics"])
    history["metrics"][0]["measurements"].append(
        {"run_id": "run_" + "a" * 24, "value": 0.9, "state": "verified"}
    )
    monkeypatch.setattr(app.catalog, "metrics", lambda *_: history)
    overview = app.agent_overview(saved["id"], a["id"])
    assert {b["baseline_id"] for b in overview["baselines"]} == {baseline["baseline_id"]}

    # Shared application changes need coverage for every confirmed affected agent.
    other = app.save_application_agent(
        saved["id"], {"name": "Research", "code_scopes": ["app.json"]}
    )
    suite = app.catalog.suite(saved["id"], a["id"], latency["id"])
    assert suite["missing"][0]["application_agent_id"] == other["id"]
    assert "narrow permitted changes" in suite["missing"][0]["reason"]
    other_focus = focus(app, saved, other)
    app.catalog.bind_measurement(
        saved["id"],
        other["id"],
        other_focus["id"],
        {"evaluation_id": evaluation["evaluation_id"], "baseline_id": baseline["baseline_id"]},
    )
    assert len(app.catalog.suite(saved["id"], a["id"], latency["id"])["members"]) == 3

    # Retained IDs are not enough after actual measurement evidence is lost.
    retained = baselines.status(application, baseline["baseline_id"])
    (application.root / retained["measurement_artifact"]).unlink()
    assert not app.catalog.readiness(saved["id"], a["id"])["fix"]["ready"]
    assert len(app.catalog.suite(saved["id"], a["id"], latency["id"])["missing"]) == 3


def test_recent_trace_selection_preserves_children_and_newest_matching_roots(app, tmp_path):
    saved = project(app, tmp_path)
    workspace = app.state.workspace(saved["id"])
    workspace.initialize()
    rows = []
    for index, name in enumerate(["Support", "Research", "Support"]):
        root = {
            "span_id": f"root-{index}",
            "root_span_id": f"root-{index}",
            "span_parents": [],
            "metadata": {"agent_name": name},
            "metrics": {"start": 100 + index, "end": 101 + index},
            "input": "question",
            "output": "answer",
        }
        child = {**root, "span_id": f"child-{index}", "span_parents": [f"root-{index}"]}
        rows.extend([root, child])
    imported = snapshots.save(
        workspace,
        saved["id"],
        {
            "kind": "traces",
            "connection_id": "connection_" + "a" * 24,
            "selection": {"project": "remote", "cap": 100},
            "provenance": {"provider": "braintrust"},
            "completeness": {"complete": True},
            "items": rows,
        },
    )
    selected = snapshots.select_traces(
        workspace, saved["id"], imported["id"], {"filters": {"agent_name": "Support"}}, 1
    )
    assert selected["provenance"]["selected_trace_ids"] == ["root-2"]
    assert len(selected["items"]) == 2
    assert snapshots.load(workspace, imported["id"])["items"] == rows
    assert (
        snapshots.select_traces(
            workspace, saved["id"], imported["id"], {"filters": {"agent_name": "Support"}}, 1
        )["id"]
        == selected["id"]
    )
    with pytest.raises(AuditError, match="No completed"):
        snapshots.select_traces(
            workspace, saved["id"], imported["id"], {"filters": {"agent_name": "Missing"}}, 1
        )


def test_agent_routes_require_session_and_reading_never_discovers(app, tmp_path):
    saved = project(app, tmp_path)
    base = f"/api/projects/{saved['id']}/application-agents"
    with running(app) as (client, _):
        assert client.get(base).json() == {"agents": []}
        response = client.post(base, json={"name": "Support", "code_scopes": ["app.py"]})
        assert response.status_code == 200
        a = response.json()
        f = client.post(
            f"{base}/{a['id']}/focuses",
            json={"category": "latency", "goal": "Reduce time to answer"},
        ).json()
        overview = client.get(f"{base}/{a['id']}/overview", params={"focus_id": f["id"]}).json()
        assert overview["active_focus_id"] == f["id"]
        assert overview["readiness"]["fix"]["ready"] is False
        client.headers.pop("X-Agentagon-Token")
        assert client.post(f"{base}/{a['id']}", json={"name": "Changed"}).status_code == 403


def test_focused_request_replay_keeps_original_binding_after_agent_edits(
    app, tmp_path, monkeypatch
):
    saved = project(app, tmp_path)
    a = agent(app, saved)
    f = focus(app, saved, a)
    monkeypatch.setattr(app.jobs, "_dispatch", lambda: None)
    request = {
        "operation_id": str(uuid.uuid4()),
        "kind": "audit",
        "application_agent_id": a["id"],
        "focus_id": f["id"],
        "options": {},
    }
    first = app.submit_job(saved["id"], request)
    root = app.state.workspace(saved["id"]).root
    (root / "other.py").write_text("other = 1\n")
    app.save_application_agent(saved["id"], {"code_scopes": ["other.py"]}, a["id"])
    replay = app.submit_job(saved["id"], request)
    assert replay["id"] == first["id"]
    assert replay["options"]["code_scopes"] == ["app.py"]
    with pytest.raises(AuditError, match="different"):
        app.submit_job(saved["id"], {**request, "goal": "Changed request"})
    assert len(app.jobs.list(saved["id"])) == 1
