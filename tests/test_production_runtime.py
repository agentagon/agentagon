"""Observable RSI journeys and conservative production outcome classification."""

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_webapp import running
from test_webapp_agents import fake_codex
from test_webapp_jobs import wait_for

from agentagon.brain.adapters import run_agent
from agentagon.capabilities.production import compare
from agentagon.core.records import AuditError
from agentagon.domain.monitoring import measurement
from agentagon.memory.lessons import validate
from agentagon.workflows.production_runtime import after
from agentagon.workflows.service import Application


def trace(trace_id="a" * 32, revision="a" * 40, duration=1000, start=None):
    start = start or datetime.now(UTC) - timedelta(minutes=10)
    ns = int(start.timestamp() * 1_000_000_000)
    return {
        "traceId": trace_id,
        "spanId": "1" * 16,
        "name": "weather",
        "startTimeUnixNano": str(ns),
        "endTimeUnixNano": str(ns + duration * 1_000_000),
        "status": {"code": 1},
        "attributes": [
            {"key": "environment", "value": {"stringValue": "production"}},
            {"key": "git.commit.sha", "value": {"stringValue": revision}},
        ],
        "metadata": {"agent_name": "Weather", "environment": "production"},
    }


class Provider:
    def __init__(self, connection, credentials):
        self.connection = connection

    def preview(self, kind, selection):
        return {
            "items": [trace()],
            "provenance": {"provider": "otlp", "project": "weather"},
            "completeness": {"complete": True, "count": 1},
        }


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "agent.py").write_text('from agents import Agent\nweather = Agent(name="Weather")\n')
    instance = Application(
        tmp_path / "state",
        execute=lambda *_: {
            "state": "completed",
            "text": json.dumps({"summary": "Assessment complete", "issues": []}),
        },
        provider_factory=Provider,
    )
    instance.scheduler.close()
    project = instance.register(str(root))["id"]
    with instance.state.locked() as state:
        state["connections"]["connection_" + "c" * 24] = {
            "id": "connection_" + "c" * 24,
            "project_id": project,
            "project": "weather",
            "provider": "langsmith",
            "credentials": {},
        }
    agent = instance.save_application_agent(
        project,
        {
            "name": "Weather",
            "code_scopes": ["agent.py"],
            "trace_selector": {
                "connection_id": "connection_" + "c" * 24,
                "project": "weather",
                "environment": "production",
            },
        },
    )
    monkeypatch.setattr(
        instance,
        "agents",
        lambda: {"agents": [{"id": "codex", "available": False, "authenticated": False}]},
    )
    yield instance, project, agent
    instance.close()


def monitor(app, **kwargs):
    instance, project, agent = app
    return instance.monitoring.save(
        project,
        {
            "agent_id": agent["id"],
            "environment": "production",
            "enabled": True,
            "diagnosis": False,
            "measurements": [{"metric": "latency_ms", "accepted": True, "minimum_samples": 2}],
            **kwargs,
        },
    )


def test_assessment_is_idempotent_async_and_retains_partial_progress(app):
    instance, project, _ = app
    root = instance.state.workspace(project).root
    (root / "research.py").write_text(
        'from agents import Agent\nresearch = Agent(name="Research")\n'
    )
    payload = {
        "operation_id": str(uuid.uuid4()),
        "workflow": "assess",
        "input": {"type": "project", "id": project},
    }
    task = instance.submit_task(project, payload)
    saved = wait_for(instance.runtime, project, task["task_id"])
    assert saved["state"] == "completed_with_limits", saved
    assert instance.production.onboarding(project)["state"] == "complete"
    assert saved["result"]["limitations"]
    assert [candidate["name"] for candidate in saved["result"]["candidates"]] == ["Research"]
    suggestion = instance.project_agents(project)["suggestions"][0]
    assert suggestion["name"] == "Research"
    assert suggestion["responsibility_inference"]["state"] == "pending"
    assert "retained" in saved["result"]["summary"].lower()
    assert instance.submit_task(project, payload)["task_id"] == task["task_id"]
    assert all(t["kind"] == "assess" for t in instance.runtime.list(project))
    assert any(r["basis"] == "not_measured" for r in instance.production.recommendations(project))


def test_assessment_blocks_an_unavailable_selected_trace_connection_but_allows_code_only(app):
    instance, project, _ = app
    connection_id = "connection_" + "c" * 24
    with instance.state.locked() as state:
        state["connections"][connection_id]["status"] = "unavailable"

    blocked = instance.prepare_workflow_start(
        project,
        {
            "workflow": "assess",
            "input": {"type": "project", "id": project},
            "options": {"assessment": {"connection_id": connection_id}},
        },
    )
    assert blocked["state"] == "needs_input"
    prerequisite = next(
        item for item in blocked["prerequisites"] if item["code"] == "trace_connection"
    )
    assert prerequisite["blocking"] is True
    assert prerequisite["resolution"]["context"]["status"] == "unavailable"

    code_only = instance.prepare_workflow_start(
        project,
        {
            "workflow": "assess",
            "input": {"type": "project", "id": project},
            "options": {"assessment": {}},
        },
    )
    assert code_only["state"] == "ready_with_limits"
    assert all(item["code"] != "trace_connection" for item in code_only["prerequisites"])


def test_recommendation_disposition_applies_only_to_the_reviewed_evidence(app):
    instance, project, agent = app
    recommendation = next(
        item
        for item in instance.production.recommendations(project)
        if item.get("agent_id") == agent["id"] and item.get("category") == "latency"
    )

    saved = instance.production.disposition_recommendation(
        project,
        recommendation["id"],
        {
            "value": "not_now",
            "reason": "Wait for representative traffic",
            "evidence_revision": recommendation["evidence_revision"],
            "expected_revision": 0,
        },
    )
    dismissed = next(
        item
        for item in instance.production.recommendations(project)
        if item["id"] == recommendation["id"]
    )
    assert dismissed["active"] is False
    assert dismissed["disposition"] == {
        "value": "not_now",
        "reason": "Wait for representative traffic",
        "decided_at": saved["decided_at"],
        "revision": 1,
    }

    instance.production.save_onboarding(
        project,
        {
            "state": "ready",
            "scope": {},
        },
    )
    setup = instance.production.onboarding(project)
    instance.state.db.put_record(
        project,
        "onboarding",
        "setup",
        {
            **setup,
            "agent_measurements": {
                agent["id"]: [
                    {
                        "metric": "latency_ms",
                        "value": 250,
                        "count": 12,
                        "coverage": 0.8,
                        "source": "assessment:new-evidence",
                    }
                ]
            },
        },
        expected_revision=setup["revision"],
    )
    refreshed = next(
        item
        for item in instance.production.recommendations(project)
        if item["id"] == recommendation["id"]
    )
    assert refreshed["evidence_revision"] != recommendation["evidence_revision"]
    assert refreshed["active"] is True
    assert refreshed["disposition"] is None


def test_repeated_discovery_does_not_revive_a_dismissed_unchanged_issue(app):
    from agentagon.domain.issues import record_issue

    instance, project, agent = app
    workspace = instance.state.workspace(project)
    diagnosis = {
        "key": "wrong-weather",
        "title": "Wrong weather",
        "summary": "The answer describes a different location.",
        "severity": "medium",
        "confidence": 0.8,
        "trace_ids": ["stable-trace"],
        "evidence": ["Requested and returned locations differ."],
    }
    first_evidence = workspace.artifact(
        {"snapshot_id": "snapshot_first", "task_id": "task_first", "diagnosis": diagnosis}
    )
    issue = record_issue(
        workspace,
        key=diagnosis["key"],
        title=diagnosis["title"],
        summary=diagnosis["summary"],
        severity=diagnosis["severity"],
        confidence=diagnosis["confidence"],
        agent_id=agent["id"],
        evidence=[first_evidence],
        occurrences=[
            {
                "id": "occurrence_stable",
                "source_id": "snapshot_first",
                "trace_ids": ["stable-trace"],
                "observed_ns": 1,
                "basis": "trace",
                "evidence": first_evidence,
            }
        ],
    )
    recommendation = next(
        item
        for item in instance.production.recommendations(project)
        if item["id"] == issue["issue_id"]
    )
    instance.production.disposition_recommendation(
        project,
        issue["issue_id"],
        {
            "value": "not_now",
            "reason": "Wait for ownership review",
            "evidence_revision": recommendation["evidence_revision"],
            "expected_revision": 0,
        },
    )

    repeated_evidence = workspace.artifact(
        {"snapshot_id": "snapshot_second", "task_id": "task_second", "diagnosis": diagnosis}
    )
    record_issue(
        workspace,
        key=diagnosis["key"],
        title=diagnosis["title"],
        summary=diagnosis["summary"],
        severity=diagnosis["severity"],
        confidence=diagnosis["confidence"],
        agent_id=agent["id"],
        evidence=[repeated_evidence],
        occurrences=[
            {
                "id": "occurrence_stable",
                "source_id": "snapshot_second",
                "trace_ids": ["stable-trace"],
                "observed_ns": 1,
                "basis": "trace",
                "evidence": repeated_evidence,
            }
        ],
    )
    unchanged = next(
        item
        for item in instance.production.recommendations(project)
        if item["id"] == issue["issue_id"]
    )
    assert unchanged["evidence_revision"] == recommendation["evidence_revision"]
    assert unchanged["active"] is False

    record_issue(
        workspace,
        key=diagnosis["key"],
        title=diagnosis["title"],
        summary=diagnosis["summary"],
        severity=diagnosis["severity"],
        confidence=diagnosis["confidence"],
        agent_id=agent["id"],
        occurrences=[
            {
                "id": "occurrence_new",
                "source_id": "snapshot_third",
                "trace_ids": ["new-trace"],
                "observed_ns": 2,
                "basis": "trace",
            }
        ],
    )
    changed = next(
        item
        for item in instance.production.recommendations(project)
        if item["id"] == issue["issue_id"]
    )
    assert changed["evidence_revision"] != recommendation["evidence_revision"]
    assert changed["active"] is True


def test_monitor_observes_without_goal_brain_or_repair(app):
    instance, project, _ = app
    policy = monitor(app)
    instance.scheduler.tick()
    policy = instance.monitoring.get(project, policy["id"])
    task = wait_for(instance.runtime, project, policy["task_id"])
    assert task["state"] == "completed", task
    observed = instance.monitoring.overview(project)["observations"]
    assert len(observed) == 1
    assert observed[0]["metrics"][0]["current"] == 1000
    assert observed[0]["metrics"][0]["status"] == "insufficient_evidence"
    assert instance.monitoring.get(project, policy["id"])["checkpoint"]
    instance.scheduler.tick()
    assert len(instance.runtime.list(project)) == 1
    groups = instance.memory.list(project, purpose="improvement")
    deadline = time.monotonic() + 5
    while (
        not instance.memory.recall(project, groups[0]["id"], "")["entries"]
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert instance.memory.recall(project, groups[0]["id"], "")["entries"]


def test_pending_restart_blocks_execution_until_explicit_control(app):
    instance, project, _ = app
    policy = monitor(app)
    instance.runtime.stopping = True
    instance.scheduler.tick()
    policy = instance.monitoring.get(project, policy["id"])
    task_id = policy["task_id"]
    job = instance.runtime._read(project, task_id)
    job["state"] = "interrupted"
    instance.runtime._write(job)
    instance.scheduler.tick(after(7200))
    assert instance.monitoring.get(project, policy["id"])["state"] == "needs_attention"
    assert len(instance.runtime.list(project)) == 1
    instance.monitoring.control(project, policy["id"], "discard")
    assert instance.runtime.get(project, task_id)["state"] == "cancelled"


def test_storage_budget_and_binding_changes_fail_without_checkpoint(app):
    instance, project, agent = app
    policy = monitor(app, storage_budget_bytes=20_000_000)
    instance.scheduler.tick()
    policy = instance.monitoring.get(project, policy["id"])
    task = wait_for(instance.runtime, project, policy["task_id"])
    assert task["state"] == "failed"
    assert "storage budget" in task["next_action"]
    assert not instance.monitoring.get(project, policy["id"]).get("checkpoint")


def sample(i, at, value, revision):
    return {
        "metadata": {},
        "trace_id": str(i),
        "started_ns": int(at.timestamp() * 1e9),
        "revision": revision,
        "latency_ms": value,
    }


def test_comparisons_require_release_coverage_acceptance_and_samples():
    end = datetime.now(UTC)
    deployed = end - timedelta(hours=12)
    definition = measurement(
        {"metric": "latency_ms", "accepted": True, "minimum_samples": 10, "material_change": 5}
    )
    policy = {
        "id": "monitor",
        "agent_id": "agent",
        "selector": {"environment": "production"},
        "measurements": [definition],
        "coverage_complete": True,
    }
    deployment = {
        "id": "deployment",
        "agent_id": "agent",
        "environment": "production",
        "deployed_revision": "new",
        "exact_tested_revision": True,
        "deployed_at": deployed.isoformat(),
    }
    rows = [sample(i, deployed - timedelta(hours=1), 100 + i % 3, "old") for i in range(100)]
    rows += [sample(i, end - timedelta(hours=1), 50 + i % 3, "new") for i in range(100, 200)]
    result = compare(rows, policy, [deployment], end.isoformat())[0]
    assert result["status"] == "improved" and result["causal"] is False
    unrelated = {**deployment, "exact_tested_revision": False}
    assert compare(rows, policy, [unrelated], end.isoformat())[0]["status"] == "not_comparable"
    interrupted = {
        **policy,
        "acquisition_windows": [
            {
                "start": (end - timedelta(hours=1)).isoformat(),
                "end": end.isoformat(),
                "complete": True,
            }
        ],
    }
    assert (
        compare(rows, interrupted, [deployment], end.isoformat())[0]["status"]
        == "insufficient_evidence"
    )
    for field, value in [("coverage_complete", False)]:
        assert (
            compare(rows, {**policy, field: value}, [deployment], end.isoformat())[0]["status"]
            == "insufficient_evidence"
        )
    rows[-1]["revision"] = "other"
    assert compare(rows, policy, [deployment], end.isoformat())[0]["status"] == "not_comparable"
    rows[-1]["revision"] = "new"
    definition["accepted"] = False
    assert (
        compare(rows, policy, [deployment], end.isoformat())[0]["status"] == "insufficient_evidence"
    )


def test_measurement_change_starts_new_series_and_access_is_scoped(app, tmp_path):
    instance, project, agent = app
    policy = monitor(app)
    updated = instance.monitoring.save(
        project,
        {
            "agent_id": agent["id"],
            "environment": "production",
            "enabled": True,
            "expected_revision": policy["revision"],
            "measurements": [{"metric": "failure_rate"}],
        },
        policy["id"],
    )
    assert updated["series"] == policy["series"] + 1
    other = tmp_path / "other"
    other.mkdir()
    other_project = instance.register(str(other))["id"]
    with pytest.raises(AuditError, match="not found"):
        instance.monitoring.get(other_project, policy["id"])


def test_operational_monitor_change_preserves_comparison_series_and_evidence(app):
    instance, project, agent = app
    policy = monitor(app)
    retained = {
        **policy,
        "checkpoint": {"ended_at": "2026-01-01T00:00:00+00:00"},
        "last_observation_id": "observation_existing",
        "references": {"snapshot_existing": {"bytes": 100}},
    }
    retained = instance.state.db.put_record(project, "monitors", policy["id"], retained)

    updated = instance.monitoring.save(
        project,
        {
            "agent_id": agent["id"],
            "environment": "production",
            "enabled": True,
            "expected_revision": retained["revision"],
            "storage_budget_bytes": 2 * 1024**3,
            "diagnosis": False,
            "measurements": [{"metric": "latency_ms", "accepted": True, "minimum_samples": 2}],
        },
        policy["id"],
    )

    assert updated["series"] == policy["series"]
    assert updated["operational_revision"] == policy["operational_revision"] + 1
    assert updated["checkpoint"] == retained["checkpoint"]
    assert updated["last_observation_id"] == "observation_existing"
    assert updated["references"] == retained["references"]


def test_pause_and_enable_advance_the_operational_revision(app):
    instance, project, _agent = app
    policy = monitor(app)

    paused = instance.monitoring.control(project, policy["id"], "pause")
    assert paused["enabled"] is False
    assert paused["series"] == policy["series"]
    assert paused["operational_revision"] == policy["operational_revision"] + 1
    assert paused["operations_digest"] != policy["operations_digest"]

    enabled = instance.monitoring.control(project, policy["id"], "enable")
    assert enabled["enabled"] is True
    assert enabled["series"] == policy["series"]
    assert enabled["operational_revision"] == paused["operational_revision"] + 1
    assert enabled["operations_digest"] == policy["operations_digest"]


def test_memory_references_are_validated_against_frozen_versions():
    job = {"improvement_memory": [{"entries": [{"id": "lesson", "version": 1}]}]}
    decision = {
        "id": "lesson",
        "version": 1,
        "decision": "used",
        "reason": "Avoid the failed retry approach",
    }
    assert validate(job, {"lessons": [decision]}) == [decision]
    with pytest.raises(AuditError, match="frozen"):
        validate(job, {"lessons": [{**decision, "version": 2}]})
    with pytest.raises(AuditError, match="frozen"):
        validate(job, {"lessons": [{**decision, "id": []}]})


def test_http_exposes_the_same_monitor_and_observation(app):
    instance, project, agent = app
    with running(instance) as (client, _):
        response = client.post(
            f"/api/projects/{project}/monitors",
            json={
                "agent_id": agent["id"],
                "environment": "production",
                "enabled": False,
                "diagnosis": False,
            },
        )
        assert response.status_code == 200, response.text
        monitor_id = response.json()["id"]
        assert (
            client.get(f"/api/projects/{project}/production").json()["monitors"][0]["id"]
            == monitor_id
        )


def test_real_browser_onboarding_and_monitor_controls(app):
    playwright = pytest.importorskip("playwright.sync_api")
    instance, project, agent = app
    with running(instance) as (_client, server), playwright.sync_playwright() as value:
        browser = value.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        origin = f"http://127.0.0.1:{server.server_port}"
        page.goto(f"{origin}/projects/{project}/onboarding")
        page.get_by_role("heading", name="Start with repo", exact=True).wait_for()
        page.get_by_role("button", name="Analyze project", exact=True).click()
        page.wait_for_url("**/tasks/task_*")
        page.get_by_text(
            "Configure and authenticate the coding backend", exact=False
        ).first.wait_for()
        page.goto(f"{origin}/projects/{project}/agents/{agent['id']}/production")
        page.get_by_role("button", name="Enable monitoring", exact=True).click()
        page.get_by_label("Diagnose new evidence at most daily", exact=False).uncheck()
        page.get_by_role("button", name="Enable monitoring", exact=True).last.click()
        page.get_by_role("button", name="Analyze now", exact=True).wait_for()
        page.get_by_role("button", name="Analyze now", exact=True).click()
        page.wait_for_url("**/tasks/task_*")
        page.get_by_text("Production observations retained;", exact=False).first.wait_for()
        page.goto(f"{origin}/projects/{project}/agents/{agent['id']}/production")
        page.get_by_role("heading", name="Production evidence", exact=True).wait_for()
        page.get_by_text("insufficient evidence", exact=False).first.wait_for()
        page.get_by_role("button", name="Pause", exact=True).click()
        page.get_by_role("button", name="Enable", exact=True).wait_for()
        assert not errors, errors
        browser.close()


def test_failed_assessment_resumes_after_restart_with_real_adapter(app, tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    instance, project, _ = app
    executable, log = fake_codex(tmp_path, "disconnect")
    (tmp_path / "codex").symlink_to(executable)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(
        instance,
        "agents",
        lambda: {"agents": [{"id": "codex", "available": True, "authenticated": True}]},
    )
    instance.runtime.execute = run_agent
    task = instance.submit_task(
        project,
        {
            "operation_id": str(uuid.uuid4()),
            "workflow": "assess",
            "assistant": "codex",
            "input": {"type": "project"},
        },
    )
    failed = wait_for(instance.runtime, project, task["task_id"])
    assert failed["state"] == "failed", failed
    assert "Codex disconnected" in failed["next_action"]
    assert failed["session_id"] == "session-1"
    environment = json.loads(log.with_suffix(".jsonl.env").read_text())
    snapshot = Path(environment["AGENTAGON_MEMORY_SNAPSHOT"])
    assert snapshot.is_absolute() and snapshot.is_file()
    assert snapshot == instance.state.workspace(project).root / failed["memory_snapshot"]["path"]
    instance.close()
    fake_codex(tmp_path, "assess")
    restarted = Application(instance.state.directory, provider_factory=Provider)
    restarted.scheduler.close()
    try:
        with running(restarted) as (_client, server), playwright.sync_playwright() as value:
            browser = value.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_default_timeout(10000)
            page.goto(
                f"http://127.0.0.1:{server.server_port}/projects/{project}/tasks/{task['task_id']}"
            )
            page.get_by_role("button", name="Resume", exact=True).click()
            done = wait_for(restarted.runtime, project, task["task_id"])
            assert done["state"] == "completed", done
            assert done["memory_snapshot"] == failed["memory_snapshot"]
            assert done["session_id"] == failed["session_id"]
            assert len(restarted.runtime.list(project)) == 1
            page.get_by_text("Assessment complete", exact=False).first.wait_for()
            assert page.get_by_text("I'll inspect the repository.", exact=True).count() == 0
            assert page.get_by_text("commandExecution", exact=False).count() == 0
            browser.close()
    finally:
        restarted.close()


def test_observation_memory_failure_retries_without_losing_evidence(app, monkeypatch):
    instance, project, _ = app
    policy = monitor(app)
    original = instance.memory.record
    monkeypatch.setattr(
        instance.memory,
        "record",
        lambda *args: (_ for _ in ()).throw(AuditError("Folder unavailable")),
    )
    instance.scheduler.tick()
    task_id = instance.monitoring.get(project, policy["id"])["task_id"]
    task = wait_for(instance.runtime, project, task_id)
    assert task["state"] == "completed" and task["memory_note"]
    assert instance.monitoring.overview(project)["observations"]
    projected = instance.task(project, task_id)
    assert projected["memory_recording"]["automatic_retry"] is True
    assert projected["needs_attention"] is False
    instance.scheduler.retry_memory()
    instance.scheduler.retry_memory()
    projected = instance.task(project, task_id)
    assert projected["memory_recording"]["automatic_retry"] is False
    assert projected["memory_recording"]["attempts"] == 3
    assert projected["needs_attention"] is True
    monkeypatch.setattr(instance.memory, "record", original)
    instance.runtime.control(
        project,
        task_id,
        "retry-memory",
        {"operation_id": str(uuid.uuid4())},
    )
    assert not instance.runtime.get(project, task_id).get("memory_note")
    groups = instance.memory.list(project, purpose="improvement")
    entries = instance.memory.recall(project, groups[0]["id"], "")["entries"]
    assert len(entries) == 1
    instance.scheduler.retry_memory()
    assert instance.memory.recall(project, groups[0]["id"], "")["entries"] == entries


def test_changed_trace_evidence_does_not_duplicate_an_occurrence(app):
    from test_workflow_runtime import finding

    from agentagon.domain.issues import list_issues
    from agentagon.workflows.discover.handler import accept

    instance, project, agent = app
    for duration in (1000, 2000):
        snapshot = instance.import_trace(
            project,
            {
                "operation_id": str(uuid.uuid4()),
                "provider": "otlp",
                "data": [trace(duration=duration)],
            },
        )
        accept(
            instance.state.workspace(project),
            {
                "id": "task_" + "f" * 24,
                "application_agent_id": agent["id"],
                "options": {"trace_snapshot_id": snapshot["id"]},
            },
            {"issues": [finding()]},
        )
    issue = list_issues(instance.state.workspace(project))[0]
    assert len(issue["occurrences"]) == 1
    assert len(issue["occurrences"][0]["source_ids"]) == 2
    assert len(issue["occurrences"][0]["evidence_versions"]) == 2


def test_official_stdio_mcp_uses_shared_production_service(app):
    import asyncio
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from agentagon.storage.state import atomic_write

    instance, project, _ = app
    policy = monitor(app)
    with running(instance) as (client, server):
        atomic_write(
            instance.state.directory / "instance.json",
            {
                "port": server.server_port,
                "token": server.session_token,
                "version": 1,
                "pid": os.getpid(),
            },
        )

        async def exercise():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "agentagon", "mcp"],
                env={**os.environ, "AGENTAGON_APP_STATE": str(instance.state.directory)},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    data = await session.call_tool("inspect_production", {"project_id": project})
                    assert json.loads(data.content[0].text)["monitors"][0]["id"] == policy["id"]
                    command = {
                        "project_id": project,
                        "workflow": "observe",
                        "input": {"type": "monitor", "id": policy["id"]},
                        "operation_id": str(uuid.uuid4()),
                    }
                    first = await session.call_tool("start_workflow", command)
                    assert not first.isError, first
                    second = await session.call_tool("start_workflow", command)
                    assert not second.isError, second
                    one, two = (json.loads(r.content[0].text) for r in (first, second))
                    assert one["task_id"] == two["task_id"]
                    return one["task_id"]

        task_id = asyncio.run(exercise())
        task = wait_for(instance.runtime, project, task_id)
        assert task["state"] == "completed", task
        assert (
            client.get(f"/api/projects/{project}/production").json()["observations"][0]["task_id"]
            == task_id
        )


def test_service_restart_and_concurrent_ticks_keep_one_pending_task(app):
    from concurrent.futures import ThreadPoolExecutor

    instance, project, _ = app
    policy = monitor(app)
    instance.runtime.stopping = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: instance.scheduler.tick(), range(2)))
    before = instance.monitoring.get(project, policy["id"])
    assert len(instance.runtime.list(project)) == 1
    directory = instance.state.directory
    instance.close()
    reopened = Application(
        directory,
        execute=lambda *_: pytest.fail("Interrupted task restarted automatically"),
        provider_factory=Provider,
    )
    try:
        reopened.scheduler.close()
        reopened.scheduler.tick(after(7200))
        retained = reopened.monitoring.get(project, policy["id"])
        assert retained["task_id"] == before["task_id"]
        assert retained["state"] == "needs_attention"
        assert reopened.runtime.get(project, retained["task_id"])["state"] == "interrupted"
        assert len(reopened.runtime.list(project)) == 1
    finally:
        reopened.close()


def test_brain_recall_versions_are_visible_and_old_lessons_remain_pinned(app, monkeypatch):
    from test_webapp_jobs import manifest

    instance, project, agent = app
    group = instance.memory.improvement_groups(project, agent["id"])[0]
    entry = instance.memory.record(
        project,
        group["id"],
        {
            "key": "assessment",
            "text": "Assess application agents with bounded evidence",
            "evidence": ["saved-attempt"],
        },
        agent["id"],
    )
    monkeypatch.setattr(
        instance,
        "agents",
        lambda: {"agents": [{"id": "codex", "available": True, "authenticated": True}]},
    )

    def brain(request, *_):
        context = manifest(request)
        recalled = [e for g in context["improvement_memory"] for e in g["entries"]]
        assert entry in recalled
        instance.memory.record(
            project,
            group["id"],
            {
                "key": "assessment",
                "text": "Changed while the task runs",
                "evidence": ["new-attempt"],
            },
            agent["id"],
        )
        return {
            "state": "completed",
            "text": json.dumps(
                {
                    "summary": "Applied a retained lesson",
                    "issues": [],
                    "lessons": [
                        {
                            "id": entry["id"],
                            "version": entry["version"],
                            "decision": "used",
                            "reason": "Bound the evidence sample",
                        }
                    ],
                }
            ),
        }

    instance.runtime.execute = brain
    task = instance.submit_task(
        project,
        {
            "workflow": "assess",
            "agent_id": agent["id"],
            "input": {"type": "project"},
            "operation_id": str(uuid.uuid4()),
        },
    )
    done = wait_for(instance.runtime, project, task["id"])
    assert done["state"] == "completed", done
    assert done["result"]["lessons"][0]["version"] == 1
    assert (
        instance.memory.recall(project, group["id"], "Changed", agent["id"])["entries"][0][
            "version"
        ]
        == 2
    )


def test_recurrence_requires_explicit_review_and_invalidates_changed_evidence(app, monkeypatch):
    from agentagon.domain.issues import record_issue

    instance, project, agent = app
    issue = record_issue(
        instance.state.workspace(project),
        key="weather-failure",
        title="Wrong weather",
        summary="Weather must match the requested location",
        agent_id=agent["id"],
        evidence=[],
        occurrences=[],
    )
    policy = monitor(
        app,
        diagnosis=True,
        measurements=[
            {
                "metric": "issue_recurrence",
                "issue_id": issue["issue_id"],
                "accepted": True,
                "minimum_samples": 2,
            }
        ],
    )
    monkeypatch.setattr(
        instance,
        "agents",
        lambda: {"agents": [{"id": "codex", "available": True, "authenticated": True}]},
    )
    response = {"summary": "No supported findings", "issues": []}
    instance.runtime.execute = lambda *_: {"state": "completed", "text": json.dumps(response)}

    def observe():
        task = instance.submit_task(
            project,
            {
                "workflow": "observe",
                "input": {"type": "monitor", "id": policy["id"]},
                "operation_id": str(uuid.uuid4()),
            },
        )
        done = wait_for(instance.runtime, project, task["task_id"])
        assert done["state"] == "completed", done
        return done["result"]["metrics"][0]

    assert observe()["count"] == 0
    response["issue_checks"] = [
        {
            "issue_id": issue["issue_id"],
            "trace_id": "a" * 32,
            "present": False,
            "reason": "The recorded requested and returned locations match.",
        }
    ]
    assert observe()["count"] == 1
    response.pop("issue_checks")
    # This provider returns a later trace revision; the earlier review cannot score it.
    third = observe()
    assert third["count"] == 0 and third["status"] == "insufficient_evidence"


def test_assessment_routes_issues_using_unique_trace_identity_without_confirming_ownership(
    app, monkeypatch
):
    instance, project, agent = app
    instance.catalog.save_agent(project, {"status": "archived"}, agent["id"])
    monkeypatch.setattr(
        instance,
        "agents",
        lambda: {"agents": [{"id": "codex", "available": True, "authenticated": True}]},
    )

    def execute(request, *_args):
        from test_webapp_jobs import manifest

        candidates = manifest(request)["preparation"]["candidates"]
        return {
            "state": "completed",
            "text": json.dumps(
                {
                    "summary": "Observed failure",
                    "candidates": [
                        {
                            "id": candidate["id"],
                            "file": candidate["file"],
                            "name": candidate["name"],
                            "keep": True,
                            "responsibility": "Answers weather questions for a requested location.",
                        }
                        for candidate in candidates
                    ],
                    "issues": [
                        {
                            "key": "wrong-weather",
                            "title": "Wrong weather",
                            "summary": "The response describes a different location.",
                            "severity": "medium",
                            "confidence": 0.8,
                            "trace_ids": ["a" * 32],
                            "evidence": ["Requested and returned locations differ."],
                        }
                    ],
                }
            ),
        }

    instance.runtime.execute = execute
    task = instance.submit_task(
        project,
        {
            "workflow": "assess",
            "input": {"type": "project"},
            "operation_id": str(uuid.uuid4()),
            "options": {"assessment": {"connection_id": "connection_" + "c" * 24}},
        },
    )
    result = wait_for(instance.runtime, project, task["task_id"])
    assert result["state"] == "completed", result
    issue = instance.state.db.list_records(project, "issues")[0]
    owner = instance.catalog.agent(project, issue["agent_id"])
    assert owner["status"] == "suggested" and not owner["code_scopes"]
    assert instance.production.recommendations(project)[0]["agent_id"] == owner["id"]
    assert all(task["kind"] == "assess" for task in instance.runtime.list(project))


def test_analyze_old_project_state_explains_recovery_and_preserves_evidence(app, tmp_path):
    instance, project, _ = app
    workspace = instance.state.workspace(project)
    workspace.state.mkdir()
    metadata = workspace.state / "workspace.json"
    original = '{"contract_version":"1"}'
    metadata.write_text(original)
    (workspace.state / "old-evidence.txt").write_text("Retained original evidence")
    command = {
        "operation_id": str(uuid.uuid4()),
        "workflow": "assess",
        "input": {"type": "project"},
    }
    with running(instance) as (client, _):
        response = client.post(f"/api/projects/{project}/tasks", json=command)
        assert response.status_code == 400, response.text
        message = response.json()["error"]
        assert "state_version" in message and "missing" in message
        assert "backup outside this project" in message
        assert "AGENTAGON_APP_STATE only isolates application settings" in message
        assert metadata.read_text() == original
        assert instance.runtime.list(project) == []
        # Simulate the user's explicit preservation step; the application never moves old state.
        backup = tmp_path / "preserved-agentagon-state"
        workspace.state.rename(backup)
        response = client.post(f"/api/projects/{project}/tasks", json=command)
        assert response.status_code == 200, response.text
        done = wait_for(instance.runtime, project, response.json()["task_id"])
        assert done["state"] == "completed_with_limits", done
        assert json.loads(metadata.read_text())["state_version"] == 2
        assert (backup / "workspace.json").read_text() == original
        assert (backup / "old-evidence.txt").read_text() == "Retained original evidence"
