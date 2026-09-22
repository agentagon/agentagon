"""Goal coordination preserves accepted checks, admissions and child ownership."""

import uuid

import pytest
from test_goal_runs import goal_system as _goal_system
from test_goal_runs import operation
from test_webapp_designs import proposal

from agentagon.capabilities.experiments import baselines
from agentagon.capabilities.experiments.budget import BudgetLedger
from agentagon.core.records import AuditError, identifier
from agentagon.domain.tasks import TaskControlError
from agentagon.workflows.goal_runs import ActiveGoalRun, InputNeeded

goal_system = _goal_system


@pytest.mark.parametrize("change", ["metric", "behavior", "scoring"])
def test_go_cannot_erase_an_accepted_measurement_contract(goal_system, change):
    app, project, agent, goal, calls = goal_system
    app.goal_runs.close()
    ids = project, agent["id"], goal["id"]
    draft = app.designs.save(*ids, proposal())
    accepted = app.designs.accept(*ids, {"expected_revision": draft["revision"]})
    replacement = proposal(expected_revision=app.designs.get(*ids)["revision"])
    if change == "metric":
        replacement["metrics"]["quality"]["weight"] = 1
    elif change == "behavior":
        replacement["behaviors"] = []
    else:
        replacement["scoring"] = {"mode": "primary", "primary": "latency"}
    draft = app.designs.save(*ids, replacement)
    run = app.goal_runs.start(*ids, operation())
    with pytest.raises(InputNeeded) as rejected:
        app.goal_runs._accept(app.goal_runs._read(project, run["id"]), draft)
    assert rejected.value.code == "measurement_contract"
    assert app.designs.accepted(*ids)["id"] == accepted["id"]
    assert len(app.state.db.list_records(project, "accepted_designs")) == 1
    assert calls == []


def test_imported_ledger_usage_does_not_consume_new_goal_allowance(goal_system, monkeypatch):
    app, project, agent, goal, calls = goal_system
    app.goal_runs.close()
    workspace = app.state.workspace(project)
    workspace.initialize()
    owner = identifier("run", "imported-evaluator")
    ledger = BudgetLedger(workspace, owner)
    ledger.create(30, 1800, journey="init")
    ledger.admit("earlier-evaluation", "preparation", units=2)
    ledger.finish("earlier-evaluation")
    imported = ledger.spent(ledger.snapshot())
    run = app.goal_runs.start(project, agent["id"], goal["id"], operation())
    record = app.goal_runs._read(project, run["id"])
    stage = {
        "stage": "baseline",
        "reserved_trials": 6,
        "initial_budget_usage": {owner: imported},
    }
    ledger.admit("current-baseline", "preparation", units=1)
    ledger.finish("current-baseline")
    monkeypatch.setattr(baselines, "status", lambda *_: {"budget_id": owner})
    task = {"result": {"baseline_id": identifier("baseline", "current")}}
    app.goal_runs._settle_trials(record, task, stage)
    assert stage["actual_trials"] == stage["reserved_trials"] == 1
    assert record["budget_usage"][owner] == 3
    assert calls == []


def test_missing_admission_ledger_cannot_establish_accounted_completion(goal_system):
    app, project, agent, goal, calls = goal_system
    app.goal_runs.close()
    run = app.goal_runs.start(project, agent["id"], goal["id"], operation())
    stage = {"stage": "optimize", "reserved_trials": 12}
    with pytest.raises(AuditError, match="admission ledger is missing"):
        app.goal_runs._settle_trials(
            run, {"result": {"run_id": identifier("run", "missing")}}, stage
        )
    assert stage["reserved_trials"] == 12
    assert calls == []


def test_failed_child_cannot_bypass_the_replacement_goal_owner(goal_system):
    app, project, agent, goal, calls = goal_system
    app.goal_runs.close()
    run = app.goal_runs.start(project, agent["id"], goal["id"], operation())
    child_id = identifier("task", str(uuid.uuid4()))
    child = app.state.db.put_record(
        project,
        "tasks",
        child_id,
        {
            "id": child_id,
            "project_id": project,
            "goal_run_id": run["id"],
            "kind": "design",
            "state": "failed",
            "elapsed_seconds": 1,
            "options": {"max_elapsed_seconds": 60},
            "receipts": {},
        },
    )
    record = app.goal_runs._read(project, run["id"])
    record.update(state="failed", stage="design", active_task_id=child_id, task_ids=[child_id])
    app.goal_runs._save(record)
    replacement = app.goal_runs.start(project, agent["id"], goal["id"], operation())
    with pytest.raises(ActiveGoalRun) as rejected:
        app.goal_runs.control(project, run["id"], "resume", operation())
    assert rejected.value.details["run_id"] == replacement["id"]
    with pytest.raises(TaskControlError) as direct:
        app.runtime.control(project, child_id, "resume", operation())
    assert direct.value.details["code"] == "goal_run_owned"
    assert "resume" not in app.runtime.get(project, child_id)["available_actions"]
    assert app.runtime.get(project, child_id)["revision"] == child["revision"]
    assert calls == []


@pytest.mark.parametrize("changed", ["agent", "goal", "dependency"])
def test_resume_rejects_changed_binding_before_restarting_paused_child(goal_system, changed):
    app, project, agent, goal, calls = goal_system
    app.goal_runs.close()
    run = app.goal_runs.start(project, agent["id"], goal["id"], operation())
    record = app.goal_runs._read(project, run["id"])
    target = agent
    if changed == "dependency":
        target = app.save_application_agent(
            project, {"name": "Sibling", "code_scopes": ["agent.py"]}
        )
        dependency = app.save_goal(
            project, target["id"], {"category": "correctness", "objective": "Preserve behavior"}
        )
        record["focus"] = {
            "agent_id": target["id"],
            "goal_id": dependency["id"],
            "binding": app.goal_runs._binding(project, target["id"], dependency["id"]),
        }
    child_id = identifier("task", str(uuid.uuid4()))
    child = app.state.db.put_record(
        project,
        "tasks",
        child_id,
        {
            "id": child_id,
            "project_id": project,
            "goal_run_id": run["id"],
            "kind": "design",
            "state": "paused",
            "elapsed_seconds": 1,
            "options": {"max_elapsed_seconds": 60},
            "receipts": {},
        },
    )
    record.update(state="paused", stage="design", active_task_id=child_id, task_ids=[child_id])
    app.goal_runs._save(record)
    if changed == "goal":
        saved_goal = app.catalog.goal_record(project, agent["id"], goal["id"])
        app.state.db.put_record(
            project, "goals", goal["id"], {**saved_goal, "objective": "A changed expectation"}
        )
    else:
        app.save_application_agent(
            project,
            {"code_scopes": ["other.py"], "expected_revision": target["revision"]},
            target["id"],
        )
    command = operation()
    blocked = app.goal_runs.control(project, run["id"], "resume", command)
    assert blocked["state"] == "failed"
    assert blocked["requirements"][0]["code"] == "goal_changed"
    assert "resume" not in blocked["available_actions"]
    assert (
        app.goal_runs.control(project, run["id"], "resume", command)["revision"]
        == blocked["revision"]
    )
    assert app.runtime.get(project, child_id)["state"] == "paused"
    assert app.runtime.get(project, child_id)["revision"] == child["revision"]
    assert calls == []


@pytest.mark.parametrize("action", ["pause", "cancel"])
@pytest.mark.parametrize("transition", ["completes", "control_fails"])
def test_stop_reconciles_child_race_without_stranding_running_parent(
    goal_system, monkeypatch, action, transition
):
    app, project, agent, goal, calls = goal_system
    app.goal_runs.close()
    run = app.goal_runs.start(project, agent["id"], goal["id"], operation())
    child_id = identifier("task", str(uuid.uuid4()))
    child = app.state.db.put_record(
        project,
        "tasks",
        child_id,
        {
            "id": child_id,
            "project_id": project,
            "goal_run_id": run["id"],
            "kind": "design",
            "state": "running",
            "elapsed_seconds": 1,
            "options": {"max_elapsed_seconds": 60},
            "receipts": {},
        },
    )
    record = app.goal_runs._read(project, run["id"])
    record.update(stage="design", active_task_id=child_id, task_ids=[child_id])
    app.goal_runs._save(record)
    original = app.runtime.get

    def finish_after_snapshot(*args):
        snapshot = original(*args)
        if snapshot["state"] == "running":
            app.state.db.put_record(project, "tasks", child_id, {**child, "state": "completed"})
        return snapshot

    def fail_control(*args, **kwargs):
        app.state.db.put_record(project, "tasks", child_id, {**child, "state": "completed"})
        raise AuditError("simulated control write failure")

    if transition == "completes":
        monkeypatch.setattr(app.runtime, "get", finish_after_snapshot)
        stopped = app.goal_runs.control(project, run["id"], action, operation())
        assert stopped["state"] == ("paused" if action == "pause" else "cancelled")
        assert (project, run["id"]) not in app.goal_runs.live
    else:
        monkeypatch.setattr(app.runtime, "control", fail_control)
        with pytest.raises(AuditError, match="simulated control write failure"):
            app.goal_runs.control(project, run["id"], action, operation())
        assert app.goal_runs.get(project, run["id"])["state"] == "running"
        assert (project, run["id"]) in app.goal_runs.live
        assert app.runtime.get(project, child_id)["state"] == "completed"
        retried = app.goal_runs.control(project, run["id"], action, operation())
        assert retried["state"] == ("paused" if action == "pause" else "cancelled")
    assert calls == []
