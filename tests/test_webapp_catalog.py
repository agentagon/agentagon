"""Agent identity, focused evidence and retained measurement history."""

import copy
import json
import uuid

import pytest
from test_baselines import complete, frozen
from test_webapp import connection as save_connection
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


def test_workspace_resource_contracts_and_unified_task_submission(app, tmp_path):
    saved = project(app, tmp_path)
    confirmed = agent(app, saved, description="Resolve support questions")
    goal = app.save_goal(
        saved["id"],
        confirmed["id"],
        {
            "category": "correctness",
            "objective": "Create exactly one correct ticket.",
            "ideal_behavior": "Retries never create duplicates.",
        },
    )

    projected = app.project_agents(saved["id"])
    assert projected["confirmed"][0]["responsibility"] == "Resolve support questions"
    assert "description" not in projected["confirmed"][0]
    assert app.goals(saved["id"], confirmed["id"])["goals"] == [goal]
    assert {item["workflow"] for item in app.skills()["skills"]} == {
        "design",
        "eval",
        "baseline",
        "fix",
        "audit",
    }
    assert {item["id"] for item in app.connector_types()["connector_types"]} == {
        "braintrust",
        "langsmith",
        "langfuse",
    }
    assert app.workflow_readiness(saved["id"], "design", confirmed["id"], goal["id"]) == {
        "workflow": "design",
        "workflow_version": 1,
        "ready": True,
        "blockers": [],
        "next_action": "start",
    }

    app.jobs.stopping = True
    command = {
        "operation_id": str(uuid.uuid4()),
        "workflow": "design",
        "agent_id": confirmed["id"],
        "goal_id": goal["id"],
        "options": {},
    }
    submitted = app.submit_task(saved["id"], command)
    assert app.submit_task(saved["id"], command)["id"] == submitted["id"]
    assert submitted["workflow_version"] == 1
    summary = app.tasks(saved["id"], {"agent_id": confirmed["id"], "goal_id": goal["id"]})
    assert [item["id"] for item in summary["tasks"]] == [submitted["id"]]
    detail = app.task(saved["id"], submitted["id"])
    assert detail["workflow"] == "design"
    assert detail["agent_name"] == "Support"
    assert detail["goal_name"] == "Task success and correctness"

    audit = app.submit_task(
        saved["id"],
        {
            "operation_id": str(uuid.uuid4()),
            "workflow": "audit",
            "agent_id": confirmed["id"],
            "options": {},
        },
    )
    assert audit["focus_id"] is None
    assert [item["id"] for item in app.tasks(saved["id"], {"workflow": "audit"})["tasks"]] == [
        audit["id"]
    ]


def test_discovery_is_explicit_bounded_and_preserves_confirmed_identity(app, tmp_path):
    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    (root / "app.py").write_text(
        'from agents import Agent\nsupport = Agent(name="Support")\nresearch = Agent(name="Research")\nraise RuntimeError("never execute discovery")\n'
    )
    assert app.application_agents(saved["id"]) == {"agents": []}
    discovered = app.catalog.discover(saved["id"])
    assert len(discovered["agents"]) == 2
    suggested = discovered["agents"][0]
    assert suggested["description"] == ""
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


def test_discovery_excludes_non_application_sources_and_retires_old_suggestions(app, tmp_path):
    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    (root / "app.py").write_text('from agents import Agent\nsupport = Agent(name="Support")\n')
    excluded = [
        "docs/archive/reference.py",
        "examples/demo.py",
        "fixtures/agent.py",
        "tests/test_agent.py",
        "vendor/package/agent.py",
    ]
    for path in excluded:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('assistant = Agent(name="Assistant")\n')
    legacy = app.catalog.save_agent(
        saved["id"],
        {
            "name": "Legacy example",
            "code_scopes": ["examples/demo.py"],
            "status": "suggested",
        },
        suggestion={"discovery_key": "legacy-example"},
    )
    assert app.catalog.agents(saved["id"]) == []

    discovered = app.catalog.discover(saved["id"])

    assert [agent["name"] for agent in discovered["agents"]] == ["Support"]
    assert discovered["scanned_files"] == 1
    assert app.catalog.agent(saved["id"], legacy["id"])["status"] == "suggested"


def test_first_discovery_saves_choices_and_applies_read_only_coding_review(
    app, tmp_path, monkeypatch
):
    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    (root / "app.py").write_text(
        'from agents import Agent\nsupport = Agent(name="Support")\nresearch = Agent(name="Research")\n'
    )
    (root / "helper.py").write_text('from agents import Agent\nhelper = Agent(name="Helper")\n')
    calls = []

    def execute(request, *_args):
        calls.append(request)
        candidates = json.loads(request["prompt"].split("Candidates: ", 1)[1])
        return {
            "state": "completed",
            "raw_final_text": json.dumps(
                {
                    "candidates": [
                        {
                            **candidate,
                            "name": "Customer support"
                            if candidate["name"] == "Support"
                            else candidate["name"],
                            "keep": candidate["name"] != "Helper",
                        }
                        for candidate in candidates
                    ]
                }
            ),
        }

    app.jobs.execute = execute
    monkeypatch.setattr(
        app,
        "agents",
        lambda: {
            "agents": [{"id": "codex", "available": True, "authenticated": True, "name": "Codex"}],
            "settings": {},
        },
    )

    discovered = app.discover_application_agents(
        saved["id"],
        {
            "preferences": {
                "coding_review": True,
                "trace_metadata": False,
                "trace_cap": 100,
                "trace_connection_id": None,
            }
        },
    )

    assert discovered["preferences"]["seen"] is True
    assert discovered["enrichment"]["coding_review"] == {"reviewed": 3, "kept": 2}
    assert [agent["name"] for agent in discovered["agents"]] == [
        "Customer support",
        "Research",
    ]
    assert calls[0]["sandbox"] == "read-only"
    assert calls[0]["response_mode"] == "raw-final"

    discovered_again = app.discover_application_agents(saved["id"], {})
    assert [agent["name"] for agent in discovered_again["agents"]] == [
        "Customer support",
        "Research",
    ]
    assert discovered_again["enrichment"]["coding_review"] == {"reviewed": 2, "kept": 2}


def test_trace_metadata_matching_is_bounded_and_advisory(app, tmp_path):
    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    (root / "support_router.py").write_text(
        'from agents import Agent\nsupport = Agent(name="Support Router")\n'
    )
    selections = []

    class Provider:
        def __init__(self, *_args):
            pass

        def test(self):
            return {
                "status": "connected",
                "projects": [{"id": "remote", "name": "Production traces"}],
            }

        def trace_metadata(self, selection):
            selections.append(selection)
            return {
                "items": [
                    {
                        "trace_id": "trace-1",
                        "name": "Support Router",
                        "metadata": {"environment": "production"},
                    }
                ]
            }

    app.provider_factory = Provider
    source = save_connection(app, saved["id"])
    app.test_connection(saved["id"], source["id"])

    discovered = app.discover_application_agents(
        saved["id"],
        {
            "preferences": {
                "coding_review": False,
                "trace_metadata": True,
                "trace_cap": 100,
                "trace_connection_id": source["id"],
            }
        },
    )

    assert discovered["enrichment"]["trace_metadata"] == {"sampled": 1, "matched": 1}
    assert selections[0]["cap"] == 100
    assert discovered["agents"][0]["trace_selector"] == {}
    trace_evidence = next(
        item for item in discovered["agents"][0]["evidence"] if item["kind"] == "trace_metadata"
    )
    assert trace_evidence["trace_id"] == "trace-1"


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


@pytest.mark.parametrize(
    ("filename", "source", "names"),
    [
        (
            "app.py",
            'from agents import Agent as OpenAIAgent\nprompt = "Do not edit the source"\nagent = OpenAIAgent(name="Support")',
            ["Support"],
        ),
        (
            "app.py",
            'import pydantic_ai as ai\nagent = ai.Agent[Dependencies, str]("model", name="Research")',
            ["Research"],
        ),
        (
            "app.py",
            "from langchain.agents import create_agent as create\nagent = create()",
            ["agent"],
        ),
        (
            "app.py",
            "from langgraph.graph import StateGraph as Graph\ngraph = Graph(dict)\nrouter = graph.compile()",
            ["router"],
        ),
        (
            "app.py",
            'import agents\ndef support():\n    agent = agents.Agent(name="Support")\ndef research():\n    agent = agents.Agent(name="Research")',
            ["Research", "Support"],
        ),
        (
            "app.ts",
            'import { Agent as OpenAIAgent } from "@openai/agents";\nconst agent = new OpenAIAgent<Context>({name: "Support"});',
            ["Support"],
        ),
        (
            "app.js",
            'import * as sdk from "@openai/agents";\nconst agent = new sdk.Agent({name: "Support"});',
            ["Support"],
        ),
        (
            "app.ts",
            'import { createReactAgent as create } from "@langchain/langgraph/prebuilt";\nconst agent = create({});',
            ["agent"],
        ),
        (
            "app.js",
            'import { StateGraph as Graph } from "@langchain/langgraph";\nconst graph = new Graph({});\nconst router = graph.compile();',
            ["router"],
        ),
        (
            "app.cjs",
            'const { Agent: OpenAIAgent } = require("@openai/agents");\nconst agent = new OpenAIAgent({name: "Support"});',
            ["Support"],
        ),
        (
            "app.cjs",
            'const sdk = require("@openai/agents");\nconst agent = new sdk.Agent({name: "Support"});',
            ["Support"],
        ),
    ],
)
def test_discovery_recognizes_imported_frameworks_and_aliases(
    app, tmp_path, filename, source, names
):
    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    (root / filename).write_text(source + "\nthrow_if_executed()\n")
    result = app.catalog.discover(saved["id"])
    assert sorted(agent["name"] for agent in result["agents"]) == names
    for candidate in result["agents"]:
        assert candidate["evidence"][0]["kind"] == "code"
        assert candidate["evidence"][0]["path"] == filename
        assert candidate["evidence"][0]["line"] >= 2
        assert candidate["evidence"][0]["call"]
    assert app.catalog.discover(saved["id"])["agents"] == result["agents"]
    assert not (root / ".agentagon").exists()


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        (
            "app.py",
            'agent = Agent(name="Unresolved")\nworkflow = Workflow()\napp = compiler.compile()',
        ),
        ("app.py", "from unrelated import Agent, Workflow\nagent = Agent()\nflow = Workflow()"),
        ("app.py", "from .agents import Agent\nagent = Agent()"),
        (
            "app.py",
            'from agents import Agent\ndef build(Agent):\n    agent = Agent(name="Shadowed")',
        ),
        (
            "app.py",
            'from agents import Agent\ndef build():\n    from .local import Agent\n    bot = Agent(name="Local utility")',
        ),
        ("app.py", "from agents import Agent\nclass Agent:\n    pass\nagent = Agent()"),
        ("app.py", "import agents as sdk\nsdk = unrelated\nagent = sdk.Agent()"),
        ("app.py", 'from agents import Agent\n# agent = Agent()\ndoc = "agent = Agent()"'),
        (
            "app.ts",
            'import { Agent, Workflow } from "unrelated";\nconst agent = new Agent();\nconst flow = new Workflow();',
        ),
        ("app.ts", 'import { Agent } from "./agents";\nconst agent = new Agent();'),
        ("app.ts", 'import type { Agent } from "@openai/agents";\nconst agent = new Agent();'),
        (
            "app.ts",
            'import { Agent } from "@openai/agents";\nfunction build(Agent) { const agent = new Agent(); }',
        ),
        (
            "app.ts",
            'import { Agent } from "@openai/agents";\nconst build = Agent => { const agent = new Agent(); };',
        ),
        (
            "app.js",
            'import { Agent } from "@openai/agents";\n// const commented = new Agent();\nconst text = "const quoted = new Agent();";\nconst template = `const templated = new Agent();`;\n/* const blocked = new Agent(); */',
        ),
        (
            "app.js",
            'const text = `import { Agent } from "@openai/agents";`;\nconst agent = new Agent();',
        ),
        (
            "app.js",
            'import { compile } from "unrelated";\nconst agent = compile();\nconst other = workflow.compile();',
        ),
    ],
)
def test_discovery_rejects_unrelated_shadowed_and_noncode_calls(app, tmp_path, filename, source):
    saved = project(app, tmp_path)
    (app.state.workspace(saved["id"]).root / filename).write_text(source)
    assert app.catalog.discover(saved["id"])["agents"] == []


def test_discovery_excludes_non_application_paths_at_any_depth_and_saved_suggestions(app, tmp_path):
    from agentagon.core.records import digest

    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    excluded = [
        "docs/archive/references/openai/examples/model_providers/litellm_auto.py",
        "src/docs/agent.py",
        "src/tests/agent.py",
        "src/example/agent.py",
        "src/vendor/agent.py",
        "src/third_party/agent.py",
        "src/generated/agent.py",
        "src/node_modules/agent.js",
        "src/__tests__/agent.ts",
        "src/test_agents.py",
        "src/agents_test.py",
        "src/agent.test.ts",
        "src/agent.spec.tsx",
        "src/example_agent.py",
        "src/agent_example.py",
        "src/demoAgent.ts",
        "src/agent.generated.py",
        "src/agent.min.js",
        "src/fixtures/agent.py",
    ]
    for relative in excluded:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('from agents import Agent\nagent = Agent(name="Assistant")')
    automatic = app.catalog.save_agent(
        saved["id"],
        {
            "name": "Old automatic example",
            "code_scopes": [excluded[0]],
            "status": "suggested",
        },
        suggestion={
            "discovery_key": digest(excluded[0]),
            "evidence": [{"kind": "code", "path": excluded[0]}],
        },
    )
    manual = app.catalog.save_agent(
        saved["id"], {"name": "Manual example", "code_scopes": [excluded[0]], "status": "suggested"}
    )
    confirmed = app.catalog.save_agent(
        saved["id"],
        {"name": "Confirmed example", "code_scopes": [excluded[0]]},
        suggestion={"discovery_key": digest("confirmed")},
    )
    assert {item["id"] for item in app.catalog.agents(saved["id"])} == {
        manual["id"],
        confirmed["id"],
    }
    result = app.catalog.discover(saved["id"])
    assert {item["id"] for item in result["agents"]} == {manual["id"], confirmed["id"]}
    assert result["discovered"] == 0
    assert app.catalog.agent(saved["id"], automatic["id"])["status"] == "suggested"
    assert "skipped" in " ".join(result["limitations"])


def test_discovery_is_bounded_and_skips_generated_symlink_and_local_framework_shadow(
    app, tmp_path, monkeypatch
):
    from agentagon.webapp import discovery

    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    source = 'from agents import Agent\nagent = Agent(name="Support")'
    (root / "app.py").write_text(source)
    (root / "agents.py").write_text("class Agent: pass\n")
    assert app.catalog.discover(saved["id"])["agents"] == []
    (root / "agents.py").unlink()
    (root / "app.py").write_text("# @generated; do not edit\n" + source)
    outside = tmp_path / "outside.py"
    outside.write_text(source)
    (root / "linked.py").symlink_to(outside)
    (root / "large.py").write_text(" " * (discovery.MAX_FILE_BYTES + 1) + source)
    assert app.catalog.discover(saved["id"])["agents"] == []
    (root / "app.py").write_text(source)
    (root / "second.py").write_text(source)
    monkeypatch.setattr(discovery, "MAX_FILES", 1)
    result = app.catalog.discover(saved["id"])
    assert len(result["agents"]) == 1
    assert result["scanned_files"] == 1
    assert any("limit reached" in value for value in result["limitations"])
    assert not (root / ".agentagon").exists()


@pytest.mark.parametrize("bound_code", [True, False])
def test_discovery_deduplicates_traces_only_with_matching_identity_provenance(
    app, tmp_path, bound_code
):
    saved = project(app, tmp_path)
    workspace = app.state.workspace(saved["id"])
    workspace.initialize()
    (workspace.root / "app.py").write_text(
        'from agents import Agent\nsupport = Agent(name="Support")'
    )
    metadata = {"agent_name": "Support", **({"code_path": "app.py"} if bound_code else {})}
    for connection in ("a", "a", "b"):
        snapshots.save(
            workspace,
            saved["id"],
            {
                "kind": "traces",
                "connection_id": "connection_" + connection * 24,
                "selection": {"project": "remote"},
                "provenance": {"provider": "braintrust"},
                "completeness": {"complete": True},
                "items": [{"metadata": metadata}, {"metadata": metadata}],
            },
        )
    first = app.catalog.discover(saved["id"])
    assert len(first["agents"]) == (2 if bound_code else 3)
    code = next(item for item in first["agents"] if item["code_scopes"])
    assert bool(code["trace_selector"]) is bound_code
    assert {
        item["trace_selector"]["connection_id"]
        for item in first["agents"]
        if item["trace_selector"]
    } == {"connection_" + "a" * 24, "connection_" + "b" * 24}
    assert app.catalog.discover(saved["id"])["agents"] == first["agents"]


def test_existing_generated_suggestions_are_hidden_without_changing_manual_agents(app, tmp_path):
    from agentagon.core.records import digest

    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    (root / "app.py").write_text(
        '# @generated; do not edit\nfrom agents import Agent\nagent = Agent(name="Generated")'
    )
    automated = app.catalog.save_agent(
        saved["id"],
        {"name": "Generated", "code_scopes": ["app.py"], "status": "suggested"},
        suggestion={"discovery_key": digest("generated")},
    )
    manual = agent(app, saved, name="Manual", status="suggested")
    confirmed = app.catalog.save_agent(
        saved["id"],
        {"name": "Confirmed", "code_scopes": ["app.py"]},
        suggestion={"discovery_key": digest("confirmed")},
    )
    assert {value["id"] for value in app.catalog.agents(saved["id"])} == {
        manual["id"],
        confirmed["id"],
    }
    assert {value["id"] for value in app.catalog.discover(saved["id"])["agents"]} == {
        manual["id"],
        confirmed["id"],
    }
    assert app.catalog.agent(saved["id"], automated["id"])["status"] == "suggested"


def test_discovery_bounds_import_reads_before_loading_and_verifies_selected_records(
    app, tmp_path, monkeypatch
):
    from agentagon.webapp import catalog

    saved = project(app, tmp_path)
    workspace = app.state.workspace(saved["id"])
    workspace.initialize()
    for index in range(3):
        snapshots.save(
            workspace,
            saved["id"],
            {
                "kind": "dataset",
                "connection_id": "connection_" + "a" * 24,
                "selection": {"dataset_id": str(index)},
                "provenance": {"provider": "braintrust"},
                "completeness": {"complete": True},
                "items": [{"input": "case"}],
            },
        )
    loaded = []
    load = snapshots.load

    def tracked_load(workspace, snapshot_id):
        loaded.append(snapshot_id)
        return load(workspace, snapshot_id)

    monkeypatch.setattr(snapshots, "load", tracked_load)
    monkeypatch.setattr(
        snapshots, "list_snapshots", lambda _: pytest.fail("must not eagerly load all imports")
    )
    monkeypatch.setattr(catalog, "MAX_DISCOVERY_IMPORTS", 2)
    result = app.catalog.discover(saved["id"])
    assert len(loaded) == result["coverage"]["import_snapshots"] == 2
    assert result["coverage"]["trace_snapshots"] == 0
    assert any("Imported-trace scan limit" in value for value in result["limitations"])
    loaded.clear()
    monkeypatch.setattr(catalog, "MAX_DISCOVERY_IMPORT_BYTES", 1)
    result = app.catalog.discover(saved["id"])
    assert loaded == []
    assert any("byte limit" in value for value in result["limitations"])


def test_saved_automatic_suggestions_do_not_read_symlinks_or_outside_source(
    app, tmp_path, monkeypatch
):
    from pathlib import Path

    from agentagon.core.records import digest

    saved = project(app, tmp_path)
    root = app.state.workspace(saved["id"]).root
    outside = tmp_path / "outside.py"
    outside.write_text('from agents import Agent\nagent = Agent(name="Private")')
    (root / "linked.py").symlink_to(outside)
    directory = tmp_path / "outside-directory"
    directory.mkdir()
    (directory / "agent.py").write_text(outside.read_text())
    (root / "linked-directory").symlink_to(directory, target_is_directory=True)

    def refuse_read(*_args, **_kwargs):
        pytest.fail("ineligible source must not be opened")

    for index, source in enumerate(
        ("linked.py", "linked-directory/agent.py", "../outside.py", "missing.py")
    ):
        candidate = agent(app, saved, name=f"Old suggestion {index}", status="suggested")
        app.state.db.put_record(
            saved["id"],
            "application_agents",
            candidate["id"],
            {**candidate, "discovery_key": digest(source), "code_scopes": [source]},
        )
    # Missing sources fail stat before open; no outside content may be read.
    monkeypatch.setattr(Path, "open", refuse_read)
    assert app.catalog.agents(saved["id"]) == []
