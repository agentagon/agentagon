"""Durable browser jobs against real private records and offline coding-host fakes."""

import copy
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentagon import operations
from agentagon.core.records import AuditError, digest, load_json, now
from agentagon.experiments import optimize_run
from agentagon.storage.config import Config
from agentagon.webapp import workflows
from agentagon.webapp.jobs import JobManager
from agentagon.webapp.state import AppState


def payload(kind="audit", **kwargs):
    return {
        "operation_id": str(uuid.uuid4()),
        "kind": kind,
        "goal": "Inspect this application",
        **kwargs,
    }


@pytest.fixture
def manager_factory(tmp_path):
    managers = []

    def create(execute, root=None):
        index = len(managers)
        root = root or tmp_path / f"project-{index}"
        root.mkdir(exist_ok=True)
        state = AppState(tmp_path / f"app-state-{index}")
        project = state.register(str(root))
        manager = JobManager(
            state, SimpleNamespace(resolve=lambda ref: "test-key"), execute=execute
        )
        managers.append(manager)
        return manager, state, project["id"]

    yield create
    for manager in managers:
        manager.close()


def wait_for(manager, project, job_id, predicate=None, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(project, job_id)
        if predicate(job) if predicate else (project, job_id) not in manager.active:
            return job
        time.sleep(0.01)
    pytest.fail(f"Job did not reach expected state: {manager.get(project, job_id)}")


def manifest(request):
    records = list((Path(request["cwd"]) / ".agentagon/webapp/job-inputs").glob("*-context.json"))
    # Only one host owns a project; its current context is the last projection refreshed.
    return load_json(max(records, key=lambda path: path.stat().st_mtime_ns))


def audit_host(request, emit, ask, cancelled):
    emit(
        {
            "type": "session",
            "session_id": request.get("session_id") or "saved-session",
            "model": "actual-model",
        }
    )
    job = manifest(request)
    return {
        "state": "completed",
        "session_id": "saved-session",
        "text": json.dumps({"summary": "Inspected saved evidence", **job["workflow_ids"]}),
    }


def test_audit_idempotent_job_uses_actual_model_and_saved_evidence(manager_factory):
    manager, state, project = manager_factory(audit_host)
    command = payload()
    submitted = manager.submit(project, command)
    result = wait_for(manager, project, submitted["id"])
    assert result["state"] == "completed_with_limits"
    audit_id = result["workflow_ids"]["audit_id"]
    audit = state.workspace(project).read_audit(audit_id)
    assert audit["model"] == "actual-model"
    assert result["result"]["audit_id"] == audit_id
    assert manager.submit(project, command)["id"] == result["id"]
    assert len(state.workspace(project).audits()) == 1
    with pytest.raises(AuditError, match="different task settings"):
        manager.submit(project, {**command, "goal": "Changed goal"})
    assert "frozen_settings" not in result
    assert not (state.workspace(project).state / "webapp/jobs").exists()
    assert state.db.get_record(project, "jobs", result["id"])["result"] == result["result"]


def test_two_managers_share_atomic_job_idempotency_and_reject_stale_writes(manager_factory):
    manager, state, project = manager_factory(lambda *args: pytest.fail("no host should run"))
    manager.stopping = True
    state.workspace(project).initialize()
    other = JobManager(AppState(state.directory), manager.credentials, execute=manager.execute)
    other.stopping = True
    command = payload()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(manager.submit, project, command)
            two = pool.submit(other.submit, project, command)
            assert one.result()["id"] == two.result()["id"]
        assert len(manager.list(project)) == 1
        first = manager._read(project, one.result()["id"])
        stale = other._read(project, first["id"])
        first["messages"].append("Preserve this guidance")
        manager._write(first)
        stale["messages"].append("Must not overwrite another update")
        with pytest.raises(AuditError, match="changed"):
            other._write(stale)
        assert manager._read(project, first["id"])["messages"] == ["Preserve this guidance"]
    finally:
        other.close()


def test_failed_answer_commit_never_releases_host_and_success_is_durable(
    manager_factory, monkeypatch
):
    from agentagon.storage.metadata import MetadataTransaction

    manager, state, project = manager_factory(lambda *args: pytest.fail("no host should run"))
    manager.stopping = True
    submitted = manager.submit(project, payload())
    job = manager._read(project, submitted["id"])
    job.update(state="needs_input", question={"id": "question-one", "kind": "approval"})
    manager._write(job)
    key = (project, job["id"])
    manager.active[key] = threading.Event()
    command = {
        "operation_id": str(uuid.uuid4()),
        "question_id": "question-one",
        "answer": {"decision": "accept"},
    }
    original = MetadataTransaction.put_record

    def fail_write(*args, **kwargs):
        raise AuditError("simulated failed commit")

    monkeypatch.setattr(MetadataTransaction, "put_record", fail_write)
    with pytest.raises(AuditError, match="failed commit"):
        manager.control(project, job["id"], "reply", command)
    assert key not in manager.answers
    assert not manager.active[key].is_set()
    assert manager._read(project, job["id"])["answers"] == {}
    monkeypatch.setattr(MetadataTransaction, "put_record", original)
    accepted = manager.control(project, job["id"], "reply", command)
    saved = AppState(state.directory).db.get_record(project, "jobs", job["id"])
    assert saved["answers"]["question-one"]["answer"] == {"decision": "accept"}
    assert command["operation_id"] in saved["receipts"]
    assert "answers" not in accepted
    assert manager.control(project, job["id"], "reply", command)["revision"] == accepted["revision"]
    with pytest.raises(AuditError, match="already has an accepted"):
        manager.control(project, job["id"], "reply", {**command, "operation_id": str(uuid.uuid4())})


def test_context_and_completion_verification_are_preserved(manager_factory):
    manager, _, project = manager_factory(audit_host)
    observed = []

    def verify(workspace, job, result):
        observed.append((job["application_agent_id"], job["focus_id"], job["options"]))
        raise AuditError("Full suite still needs verification")

    manager.verify_result = verify
    command = payload(
        application_agent_id="agent_" + "a" * 24,
        focus_id="focus_" + "b" * 24,
        options={
            "investigation_plan": {"hypothesis": "Grounded quality issue"},
            "suite_manifest": {"version": 1},
        },
    )
    job = manager.submit(project, command)
    result = wait_for(manager, project, job["id"])
    assert result["state"] == "failed"
    assert result["next_action"] == "Full suite still needs verification"
    assert observed[0][:2] == (command["application_agent_id"], command["focus_id"])
    assert observed[0][2]["suite_manifest"] == {"version": 1}


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_saved_model_is_selected_per_host(manager_factory, agent):
    manager, state, project = manager_factory(lambda *args: pytest.fail("host should not run"))
    manager.stopping = True
    with state.locked() as settings:
        settings["agents"].update(
            default_agent="codex",
            models={"codex": "codex-choice", "claude": "claude-choice"},
        )
    selected = manager.submit(project, payload(agent=agent))
    assert selected["model"] == f"{agent}-choice"
    explicit = manager.submit(project, payload(agent=agent, model="one-off-choice"))
    assert explicit["model"] == "one-off-choice"
    host_default = manager.submit(project, payload(agent=agent, model=""))
    assert host_default["model"] == ""
    assert state.read()["agents"]["models"][agent] == f"{agent}-choice"
    with pytest.raises(AuditError, match="invalid coding agent or model"):
        manager.submit(project, payload(agent=agent, model=None))


def test_explicit_codex_default_reaches_managed_session(manager_factory):
    observed = []

    def host(request, emit, ask, cancelled):
        observed.append(request["model"])
        return audit_host(request, emit, ask, cancelled)

    manager, state, project = manager_factory(host)
    with state.locked() as settings:
        settings["agents"]["models"]["codex"] = "saved-model"
    submitted = manager.submit(project, payload(agent="codex", model=""))
    result = wait_for(manager, project, submitted["id"])
    assert result["state"] == "completed_with_limits"
    assert observed == [""]
    assert result["model"] == ""
    assert result["actual_model"] == "actual-model"


def test_raw_request_replay_preserves_original_frozen_context(manager_factory):
    manager, _, project = manager_factory(lambda *args: pytest.fail("host should not run"))
    manager.stopping = True
    original = payload()
    binding = digest(original)
    prepared = {**original, "options": {"investigation_plan": {"focus_version": 1}}}
    first = manager.submit(project, prepared, request_binding=binding)
    changed_context = {**original, "options": {"investigation_plan": {"focus_version": 2}}}
    replay = manager.submit(project, changed_context, request_binding=binding)
    assert replay == first
    assert manager.existing_submission(project, original["operation_id"], binding) == first
    assert replay["options"]["investigation_plan"]["focus_version"] == 1
    with pytest.raises(AuditError, match="different task settings"):
        manager.existing_submission(
            project, original["operation_id"], digest({**original, "goal": "Changed"})
        )
    assert "request_binding" not in first


def test_reflection_uses_same_capacity_then_resumes_saved_author(manager_factory, monkeypatch):
    from agentagon.webapp import agents

    author_requests = []
    reflection_requests = []
    handoff = {"owner_id": "run_" + "c" * 24, "request_id": "request_" + "d" * 24}

    def host(request, emit, ask, cancelled):
        author_requests.append(copy.deepcopy(request))
        emit({"type": "session", "session_id": "author-native", "model": "actual-model"})
        if len(author_requests) == 1:
            output = {"needs_reflection": handoff}
        else:
            assert request["session_id"] == "author-native"
            output = manifest(request)["workflow_ids"]
        return {"state": "completed", "session_id": "author-native", "text": json.dumps(output)}

    manager, _, project = manager_factory(host)

    def reflect(workspace, owner_id, request_id, **kwargs):
        reflection_requests.append((owner_id, request_id))
        assert len(manager.active) == 1
        assert kwargs["timeout_seconds"] <= 1800
        assert kwargs["forbidden_session_id"] == "author-native"
        kwargs["emit"]({"type": "reflection_session", "session_id": "reflection-native"})
        return {"state": "completed", "session_id": "reflection-native"}

    monkeypatch.setattr(agents, "run_reflection", reflect)
    monkeypatch.setattr(
        workflows,
        "reflection_handoffs",
        lambda workspace, job, text: (
            [json.loads(text)["needs_reflection"]] if "needs_reflection" in json.loads(text) else []
        ),
    )
    submitted = manager.submit(project, payload())
    finished = wait_for(manager, project, submitted["id"])
    assert finished["state"] == "completed_with_limits", finished.get("next_action")
    assert len(author_requests) == 2
    assert reflection_requests == [(handoff["owner_id"], handoff["request_id"])]
    assert finished["session_id"] == "author-native"
    assert finished["active_reflections"] == []
    assert next(iter(finished["reflection_tasks"].values()))["session_id"] == "reflection-native"


def test_missing_host_never_creates_an_audit(manager_factory):
    def unavailable(*args):
        raise AuditError("Install the coding host")

    manager, state, project = manager_factory(unavailable)
    job = manager.submit(project, payload())
    result = wait_for(manager, project, job["id"])
    assert result["state"] == "failed"
    assert result["next_action"] == "Install the coding host"
    assert state.workspace(project).audits() == []


def test_claude_environment_credentials_work_without_storing_secret(manager_factory, monkeypatch):
    from agentagon.webapp.providers import CredentialStore

    monkeypatch.setenv("ANTHROPIC_API_KEY", "never-persist-this-api-key")

    def host(request, emit, ask, cancelled):
        assert request["api_key"] == "never-persist-this-api-key"
        assert request["model"] == ""
        return audit_host(request, emit, ask, cancelled)

    manager, state, project = manager_factory(host)
    manager.credentials = CredentialStore()
    with state.locked() as settings:
        settings["agents"]["models"]["codex"] = "codex-specific-model"
    submitted = manager.submit(project, payload(agent="claude"))
    job = wait_for(manager, project, submitted["id"])
    assert job["state"] == "completed_with_limits"
    saved = manager._read(project, job["id"])
    assert saved["agent_settings"]["claude_api_key_ref"] == "env:ANTHROPIC_API_KEY"
    assert "never-persist-this-api-key" not in json.dumps(saved)


def test_resume_uses_repaired_claude_credentials_without_changing_session_settings(
    manager_factory, monkeypatch
):
    from agentagon.webapp.providers import CredentialStore

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls = []

    def host(request, emit, ask, cancelled):
        calls.append(request)
        return audit_host(request, emit, ask, cancelled)

    manager, state, project = manager_factory(host)
    manager.credentials = CredentialStore()
    submitted = manager.submit(project, payload(agent="claude", model="chosen-model"))
    failed = wait_for(manager, project, submitted["id"])
    assert failed["state"] == "failed"
    assert not calls
    ref = manager.credentials.set("repaired-key")
    with state.locked() as settings:
        settings["agents"]["claude_api_key_ref"] = ref
        settings["agents"]["models"]["claude"] = "different-later-default"
    manager.control(project, submitted["id"], "resume", {"operation_id": str(uuid.uuid4())})
    result = wait_for(manager, project, submitted["id"])
    assert result["state"] == "completed_with_limits"
    assert calls[0]["api_key"] == "repaired-key"
    assert calls[0]["model"] == "chosen-model"


def test_audit_request_recovers_crash_after_creation(workspace):
    options = dict(
        mode="code",
        source=None,
        project=None,
        start_time=None,
        end_time=None,
        limit=None,
        scopes=[],
        host="codex",
        model="actual-model",
        goal="Review",
        request_id="same-job",
    )
    first = operations.start(workspace, **options)
    (workspace.root / "app.py").write_text("print('changed after crash')\n")
    second = operations.start(workspace, **options)
    assert first["audit_id"] == second["audit_id"]
    assert len(workspace.audits()) == 1
    with pytest.raises(AuditError, match="different settings"):
        operations.start(workspace, **{**options, "goal": "Other work"})


def test_questions_show_actual_text_and_expire_without_late_resurrection(manager_factory):
    def waiting(request, emit, ask, cancelled):
        emit({"type": "session", "session_id": "waiting", "model": "actual-model"})
        ask(
            {
                "kind": "question",
                "questions": [{"id": "rule", "question": "Which answer is correct?"}],
            }
        )
        return {"state": "completed", "session_id": "waiting", "text": "{}"}

    manager, _, project = manager_factory(waiting)
    job = manager.submit(project, payload(options={"max_elapsed_seconds": 1}))
    blocked = wait_for(manager, project, job["id"], lambda record: record["state"] == "needs_input")
    assert blocked["question"]["text"] == "Which answer is correct?"
    result = wait_for(manager, project, job["id"])
    assert result["state"] == "failed"
    with pytest.raises(AuditError, match="no live unanswered"):
        manager.control(
            project,
            job["id"],
            "reply",
            {
                "operation_id": str(uuid.uuid4()),
                "question_id": blocked["question"]["id"],
                "answer": {"text": "A"},
            },
        )
    assert manager.get(project, job["id"])["state"] == "failed"


def test_pause_resume_preserves_session_and_audit(manager_factory):
    calls = []

    def host(request, emit, ask, cancelled):
        calls.append(copy.deepcopy(request))
        emit(
            {
                "type": "session",
                "session_id": request.get("session_id") or "author-one",
                "model": "actual-model",
            }
        )
        if len(calls) == 1:
            ask({"kind": "approval", "command": "python -m pytest"})
            return {"state": "cancelled", "session_id": "author-one", "text": ""}
        return {
            "state": "completed",
            "session_id": "author-one",
            "text": json.dumps(manifest(request)["workflow_ids"]),
        }

    manager, state, project = manager_factory(host)
    job = manager.submit(project, payload())
    blocked = wait_for(manager, project, job["id"], lambda record: record["state"] == "needs_input")
    assert "python -m pytest" in blocked["question"]["text"]
    manager.control(project, job["id"], "pause", {"operation_id": str(uuid.uuid4())})
    paused = wait_for(manager, project, job["id"])
    assert paused["state"] == "paused"
    manager.control(project, job["id"], "resume", {"operation_id": str(uuid.uuid4())})
    complete = wait_for(manager, project, job["id"])
    assert complete["state"] == "completed_with_limits"
    assert calls[1]["session_id"] == "author-one"
    assert complete["workflow_ids"] == paused["workflow_ids"]
    assert calls[1]["timeout_seconds"] < calls[0]["timeout_seconds"]
    assert len(state.workspace(project).audits()) == 1


def test_shared_capacity_project_serialization_and_cross_project_rejection(
    manager_factory, tmp_path
):
    def host(request, emit, ask, cancelled):
        emit({"type": "session", "session_id": str(uuid.uuid4()), "model": "actual-model"})
        ask({"kind": "approval", "command": "Run tests"})
        return {"state": "cancelled", "text": ""}

    manager, state, first = manager_factory(host)
    other = tmp_path / "other-project"
    other.mkdir()
    second = state.register(str(other))["id"]
    with state.locked() as data:
        data["agents"]["concurrency"] = 2
    one = manager.submit(first, payload())
    two = manager.submit(first, payload())
    three = manager.submit(second, payload())
    wait_for(manager, first, one["id"], lambda record: record["state"] == "needs_input")
    wait_for(manager, second, three["id"], lambda record: record["state"] == "needs_input")
    assert manager.get(first, two["id"])["state"] == "queued"
    assert len(manager.active) == 2
    with pytest.raises(AuditError, match="not found"):
        manager.get(second, one["id"])
    manager.control(first, one["id"], "cancel", {"operation_id": str(uuid.uuid4())})
    wait_for(manager, first, two["id"], lambda record: record["state"] == "needs_input")
    assert manager.get(second, three["id"])["state"] == "needs_input"


def test_restart_charges_elapsed_and_requires_explicit_resume(manager_factory):
    calls = []
    manager, state, project = manager_factory(lambda *args: calls.append(args))
    manager.stopping = True
    job = manager.submit(project, payload())
    saved = manager._read(project, job["id"])
    saved.update(
        state="running",
        session_id="saved-before-crash",
        attempt_started_at=(datetime.now(UTC) - timedelta(seconds=3)).isoformat(),
    )
    manager._write(saved)
    restarted = JobManager(state, manager.credentials, execute=lambda *args: calls.append(args))
    try:
        recovered = restarted.get(project, job["id"])
        assert recovered["state"] == "interrupted"
        assert recovered["session_id"] == "saved-before-crash"
        assert recovered["elapsed_seconds"] >= 3
        assert not calls
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "options",
    [
        {"audit_id": []},
        {"trace_snapshot_id": "../../file"},
        {"code_scopes": [None]},
        {"profile": []},
    ],
)
def test_malformed_references_fail_before_dispatch(manager_factory, options):
    manager, _, project = manager_factory(lambda *args: pytest.fail("host should not start"))
    with pytest.raises(AuditError):
        manager.submit(project, payload(options=options))


def test_private_config_freezes_selected_profile_and_bounds_native_cli(
    manager_factory, application, monkeypatch
):
    manager, state, project = manager_factory(audit_host, application.root)
    manager.stopping = True
    submitted = manager.submit(
        project,
        payload(
            "eval",
            options={
                "profile": "local",
                "max_trials": 4,
                "max_elapsed_seconds": 20,
                "trial_timeout_seconds": 2,
            },
        ),
    )
    saved = manager._read(project, submitted["id"])
    workflows.freeze_settings(application, saved)
    path = application.state / "webapp/job-inputs" / f"{saved['id']}-config.json"
    monkeypatch.setenv("AGENTAGON_CONFIG", str(path))
    limits = Config().profile(application.root, "local")["limits"]
    assert limits["max_trials"] == 4
    assert limits["max_elapsed_seconds"] == 20
    assert limits["trial_timeout_seconds"] == 2
    assert str(path) in workflows.prompt(application, saved)
    with pytest.raises(AuditError, match="profile not found"):
        Config().profile(application.root, "some-other-profile")


def test_nested_progress_json_does_not_replace_final_workflow_identity():
    record = {"run_id": "run_" + "a" * 24, "summary": {"summary": "Nested detail"}}
    assert (
        workflows.result_object("Some progress\n```json\n" + json.dumps(record) + "\n```") == record
    )


def validation_job(kind, **options):
    return {
        "kind": kind,
        "created_at": now(),
        "workflow_ids": {},
        "options": {
            "max_trials": 60,
            "max_elapsed_seconds": 600,
            "trial_timeout_seconds": 10,
            "profile": "local",
            **options,
        },
    }


@pytest.mark.parametrize("pause_review", [False, True])
def test_baseline_runs_distinct_managed_reviewer_and_resumes_author(
    manager_factory, application, specification, pause_review
):
    from support.experiments import executions, passing_review
    from test_baselines import frozen

    from agentagon.experiments import baselines
    from agentagon.experiments.host_bridge import HostBridge

    evaluation = frozen(application, specification)
    calls = []
    review_calls = 0

    def host(request, emit, ask, cancelled):
        nonlocal review_calls
        calls.append(copy.deepcopy(request))
        reviewing = request["sandbox"] == "read-only"
        session = "reviewer-native" if reviewing else "author-native"
        emit({"type": "session", "session_id": session, "model": "actual-model"})
        job = manifest(request)
        if reviewing:
            review_calls += 1
            if pause_review and review_calls == 1:
                ask({"kind": "question", "text": "Inspect the preserved review context?"})
                return {"state": "cancelled", "session_id": session, "text": ""}
            task = job["review_tasks"][job["active_review_id"]]
            review = passing_review({"review_template": task["template"]})
            return {
                "state": "completed",
                "session_id": session,
                "text": json.dumps({"review": review}),
            }
        baseline_id = job["workflow_ids"]["baseline_id"]
        result = baselines.advance(application, baseline_id)
        output = {"baseline_id": baseline_id}
        if result["state"] == "host_pending":
            owner = result["budget_id"]
            pending = HostBridge(application, owner).pending()[0]
            output["needs_review"] = {"owner_id": owner, "request_id": pending["request_id"]}
        return {"state": "completed", "session_id": session, "text": json.dumps(output)}

    manager, _, project = manager_factory(host, application.root)
    before = len(executions())
    job = manager.submit(
        project, payload("baseline", options={"evaluation_id": evaluation["evaluation_id"]})
    )
    if pause_review:
        pending = wait_for(
            manager, project, job["id"], lambda record: record["state"] == "needs_input"
        )
        review_id = pending["active_review_id"]
        assert pending["review_tasks"][review_id]["session_id"] == "reviewer-native"
        manager.control(project, job["id"], "pause", {"operation_id": str(uuid.uuid4())})
        wait_for(manager, project, job["id"])
        manager.control(project, job["id"], "resume", {"operation_id": str(uuid.uuid4())})
    result = wait_for(manager, project, job["id"], timeout=20)
    assert result["state"] == "completed", result.get("next_action")
    assert result["session_id"] == "author-native"
    assert result["result"]["evidence_state"] == "completed"
    review = next(iter(result["review_tasks"].values()))
    assert review["session_id"] == "reviewer-native"
    assert review["state"] == "completed"
    assert calls[-1]["session_id"] == "author-native"
    if pause_review:
        assert calls[-2]["session_id"] == "reviewer-native"
    assert len(executions()) - before == 3


def test_completed_optimizer_preserves_exact_verified_choice(application, specification):
    from test_optimize_run import _host, _start

    from agentagon.experiments.store import load_run

    job = validation_job("fix", engine="omni")
    run_id = _start(application, specification, optimizer="omni", target=0.62)
    finished = optimize_run.advance(application, run_id, host_handler=_host)
    assert finished["state"] == "completed"
    assert load_run(application, run_id)["state"] == "active"
    # The core fake host represents a reviewer whose native binding the app has saved.
    job["review_tasks"] = {
        "review-fixture": {
            "state": "completed",
            "session_id": "independent-reviewer",
            "run_id": run_id,
        }
    }
    state, result, action = workflows.validate_result(
        application, job, {"text": json.dumps({"run_id": run_id})}
    )
    assert state == "completed"
    assert result["comparison"] == finished["selection"]
    assert action is None
    with pytest.raises(AuditError, match="accepted max_trials"):
        workflows.validate_result(
            application,
            {**job, "options": {**job["options"], "max_trials": 1}},
            {"text": json.dumps({"run_id": run_id})},
        )


def test_fix_runs_managed_reviewers_for_baseline_candidate_and_final_choice(
    manager_factory, application, specification
):
    from support.experiments import executions
    from test_optimize_run import _host
    from test_scoring import definition

    from agentagon.experiments import engine
    from agentagon.experiments.host_bridge import HostBridge

    calls = []

    def host(request, emit, ask, cancelled):
        reviewing = request["sandbox"] == "read-only"
        before = manifest(request)
        session = "reviewer-" + before["active_review_id"] if reviewing else "author-native"
        calls.append(
            {"reviewing": reviewing, "requested": request["session_id"], "actual": session}
        )
        emit({"type": "session", "session_id": session, "model": "actual-model"})
        job = manifest(request)
        if reviewing:
            task = job["review_tasks"][job["active_review_id"]]
            review = _host({"role": "review", "payload": {"review_template": task["template"]}})
            return {"state": "completed", "session_id": session, "text": json.dumps(review)}
        run_id = job["workflow_ids"].get("run_id")
        if not run_id:
            spec = copy.deepcopy(specification)
            spec.update(scoring={**definition(), "target": 0.62}, repetitions=1, seeds=[0])
            run_id = engine.start(
                application, spec, "local", execution_profile=job["execution_profile"]
            )["run_id"]
            optimize_run.configure(
                application, run_id, host="codex", model="actual-model", optimizer="omni"
            )
        while True:
            result = optimize_run.advance(application, run_id)
            output = {"run_id": run_id}
            if not result["pending"]:
                break
            pending = result["pending"][0]
            if pending["role"] == "review":
                output["needs_review"] = {"owner_id": run_id, "request_id": pending["request_id"]}
                # A rejected scope cannot enter an independent reviewer, even when
                # the immutable evaluator allows a broader application surface.
                if len(calls) > 2:
                    with pytest.raises(AuditError, match="permitted paths"):
                        workflows.review_handoff(
                            application,
                            {**job, "options": {**job["options"], "permitted_paths": ["tool.sh"]}},
                            json.dumps(output),
                        )
                break
            bridge = HostBridge(application, run_id)
            bridge.start(pending["request_id"])
            bridge.reply(
                pending["request_id"],
                _host(pending),
                host=pending["host"],
                model=pending["model"],
                binding_digest=pending["binding_digest"],
            )
        return {"state": "completed", "session_id": session, "text": json.dumps(output)}

    manager, _, project = manager_factory(host, application.root)
    before = len(executions())
    submitted = manager.submit(project, payload("fix", options={"permitted_paths": ["app.json"]}))
    job = wait_for(manager, project, submitted["id"], timeout=30)
    assert job["state"] == "completed", job.get("next_action")
    assert len(executions()) - before == 3
    assert job["result"]["comparison"]["winner"]["score"]["target_reached"]
    reviews = list(job["review_tasks"].values())
    assert len(reviews) == 3
    assert all(review["state"] == "completed" for review in reviews)
    assert len({review["session_id"] for review in reviews}) == 3
    assert all(call["requested"] == "author-native" for call in calls[1:] if not call["reviewing"])
    assert all(call["requested"] is None for call in calls if call["reviewing"])
    with pytest.raises(AuditError, match="permitted paths"):
        workflows.validate_result(
            application,
            {**job, "options": {**job["options"], "permitted_paths": ["tool.sh"]}},
            {"text": json.dumps(job["result"])},
        )


def test_suite_children_preserve_primary_job_identity_across_restart(
    manager_factory, application, specification
):
    from support.experiments import executions, passing_review
    from test_suites import _host, _suite

    from agentagon.experiments import engine, preparation, suites
    from agentagon.experiments.host_bridge import HostBridge

    # Existing reviewed measurements predate the job; its primary Fix is created by its author.
    _, suite_manifest = _suite(application, specification)
    primary = None
    paused_child = None
    calls = []

    def host(request, emit, ask, cancelled):
        nonlocal primary, paused_child
        context = manifest(request)
        reviewing = request["sandbox"] == "read-only"
        task = context["review_tasks"][context["active_review_id"]] if reviewing else None
        session = "reviewer-" + context["active_review_id"] if reviewing else "author-native"
        calls.append(
            {"requested": request["session_id"], "session": session, "task": copy.deepcopy(task)}
        )
        emit({"type": "session", "session_id": session, "model": "actual-model"})
        context = manifest(request)
        if reviewing:
            assert context["workflow_ids"]["run_id"] == primary
            if task["source"].get("execution_run_id") and paused_child is None:
                paused_child = task["run_id"]
                ask({"kind": "question", "text": "Pause while inspecting this exact suite child."})
                return {"state": "cancelled", "session_id": session, "text": ""}
            return {
                "state": "completed",
                "session_id": session,
                "text": json.dumps(
                    {"review": passing_review({"review_template": task["template"]})}
                ),
            }
        run_id = context["workflow_ids"].get("run_id")
        if run_id is None:
            run_id = engine.start(
                application,
                preparation.fix_spec(
                    application, suite_manifest["members"][-1]["evaluation_id"], reuse=True
                ),
                "local",
                execution_profile=context["execution_profile"],
            )["run_id"]
            primary = run_id
            suites.bind(application, run_id, suite_manifest, finalist_count=1)
            optimize_run.configure(
                application,
                run_id,
                host="codex",
                model="actual-model",
                optimizer="gepa",
                max_trials=10,
            )
        assert run_id == primary
        bridge = HostBridge(application, run_id)
        for _ in range(20):
            result = optimize_run.advance(application, run_id)
            pending = bridge.pending()
            if not pending:
                assert result["state"] == "completed", result
                output = {"run_id": run_id}
                break
            work = pending[0]
            if work["role"] == "review":
                output = {
                    "run_id": run_id,
                    "needs_review": {"owner_id": run_id, "request_id": work["request_id"]},
                }
                break
            bridge.fulfill(work, _host())
        else:
            pytest.fail("author did not reach a review handoff or completion")
        return {"state": "completed", "session_id": session, "text": json.dumps(output)}

    manager, state, project = manager_factory(host, application.root)
    before = len(executions())
    submitted = manager.submit(
        project,
        payload(
            "fix",
            options={
                "engine": "gepa",
                "finalist_count": 1,
                "suite_manifest": suite_manifest,
                "permitted_paths": ["app.json"],
                "evaluation_id": suite_manifest["members"][-1]["evaluation_id"],
                "max_trials": 60,
            },
        ),
    )
    paused = wait_for(
        manager,
        project,
        submitted["id"],
        lambda job: job["state"] in {"needs_input", "failed"},
        timeout=40,
    )
    assert paused["state"] == "needs_input", paused.get("next_action")
    assert paused["workflow_ids"]["run_id"] == primary and paused_child != primary
    review_id = paused["active_review_id"]
    native_session = paused["review_tasks"][review_id]["session_id"]
    trials_at_pause = len(executions())
    manager.close()
    calls_at_restart = len(calls)
    resumed = JobManager(AppState(state.directory), manager.credentials, execute=host)
    try:
        recovered = resumed.get(project, submitted["id"])
        assert recovered["state"] == "interrupted"
        assert recovered["workflow_ids"]["run_id"] == primary
        assert recovered["active_review_id"] == review_id
        assert len(calls) == calls_at_restart and len(executions()) == trials_at_pause
        resumed.control(project, submitted["id"], "resume", {"operation_id": str(uuid.uuid4())})
        finished = wait_for(resumed, project, submitted["id"], timeout=40)
        assert finished["state"] == "completed", finished.get("next_action")
        assert finished["workflow_ids"]["run_id"] == primary == finished["result"]["run_id"]
        assert finished["result"]["suite"]["passed"]
        assert calls[calls_at_restart]["requested"] == native_session
        assert len(executions()) - before == 7
        child_reviews = [
            task for task in finished["review_tasks"].values() if task["run_id"] != primary
        ]
        assert len({task["run_id"] for task in child_reviews}) == 4
        assert all(
            task["workflow_run_id"] == primary and task["state"] == "completed"
            for task in child_reviews
        )
        assert all(
            call["requested"] == "author-native" for call in calls[1:] if call["task"] is None
        )
    finally:
        resumed.close()


def test_explicit_frozen_evaluation_reuse_keeps_prior_verified_review(application, specification):
    from support.experiments import executions
    from test_baselines import frozen

    evaluated = frozen(application, specification)
    before = len(executions())
    job = validation_job("eval", evaluation_id=evaluated["evaluation_id"], max_trials=1)
    state, result, action = workflows.validate_result(
        application, job, {"text": json.dumps({"evaluation_id": evaluated["evaluation_id"]})}
    )
    assert state == "completed"
    assert result["evaluation_id"] == evaluated["evaluation_id"]
    assert action is None
    assert len(executions()) == before


@pytest.mark.parametrize("review_outcome", ["pass", "author_session", "reject", "missing"])
def test_evaluation_requires_actual_separate_review_session(
    manager_factory, application, specification, review_outcome
):
    from support.evaluation import review_for

    from agentagon.experiments import preparation

    missing_evidence = review_outcome == "missing"
    review_requests = []

    def host(request, emit, ask, cancelled):
        reviewing = request["sandbox"] == "read-only"
        session = (
            "reviewer-session"
            if reviewing and review_outcome != "author_session"
            else "author-session"
        )
        emit({"type": "session", "session_id": session, "model": "actual-model"})
        job = manifest(request)
        if reviewing:
            review_requests.append(request["session_id"])
            task = job["review_tasks"][job["active_review_id"]]
            review = review_for({"checks": [{"review_template": task["template"]}]})
            if review_outcome == "reject":
                review.update(verdict="fail", rationale="The expected quality rule needs repair.")
            return {
                "state": "completed",
                "session_id": session,
                "text": json.dumps(
                    {"needs_input": "Provide the missing coverage evidence."}
                    if missing_evidence
                    else {"review": review}
                ),
            }
        evaluation_id = job["workflow_ids"].get("evaluation_id")
        if not evaluation_id:
            draft = preparation.start(
                application,
                "local",
                {
                    key: job["options"][key]
                    for key in ("max_trials", "max_elapsed_seconds", "trial_timeout_seconds")
                },
                goal=job["goal"],
                author=session,
            )
            evaluation_id = draft["evaluation_id"]
            negative = application.state / "wrong-example.json"
            negative.write_text(
                json.dumps({"latency": 100, "quality": 0.1, "variant": "incorrect"})
            )
            plan = {
                "spec": copy.deepcopy(specification),
                "provenance": "Synthetic fixture has known quality correctness rule.",
                "coverage": {
                    "status": "limited",
                    "rationale": "Synthetic fixture only.",
                    "holdout_paths": [],
                },
                "negative_cases": [
                    {
                        "id": "wrong",
                        "description": "Fail quality floor",
                        "mutations": [
                            {
                                "path": "app.json",
                                "source": str(negative.relative_to(application.root)),
                            }
                        ],
                        "expected_checks": ["quality-control"],
                    }
                ],
            }
            preparation.check(application, evaluation_id, plan)
            output = {
                "evaluation_id": evaluation_id,
                "needs_review": {"evaluation_id": evaluation_id},
            }
        else:
            assert preparation.load(application, evaluation_id)["state"] == (
                "awaiting_review" if review_outcome == "reject" else "frozen"
            )
            output = {"evaluation_id": evaluation_id}
            if review_outcome == "reject":
                output["needs_input"] = (
                    "Review found an unsupported expectation. Author needs guidance."
                )
        return {"state": "completed", "session_id": session, "text": json.dumps(output)}

    manager, _, project = manager_factory(host, application.root)
    submitted = manager.submit(project, payload("eval", goal=specification["goal"]))
    job = wait_for(manager, project, submitted["id"], timeout=20)
    if review_outcome == "author_session":
        assert job["state"] == "failed"
        assert "different native session" in job["next_action"]
    elif review_outcome in {"reject", "missing"}:
        assert job["state"] == "needs_input", job.get("next_action")
        task = next(iter(job["review_tasks"].values()))
        assert task["state"] == ("rejected" if review_outcome == "reject" else "needs_input")
        assert bool(job["active_review_id"]) == (review_outcome == "missing")
        if review_outcome == "reject":
            assert task["response"]["review"]["verdict"] == "fail"
        else:
            assert "response" not in task
            assert task["session_id"] == "reviewer-session"
            missing_evidence = False
            manager.control(project, job["id"], "resume", {"operation_id": str(uuid.uuid4())})
            resumed = wait_for(manager, project, job["id"], timeout=20)
            assert resumed["state"] == "completed", resumed.get("next_action")
            assert review_requests == [None, "reviewer-session"]
    else:
        assert job["state"] == "completed", job.get("next_action")
        evaluation = preparation.load(application, job["result"]["evaluation_id"])
        assert evaluation["review"]["reviewer"] == "reviewer-session"
        assert evaluation["author"] == "author-session"
        forged_job = manager._read(project, job["id"])
        forged_job["review_tasks"] = {}
        with pytest.raises(AuditError, match="application-managed reviewer"):
            workflows.validate_result(application, forged_job, {"text": json.dumps(job["result"])})


def test_parallel_reflections_reserve_capacity_and_queue_distinct_questions(
    manager_factory, monkeypatch, tmp_path
):
    from agentagon.experiments.budget import BudgetLedger
    from agentagon.experiments.host_bridge import HostBridge

    author_calls, native_calls, answers = [], [], {}
    barrier = threading.Barrier(2)
    first_root = tmp_path / "batched-project"

    def host(request, emit, ask, cancelled):
        if request.get("response_mode") == "raw-final":
            name = request["prompt"]
            native_calls.append(name)
            emit({"type": "session", "session_id": "native-" + name, "model": "actual-model"})
            barrier.wait(timeout=3)  # A serial dispatcher cannot fulfill this batch.
            answers[name] = ask({"kind": "question", "text": name})
            return {
                "state": "completed",
                "session_id": "native-" + name,
                "raw_final_text": "```\n" + name + "\n```",
            }
        emit(
            {
                "type": "session",
                "session_id": request.get("session_id") or "author-" + Path(request["cwd"]).name,
                "model": "actual-model",
            }
        )
        if Path(request["cwd"]) == first_root:
            author_calls.append(request["session_id"])
            if len(author_calls) == 1:
                from agentagon.storage.workspace import Workspace

                workspace = Workspace(first_root)
                owner = "run_" + "d" * 24
                BudgetLedger(workspace, owner).create(20, 100)
                bridge = HostBridge(workspace, owner)
                batch = []
                for index in range(2):
                    pending = bridge.request(
                        f"gepa:reflection:{index}",
                        source="commit",
                        evaluator="eval",
                        role="proposal",
                        scope=["app.py"],
                        host="codex",
                        model="actual-model",
                        payload={
                            "protocol": "gepa-reflection-v1",
                            "engine": "gepa",
                            "stage": "gepa",
                            "prompt": f"prompt-{index}",
                        },
                    )
                    batch.append({"owner_id": owner, "request_id": pending["request_id"]})
                return {
                    "state": "completed",
                    "session_id": "author-batched-project",
                    "text": json.dumps({"needs_reflection": batch}),
                }
        return {
            "state": "completed",
            "session_id": "author-" + Path(request["cwd"]).name,
            "text": json.dumps(manifest(request)["workflow_ids"]),
        }

    monkeypatch.setattr(
        workflows,
        "reflection_handoffs",
        lambda workspace, job, text: json.loads(text).get("needs_reflection", []),
    )
    manager, state, project = manager_factory(host, first_root)
    other_root = tmp_path / "queued-project"
    other_root.mkdir()
    other = state.register(str(other_root))["id"]
    with state.locked() as settings:
        settings["agents"]["concurrency"] = 2
    submitted = manager.submit(project, payload(options={"host_concurrency": 2}))
    wait_for(manager, project, submitted["id"], lambda job: job["state"] == "needs_input")
    queued = manager.submit(other, payload())
    assert manager.get(other, queued["id"])["state"] == "queued"
    assert manager.slots[(project, submitted["id"])] == 2
    seen = set()
    for _ in range(2):
        question = wait_for(
            manager,
            project,
            submitted["id"],
            lambda job: job["state"] == "needs_input" and job["question"]["id"] not in seen,
        )
        seen.add(question["question"]["id"])
        manager.control(
            project,
            submitted["id"],
            "reply",
            {
                "operation_id": str(uuid.uuid4()),
                "question_id": question["question"]["id"],
                "answer": {"text": question["question"]["text"]},
            },
        )
    finished = wait_for(manager, project, submitted["id"])
    assert finished["state"] == "completed_with_limits", finished
    assert sorted(native_calls) == ["prompt-0", "prompt-1"]
    assert answers == {name: {"text": name} for name in native_calls}
    assert len(author_calls) == 2 and author_calls[1] == "author-batched-project"
    assert len(finished["reflection_tasks"]) == 2 and finished["active_reflections"] == []
    assert wait_for(manager, other, queued["id"])["state"] == "completed_with_limits"


def test_reflection_batch_restart_keeps_completed_sibling_and_native_identity(
    manager_factory, monkeypatch
):
    from agentagon.experiments.budget import BudgetLedger
    from agentagon.experiments.host_bridge import HostBridge

    sessions, author_calls = [], []
    first_done = threading.Event()
    interrupted = False
    batch = []

    def host(request, emit, ask, cancelled):
        nonlocal interrupted
        if request.get("response_mode") == "raw-final":
            name = request["prompt"]
            sessions.append((name, request["session_id"]))
            emit({"type": "session", "session_id": "native-" + name, "model": "actual-model"})
            if name == "first":
                first_done.set()
            elif not request["session_id"]:
                assert first_done.wait(3)
                ask({"kind": "question", "text": "Pause this batch"})
                interrupted = True
                return {"state": "interrupted", "session_id": "native-second"}
            return {
                "state": "completed",
                "session_id": "native-" + name,
                "raw_final_text": "```\n" + name + "\n```",
            }
        author_calls.append(request["session_id"])
        emit({"type": "session", "session_id": "author", "model": "actual-model"})
        if len(author_calls) == 1:
            workspace = state.workspace(project)
            owner = "run_" + "e" * 24
            BudgetLedger(workspace, owner).create(20, 100)
            bridge = HostBridge(workspace, owner)
            for name in ("first", "second"):
                pending = bridge.request(
                    name,
                    source="source",
                    evaluator="eval",
                    role="proposal",
                    scope=["app.py"],
                    host="codex",
                    model="actual-model",
                    payload={
                        "protocol": "gepa-reflection-v1",
                        "engine": "gepa",
                        "stage": "gepa",
                        "prompt": name,
                    },
                )
                batch.append({"owner_id": owner, "request_id": pending["request_id"]})
            return {
                "state": "completed",
                "session_id": "author",
                "text": json.dumps({"needs_reflection": batch}),
            }
        return {
            "state": "completed",
            "session_id": "author",
            "text": json.dumps(manifest(request)["workflow_ids"]),
        }

    manager, state, project = manager_factory(host)
    with state.locked() as settings:
        settings["agents"]["concurrency"] = 2
    monkeypatch.setattr(
        workflows,
        "reflection_handoffs",
        lambda workspace, job, text: json.loads(text).get("needs_reflection", []),
    )
    job = manager.submit(project, payload(options={"host_concurrency": 2}))
    wait_for(
        manager,
        project,
        job["id"],
        lambda saved: saved["state"] == "needs_input" and len(saved["reflection_tasks"]) == 1,
    )
    manager.close()
    saved = wait_for(manager, project, job["id"])
    assert interrupted and len(saved["active_reflections"]) == 1
    with state.locked() as settings:
        settings["agents"]["concurrency"] = 1
    resumed = JobManager(AppState(state.directory), manager.credentials, execute=host)
    try:
        resumed.control(project, job["id"], "resume", {"operation_id": str(uuid.uuid4())})
        queued = resumed.get(project, job["id"])
        assert queued["state"] == "queued" and "at least 2" in queued["next_action"]
        assert len(sessions) == 2
        with state.locked() as settings:
            settings["agents"]["concurrency"] = 2
        with resumed.condition:
            resumed._dispatch()
        final = wait_for(resumed, project, job["id"])
        assert final["state"] == "completed_with_limits", final
        assert sessions.count(("first", None)) == 1
        assert ("second", None) in sessions and ("second", "native-second") in sessions
        assert len(sessions) == 3 and author_calls == [None, "author"]
        assert len(final["reflection_tasks"]) == 2 and not final["active_reflections"]
    finally:
        resumed.close()


def test_managed_gepa_batch_completes_two_real_finalists(
    manager_factory, application, specification
):
    from support.experiments import executions, passing_review
    from support.optimizer import candidate_text
    from test_scoring import definition

    from agentagon.experiments import engine
    from agentagon.experiments.orchestration import DEFAULT_SETTINGS

    profile = Config().profile(application.root, "local")
    profile["runner"]["independent_capacity"] = True
    profile["limits"].update(parallel_candidates=2, parallel_trials=2, max_candidates=12)
    profile["orchestration"] = {**DEFAULT_SETTINGS, "host_capacity": 2, "resource_slots": 2}
    Config().update_profile("project", "local", profile, application.root)
    barrier = threading.Barrier(2)
    native = []
    native_lock = threading.Lock()

    def host(request, emit, ask, cancelled):
        if request.get("response_mode") == "raw-final":
            with native_lock:
                index = len(native)
                native.append(request["prompt"])
            emit({"type": "session", "session_id": f"reflection-{index}", "model": "actual-model"})
            barrier.wait(timeout=4)
            proposal = json.loads(
                candidate_text(
                    {"payload": {"protocol": "gepa-reflection-v1", "prompt": request["prompt"]}}
                )
            )
            proposal["files"]["app.json"] = json.dumps(
                {"latency": 80 + index, "quality": 0.9, "variant": f"batch-{index}"}
            )
            return {
                "state": "completed",
                "session_id": f"reflection-{index}",
                "raw_final_text": "```\n" + json.dumps(proposal) + "\n```",
            }
        context = manifest(request)
        task = context["review_tasks"].get(context["active_review_id"])
        session = "reviewer-" + task["id"] if task else "author-native"
        emit({"type": "session", "session_id": session, "model": "actual-model"})
        if task:
            return {
                "state": "completed",
                "session_id": session,
                "text": json.dumps(
                    {"review": passing_review({"review_template": task["template"]})}
                ),
            }
        context = manifest(request)
        run_id = context["workflow_ids"].get("run_id")
        if run_id is None:
            spec = copy.deepcopy(specification)
            spec.update(scoring={**definition(), "target": 0.7}, repetitions=1, seeds=[0])
            run_id = engine.start(
                application, spec, "local", execution_profile=context["execution_profile"]
            )["run_id"]
            optimize_run.configure(
                application,
                run_id,
                host="codex",
                model="actual-model",
                optimizer="gepa",
                finalist_count=2,
                host_concurrency=context["host_slots"],
            )
        progress = optimize_run.advance(application, run_id)
        result = {"run_id": run_id}
        if progress["pending"]:
            pending = progress["pending"][0]
            result["needs_reflection" if pending["role"] == "proposal" else "needs_review"] = {
                "owner_id": run_id,
                "request_id": pending["request_id"],
            }
        return {"state": "completed", "session_id": session, "text": json.dumps(result)}

    manager, state, project = manager_factory(host, application.root)
    with state.locked() as settings:
        settings["agents"]["concurrency"] = 2
    submitted = manager.submit(
        project,
        payload(
            "fix",
            options={
                "engine": "gepa",
                "host_concurrency": 2,
                "finalist_count": 2,
                "permitted_paths": ["app.json"],
            },
        ),
    )
    finished = wait_for(manager, project, submitted["id"], timeout=40)
    assert finished["state"] == "completed", finished
    assert len(native) == len(finished["reflection_tasks"]) == 2
    optimized = optimize_run.status(application, finished["workflow_ids"]["run_id"])
    assert optimized["selection"]["winner"] and len(optimized["selection"]["alternatives"]) == 1
    assert len(executions()) == 5  # Baseline, two candidates, two independent final measurements.
    assert len(finished["review_tasks"]) == 5
