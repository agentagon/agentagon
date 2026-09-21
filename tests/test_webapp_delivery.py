"""Verified delivery stays local until the user explicitly publishes it."""

import threading
import uuid

import httpx
import pytest
from support.delivery import creates, pushes
from support.evaluation import draft, review_for

from agentagon.capabilities.experiments import preparation
from agentagon.core.records import AuditError
from agentagon.dashboard.server import create_server
from agentagon.workflows.service import Application


@pytest.fixture
def app(tmp_path):
    value = Application(tmp_path / "webapp", execute=lambda *_: {})
    yield value
    value.close()


def test_prepare_download_and_explicit_publish(app, selected, github, tmp_path):
    project = app.register(str(selected["workspace"].root))
    payload = {"kind": "optimize", "source_id": selected["run_id"], "publish": False}
    result = app.result(project["id"], "optimize", selected["run_id"])
    assert result["selection"]["recommended_candidate_id"] == selected["candidate_id"]
    assert result["selection"]["decision"] is None
    with pytest.raises(AuditError, match="choose a verified candidate"):
        app.deliver(project["id"], payload)
    decision_request = {
        "operation_id": str(uuid.uuid4()),
        "expected_revision": 0,
        "decision": "select_candidate",
        "candidate_id": selected["candidate_id"],
    }
    decision = app.decide_result(project["id"], "optimize", selected["run_id"], decision_request)
    assert decision["current"] is True and decision["revision"] == 1
    assert (
        app.decide_result(project["id"], "optimize", selected["run_id"], decision_request)
        == decision
    )
    with pytest.raises(AuditError, match="prepare the local"):
        app.deliver(
            project["id"],
            {**payload, "publish": True, "remote": "origin", "base": selected["base"]},
        )
    prepared = app.deliver(project["id"], payload)
    assert prepared["state"] == "prepared"
    assert prepared["user_decision_id"] == decision["id"]
    retained = app.result(project["id"], "optimize", selected["run_id"])
    assert retained["deliveries"][0]["artifact_urls"]["diff"].startswith(
        f"/api/projects/{project['id']}/deliveries/"
    )
    assert retained["deliveries"][0]["user_decision_id"] == decision["id"]
    assert not creates(github) and not pushes(github)
    assert app.delivery_artifact(project["id"], prepared["delivery_id"], "diff")
    assert prepared["artifact_urls"]["diff"].startswith(f"/api/projects/{project['id']}/")
    other = tmp_path / "other"
    other.mkdir()
    other_project = app.register(str(other))
    with pytest.raises(AuditError, match="not found"):
        app.delivery_artifact(other_project["id"], prepared["delivery_id"], "diff")
    published = app.deliver(
        project["id"],
        {
            **payload,
            "publish": True,
            "delivery_id": prepared["delivery_id"],
            "remote": "origin",
            "base": selected["base"],
        },
    )
    assert published["state"] == "published"
    assert len(creates(github)) == 1
    app.deliver(
        project["id"],
        {
            **payload,
            "publish": True,
            "delivery_id": prepared["delivery_id"],
            "remote": "origin",
            "base": selected["base"],
        },
    )
    assert len(creates(github)) == 1


def test_evaluation_delivery_artifacts_survive_service_restart(
    application, specification, tmp_path
):
    started, plan = draft(application, specification)
    source = application.root / started["worktree"] / "checks.py"
    source.write_text(source.read_text() + "\n# Reviewed evaluator update\n")
    checked = preparation.check(application, started["evaluation_id"], plan)
    frozen = preparation.freeze(application, started["evaluation_id"], review_for(checked))
    state = tmp_path / "application-state"
    first = Application(state, execute=lambda *_: {})
    project = first.register(str(application.root))
    prepared = first.deliver(
        project["id"],
        {"kind": "eval", "source_id": frozen["evaluation_id"], "publish": False},
    )
    first.close()

    reopened = Application(state, execute=lambda *_: {})
    try:
        shown = reopened.result(project["id"], "eval", frozen["evaluation_id"])
        retained = shown["deliveries"][0]
        assert retained["delivery_id"] == prepared["delivery_id"]
        assert set(retained["artifact_urls"]) == {"summary", "pr_body", "diff", "diffstat"}
        assert reopened.delivery_artifact(project["id"], prepared["delivery_id"], "summary")
    finally:
        reopened.close()


def test_keep_current_is_durable_and_cannot_authorize_delivery(app, selected):
    project = app.register(str(selected["workspace"].root))
    operation = str(uuid.uuid4())
    request = {
        "operation_id": operation,
        "expected_revision": 0,
        "decision": "keep_current",
    }
    receipt = app.decide_result(project["id"], "optimize", selected["run_id"], request)
    assert receipt["decision"] == "keep_current" and receipt["candidate_id"] is None
    assert receipt["current"] is True
    with pytest.raises(AuditError, match="choose a verified candidate"):
        app.deliver(
            project["id"],
            {"kind": "optimize", "source_id": selected["run_id"], "publish": False},
        )
    with pytest.raises(AuditError, match="different result decision"):
        app.decide_result(
            project["id"],
            "optimize",
            selected["run_id"],
            {**request, "decision": "select_candidate", "candidate_id": selected["candidate_id"]},
        )


def test_optimize_result_exposes_and_accepts_each_verified_alternative(app, selected):
    from support.experiments import propose, verify

    project = app.register(str(selected["workspace"].root))
    created, _ = propose(
        selected["workspace"],
        selected["run_id"],
        latency=90,
        quality=0.9,
        variant="quality-alternative",
    )
    alternative = verify(
        selected["workspace"], selected["run_id"], created["candidate"]["candidate_id"]
    )["candidate"]

    shown = app.result(project["id"], "optimize", selected["run_id"])
    assert {item["id"] for item in shown["comparisons"]["alternatives"]} == {
        selected["candidate_id"],
        alternative["candidate_id"],
    }
    decision = app.decide_result(
        project["id"],
        "optimize",
        selected["run_id"],
        {
            "operation_id": str(uuid.uuid4()),
            "expected_revision": shown["selection"]["expected_revision"],
            "decision": "select_candidate",
            "candidate_id": alternative["candidate_id"],
        },
    )
    assert decision["candidate_id"] == alternative["candidate_id"]
    assert decision["current"] is True


def test_delivery_history_retains_exact_decision_candidate_and_revision(app, selected):
    from support.experiments import propose, verify

    project = app.register(str(selected["workspace"].root))
    first = app.decide_result(
        project["id"],
        "optimize",
        selected["run_id"],
        {
            "operation_id": str(uuid.uuid4()),
            "expected_revision": 0,
            "decision": "select_candidate",
            "candidate_id": selected["candidate_id"],
        },
    )
    first_delivery = app.deliver(
        project["id"],
        {"kind": "optimize", "source_id": selected["run_id"], "publish": False},
    )
    created, _ = propose(
        selected["workspace"],
        selected["run_id"],
        latency=90,
        quality=0.9,
        variant="later-choice",
    )
    alternative = verify(
        selected["workspace"], selected["run_id"], created["candidate"]["candidate_id"]
    )["candidate"]
    second = app.decide_result(
        project["id"],
        "optimize",
        selected["run_id"],
        {
            "operation_id": str(uuid.uuid4()),
            "expected_revision": first["revision"],
            "decision": "select_candidate",
            "candidate_id": alternative["candidate_id"],
        },
    )
    second_delivery = app.deliver(
        project["id"],
        {"kind": "optimize", "source_id": selected["run_id"], "publish": False},
    )

    shown = app.result(project["id"], "optimize", selected["run_id"])
    deliveries = {item["delivery_id"]: item for item in shown["deliveries"]}
    retained_first = deliveries[first_delivery["delivery_id"]]
    retained_second = deliveries[second_delivery["delivery_id"]]
    assert retained_first["candidate_id"] == selected["candidate_id"]
    assert retained_first["source_revision"] == first["source_revision"]
    assert retained_first["user_decision_id"] == first["id"]
    assert retained_first["user_decision_revision"] == first["revision"]
    assert retained_second["candidate_id"] == alternative["candidate_id"]
    assert retained_second["source_revision"] == second["source_revision"]
    assert retained_second["user_decision_id"] == second["id"]
    assert retained_second["user_decision_revision"] == second["revision"]
    assert shown["selection"]["decision"]["candidate_id"] == alternative["candidate_id"]
    assert shown["selection"]["decision"]["revision"] == second["revision"]


def test_interrupted_decision_resumes_the_retained_operation(app, selected, monkeypatch):
    from agentagon.capabilities.experiments import engine

    project = app.register(str(selected["workspace"].root))
    request = {
        "operation_id": str(uuid.uuid4()),
        "expected_revision": 0,
        "decision": "select_candidate",
        "candidate_id": selected["candidate_id"],
    }
    original = engine.select
    monkeypatch.setattr(engine, "select", lambda *_: (_ for _ in ()).throw(OSError("stopped")))
    with pytest.raises(OSError, match="stopped"):
        app.decide_result(project["id"], "optimize", selected["run_id"], request)

    selection = app.result(project["id"], "optimize", selected["run_id"])["selection"]
    assert selection["decision_operation"] == {
        "operation_id": request["operation_id"],
        "state": "pending",
        "persisted_state": "pending",
        "expected_revision": 0,
        "decision": "select_candidate",
        "candidate_id": selected["candidate_id"],
        "current": True,
        "next_action": "retry_pending_decision",
        "reason": None,
        "updated_at": selection["decision_operation"]["updated_at"],
    }
    assert selection["allowed_actions"] == ["retry_pending_decision"]
    with pytest.raises(AuditError, match="resume its saved operation"):
        app.decide_result(
            project["id"],
            "optimize",
            selected["run_id"],
            {**request, "operation_id": str(uuid.uuid4())},
        )

    monkeypatch.setattr(engine, "select", original)
    receipt = app.decide_result(project["id"], "optimize", selected["run_id"], request)
    assert receipt["operation_id"] == request["operation_id"]
    assert receipt["current"] is True
    assert (
        app.result(project["id"], "optimize", selected["run_id"])["selection"]["decision_operation"]
        is None
    )


def test_drifted_pending_decision_must_be_reconciled_before_a_new_choice(
    app, selected, monkeypatch
):
    from agentagon.capabilities.experiments import engine, store

    project = app.register(str(selected["workspace"].root))
    request = {
        "operation_id": str(uuid.uuid4()),
        "expected_revision": 0,
        "decision": "select_candidate",
        "candidate_id": selected["candidate_id"],
    }
    monkeypatch.setattr(engine, "select", lambda *_: (_ for _ in ()).throw(OSError("stopped")))
    with pytest.raises(OSError, match="stopped"):
        app.decide_result(project["id"], "optimize", selected["run_id"], request)

    workspace = app.state.workspace(project["id"])
    with store.locked(workspace, selected["run_id"]):
        run = store.load_run(workspace, selected["run_id"])
        run["candidates"][selected["candidate_id"]]["source_digest"] = "0" * 64
        store.save_run(workspace, run)

    shown = app.result(project["id"], "optimize", selected["run_id"])
    assert shown["comparisons"]["result"] == "verification_incomplete"
    assert shown["limitations"]
    selection = shown["selection"]
    assert selection["decision_operation"]["state"] == "stale_pending"
    assert selection["decision_operation"]["current"] is False
    assert selection["allowed_actions"] == ["reconcile_stale_decision"]
    with pytest.raises(AuditError, match="resume its saved operation"):
        app.decide_result(
            project["id"],
            "optimize",
            selected["run_id"],
            {
                "operation_id": str(uuid.uuid4()),
                "expected_revision": selection["expected_revision"],
                "decision": "keep_current",
            },
        )

    stale = app.decide_result(project["id"], "optimize", selected["run_id"], request)
    assert stale["state"] == "stale"
    assert stale["operation_id"] == request["operation_id"]
    assert stale["reason"] == "result_evidence_changed"
    assert app.decide_result(project["id"], "optimize", selected["run_id"], request) == stale
    selection = app.result(project["id"], "optimize", selected["run_id"])["selection"]
    assert selection["decision_operation"]["state"] == "stale"
    assert selection["allowed_actions"] == ["keep_current"]

    receipt = app.decide_result(
        project["id"],
        "optimize",
        selected["run_id"],
        {
            "operation_id": str(uuid.uuid4()),
            "expected_revision": selection["expected_revision"],
            "decision": "keep_current",
        },
    )
    assert receipt["decision"] == "keep_current" and receipt["current"] is True


def test_fix_delivery_accepts_only_its_internally_prepared_candidate(app, selected):
    project = app.register(str(selected["workspace"].root))
    receipt = app.decide_result(
        project["id"],
        "fix",
        selected["run_id"],
        {
            "operation_id": str(uuid.uuid4()),
            "expected_revision": 0,
            "decision": "select_candidate",
            "candidate_id": selected["candidate_id"],
        },
    )
    prepared = app.deliver(
        project["id"],
        {"kind": "fix", "source_id": selected["run_id"], "publish": False},
    )
    assert prepared["state"] == "prepared"
    assert prepared["user_decision_id"] == receipt["id"]


def test_result_includes_authoritative_task_state_result_and_limits(app, selected):
    project = app.register(str(selected["workspace"].root))
    task_id = "task_" + "7" * 24
    app.state.db.put_record(
        project["id"],
        "tasks",
        task_id,
        {
            "id": task_id,
            "project_id": project["id"],
            "kind": "optimize",
            "state": "completed_with_limits",
            "goal": "Reduce latency",
            "options": {
                "max_trials": 4,
                "max_elapsed_seconds": 300,
                "trial_timeout_seconds": 30,
            },
            "workflow_ids": {"run_id": selected["run_id"]},
            "result": {
                "run_id": selected["run_id"],
                "summary": "One verified alternative was retained.",
                "limitations": ["Production recovery is not established."],
            },
        },
    )

    shown = app.result(project["id"], "optimize", selected["run_id"])
    assert shown["task"] == {
        "id": task_id,
        "workflow": "optimize",
        "title": "Reduce latency",
        "state": "completed_with_limits",
        "result": {
            "run_id": selected["run_id"],
            "summary": "One verified alternative was retained.",
            "limitations": ["Production recovery is not established."],
        },
        "limits": {
            "max_trials": 4,
            "max_elapsed_seconds": 300,
            "trial_timeout_seconds": 30,
        },
        "next_action": None,
        "updated_at": shown["task"]["updated_at"],
    }


def test_http_result_and_decision_are_thin_application_adapters(app, selected):
    project = app.register(str(selected["workspace"].root))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    headers = {"Origin": origin, "X-Agentagon-Token": server.session_token}
    try:
        with httpx.Client(base_url=origin, headers=headers, trust_env=False, timeout=5) as client:
            path = f"/api/projects/{project['id']}/results/optimize/{selected['run_id']}"
            shown = client.get(path)
            assert shown.status_code == 200 and shown.json()["selection"]["decision"] is None
            chosen = client.post(
                path + "/decision",
                json={
                    "operation_id": str(uuid.uuid4()),
                    "expected_revision": 0,
                    "decision": "select_candidate",
                    "candidate_id": selected["candidate_id"],
                },
            )
            assert chosen.status_code == 200 and chosen.json()["current"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_run_controls_require_current_revision_and_reject_host_ack(app, selected):
    project = app.register(str(selected["workspace"].root))
    run = app.result(project["id"], "optimize", selected["run_id"])
    request = {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "expected_revision": run["revision"],
        "action": "select",
        "candidate_id": selected["candidate_id"],
    }
    reply = app.control_run(project["id"], selected["run_id"], request)
    assert "revision" in reply
    assert (
        app.control_run(project["id"], selected["run_id"], request)["revision"] == reply["revision"]
    )
    with pytest.raises(AuditError, match="unsupported"):
        app.control_run(project["id"], selected["run_id"], {**request, "action": "ack"})
