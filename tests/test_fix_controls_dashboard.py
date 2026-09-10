"""Opt-in dashboard controls protect writes and preserve the host-work boundary."""

import http.client
import json
import threading
import uuid
from contextlib import contextmanager

import pytest

from agentagon.dashboard import create_server
from agentagon.experiments import engine
from agentagon.experiments.store import load_run


@pytest.fixture
def control_run(request):
    workspace = request.getfixturevalue("application")
    spec = request.getfixturevalue("specification")
    started = engine.start(workspace, spec, "local")
    return workspace, started["run_id"]


@contextmanager
def dashboard(control_run, *, controls=True):
    workspace, run_id = control_run
    server = create_server(workspace, run_id=run_id, controls=controls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, path, *, method="GET", body=None, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        content = response.read()
        return response.status, dict(response.getheaders()), content
    finally:
        client.close()


def bootstrap(server):
    status, headers, body = request(
        server,
        "/api/session",
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "same-origin",
            "X-Agentagon-Bootstrap": "1",
        },
    )
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers
    return json.loads(body)


def authorized(server):
    return {
        "Origin": f"http://127.0.0.1:{server.server_port}",
        "Content-Type": "application/json",
        "X-Agentagon-Token": bootstrap(server)["token"],
    }


def payload(workspace, run_id, action="directive", **fields):
    return {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "expected_revision": load_run(workspace, run_id).get("revision", 0),
        "action": action,
        **fields,
    }


def post(server, run_id, body, headers):
    return request(
        server,
        f"/api/runs/{run_id}/control",
        method="POST",
        body=json.dumps(body),
        headers=headers,
    )


def test_default_server_remains_read_only_and_has_no_token(control_run):
    workspace, run_id = control_run
    before = load_run(workspace, run_id)
    with dashboard(control_run, controls=False) as server:
        assert bootstrap(server) == {"controls_enabled": False, "token": None}
        assert post(server, run_id, {"action": "stop"}, {})[0] == 405
        page = request(server, "/")[2]
        assert b'id="fix-controls"' in page
        assert b'aria-labelledby="controls-heading" hidden' in page
    assert load_run(workspace, run_id) == before


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "same-origin",
            "X-Agentagon-Bootstrap": "1",
        },
        {
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "X-Agentagon-Bootstrap": "1",
        },
        {
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "same-origin",
            "X-Agentagon-Bootstrap": "1",
            "Origin": "https://attacker.example",
        },
    ],
)
def test_token_bootstrap_requires_same_origin_page(control_run, headers):
    with dashboard(control_run) as server:
        status, response_headers, body = request(server, "/api/session", headers=headers)
        assert status == 403
        assert "token" not in json.loads(body)
        assert "Access-Control-Allow-Origin" not in response_headers


@pytest.mark.parametrize(
    "replacement",
    [
        {"Host": "attacker.example"},
        {"Origin": "http://attacker.example"},
        {"Origin": "null"},
        {"X-Agentagon-Token": "wrong"},
        {"X-Agentagon-Token": "non-ascii-é"},
    ],
)
def test_control_writes_require_exact_host_origin_and_session(control_run, replacement):
    workspace, run_id = control_run
    before = load_run(workspace, run_id)
    with dashboard(control_run) as server:
        headers = {**authorized(server), **replacement}
        assert post(server, run_id, payload(workspace, run_id, "stop"), headers)[0] == 403
    assert load_run(workspace, run_id) == before


def test_token_is_per_server_and_absent_from_page_and_run_state(control_run):
    workspace, run_id = control_run
    with dashboard(control_run) as first, dashboard(control_run) as second:
        token = bootstrap(first)["token"]
        assert token != bootstrap(second)["token"]
        headers = {**authorized(second), "X-Agentagon-Token": token}
        assert post(second, run_id, payload(workspace, run_id, "stop"), headers)[0] == 403
        for path in ["/", "/app.js", "/api/runs", f"/api/runs/{run_id}"]:
            assert token.encode() not in request(first, path)[2]
    assert token not in json.dumps(load_run(workspace, run_id))


def test_queued_control_retry_conflict_and_cancel(control_run):
    workspace, run_id = control_run
    original_candidates = list(load_run(workspace, run_id)["candidates"])
    with dashboard(control_run) as server:
        headers = authorized(server)
        directive = payload(workspace, run_id, text="Investigate the latency regression")
        status, _, body = post(server, run_id, directive, headers)
        assert status == 200, body
        saved = json.loads(body)
        operation = saved["controls"][-1]
        assert operation["operation_id"] == directive["operation_id"]
        assert operation["state"] == "queued"
        assert post(server, run_id, directive, headers)[0] == 200
        assert len(load_run(workspace, run_id)["controls"]) == 1
        stale = {**directive, "operation_id": str(uuid.uuid4()), "text": "Another directive"}
        status, _, body = post(server, run_id, stale, headers)
        assert status == 409, body
        assert json.loads(body)["error"]
        cancel = payload(workspace, run_id, "cancel", target_operation_id=directive["operation_id"])
        assert post(server, run_id, cancel, headers)[0] == 200
        detail = json.loads(request(server, f"/api/runs/{run_id}")[2])
        first = next(
            item for item in detail["controls"] if item["operation_id"] == directive["operation_id"]
        )
        assert first["state"] == "cancelled"
        assert detail["revision"] > directive["expected_revision"]
        assert detail["objectives"]
    assert list(load_run(workspace, run_id)["candidates"]) == original_candidates


def test_policy_applies_without_changing_frozen_evaluation(control_run):
    workspace, run_id = control_run
    original = load_run(workspace, run_id)
    with dashboard(control_run) as server:
        policy = {"strategy": "top_k", "objective": "latency", "k": 2, "seed": 4}
        status, _, body = post(
            server, run_id, payload(workspace, run_id, "policy", policy=policy), authorized(server)
        )
        assert status == 200, body
        saved = json.loads(body)
        assert saved["search_policy"] == policy
        assert saved["controls"][-1]["state"] == "applied"
        serialized = json.dumps(saved)
        assert "EXECUTION_LOG" not in serialized
        assert "argv" not in serialized
        assert "review_template" not in serialized
    current = load_run(workspace, run_id)
    assert current["evaluation_digest"] == original["evaluation_digest"]
    assert current["spec"] == original["spec"]


@pytest.mark.parametrize("action", ["ack", "scan", "insights", "shell", [], {}])
def test_browser_cannot_acknowledge_host_work_or_invoke_unlisted_actions(control_run, action):
    workspace, run_id = control_run
    with dashboard(control_run) as server:
        assert (
            post(server, run_id, payload(workspace, run_id, action), authorized(server))[0] == 400
        )


def test_control_endpoint_rejects_other_methods_and_unbounded_bodies(control_run):
    _, run_id = control_run
    with dashboard(control_run) as server:
        headers = authorized(server)
        path = f"/api/runs/{run_id}/control"
        assert request(server, path, method="POST", headers=headers, body="{")[0] == 400
        assert request(server, path, method="POST", headers=headers, body="x" * 65537)[0] == 413
        assert (
            request(
                server,
                path,
                method="POST",
                headers={**headers, "Content-Type": "text/plain"},
                body="{}",
            )[0]
            == 415
        )
        for method in ["PUT", "PATCH", "DELETE", "OPTIONS"]:
            assert request(server, path, method=method, headers=headers)[0] == 405
        assert (
            request(server, "/api/audits/control", method="POST", headers=headers, body="{}")[0]
            == 404
        )
