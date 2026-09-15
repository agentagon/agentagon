"""Baseline controls are explicit; settings and exports preserve private boundaries."""

import json
import threading
import uuid
from contextlib import contextmanager

import pytest
from test_fix_controls_dashboard import authorized, request

from agentagon.dashboard import create_server
from agentagon.dashboard_controls import settings_projection
from agentagon.storage.config import Config


@contextmanager
def dashboard(workspace, controls=True):
    server = create_server(workspace, controls=controls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_settings_read_omits_execution_profiles_and_never_resolves_secrets(
    application, monkeypatch
):
    monkeypatch.setenv("TRACE_SECRET", "never-export-this-secret")
    Config().update("project", application.root, {"traces.api_key_env": "TRACE_SECRET"})
    before = Config().path.read_bytes()
    with dashboard(application, controls=False) as server:
        status, _, body = request(server, "/api/settings")
        assert status == 200
        assert b"TRACE_SECRET" in body
        assert b"never-export-this-secret" not in body
        assert b"EXECUTION_LOG" not in body
        assert request(server, "/api/settings", method="POST", body="{}")[0] == 405
    assert Config().path.read_bytes() == before


def test_settings_require_origin_session_revision_and_retain_idempotency(application):
    payload = {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "expected_revision": settings_projection(application)["revision"],
        "values": {"traces.api_key_env": "TRACES_API_KEY"},
    }
    with dashboard(application) as server:
        assert request(server, "/api/settings", method="POST", body=json.dumps(payload))[0] == 403
        headers = authorized(server)

        def post(data):
            return request(
                server, "/api/settings", method="POST", body=json.dumps(data), headers=headers
            )

        assert post(payload)[0] == 200
        assert post(payload)[0] == 200
        assert post({**payload, "values": {"traces.api_key_env": "ANOTHER_KEY"}})[0] == 400
        assert post({**payload, "operation_id": str(uuid.uuid4())})[0] == 400
        assert Config().effective(application.root)["traces"]["api_key_env"] == "TRACES_API_KEY"


def test_settings_retry_recovers_commit_before_lost_receipt(application, monkeypatch):
    from agentagon.dashboard_controls import update_settings

    payload = {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "expected_revision": settings_projection(application)["revision"],
        "values": {"traces.api_key_env": "TRACES_API_KEY"},
    }
    original = application.write
    lost = False

    def lose_receipt(path, record):
        nonlocal lost
        if record.get("state") == "complete" and not lost:
            lost = True
            raise OSError("lost receipt")
        return original(path, record)

    monkeypatch.setattr(application, "write", lose_receipt)
    with pytest.raises(OSError, match="lost receipt"):
        update_settings(application, payload)
    assert (
        update_settings(application, payload)["settings"]["traces"]["api_key_env"]
        == "TRACES_API_KEY"
    )


@pytest.mark.parametrize(
    "value", ["sk-private-secret", "secret with spaces", "https://user:password@provider.test"]
)
def test_settings_reject_secret_values_and_authenticated_endpoints(application, value):
    key = "traces.endpoint" if value.startswith("https") else "traces.api_key_env"
    payload = {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "expected_revision": settings_projection(application)["revision"],
        "values": {key: value},
    }
    with dashboard(application) as server:
        assert (
            request(
                server,
                "/api/settings",
                method="POST",
                body=json.dumps(payload),
                headers=authorized(server),
            )[0]
            == 400
        )


@pytest.mark.parametrize("source", ["evaluation", "baseline"])
def test_baseline_read_does_not_advance_and_explicit_rerun_returns_durable_job(
    application, monkeypatch, source
):
    from agentagon.experiments import baselines

    records = [
        {
            "baseline_id": "baseline_" + "a" * 24,
            "state": "pending",
            "evaluation_id": "eval_" + "a" * 24,
            "pending_action": "Complete independent review in your coding host",
        }
    ]
    advanced = threading.Event()

    def create_job(workspace, source_id, **kwargs):
        assert source_id == records[0][f"{source}_id"]
        return records[0]

    monkeypatch.setattr(baselines, "list_baselines", lambda workspace: records)
    monkeypatch.setattr(baselines, "public_projection", lambda record: record)
    monkeypatch.setattr(baselines, "start", create_job)
    monkeypatch.setattr(baselines, "rerun", create_job)
    monkeypatch.setattr(baselines, "advance", lambda workspace, baseline_id: advanced.set())
    with dashboard(application) as server:
        assert request(server, "/api/baselines")[0] == 200
        assert not advanced.is_set()
        key = f"{source}_id"
        payload = {key: records[0][key], "operation_id": str(uuid.uuid4())}
        status, _, body = request(
            server,
            "/api/baselines/rerun",
            method="POST",
            body=json.dumps(payload),
            headers=authorized(server),
        )
        assert status == 202
        assert json.loads(body)["pending_action"] == records[0]["pending_action"]
        assert advanced.wait(timeout=5)
