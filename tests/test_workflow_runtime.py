"""Public journeys exercise real evidence, shared service, and stdio MCP boundaries."""

import asyncio
import copy
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from support.experiments import git, passing_review
from test_webapp import running
from test_webapp_jobs import manifest, wait_for

from agentagon.capabilities.experiments import engine
from agentagon.core.records import AuditError, load_json
from agentagon.domain.issues import get_issue, list_issues
from agentagon.memory.execution import freeze_spec, recalled_snapshot, verify_snapshot
from agentagon.memory.store import FolderStore
from agentagon.storage.state import atomic_write
from agentagon.workflows.discover.handler import accept
from agentagon.workflows.fix.handler import configure
from agentagon.workflows.service import Application


def trace_data():
    return [
        {
            "traceId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "spanId": "1111111111111111",
            "name": "weather",
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "2000000000",
            "status": {"code": 2, "message": "timeout"},
        }
    ]


def finding(key="weather-timeout"):
    return {
        "key": key,
        "title": "Weather times out",
        "summary": "The weather request fails without recovery",
        "expected_behavior": "Return weather data or explain inability",
        "severity": "high",
        "confidence": 0.8,
        "trace_ids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
        "evidence": ["The weather span reports timeout"],
    }


@pytest.fixture
def system(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "agent.py").write_text("def answer(): return 42\n")
    app = Application(tmp_path / "service", execute=lambda *_: {})
    project = app.register(str(root))["id"]
    agent = app.save_application_agent(project, {"name": "Weather", "code_scopes": ["agent.py"]})
    yield app, project, agent
    app.close()


def imported(app, project, operation=None):
    return app.import_trace(
        project,
        {"operation_id": operation or str(uuid.uuid4()), "provider": "otlp", "data": trace_data()},
    )


def test_trace_discovery_groups_occurrences_without_repairs_or_audit(system):
    app, project, agent = system
    workspace = app.state.workspace(project)
    first = imported(app, project)
    job = {
        "id": "task_" + "a" * 24,
        "application_agent_id": agent["id"],
        "options": {"trace_snapshot_id": first["id"]},
    }
    state, result, _ = accept(workspace, job, {"issues": [finding(), finding("missing-fallback")]})
    assert state == "completed" and len(result["issue_ids"]) == 2
    assert (
        not result["repairs_started"] and not app.runtime.list(project) and not workspace.audits()
    )
    second = imported(app, project)
    job["options"]["trace_snapshot_id"] = second["id"]
    accept(workspace, job, {"issues": [finding(), finding("missing-fallback")]})
    from agentagon.domain.issues import issue_detail

    issues = list_issues(workspace)
    detail = issue_detail(workspace, issues[0]["issue_id"])
    assert detail["diagnoses"][0]["evidence"] == finding()["evidence"]
    assert len(issues) == 2 and all(len(i["occurrences"]) == 1 for i in issues)
    with pytest.raises(AuditError, match="cite traces"):
        accept(workspace, job, {"issues": [{**finding(), "trace_ids": ["absent"]}]})


def test_trace_occurrence_identity_survives_provider_reconnection(system):
    from agentagon.capabilities.traces import snapshots

    app, project, agent = system
    workspace = app.state.workspace(project)
    workspace.initialize()
    for suffix in ("a", "b"):
        snapshot = snapshots.save(
            workspace,
            project,
            {
                "kind": "traces",
                "connection_id": "connection_" + suffix * 24,
                "selection": {"project": "provider-project"},
                "items": trace_data(),
                "provenance": {"provider": "otlp", "project": "provider-project"},
                "completeness": {"complete": True, "count": 1},
            },
        )
        accept(
            workspace,
            {
                "id": "task_" + suffix * 24,
                "application_agent_id": agent["id"],
                "options": {"trace_snapshot_id": snapshot["id"]},
            },
            {"issues": [finding()]},
        )

    issue = list_issues(workspace)[0]
    assert len(issue["occurrences"]) == 1
    assert len(issue["occurrences"][0]["source_ids"]) == 2


def test_import_idempotency_and_limits(system):
    app, project, _ = system
    operation = str(uuid.uuid4())
    one = imported(app, project, operation)
    assert imported(app, project, operation) == one
    with pytest.raises(AuditError, match="another import"):
        app.import_trace(project, {"operation_id": operation, "provider": "otlp", "data": []})
    with pytest.raises(AuditError, match="no supported"):
        app.import_trace(
            project,
            {"operation_id": str(uuid.uuid4()), "provider": "otlp", "data": [{"invalid": True}]},
        )


def test_memory_access_versions_and_pinned_execution(system, tmp_path, monkeypatch):
    app, project, agent = system
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = app.register(str(other_root))["id"]
    group = app.memory.create(
        project,
        {
            "name": "Target behavior",
            "purpose": "agent",
            "path": str(tmp_path / "target-memory"),
            "agent_ids": [agent["id"]],
        },
    )
    lesson = {
        "key": "weather",
        "text": "A timeout needs a fallback",
        "evidence": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
        "uncertainty": "One observation",
    }
    first = app.memory.record(project, group["id"], lesson, agent["id"])
    assert app.memory.record(project, group["id"], lesson, agent["id"]) == first
    for project_id, agent_id in [
        (other, agent["id"]),
        (project, None),
        (project, "agent_" + "b" * 24),
    ]:
        with pytest.raises(AuditError):
            app.memory.recall(project_id, group["id"], "weather", agent_id)
    pinned = app.memory.snapshot(project, agent["id"])
    workspace = app.state.workspace(project)
    app.memory.record(project, group["id"], {**lesson, "text": "Changed memory"}, agent["id"])
    recalled = recalled_snapshot(
        workspace.root / pinned["path"], project, group["id"], "", agent["id"]
    )
    assert recalled["entries"][0]["text"] == lesson["text"]
    monkeypatch.setenv("AGENTAGON_MEMORY_SNAPSHOT", str(workspace.root / pinned["path"]))
    spec = freeze_spec(workspace, {})
    assert spec["inputs"][0]["path"] == "agentagon-private/memory.json"
    with pytest.raises(AuditError, match="omitted"):
        verify_snapshot(workspace, {"memory_snapshot": pinned}, {"frozen": []})
    path = next(Path(group["path"]).glob("entry-*.json"))
    record = load_json(path)
    record["text"] = "tampered"
    path.write_text(json.dumps(record))
    with pytest.raises(AuditError, match="integrity"):
        FolderStore(group).entries()


def test_lesson_projection_preserves_versions_and_citations(system, tmp_path):
    app, project, agent = system
    group = app.memory.create(
        project,
        {
            "name": "Improvement history",
            "purpose": "improvement",
            "path": str(tmp_path / "improvement-history"),
            "agent_ids": [agent["id"]],
        },
    )
    source_id = "task_" + "a" * 24
    first = app.memory.record(
        project,
        group["id"],
        {
            "key": source_id,
            "text": "Reproduce the timeout before changing fallback behavior.",
            "evidence": ["artifact-one"],
            "uncertainty": "Observed on one retained case.",
        },
        agent["id"],
    )
    second = app.memory.record(
        project,
        group["id"],
        {
            "key": source_id,
            "text": "Reproduce timeouts across retained cases before changing fallback behavior.",
            "evidence": ["artifact-one", "artifact-two"],
            "uncertainty": "Production recovery is still unknown.",
        },
        agent["id"],
    )

    def save_task(task_id, result, improvement_memory=None):
        app.state.db.put_record(
            project,
            "tasks",
            task_id,
            {
                "id": task_id,
                "project_id": project,
                "application_agent_id": agent["id"],
                "kind": "assess",
                "goal": "Assess timeout handling",
                "state": "completed",
                "result": result,
                "improvement_memory": improvement_memory or [],
                "created_at": "2026-09-20T00:00:00Z",
            },
        )

    save_task(
        source_id,
        {
            "summary": "The timeout needs a bounded fallback.",
            "learning": {
                "hypothesis": "Fallback coverage is incomplete.",
                "action": "Reproduce before editing.",
                "result": "The failure reproduced.",
            },
        },
    )
    save_task(
        "task_" + "b" * 24,
        {
            "lessons": [
                {
                    "id": first["id"],
                    "version": 1,
                    "decision": "used",
                    "reason": "It shaped the reproduction.",
                }
            ]
        },
        [{"group_id": group["id"], "entries": [first]}],
    )
    save_task(
        "task_" + "c" * 24,
        {
            "lessons": [
                {
                    "id": second["id"],
                    "version": 2,
                    "decision": "rejected",
                    "reason": "This case had a different cause.",
                }
            ]
        },
        [{"group_id": group["id"], "entries": [second]}],
    )

    listing = app.lessons(project, agent["id"])
    assert listing["lessons"][0]["version"] == 2
    assert listing["lessons"][0]["rejected_count"] == 1
    assert "path" not in listing["lessons"][0]["group"]
    detail = app.lesson(project, group["id"], first["id"], agent["id"])
    assert [version["version"] for version in detail["versions"]] == [2, 1]
    assert detail["versions"][1]["considerations"][0]["decision"] == "used"
    assert detail["latest"]["source"]["learning"]["hypothesis"] == (
        "Fallback coverage is incomplete."
    )

    assert detail["latest"]["uncertainty"] == second["uncertainty"]

    # Project-wide reads expose registry metadata elsewhere, but lesson content
    # still requires the explicit agent binding.
    assert app.lessons(project)["lessons"] == []
    agentless_groups = app.memory.improvement_groups(project)
    assert agentless_groups
    assert all(not item["agent_ids"] for item in agentless_groups)


def test_read_only_shared_improvement_memory_does_not_block_local_outcome_store(system, tmp_path):
    app, project, _agent = system
    other_root = tmp_path / "other-project"
    other_root.mkdir()
    other = app.register(str(other_root))["id"]
    shared = app.memory.create(
        project,
        {
            "name": "Shared read-only lessons",
            "purpose": "improvement",
            "path": str(tmp_path / "shared-improvement-memory"),
            "project_ids": [project, other],
            "write_project_ids": [project],
        },
    )

    groups = app.memory.improvement_groups(other)

    assert shared["id"] in {item["id"] for item in groups}
    writable = [item for item in groups if other in item["write_project_ids"]]
    assert len(writable) == 1
    assert writable[0]["owner_project_id"] == other


def test_lesson_corrections_append_versions_and_remove_outdated_from_recall(system, tmp_path):
    app, project, agent = system
    group = app.memory.create(
        project,
        {
            "name": "Correctable lessons",
            "purpose": "improvement",
            "path": str(tmp_path / "correctable-lessons"),
            "agent_ids": [agent["id"]],
        },
    )
    original = app.memory.record(
        project,
        group["id"],
        {
            "key": "task_" + "d" * 24,
            "text": "Retry every failed call.",
            "evidence": ["task_" + "e" * 24, "trace-timeout"],
            "uncertainty": "Only one provider was observed.",
        },
        agent["id"],
    )

    noted = app.correct_lesson(
        project,
        group["id"],
        original["id"],
        {
            "action": "add_note",
            "agent_id": agent["id"],
            "expected_version": 1,
            "note": "Do not retry non-idempotent tools.",
        },
    )["latest"]
    assert noted["version"] == 2
    assert noted["statement"] == original["text"]
    assert noted["evidence"] == original["evidence"]
    assert noted["revision_kind"] == "note"
    assert noted["revision_note"] == "Do not retry non-idempotent tools."
    repeated_note = app.correct_lesson(
        project,
        group["id"],
        original["id"],
        {
            "action": "add_note",
            "agent_id": agent["id"],
            "expected_version": 2,
            "note": "Do not retry non-idempotent tools.",
        },
    )["latest"]
    assert repeated_note["version"] == 3
    with pytest.raises(AuditError, match="changed; reload"):
        app.correct_lesson(
            project,
            group["id"],
            original["id"],
            {
                "action": "mark_outdated",
                "agent_id": agent["id"],
                "expected_version": 1,
                "reason": "A newer retry policy exists.",
            },
        )

    outdated = app.correct_lesson(
        project,
        group["id"],
        original["id"],
        {
            "action": "mark_outdated",
            "agent_id": agent["id"],
            "expected_version": 3,
            "reason": "Unbounded retries can duplicate side effects.",
        },
    )["latest"]
    assert outdated["version"] == 4 and outdated["status"] == "outdated"
    assert outdated["evidence"] == original["evidence"]
    assert app.memory.recall(project, group["id"], "retry", agent["id"])["entries"] == []
    listing = app.lessons(project, agent["id"])["lessons"]
    assert listing[0]["status"] == "outdated"
    with pytest.raises(AuditError, match="authorized target agent"):
        app.correct_lesson(
            project,
            group["id"],
            original["id"],
            {
                "action": "add_note",
                "expected_version": 4,
                "note": "This must not bypass the group binding.",
            },
        )

    revised = app.correct_lesson(
        project,
        group["id"],
        original["id"],
        {
            "action": "revise",
            "agent_id": agent["id"],
            "expected_version": 4,
            "statement": "Retry idempotent failed calls within a bounded policy.",
            "uncertainty": "Tool idempotency must still be classified.",
            "reason": "Limit the original advice to safe calls.",
        },
    )["latest"]
    assert revised["version"] == 5 and revised["status"] == "active"
    assert revised["revision_kind"] == "revised"
    assert revised["evidence"] == original["evidence"]
    assert (
        app.memory.recall(project, group["id"], "retry", agent["id"])["entries"][0]["version"] == 5
    )
    history = app.lesson(project, group["id"], original["id"], agent["id"])
    assert [version["version"] for version in history["versions"]] == [5, 4, 3, 2, 1]
    assert [version["status"] for version in history["versions"]] == [
        "active",
        "outdated",
        "active",
        "active",
        "active",
    ]

    with running(app) as (client, _server):
        response = client.post(
            f"/api/projects/{project}/lessons/{group['id']}/{original['id']}",
            json={
                "action": "add_note",
                "agent_id": agent["id"],
                "expected_version": 5,
                "note": "HTTP corrections use the same immutable operation.",
            },
        )
        assert response.status_code == 200
        assert response.json()["latest"]["version"] == 6


def test_supplied_trace_reaches_verified_repair_without_goal_or_audit(
    application, specification, tmp_path
):
    check = application.root / "regression.py"
    check.write_text(
        'import json\nfrom pathlib import Path\nraise SystemExit(0 if json.loads(Path("app.json").read_text())["latency"] < 100 else 1)\n'
    )
    git(application.root, "add", "regression.py")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Add regression",
    )
    spec = copy.deepcopy(specification)
    spec["repetitions"] = 1
    spec["editable_paths"] = ["app.json"]
    spec["evaluation_paths"].append("regression.py")
    spec["checks"].append(
        {
            "id": "timeout-regression",
            "argv": [sys.executable, "regression.py"],
            "baseline_expected": "fail",
        }
    )
    reviews = []

    def brain(request, emit, ask, cancelled):
        job = manifest(request)
        if job.get("active_review_id"):
            review = job["review_tasks"][job["active_review_id"]]
            session = "review-" + str(len(reviews))
            reviews.append(session)
            emit({"type": "session", "session_id": session, "model": "test-model"})
            return {
                "state": "completed",
                "session_id": session,
                "text": json.dumps(
                    {"review": passing_review({"review_template": review["template"]}, session)}
                ),
            }
        emit({"type": "session", "session_id": "author-session", "model": "test-model"})
        job = manifest(request)
        run_id = job["workflow_ids"].get("run_id")
        if not run_id:
            started = engine.start(
                application, spec, "local", execution_profile=job["execution_profile"]
            )
            run_id = started["run_id"]
            configure(application, run_id, job)
        from agentagon.capabilities.experiments.store import load_run

        data = load_run(application, run_id)
        baseline = data["candidates"][data["baseline_id"]]
        if baseline["state"] != "verified":
            measured = engine.run(application, run_id)
            candidate = measured["candidate"]
        elif len(data["candidates"]) == 1:
            created = engine.new(
                application, run_id, hypothesis="Avoid repeated timeout", author="author-session"
            )
            candidate = created["candidate"]
            path = application.root / candidate["worktree"] / "app.json"
            value = json.loads(path.read_text())
            value["latency"] = 80
            path.write_text(json.dumps(value))
            candidate = engine.run(application, run_id, candidate["candidate_id"])["candidate"]
        else:
            candidate = next(
                c for c in data["candidates"].values() if c["candidate_id"] != data["baseline_id"]
            )
            engine.select(application, run_id, candidate["candidate_id"])
            return {
                "state": "completed",
                "session_id": "author-session",
                "text": json.dumps(
                    {"run_id": run_id, "summary": "Regression repaired", "diagnosis": finding()}
                ),
            }
        assert candidate["state"] == "awaiting_review"
        return {
            "state": "completed",
            "session_id": "author-session",
            "text": json.dumps(
                {
                    "run_id": run_id,
                    "needs_review": {"run_id": run_id, "candidate_id": candidate["candidate_id"]},
                }
            ),
        }

    app = Application(tmp_path / "repair-service", execute=brain)
    try:
        project = app.register(str(application.root))["id"]
        agent = app.save_application_agent(
            project, {"name": "Target", "code_scopes": ["app.json", "model.bin"]}
        )
        trace = imported(app, project)
        started = app.submit_task(
            project,
            {
                "operation_id": str(uuid.uuid4()),
                "workflow": "fix",
                "agent_id": agent["id"],
                "input": {"type": "trace", "id": trace["id"]},
                "options": {"profile": "local"},
            },
        )
        done = wait_for(app.runtime, project, started["id"], timeout=20)
        assert done["state"] == "completed", done.get("next_action")
        assert len(reviews) == 2 and done["goal_id"] is None
        issue = get_issue(app.state.workspace(project), done["result"]["issue_id"])
        assert issue["status"] == "resolved_verified" and not issue["production_recovery_verified"]
        assert issue["tested_revision"] == done["result"]["source_revision"]
        from agentagon.capabilities.experiments.store import load_run

        run = load_run(application, done["result"]["run_id"])
        extended = {**run["limits"], "max_candidates": 4}
        with pytest.raises(AuditError, match="limits are frozen"):
            engine._continue(run, True, extended)
        assert not application.audits() and not app.catalog.goals(project, agent["id"])
        from agentagon.core.records import now
        from agentagon.domain.improvements import deploy, list_improvements

        improvements = list_improvements(app, project)
        assert len(improvements) == 1 and improvements[0]["recommended_by_task"]
        assert improvements[0]["selected_by_user"] is False
        assert any(
            item.get("run_id") == done["result"]["run_id"]
            for item in app.monitoring.overview(project)["attention"]
        )
        assert improvements[0]["tested_revision"] == issue["tested_revision"]
        app.decide_result(
            project,
            "fix",
            done["result"]["run_id"],
            {
                "operation_id": str(uuid.uuid4()),
                "expected_revision": 0,
                "decision": "select_candidate",
                "candidate_id": improvements[0]["candidate_id"],
            },
        )
        improvements = list_improvements(app, project)
        assert improvements[0]["selected_by_user"] is True
        assert not any(
            item.get("run_id") == done["result"]["run_id"]
            for item in app.monitoring.overview(project)["attention"]
        )
        deployment = deploy(
            app,
            project,
            {
                "operation_id": str(uuid.uuid4()),
                "improvement_id": improvements[0]["id"],
                "release": "release-1",
                "environment": "production",
                "revision": issue["tested_revision"],
                "deployed_at": now(),
            },
        )
        assert deployment["linkage"] == "user_declared"
        from datetime import UTC, datetime
        from types import SimpleNamespace

        from test_production_runtime import trace as production_trace

        app.scheduler.close()
        connection_id = "connection_" + "c" * 24
        with app.state.locked() as settings:
            settings["connections"][connection_id] = {
                "id": connection_id,
                "project_id": project,
                "project": "target",
                "provider": "langsmith",
                "credentials": {},
            }
        app.catalog.save_agent(
            project,
            {
                "trace_selector": {
                    "connection_id": connection_id,
                    "project": "target",
                    "environment": "production",
                }
            },
            agent["id"],
        )
        production_row = production_trace(
            revision=issue["tested_revision"], duration=0, start=datetime.now(UTC)
        )
        production_row["metadata"]["release"] = "release-1"
        app.provider_factory = lambda *_: SimpleNamespace(
            preview=lambda *_: {
                "items": [production_row],
                "provenance": {"provider": "otlp", "project": "target"},
                "completeness": {"complete": True, "count": 1},
            }
        )
        monitor = app.monitoring.save(
            project,
            {
                "agent_id": agent["id"],
                "environment": "production",
                "diagnosis": False,
                "measurements": [{"metric": "latency_ms", "accepted": True}],
            },
        )
        observation = app.submit_task(
            project,
            {
                "operation_id": str(uuid.uuid4()),
                "workflow": "observe",
                "input": {"type": "monitor", "id": monitor["id"]},
            },
        )
        observed = wait_for(app.runtime, project, observation["task_id"])
        assert observed["state"] == "completed", observed
        metric = observed["result"]["metrics"][0]
        assert metric["deployment_id"] == deployment["id"]
        assert metric["status"] in {"not_comparable", "insufficient_evidence"}
        assert {d["linkage"] for d in app.monitoring.overview(project)["deployments"]} == {
            "user_declared",
            "trace_reported",
        }
        assert not get_issue(app.state.workspace(project), issue["issue_id"])[
            "production_recovery_verified"
        ]
        groups = app.memory.list(project, agent["id"], "improvement")
        assert app.memory.recall(project, groups[0]["id"], "", agent["id"])["entries"]
    finally:
        app.close()


def test_stdio_mcp_and_dashboard_share_task_and_idempotency(system, monkeypatch):
    app, project, agent = system
    app.runtime.stopping = True
    trace = imported(app, project)
    with running(app) as (client, _server):
        # The same private service descriptor is consumed by the real stdio subprocess.
        server = _server if hasattr(_server, "session_token") else None
        if server is None:
            pytest.fail("running helper must expose server")
        atomic_write(
            app.state.directory / "instance.json",
            {
                "port": server.server_port,
                "token": server.session_token,
                "version": 1,
                "pid": os.getpid(),
            },
        )
        command = {
            "project_id": project,
            "workflow": "discover",
            "input": {"type": "trace", "id": trace["id"]},
            "operation_id": str(uuid.uuid4()),
        }

        async def exercise():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "agentagon", "mcp"],
                env={**os.environ, "AGENTAGON_APP_STATE": str(app.state.directory)},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert {
                        "start_workflow",
                        "inspect_task",
                        "inspect_trace",
                        "inspect_result",
                        "decide_result",
                        "prepare_delivery",
                        "recall_memory",
                        "record_memory",
                    } <= names
                    inspected = await session.call_tool(
                        "inspect_trace",
                        {"project_id": project, "snapshot_id": trace["id"], "max_spans": 2},
                    )
                    assert not inspected.isError, inspected
                    trace_view = json.loads(inspected.content[0].text)
                    assert trace_view["readiness"]["diagnosis_ready"] is True
                    assert trace_view["coverage"]["returned_spans"] <= 2
                    response = await session.call_tool("start_workflow", command)
                    assert not response.isError, response
                    one = json.loads(response.content[0].text)
                    two = await session.call_tool("start_workflow", command)
                    assert json.loads(two.content[0].text)["task_id"] == one["task_id"]
                    return one

        task = asyncio.run(exercise())
        shown = client.get(f"/api/projects/{project}/tasks/{task['task_id']}").json()
        assert shown["id"] == task["task_id"] and shown["state"] == "queued"
        assert len(app.runtime.list(project)) == 1
        assert task["dashboard_url"].startswith(str(client.base_url).rstrip("/"))
        # Stdio client disconnection does not cancel service-owned work.
        assert app.runtime.get(project, task["task_id"])["state"] == "queued"


def test_unverified_brain_claim_cannot_resolve_trace(system):
    app, project, agent = system
    trace = imported(app, project)

    def brain(request, emit, *_):
        emit({"type": "session", "session_id": "unverified-author", "model": "test-model"})
        return {
            "state": "completed",
            "session_id": "unverified-author",
            "text": json.dumps({"summary": "All fixed!", "diagnosis": finding()}),
        }

    app.runtime.execute = brain
    # Discovery also requires evidence-citing issue output, never a prose success claim.
    task = app.submit_task(
        project,
        {
            "operation_id": str(uuid.uuid4()),
            "workflow": "discover",
            "input": {"type": "trace", "id": trace["id"]},
        },
    )
    done = wait_for(app.runtime, project, task["id"])
    assert done["state"] == "failed"
    assert not list_issues(app.state.workspace(project))


def test_retained_expectation_question_is_shared_and_answered_once(system):
    app, project, _agent = system
    trace = imported(app, project)
    calls = []

    def brain(request, emit, *_):
        emit({"type": "session", "session_id": "question-author", "model": "test-model"})
        task = manifest(request)
        calls.append(task)
        result = (
            {"issues": [], "summary": "Expected behavior clarified"}
            if task["messages"]
            else {"needs_input": "Should a timeout retry or report failure?"}
        )
        return {"state": "completed", "session_id": "question-author", "text": json.dumps(result)}

    app.runtime.execute = brain
    task = app.submit_task(
        project,
        {
            "operation_id": str(uuid.uuid4()),
            "workflow": "discover",
            "input": {"type": "trace", "id": trace["id"]},
        },
    )
    pending = wait_for(app.runtime, project, task["id"])
    assert pending["state"] == "needs_input" and pending["question"]["retained"]
    answer = {
        "operation_id": str(uuid.uuid4()),
        "question_id": pending["question"]["id"],
        "answer": {"text": "Report failure without retrying"},
    }
    app.runtime.control(project, task["id"], "answer", answer)
    done = wait_for(app.runtime, project, task["id"])
    assert done["state"] == "completed" and len(calls) == 2
    app.runtime.control(project, task["id"], "answer", answer)
    assert len(calls) == 2


def test_memory_snapshot_is_staged_into_actual_execution(
    application, specification, tmp_path, monkeypatch
):
    from agentagon.capabilities.experiments.store import load_run

    app = Application(tmp_path / "snapshot-service", execute=lambda *_: {})
    try:
        project = app.register(str(application.root))["id"]
        agent = app.save_application_agent(project, {"name": "Target", "code_scopes": ["app.json"]})
        group = app.memory.create(
            project,
            {
                "name": "Target memory",
                "purpose": "agent",
                "agent_ids": [agent["id"]],
                "path": str(tmp_path / "target-memory"),
            },
        )
        entry = {"key": "rule", "text": "Original knowledge", "evidence": ["fixture"]}
        app.memory.record(project, group["id"], entry, agent["id"])
        pinned = app.memory.snapshot(project, agent["id"])
        app.memory.record(project, group["id"], {**entry, "text": "Later knowledge"}, agent["id"])
        monkeypatch.setenv("AGENTAGON_MEMORY_SNAPSHOT", str(application.root / pinned["path"]))
        spec = copy.deepcopy(specification)
        spec["repetitions"] = 1
        spec["checks"].append(
            {
                "id": "pinned-memory",
                "argv": [
                    sys.executable,
                    "-c",
                    'import os,json; from pathlib import Path; value=json.loads(Path(os.environ["AGENTAGON_MEMORY_SNAPSHOT"]).read_text()); assert value["groups"][0]["entries"][0]["text"] == "Original knowledge"',
                ],
            }
        )
        started = engine.start(application, spec, "local")
        run = load_run(application, started["run_id"])
        verify_snapshot(application, {"memory_snapshot": pinned}, run)
        measured = engine.run(application, started["run_id"])
        assert measured["candidate"]["state"] == "awaiting_review", measured
        assert all(check["passed"] for check in measured["candidate"]["checks"])
        newer = app.memory.snapshot(project, agent["id"])
        monkeypatch.setenv("AGENTAGON_MEMORY_SNAPSHOT", str(application.root / newer["path"]))
        with pytest.raises(AuditError, match="new evaluator"):
            freeze_spec(application, run["spec"])
    finally:
        app.close()


def test_focused_repair_runs_existing_regression_suite_without_optimizer(
    application, specification
):
    from support.experiments import propose, verify
    from test_suites import _host, _suite
    from test_webapp_jobs import validation_job

    from agentagon.capabilities.experiments import suites
    from agentagon.capabilities.experiments.store import load_run
    from agentagon.core.records import digest

    context = validation_job("fix")
    context.update(id="task_" + "c" * 24, agent="codex", model="fake", session_id="author")
    _, manifest = _suite(application, specification)
    # A repair preserves every existing goal; none is its primary improvement target.
    manifest["goal_id"] = None
    manifest["digest"] = digest(
        {k: v for k, v in manifest.items() if k not in {"digest", "missing"}}
    )
    context["options"]["suite_manifest"] = manifest
    spec = copy.deepcopy(specification)
    spec["goal"] = "Repair an independently reproduced defect"
    spec["repetitions"] = 1
    started = engine.start(application, spec, "local")
    configure(application, started["run_id"], context)
    verify(application, started["run_id"], started["candidate_id"])
    candidate, _ = propose(application, started["run_id"], quality=0.9)
    verify(application, started["run_id"], candidate["candidate_id"])
    result = suites.advance(
        application, started["run_id"], candidate_id=candidate["candidate_id"], host_handler=_host()
    )
    assert result["state"] == "completed", result
    assert not load_run(application, started["run_id"]).get("optimizer_configured")
    engine.select(application, started["run_id"], candidate["candidate_id"])


def test_incomplete_trace_is_limited_and_assignment_preserves_issue_identity(system):
    from agentagon.domain.issues import assign_agent

    app, project, agent = system
    partial = trace_data()
    partial[0]["parentSpanId"] = "2222222222222222"
    trace = app.import_trace(
        project, {"operation_id": str(uuid.uuid4()), "provider": "otlp", "data": partial}
    )
    workspace = app.state.workspace(project)
    job = {"id": "task_" + "b" * 24, "options": {"trace_snapshot_id": trace["id"]}}
    state, result, _ = accept(workspace, job, {"issues": [finding()]})
    assert state == "completed_with_limits"
    issue_id = result["issue_ids"][0]
    assign_agent(workspace, issue_id, agent["id"])
    job["application_agent_id"] = agent["id"]
    _, repeated, _ = accept(workspace, job, {"issues": [finding()]})
    assert repeated["issue_ids"] == [issue_id]
    assert len(list_issues(workspace)) == 1
    with pytest.raises(AuditError, match="another agent"):
        assign_agent(workspace, issue_id, "another-agent")
