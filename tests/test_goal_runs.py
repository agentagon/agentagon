"""Goal-run authorization and recovery through the shared application service."""

import time
import uuid

import pytest

from agentagon.core.records import AuditError
from agentagon.workflows.goal_runs import ActiveGoalRun
from agentagon.workflows.service import Application


def operation(**values):
    return {"operation_id": str(uuid.uuid4()), **values}


@pytest.fixture
def goal_system(tmp_path):
    root = tmp_path / "agent"
    root.mkdir()
    (root / "agent.py").write_text("def answer(): return 42\n")
    (root / "other.py").write_text("def other(): return 0\n")
    calls = []
    app = Application(tmp_path / "state", execute=lambda *args: calls.append(args))
    project = app.register(str(root))["id"]
    agent = app.save_application_agent(project, {"name": "Answer", "code_scopes": ["agent.py"]})
    goal = app.save_goal(
        project, agent["id"], {"category": "correctness", "objective": "Answer accurately"}
    )
    yield app, project, agent, goal, calls
    app.close()


def settled(app, project, run_id, state):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = app.goal_runs.get(project, run_id)
        if run["state"] == state:
            return run
        time.sleep(0.01)
    pytest.fail(f"Goal run did not reach {state}: {run}")


def test_go_retries_keep_one_run_and_reject_changed_intent(goal_system):
    app, project, agent, goal, calls = goal_system
    request = operation(details="Preserve refusal behavior", max_trials=12)
    with app.lock:
        run = app.goal_runs.start(project, agent["id"], goal["id"], request)
        assert app.goal_runs.start(project, agent["id"], goal["id"], request)["id"] == run["id"]
        assert (
            app.goal_runs.start(
                project,
                agent["id"],
                goal["id"],
                operation(details=request["details"], max_trials=12),
            )["id"]
            == run["id"]
        )
        with pytest.raises(AuditError, match="different goal run settings"):
            app.goal_runs.start(
                project, agent["id"], goal["id"], {**request, "details": "Different"}
            )
        with pytest.raises(ActiveGoalRun):
            app.goal_runs.start(project, agent["id"], goal["id"], operation(details="Different"))
        assert len(app.goal_runs.list(project, agent["id"], goal["id"])["runs"]) == 1
        assert run["authorization"]["delivery_authorized"] is False
        assert calls == []


def test_non_git_goal_explains_prerequisite_without_dispatch(goal_system):
    app, project, agent, goal, calls = goal_system
    run = app.goal_runs.start(project, agent["id"], goal["id"], operation())
    blocked = settled(app, project, run["id"], "needs_input")
    prerequisite = next(item for item in blocked["requirements"] if item["code"] == "clean_source")
    assert "committed Git" in prerequisite["message"]
    assert blocked["task_ids"] == []
    assert blocked["accounting"]["trials_reserved"] == 0
    assert calls == []


def test_stop_is_idempotent_and_new_go_has_fresh_authorization(goal_system):
    app, project, agent, goal, calls = goal_system
    with app.lock:
        run = app.goal_runs.start(
            project, agent["id"], goal["id"], operation(details="Keep context")
        )
        stop = operation()
        stopped = app.goal_runs.control(project, run["id"], "cancel", stop)
        assert stopped["state"] == "cancelled"
        assert stopped["available_actions"] == []
        assert app.goal_runs.control(project, run["id"], "cancel", stop)["id"] == run["id"]
        with pytest.raises(AuditError, match="another goal run control"):
            app.goal_runs.control(project, run["id"], "resume", stop)
        new = app.goal_runs.start(
            project, agent["id"], goal["id"], operation(details="Keep context")
        )
        assert new["id"] != run["id"]
        assert new["authorization"]["operation_id"] != run["authorization"]["operation_id"]
        assert new["details"] == stopped["details"]
        assert calls == []


def test_pause_survives_restart_and_reads_do_not_restart_work(goal_system):
    app, project, agent, goal, calls = goal_system
    with app.lock:
        run = app.goal_runs.start(project, agent["id"], goal["id"], operation())
        app.goal_runs.control(project, run["id"], "pause", operation())
    app.close()
    restarted = Application(app.state.directory, execute=lambda *args: calls.append(args))
    try:
        record = restarted.goal_runs.get(project, run["id"])
        assert record["state"] == "paused"
        assert "resume" in record["available_actions"]
        assert (
            restarted.goal_runs.list(project, agent["id"], goal["id"])["runs"][0]["revision"]
            == record["revision"]
        )
        assert restarted.goal_runs.live == set()
        assert calls == []
    finally:
        restarted.close()


def test_changed_agent_scope_requires_new_go(goal_system):
    app, project, agent, goal, calls = goal_system
    with app.lock:
        run = app.goal_runs.start(project, agent["id"], goal["id"], operation())
        app.goal_runs.control(project, run["id"], "pause", operation())
        app.save_application_agent(
            project,
            {"code_scopes": ["other.py"], "expected_revision": agent["revision"]},
            agent["id"],
        )
        app.goal_runs.control(project, run["id"], "resume", operation())
    failed = settled(app, project, run["id"], "failed")
    assert failed["requirements"][0]["code"] == "goal_changed"
    assert "resume" not in failed["available_actions"]
    assert failed["task_ids"] == []
    assert calls == []
