"""Production feedback extends the verified attempt lesson when evidence links it."""

import json

import pytest

from agentagon.core.records import identifier
from agentagon.workflows.outcomes import record_outcome
from agentagon.workflows.service import Application


@pytest.fixture
def lesson_system(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "agent.py").write_text('from agents import Agent\nweather = Agent(name="Weather")\n')
    application = Application(
        tmp_path / "state",
        execute=lambda *_: {
            "state": "completed",
            "text": json.dumps({"summary": "complete", "issues": []}),
        },
    )
    application.scheduler.close()
    project_id = application.register(str(root))["id"]
    agent = application.save_application_agent(
        project_id,
        {"name": "Weather", "code_scopes": ["agent.py"]},
    )
    yield application, project_id, agent
    application.close()


def _observe_job(project_id, agent_id, observation_id):
    return {
        "id": "task_" + "o" * 24,
        "project_id": project_id,
        "application_agent_id": agent_id,
        "kind": "observe",
        "state": "completed",
        "result": {"observation_id": observation_id},
    }


@pytest.mark.parametrize("status", ["improved", "regressed"])
def test_material_production_outcome_versions_originating_task_lesson(lesson_system, status):
    application, project_id, agent = lesson_system
    workspace = application.state.workspace(project_id)
    group = application.memory.improvement_groups(project_id, agent["id"])[0]
    task_id = "task_" + "f" * 24
    verification = workspace.artifact({"kind": "verification", "task_id": task_id})
    original = application.memory.record(
        project_id,
        group["id"],
        {
            "key": task_id,
            "text": "Fix task retained a verified candidate.",
            "evidence": [verification],
            "uncertainty": "Production recovery is not established.",
        },
        agent["id"],
    )
    improvement_id = "improvement_" + "i" * 24
    deployment_id = "deployment_" + "d" * 24
    application.state.db.put_record(
        project_id,
        "tasks",
        task_id,
        {
            "id": task_id,
            "kind": "fix",
            "application_agent_id": agent["id"],
            "state": "completed",
        },
    )
    application.state.db.put_record(
        project_id,
        "improvements",
        improvement_id,
        {
            "id": improvement_id,
            "workflow": "fix",
            "agent_id": agent["id"],
            "task_id": task_id,
            "evaluation_state": "verified",
            "tested_revision": "a" * 40,
            "verification": verification,
        },
    )
    application.state.db.put_record(
        project_id,
        "deployments",
        deployment_id,
        {
            "id": deployment_id,
            "agent_id": agent["id"],
            "improvement_id": improvement_id,
            "tested_revision": "a" * 40,
            "deployed_revision": "a" * 40,
            "exact_tested_revision": True,
        },
    )
    observation_id = "observation_" + "p" * 24
    observation_evidence = workspace.artifact(
        {"kind": "production_observation", "observation_id": observation_id}
    )
    application.state.db.put_record(
        project_id,
        "observations",
        observation_id,
        {
            "id": observation_id,
            "agent_id": agent["id"],
            "lesson_due": True,
            "evidence": observation_evidence,
            "metrics": [
                {
                    "name": "latency",
                    "status": status,
                    "deployment_id": deployment_id,
                    "reference": 100,
                    "current": 80 if status == "improved" else 120,
                    "delta": -20 if status == "improved" else 20,
                    "reference_count": 150,
                    "count": 160,
                }
            ],
        },
    )
    job = _observe_job(project_id, agent["id"], observation_id)

    record_outcome(application, workspace, job)

    history = application.memory.history(project_id, group["id"], original["id"], agent["id"])[
        "versions"
    ]
    assert len(history) == 2
    assert history[-1]["key"] == task_id
    assert f"latency was {status}" in history[-1]["text"]
    assert deployment_id in history[-1]["text"]
    assert history[-1]["evidence"] == [
        verification,
        observation_evidence,
        observation_id,
        deployment_id,
        improvement_id,
    ]
    assert "operational" not in history[-1]["text"].casefold()
    assert "Observation records remain authoritative" in history[-1]["uncertainty"]
    entries = application.memory.recall(project_id, group["id"], "")["entries"]
    assert [entry["key"] for entry in entries] == [task_id]

    # Runtime retries after a partial memory failure must not append the same
    # immutable outcome twice.
    record_outcome(application, workspace, job)
    assert (
        len(
            application.memory.history(project_id, group["id"], original["id"], agent["id"])[
                "versions"
            ]
        )
        == 2
    )


def test_unlinked_material_outcome_retains_explicit_limitation(lesson_system):
    application, project_id, agent = lesson_system
    workspace = application.state.workspace(project_id)
    group = application.memory.improvement_groups(project_id, agent["id"])[0]
    observation_id = "observation_" + "u" * 24
    evidence = workspace.artifact({"kind": "production_observation"})
    application.state.db.put_record(
        project_id,
        "observations",
        observation_id,
        {
            "id": observation_id,
            "agent_id": agent["id"],
            "lesson_due": True,
            "evidence": evidence,
            "metrics": [
                {
                    "name": "failure rate",
                    "status": "regressed",
                    "deployment_id": None,
                    "reference": 0.01,
                    "current": 0.1,
                }
            ],
        },
    )
    job = _observe_job(project_id, agent["id"], observation_id)

    record_outcome(application, workspace, job)
    entry_id = identifier("memory", group["id"], observation_id)
    history = application.memory.history(project_id, group["id"], entry_id, agent["id"])["versions"]
    assert len(history) == 1
    assert history[0]["key"] == observation_id
    assert "Unlinked production feedback" in history[0]["text"]
    assert "did not identify a deployment" in history[0]["text"]
    assert history[0]["evidence"] == [evidence, observation_id]

    record_outcome(application, workspace, job)
    assert (
        len(application.memory.history(project_id, group["id"], entry_id, agent["id"])["versions"])
        == 1
    )


def test_unchanged_observation_does_not_write_memory(lesson_system):
    application, project_id, agent = lesson_system
    workspace = application.state.workspace(project_id)
    group = application.memory.improvement_groups(project_id, agent["id"])[0]
    observation_id = "observation_" + "n" * 24
    application.state.db.put_record(
        project_id,
        "observations",
        observation_id,
        {
            "id": observation_id,
            "agent_id": agent["id"],
            "lesson_due": False,
            "evidence": workspace.artifact({"kind": "production_observation"}),
            "metrics": [],
        },
    )

    record_outcome(
        application,
        workspace,
        _observe_job(project_id, agent["id"], observation_id),
    )

    assert application.memory.recall(project_id, group["id"], "")["entries"] == []
