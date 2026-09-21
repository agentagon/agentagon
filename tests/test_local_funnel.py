"""Project-local funnel instrumentation and privacy boundaries."""

from types import SimpleNamespace

from agentagon.domain.funnel import (
    _task_id,
    projection,
    record_duplicate_submission,
    record_intent,
)
from agentagon.storage.state import AppState, private_directory


def _state(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state = AppState(tmp_path / "state")
    return state, state.register(str(project))


def test_local_projection_derives_durable_journey_without_project_content(tmp_path):
    state, project = _state(tmp_path)
    project_id = project["id"]
    operation = "11111111-1111-4111-8111-111111111111"
    prepared = SimpleNamespace(
        operation=operation,
        workflow="fix",
        state="needs_input",
        prerequisites=[{"code": "clean_source", "blocking": True}],
    )
    assert record_intent(state, project_id, prepared)

    prepared.state = "ready"
    prepared.prerequisites = [{"code": "clean_source", "blocking": False}]
    assert record_intent(state, project_id, prepared)

    task_id = _task_id(project_id, operation)
    task = state.db.put_record(
        project_id,
        "tasks",
        task_id,
        {
            "id": task_id,
            "kind": "fix",
            "state": "completed",
            "answers": {"question-one": {"accepted_at": project["created_at"]}},
            "question": {"id": "question-two"},
            "workflow_ids": {"run_id": "run_" + "2" * 24},
            "created_at": project["created_at"],
        },
    )
    assert record_duplicate_submission(state, project_id, task)
    assert record_duplicate_submission(state, project_id, task)

    state.db.put_record(
        project_id,
        "application_agents",
        "agent_" + "3" * 24,
        {
            "id": "agent_" + "3" * 24,
            "description": "Answers customer support questions.",
            "status": "suggested",
            "responsibility_inference": {
                "state": "inferred",
                "at": project["created_at"],
            },
        },
    )
    run_id = "run_" + "2" * 24
    improvement_id = "improvement_" + "4" * 24
    decision_id = "result_decision_" + "5" * 24
    state.db.put_record(
        project_id,
        "improvements",
        improvement_id,
        {
            "id": improvement_id,
            "run_id": run_id,
            "created_at": project["created_at"],
        },
    )
    state.db.put_record(
        project_id,
        "result_decisions",
        decision_id,
        {
            "id": decision_id,
            "run_id": run_id,
            "workflow": "fix",
            "decision": "select_candidate",
            "decided_at": project["created_at"],
        },
    )
    workspace = state.workspace(project_id)
    delivery_id = "delivery_" + "6" * 24
    workspace.write(
        private_directory(workspace, "deliveries") / f"{delivery_id}.json",
        {
            "delivery_id": delivery_id,
            "app_kind": "fix",
            "app_source_id": run_id,
            "user_decision_id": decision_id,
            "state": "prepared",
            "created_at": project["created_at"],
        },
    )

    result = projection(state, project_id)
    assert result["privacy"] == {
        "storage": "project-local application metadata and project evidence",
        "automatic_external_transmission": False,
        "export": "manual JSON download only",
        "contains_project_path": False,
        "contains_source_or_trace_content": False,
    }
    assert result["metrics"]["registration_to_first_understood_agent"]["completed"]
    assert result["metrics"]["action_intent_to_accepted_start"]["accepted"] == 1
    assert result["metrics"]["prerequisite_blocks"]["by_code"] == {"clean_source": 1}
    assert result["metrics"]["questions"] == {
        "total": 2,
        "tasks": 1,
        "repeated": 1,
        "open": 1,
    }
    assert result["metrics"]["duplicate_submissions"] == {"total": 2, "task_count": 1}
    assert result["metrics"]["result_selection"] == {
        "decisions": 1,
        "selected_candidates": 1,
        "delivered": 1,
    }
    serialized = str(result)
    assert str(tmp_path) not in serialized
    assert "Answers customer support questions" not in serialized


def test_intent_without_operation_is_not_recorded(tmp_path):
    state, project = _state(tmp_path)
    prepared = SimpleNamespace(operation=None, workflow="audit", state="ready", prerequisites=[])
    assert not record_intent(state, project["id"], prepared)
    assert state.db.get_record(project["id"], "funnel", "local") is None
