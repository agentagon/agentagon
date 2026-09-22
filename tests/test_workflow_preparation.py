"""Preparation exposes the exact workflow contract without starting work."""

import subprocess
import uuid

import pytest

from agentagon.capabilities.traces import snapshots
from agentagon.core.records import AuditError
from agentagon.storage.config import Config
from agentagon.workflows.operations.preparation import prepare_workflow_start
from agentagon.workflows.runtime import task_sandbox
from agentagon.workflows.service import Application


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path / "state", execute=lambda *_: {})
    application.assistants = lambda: {
        "assistants": [
            {
                "id": "codex",
                "name": "Codex",
                "available": True,
                "authenticated": True,
                "version": "test",
            }
        ],
        "defaults": {},
    }
    yield application
    application.close()


def project(app, tmp_path, name="customer"):
    root = tmp_path / name
    root.mkdir()
    (root / "agent.py").write_text("def answer(question):\n    return question\n")
    return app.register(str(root))


def git(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def git_project(app, tmp_path, name="versioned-customer"):
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-q")
    (root / "agent.py").write_text("def answer(question):\n    return question\n")
    git(root, "add", "agent.py")
    git(
        root,
        "-c",
        "user.name=Agentagon Tests",
        "-c",
        "user.email=tests@example.invalid",
        "commit",
        "-qm",
        "test: establish source",
    )
    return app.register(str(root)), root


def agent(app, project_id, name="Support"):
    return app.save_application_agent(
        project_id,
        {
            "name": name,
            "description": "Answer support questions",
            "code_scopes": ["agent.py"],
        },
    )


def suggested_agent(app, project_id, name="Suggested support"):
    return app.catalog.save_agent(
        project_id,
        {
            "name": name,
            "description": "Answer support questions",
            "code_scopes": ["agent.py"],
            "status": "suggested",
        },
        suggestion={"discovery_key": f"discovery-{name.casefold().replace(' ', '-')}"},
    )


def fix_intent(agent_id, **changes):
    return {
        "workflow": "fix",
        "agent_id": agent_id,
        "input": {"type": "description", "text": "Stop duplicate support replies"},
        "options": {},
        **changes,
    }


def audit_intent(agent_id, **changes):
    return {
        "workflow": "audit",
        "agent_id": agent_id,
        "input": {"type": "agent", "id": agent_id},
        "options": {},
        **changes,
    }


def prerequisite(result, code):
    return next(item for item in result["prerequisites"] if item["code"] == code)


def test_fix_preparation_shows_scope_and_missing_regression_baselines_without_side_effects(
    app, tmp_path
):
    saved = project(app, tmp_path)
    owner = agent(app, saved["id"])
    goal = app.save_goal(
        saved["id"],
        owner["id"],
        {"category": "correctness", "objective": "Never create duplicate replies"},
    )

    result = prepare_workflow_start(app, saved["id"], fix_intent(owner["id"]))

    assert result["state"] == "needs_input"
    assert result["normalized_intent"]["scope"] == ["agent.py"]
    assert result["normalized_intent"]["limits"] == {
        "max_trials": 24,
        "max_elapsed_seconds": 1800,
        "trial_timeout_seconds": 60,
    }
    code_scope = prerequisite(result, "code_scope")
    assert code_scope["state"] == "satisfied"
    assert code_scope["evidence_refs"][0]["paths"] == ["agent.py"]
    clean_source = prerequisite(result, "clean_source")
    assert clean_source["state"] == "missing"
    assert clean_source["resolution"]["action"] == "prepare_committed_source"
    assert clean_source["resolution"]["context"]["source_kind"] == "folder"
    regression = prerequisite(result, "regression_baselines")
    assert regression["state"] == "missing"
    assert regression["resolution"]["action"] == "establish_baselines"
    assert regression["resolution"]["context"]["names"] == [goal["name"]]
    assert regression["resolution"]["context"]["focused_regression_required"] is True
    assert app.tasks(saved["id"], {})["tasks"] == []
    assert app.state.db.list_records(saved["id"], "memory_groups") == []


def test_submit_uses_preparation_blockers_and_does_not_consume_operation(app, tmp_path):
    saved, _root = git_project(app, tmp_path, "blocked-measured-customer")
    owner = agent(app, saved["id"])
    app.save_goal(
        saved["id"],
        owner["id"],
        {"category": "correctness", "objective": "Never create duplicate replies"},
    )
    operation = str(uuid.uuid4())
    command = fix_intent(owner["id"], operation_id=operation)

    with pytest.raises(AuditError, match="current baselines"):
        app.submit_task(saved["id"], command)

    assert app.tasks(saved["id"], {})["tasks"] == []


def test_suggested_identity_policy_allows_only_read_only_workflows(app, tmp_path):
    saved = project(app, tmp_path, "suggested-policy")
    owner = suggested_agent(app, saved["id"])

    audit = prepare_workflow_start(app, saved["id"], audit_intent(owner["id"]))
    assert audit["state"] == "ready"
    assert audit["normalized_intent"]["scope"] == ["agent.py"]
    review = prerequisite(audit, "suggested_agent_read_only")
    assert review["state"] == "satisfied"
    assert review["context"] == {
        "agent_id": owner["id"],
        "workflow": "audit",
        "read_only": True,
    }
    assert review["evidence_refs"][0]["binding_digest"] == owner["binding_digest"]
    with pytest.raises(AuditError, match="exact displayed code scope"):
        prepare_workflow_start(app, saved["id"], audit_intent(owner["id"], scope=["different.py"]))

    assessment = prepare_workflow_start(
        app,
        saved["id"],
        {
            "workflow": "assess",
            "agent_id": owner["id"],
            "input": {"type": "project", "id": saved["id"]},
            "options": {},
        },
    )
    assert assessment["state"] == "ready_with_limits"
    assert prerequisite(assessment, "suggested_agent_read_only")["state"] == "satisfied"
    app.runtime.stopping = True
    assessment_task = app.submit_task(
        saved["id"],
        {**assessment["normalized_intent"], "operation_id": str(uuid.uuid4())},
    )
    retained_assessment = app.runtime.get(saved["id"], assessment_task["task_id"])
    assert retained_assessment["options"]["agent_review_scope"] == {
        "version": 1,
        "agent_id": owner["id"],
        "revision": owner["revision"],
        "binding_version": owner["binding_version"],
        "binding_digest": owner["binding_digest"],
        "code_scopes": ["agent.py"],
        "shared_dependencies": [],
        "trace_selector": {},
    }
    assert task_sandbox(retained_assessment) == "read-only"

    workspace = app.state.workspace(saved["id"])
    workspace.initialize()
    trace = snapshots.save(
        workspace,
        saved["id"],
        {
            "kind": "traces",
            "connection_id": "connection_" + "a" * 24,
            "selection": {"project": "customer", "cap": 1},
            "provenance": {"provider": "braintrust"},
            "completeness": {"complete": True},
            "items": [
                {
                    "span_id": "root",
                    "root_span_id": "root",
                    "span_parents": [],
                    "metadata": {"agent_name": owner["name"]},
                    "metrics": {"start": 1, "end": 2},
                    "input": "question",
                    "output": "answer",
                }
            ],
        },
    )
    discovery = prepare_workflow_start(
        app,
        saved["id"],
        {
            "workflow": "discover",
            "agent_id": owner["id"],
            "input": {"type": "trace", "id": trace["id"]},
            "options": {},
        },
    )
    assert prerequisite(discovery, "suggested_agent_read_only")["state"] == "satisfied"
    assert prerequisite(discovery, "trace_selection")["state"] == "satisfied"
    discovery_task = app.submit_task(
        saved["id"],
        {**discovery["normalized_intent"], "operation_id": str(uuid.uuid4())},
    )
    retained_discovery = app.runtime.get(saved["id"], discovery_task["task_id"])
    assert retained_discovery["options"]["agent_review_scope"]["revision"] == owner["revision"]
    assert task_sandbox(retained_discovery) == "read-only"

    blocked = {
        "fix": {"type": "description", "text": "Repair duplicate replies"},
        "design": {"type": "goal"},
        "eval": {"type": "goal"},
        "baseline": {"type": "goal"},
        "optimize": {"type": "goal"},
        "observe": {"type": "monitor"},
    }
    for workflow, source in blocked.items():
        prepared = prepare_workflow_start(
            app,
            saved["id"],
            {"workflow": workflow, "agent_id": owner["id"], "input": source, "options": {}},
        )
        confirmation = prerequisite(prepared, "agent_confirmation")
        assert confirmation["state"] == "missing", workflow


def test_suggested_audit_freezes_scope_and_forces_read_only_sandbox(app, tmp_path):
    saved = project(app, tmp_path, "suggested-audit")
    owner = suggested_agent(app, saved["id"])
    prepared = prepare_workflow_start(app, saved["id"], audit_intent(owner["id"]))
    app.runtime.stopping = True

    started = app.submit_task(
        saved["id"],
        {**prepared["normalized_intent"], "operation_id": str(uuid.uuid4())},
    )
    task = app.runtime.get(saved["id"], started["task_id"])
    frozen = task["options"]["agent_review_scope"]
    assert frozen["revision"] == owner["revision"]
    assert frozen["binding_digest"] == owner["binding_digest"]
    assert frozen["code_scopes"] == ["agent.py"]
    assert task["options"]["investigation_plan"]["binding_digest"] == owner["binding_digest"]
    assert task_sandbox(task) == "read-only"
    unchanged = app.catalog.agent(saved["id"], owner["id"])
    assert unchanged["status"] == "suggested"
    assert unchanged["revision"] == owner["revision"]

    stale = prepare_workflow_start(app, saved["id"], audit_intent(owner["id"]))
    app.save_application_agent(
        saved["id"],
        {
            "description": "Answer account and support questions",
            "expected_revision": owner["revision"],
        },
        owner["id"],
    )
    with pytest.raises(AuditError, match="changed after preparation"):
        app.submit_task(
            saved["id"],
            {**stale["normalized_intent"], "operation_id": str(uuid.uuid4())},
        )

    with pytest.raises(AuditError, match="confirm this application agent"):
        app.submit_job(
            saved["id"],
            {
                "operation_id": str(uuid.uuid4()),
                "kind": "audit",
                "application_agent_id": owner["id"],
                "goal_id": None,
                "options": {},
            },
        )


def test_excluded_identity_remains_blocked_for_read_only_audit(app, tmp_path):
    saved = project(app, tmp_path, "excluded-policy")
    owner = suggested_agent(app, saved["id"])
    excluded = app.catalog.exclude_suggestion(
        saved["id"], owner["id"], "Not an application agent", owner["revision"]
    )

    prepared = prepare_workflow_start(app, saved["id"], audit_intent(excluded["id"]))

    confirmation = prerequisite(prepared, "agent_confirmation")
    assert prepared["state"] == "needs_input"
    assert confirmation["state"] == "missing"
    assert confirmation["resolution"]["action"] == "restore_agent"


def test_stale_preparation_rejects_start_then_fresh_revision_uses_same_operation(app, tmp_path):
    saved = project(app, tmp_path)
    owner = agent(app, saved["id"])
    first = prepare_workflow_start(app, saved["id"], audit_intent(owner["id"]))
    assert first["state"] == "ready"
    app.save_application_agent(
        saved["id"],
        {
            "description": "Answer support and account questions",
            "expected_revision": owner["revision"],
        },
        owner["id"],
    )
    operation = str(uuid.uuid4())
    stale = {
        **first["normalized_intent"],
        "operation_id": operation,
    }

    with pytest.raises(AuditError, match="changed after preparation"):
        app.submit_task(saved["id"], stale)
    assert app.tasks(saved["id"], {})["tasks"] == []

    current = prepare_workflow_start(app, saved["id"], audit_intent(owner["id"]))
    app.runtime.stopping = True
    command = {**current["normalized_intent"], "operation_id": operation}
    started = app.submit_task(saved["id"], command)
    assert started["task_id"].startswith("task_")
    assert app.submit_task(saved["id"], command)["task_id"] == started["task_id"]
    funnel = app.local_funnel(saved["id"])["metrics"]
    assert funnel["action_intent_to_accepted_start"]["accepted"] == 1
    assert funnel["duplicate_submissions"] == {"total": 1, "task_count": 1}


def test_git_revision_is_frozen_between_preparation_and_start(app, tmp_path):
    saved, root = git_project(app, tmp_path)
    owner = agent(app, saved["id"])
    first = prepare_workflow_start(app, saved["id"], audit_intent(owner["id"]))

    assert first["state"] == "ready"
    assert first["code_source"]["kind"] == "git"
    assert first["code_source"]["revision"] == git(root, "rev-parse", "HEAD")
    assert first["code_source"]["dirty"] is False
    assert first["code_source"]["complete"] is True
    assert first["revisions"]["code_source"] == first["code_source"]["fingerprint"]

    (root / "agent.py").write_text("def answer(question):\n    return question.strip()\n")
    git(root, "add", "agent.py")
    git(
        root,
        "-c",
        "user.name=Agentagon Tests",
        "-c",
        "user.email=tests@example.invalid",
        "commit",
        "-qm",
        "test: change source",
    )

    with pytest.raises(AuditError, match="changed after preparation"):
        app.submit_task(
            saved["id"],
            {**first["normalized_intent"], "operation_id": str(uuid.uuid4())},
        )


def test_dirty_git_content_is_frozen_even_when_dirty_state_does_not_change(app, tmp_path):
    saved, root = git_project(app, tmp_path)
    owner = agent(app, saved["id"])
    Config().update_profile(
        "project",
        "local",
        {
            "runner": {"kind": "local"},
            "limits": {
                "max_candidates": 3,
                "max_trials": 12,
                "max_elapsed_seconds": 120,
                "parallel_candidates": 1,
                "parallel_trials": 1,
                "trial_timeout_seconds": 10,
            },
        },
        root,
    )
    (root / "agent.py").write_text("def answer(question):\n    return question.lower()\n")
    first = prepare_workflow_start(app, saved["id"], audit_intent(owner["id"]))

    assert first["state"] == "ready"
    assert first["code_source"]["dirty"] is True
    assert first["code_source"]["changed_paths"] == 1

    (root / "agent.py").write_text("def answer(question):\n    return question.upper()\n")
    with pytest.raises(AuditError, match="changed after preparation"):
        app.submit_task(
            saved["id"],
            {**first["normalized_intent"], "operation_id": str(uuid.uuid4())},
        )


def test_measured_fix_requires_a_clean_committed_revision(app, tmp_path):
    saved, root = git_project(app, tmp_path, "dirty-measured-source")
    owner = agent(app, saved["id"])
    (root / "agent.py").write_text("def answer(question):\n    return question.lower()\n")

    prepared = prepare_workflow_start(app, saved["id"], fix_intent(owner["id"]))

    clean_source = prerequisite(prepared, "clean_source")
    assert prepared["state"] == "needs_input"
    assert clean_source["state"] == "missing"
    assert clean_source["resolution"]["context"]["dirty"] is True


def test_required_coding_backend_is_checked_before_start(app, tmp_path):
    saved = project(app, tmp_path, "backend-readiness")
    owner = agent(app, saved["id"])
    app.assistants = lambda: {
        "assistants": [
            {
                "id": "codex",
                "name": "Codex",
                "available": True,
                "authenticated": None,
                "version": "test",
            }
        ],
        "defaults": {},
    }

    prepared = prepare_workflow_start(app, saved["id"], audit_intent(owner["id"]))

    backend = prerequisite(prepared, "coding_backend")
    assert prepared["state"] == "needs_input"
    assert backend["state"] == "missing"
    assert backend["resolution"]["action"] == "configure_coding_backend"
    assert "unknown" in backend["resolution"]["context"]["reason"].lower()


def test_non_git_folder_identity_is_frozen_between_preparation_and_start(app, tmp_path):
    saved = project(app, tmp_path)
    owner = agent(app, saved["id"])
    first = prepare_workflow_start(app, saved["id"], audit_intent(owner["id"]))

    assert first["state"] == "ready"
    assert first["code_source"]["kind"] == "folder"
    assert first["code_source"]["revision"] is None
    assert first["code_source"]["complete"] is True
    assert first["revisions"]["code_source"] == first["code_source"]["fingerprint"]

    (tmp_path / "customer" / "agent.py").write_text(
        "def answer(question):\n    return question.strip()\n"
    )
    with pytest.raises(AuditError, match="changed after preparation"):
        app.submit_task(
            saved["id"],
            {**first["normalized_intent"], "operation_id": str(uuid.uuid4())},
        )


def test_code_only_assessment_is_a_side_effect_free_limited_start(app, tmp_path):
    saved = project(app, tmp_path)
    before = app.state.db.get_record(saved["id"], "onboarding", "setup")

    result = prepare_workflow_start(
        app,
        saved["id"],
        {
            "workflow": "assess",
            "input": {"type": "project", "id": saved["id"]},
            "options": {},
        },
    )

    assert result["state"] == "ready_with_limits"
    assert result["limitations"] == [
        {
            "code": "code_only_assessment",
            "evidence_refs": [],
            "field": "options.assessment",
        }
    ]
    assert result["normalized_intent"]["options"] == {
        "assessment": {"trace_cap": 100, "lookback_days": 7}
    }
    assert app.state.db.get_record(saved["id"], "onboarding", "setup") == before
    assert app.tasks(saved["id"], {})["tasks"] == []


def test_ambiguous_agent_is_typed_and_resolvable(app, tmp_path):
    saved = project(app, tmp_path)
    first = agent(app, saved["id"], "Support")
    second = agent(app, saved["id"], "Research")

    result = prepare_workflow_start(
        app,
        saved["id"],
        {
            "workflow": "fix",
            "input": {"type": "description", "text": "Repair the answer"},
        },
    )

    blocker = prerequisite(result, "agent_selection")
    assert result["state"] == "needs_input"
    assert blocker["resolution"]["action"] == "select_agent"
    assert set(blocker["resolution"]["context"]["eligible_agent_ids"]) == {
        first["id"],
        second["id"],
    }
