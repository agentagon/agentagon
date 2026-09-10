import asyncio
import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from agentagon.cli.main import main
from agentagon.core.records import AuditError, digest, validate_record
from agentagon.lookup import client as lookup_client
from agentagon.lookup.client import (
    lookup,
    redact_query,
    service_url,
)
from agentagon.operations import start
from agentagon.reporting import report
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace

RESPONSE = {
    "knowledge_version": "response-content-fingerprint",
    "suggestions": [
        {
            "id": "bounded-retries",
            "title": "Bound retries",
            "suggestion": "Inspect retry paths using local evidence.",
        }
    ],
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, isolated_config):
    monkeypatch.delenv("AGENTAGON_API_KEY", raising=False)
    Config().update("user", values={"intelligence.endpoint": "https://guidance.example"})


def test_missing_endpoint_is_local_and_can_be_configured_on_resume(
    workspace, imported, monkeypatch
):
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")
    Config().update("user", unset=("intelligence.endpoint",))

    def unexpected(request):
        pytest.fail("No network call without an endpoint")

    result = lookup(
        workspace, imported, "Project overview", transport=httpx.MockTransport(unexpected)
    )
    assert result["status"] == "missing_endpoint" and result["endpoint"] is None
    assert (
        Config().summary()["intelligence"]["key_configured"]
        and not Config().summary()["intelligence"]["configured"]
    )
    assert not Config().summary()["intelligence"]["endpoint_configured"]
    assert report(workspace, imported)["pending_action"] == "evidence"

    Config().update("user", values={"intelligence.endpoint": "https://guidance.example/"})

    def handler(request):
        assert str(request.url) == "https://guidance.example/v1/audit"
        return httpx.Response(200, json=RESPONSE)

    resumed = lookup(
        workspace, imported, "Project overview", transport=httpx.MockTransport(handler)
    )
    assert resumed["status"] == "complete" and not resumed["cached"]
    assert Config().summary()["intelligence"]["configured"]


def test_absent_api_configuration_keeps_audit_available(workspace, imported, monkeypatch):
    Config().update("user", unset=("intelligence.endpoint",))
    result = lookup(workspace, imported, "Project overview")
    assert result["status"] == "missing_key"
    assert service_url(Config().effective()) is None
    assert not Config().summary()["intelligence"]["configured"]
    assert report(workspace, imported)["pending_action"] == "evidence"


def test_missing_key_is_local_and_does_not_block_audit(workspace, imported):
    def unexpected(request):
        pytest.fail("No network call without a key")

    result = lookup(
        workspace,
        imported,
        "An assistant retrieves product information.",
        transport=httpx.MockTransport(unexpected),
    )
    assert result["status"] == "missing_key"
    assert not Config().summary()["intelligence"]["configured"]
    assert "hello@agentagon.ai" in Config().summary()["intelligence"]["access_message"]
    assert report(workspace, imported)["pending_action"] == "evidence"


def test_code_only_audit_can_send_a_project_brief_before_findings(workspace, monkeypatch):
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")
    audit_id = start(
        workspace,
        mode="code",
        source=None,
        project=None,
        start_time=None,
        end_time=None,
        limit=None,
        scopes=[],
        host="test",
        model="fixture",
    )["audit_id"]
    brief = (
        "Purpose: Help readers answer questions from a document collection with citations.\n"
        "Workflow: Retrieve passages, then generate a sourced answer.\n"
        "Objective: Improve answer quality while preserving access boundaries.\n"
        "Evidence: Unit tests exist; no runtime traces or confirmed defects are available."
    )

    def handler(request):
        assert request.url.path == "/v1/audit"
        assert json.loads(request.content) == {"context": brief, "limit": 3}
        return httpx.Response(200, json=RESPONSE)

    result = lookup(workspace, audit_id, brief, limit=3, transport=httpx.MockTransport(handler))
    assert result["status"] == "complete" and result["response"] == RESPONSE
    generated = json.loads(open(report(workspace, audit_id)["json_report"]).read())
    assert generated["intelligence"][0]["response"] == RESPONSE
    assert report(workspace, audit_id)["pending_action"] == "evidence"


def test_redacted_request_and_durable_resumption(workspace, imported, monkeypatch):
    key = "agi_fixture_a-very-long-synthetic-secret"
    monkeypatch.setenv("AGENTAGON_API_KEY", key)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/audit"
        assert request.headers["authorization"] == f"Bearer {key}"
        payload = json.loads(request.content)
        assert payload["limit"] == 5
        assert all(
            value not in payload["context"]
            for value in [key, "person@example.com", "hunter2", "private.example", "/Users/guru"]
        )
        return httpx.Response(200, json=RESPONSE, headers={"X-Request-ID": "request-1"})

    query = f"Retrieval assistant timeout. person@example.com https://private.example/project password=hunter2 /Users/guru/project {key}"
    first = lookup(workspace, imported, query, limit=50, transport=httpx.MockTransport(handler))
    assert first["status"] == "complete" and not first["cached"]
    resumed = lookup(
        Workspace(workspace.root), imported, query, limit=50, transport=httpx.MockTransport(handler)
    )
    assert resumed["cached"] and resumed["response"] == RESPONSE and len(calls) == 1
    assert key not in json.dumps(first)
    generated = report(workspace, imported)
    data = json.loads(open(generated["json_report"]).read())
    assert data["intelligence"][0]["response"] == RESPONSE
    lookup(workspace, imported, query, phase="follow_up", transport=httpx.MockTransport(handler))
    assert len(calls) == 2


def test_legacy_query_receipt_survives_reports_and_new_request_resumption(
    workspace, imported, monkeypatch
):
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")
    endpoint = "https://guidance.example/v1/audit"
    context = "Document Q&A with retrieval and caching."
    focus = "Reduce response latency."
    legacy_payload = {"query": f"{context}\n{focus}", "limit": 5}
    legacy = {
        "phase": "initial",
        "request_digest": digest([endpoint, "initial", legacy_payload]),
        "at": "2026-09-07T00:00:00Z",
        "endpoint": endpoint,
        "request": legacy_payload,
        "status": "complete",
        "response": {
            "knowledge_version": "legacy-knowledge-version",
            "suggestions": [
                {
                    "id": "legacy-retrieval",
                    "title": "Review retrieval",
                    "suggestion": "Inspect retrieval against local evidence.",
                }
            ],
        },
        "request_id": "legacy-request",
    }
    legacy_path = workspace.artifact(legacy)
    legacy_bytes = (workspace.root / legacy_path).read_bytes()
    legacy_index = {
        "phase": legacy["phase"],
        "request_digest": legacy["request_digest"],
        "status": legacy["status"],
        "path": legacy_path,
    }
    audit = workspace.read_audit(imported)
    audit["intelligence"] = [legacy_index]
    workspace.save_audit(audit)

    generated = report(workspace, imported)
    data = json.loads(Path(generated["json_report"]).read_text(encoding="utf-8"))
    assert data["intelligence"] == [legacy]
    markdown = Path(generated["report"]).read_text(encoding="utf-8")
    assert "`legacy-knowledge-version`" in markdown and "`legacy-retrieval`" in markdown

    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST" and str(request.url) == endpoint
        assert request.headers["authorization"] == "Bearer synthetic-key"
        assert json.loads(request.content) == {"context": context, "focus": focus, "limit": 5}
        return httpx.Response(200, json=RESPONSE)

    transport = httpx.MockTransport(handler)
    first = lookup(workspace, imported, context, focus=focus, transport=transport)
    assert first["status"] == "complete" and not first["cached"] and len(calls) == 1
    assert first["receipt"] != legacy_path and first["response"] == RESPONSE

    reopened = Workspace(workspace.root)
    resumed = lookup(reopened, imported, context, focus=focus, transport=transport)
    assert resumed == {**first, "cached": True} and len(calls) == 1
    assert reopened.read_audit(imported)["intelligence"] == [
        legacy_index,
        {
            "phase": first["phase"],
            "request_digest": first["request_digest"],
            "status": first["status"],
            "path": first["receipt"],
        },
    ]

    generated = report(reopened, imported)
    data = json.loads(Path(generated["json_report"]).read_text(encoding="utf-8"))
    assert data["intelligence"] == [legacy, reopened.read_artifact(first["receipt"])]
    markdown = Path(generated["report"]).read_text(encoding="utf-8")
    for response in (legacy["response"], RESPONSE):
        assert f"`{response['knowledge_version']}`" in markdown
        assert f"`{response['suggestions'][0]['id']}`" in markdown
    assert (reopened.root / legacy_path).read_bytes() == legacy_bytes


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "unauthorized"),
        (403, "unauthorized"),
        (429, "rate_limited"),
        (503, "unavailable"),
        (302, "unavailable"),
    ],
)
def test_http_errors_are_saved_without_body_or_retry(
    workspace, imported, monkeypatch, status, expected
):
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="potentially sensitive error body",
            headers={"Retry-After": "30", "Location": "https://untrusted.example"},
        )

    result = lookup(workspace, imported, "Project overview", transport=httpx.MockTransport(handler))
    assert result["status"] == expected and len(calls) == 1
    assert "sensitive" not in json.dumps(result)
    assert result["retry_after"] == 30


@pytest.mark.parametrize(
    "response",
    [
        {"knowledge_version": "v", "suggestions": RESPONSE["suggestions"] * 2},
        {"knowledge_version": "v", "suggestions": RESPONSE["suggestions"] * 6},
        {"knowledge_version": "v", "suggestions": [{"id": "x"}]},
        {"knowledge_version": "v", "suggestions": RESPONSE["suggestions"], "extra": True},
        {
            "knowledge_version": "v",
            "suggestions": [{"id": "x", "title": "key", "suggestion": "synthetic-key"}],
        },
    ],
)
def test_invalid_response_does_not_become_guidance(workspace, imported, monkeypatch, response):
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")
    result = lookup(
        workspace,
        imported,
        "Project overview",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
    )
    assert result["status"] == "invalid_response"
    assert "response" not in result


def test_timeout_and_size_bounds(workspace, imported, monkeypatch):
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")

    def timeout(request):
        raise httpx.ReadTimeout("private details")

    assert (
        lookup(workspace, imported, "Summary", transport=httpx.MockTransport(timeout))["status"]
        == "unavailable"
    )
    result = lookup(
        workspace,
        imported,
        "Summary",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b" " * 131073)),
    )
    assert result["status"] == "invalid_response"


@pytest.mark.parametrize(
    "origin",
    [
        "http://external.example",
        "https://user:pass@external.example",
        "https://external.example/path",
        "https://external.example?key=secret",
        "http://[",
        "https://external.example:invalid",
    ],
)
def test_invalid_endpoint_configuration(origin):
    with pytest.raises(AuditError):
        Config().update("user", values={"intelligence.endpoint": origin})


def test_query_rejects_code_and_invalid_inputs(workspace, imported):
    with pytest.raises(AuditError):
        redact_query('```python\nprint("customer data")\n```', ())
    for query, limit in [("x", True), ("x", 0), ("x", 1.5), ("x" * 4001, 5), ("\ud800", 5)]:
        with pytest.raises(AuditError):
            lookup(workspace, imported, query, limit=limit)


def test_onboarding_acknowledgment_survives_resumption(workspace):
    assert Config().summary(workspace.root)["intelligence"]["onboarding_pending"]
    Config().update("user", values={"intelligence.access_presented": True})
    assert not Config().summary(Workspace(workspace.root).root)["intelligence"][
        "onboarding_pending"
    ]
    assert not Config().summary()["intelligence"]["onboarding_pending"]


def test_checkout_endpoint_and_key_reference_are_used_and_redacted(
    workspace, imported, monkeypatch
):
    Config().update(
        "project",
        workspace.root,
        {
            "intelligence.endpoint": "https://checkout-guidance.example",
            "intelligence.api_key_env": "CHECKOUT_INTELLIGENCE_KEY",
        },
    )
    key = "checkout-secret-value"
    monkeypatch.setenv("CHECKOUT_INTELLIGENCE_KEY", key)

    def handler(request):
        assert str(request.url) == "https://checkout-guidance.example/v1/audit"
        assert request.headers["authorization"] == f"Bearer {key}"
        assert key not in request.content.decode()
        return httpx.Response(200, json=RESPONSE)

    result = lookup(workspace, imported, f"Summary {key}", transport=httpx.MockTransport(handler))
    assert result["status"] == "complete"
    assert key not in json.dumps(result)
    assert (
        Config().effective(workspace.root)["intelligence"]["api_key_env"]
        == "CHECKOUT_INTELLIGENCE_KEY"
    )
    assert not Config().summary()["intelligence"]["key_configured"]


def test_old_endpoint_environment_is_not_a_configuration_path(monkeypatch):
    Config().update("user", unset=("intelligence.endpoint",))
    monkeypatch.setenv("AGENTAGON_INTELLIGENCE_URL", "https://old-config.example")
    assert service_url(Config().effective()) is None


def test_setup_during_lookup_cannot_mix_endpoint_and_credential(workspace, imported, monkeypatch):
    config = Config()
    config.update(
        "project",
        workspace.root,
        {
            "intelligence.endpoint": "https://first-guidance.example",
            "intelligence.api_key_env": "FIRST_GUIDANCE_KEY",
        },
    )
    first_key = "first-synthetic-secret"
    second_key = "second-synthetic-secret"
    monkeypatch.setenv("FIRST_GUIDANCE_KEY", first_key)
    monkeypatch.setenv("SECOND_GUIDANCE_KEY", second_key)
    original_lock = workspace.locked

    @contextmanager
    def concurrent_setup():
        config.update(
            "project",
            workspace.root,
            {
                "intelligence.endpoint": "https://second-guidance.example",
                "intelligence.api_key_env": "SECOND_GUIDANCE_KEY",
            },
        )
        with original_lock():
            yield

    monkeypatch.setattr(workspace, "locked", concurrent_setup)

    def handler(request):
        assert str(request.url) == "https://first-guidance.example/v1/audit"
        assert request.headers["authorization"] == f"Bearer {first_key}"
        assert first_key not in request.content.decode()
        return httpx.Response(200, json=RESPONSE)

    result = lookup(
        workspace,
        imported,
        f"Project overview {first_key}",
        transport=httpx.MockTransport(handler),
    )
    assert result["status"] == "complete"
    assert config.effective(workspace.root)["intelligence"]["api_key_env"] == "SECOND_GUIDANCE_KEY"


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"context": " "},
        {"focus": "\n"},
        {"context": "Project", "focus": ""},
        {"context": 42},
        {"focus": "\ud800"},
        {"context": "x" * 2001, "focus": "y" * 2000},
        {"context": "x" * 3995 + " a@b.co"},
        {"context": "x" * 3990 + " a@b.co"},
        {"focus": "```private code```"},
    ],
)
def test_context_focus_reject_invalid_or_oversized_requests(workspace, imported, fields):
    # Size is checked before redaction can shorten it, and after redaction can expand it.
    with pytest.raises(AuditError):
        lookup(workspace, imported, **fields)
    assert not workspace.read_audit(imported).get("intelligence")


@pytest.mark.parametrize(
    "payload",
    [{"query": "legacy"}, {"context": "Project", "query": "legacy"}, {"focus": None}],
)
def test_contract_rejects_query_and_null(payload):
    with pytest.raises(AuditError):
        validate_record("intelligence-request", payload)


@pytest.mark.parametrize(
    "fields",
    [
        {"context": "x" * 4000},
        {"focus": "Reduce response latency while preserving answer quality."},
        {"context": "x" * 2000, "focus": "y" * 2000},
    ],
)
def test_context_only_focus_only_and_combined_requests(workspace, imported, monkeypatch, fields):
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")

    def handler(request):
        assert json.loads(request.content) == {**fields, "limit": 5}
        assert set(request.extensions["timeout"].values()) == {30}
        return httpx.Response(200, json={"knowledge_version": "v", "suggestions": []})

    result = lookup(workspace, imported, **fields, transport=httpx.MockTransport(handler))
    assert result["status"] == "complete"
    assert result["response"]["suggestions"] == []


def test_both_fields_are_redacted_and_receipts_bind_each_field(workspace, imported, monkeypatch):
    key = "synthetic-long-api-key"
    monkeypatch.setenv("AGENTAGON_API_KEY", key)
    calls = []

    def handler(request):
        fields = json.loads(request.content)
        calls.append(fields)
        assert key not in json.dumps(fields)
        assert "person@example.com" not in json.dumps(fields)
        return httpx.Response(200, json=RESPONSE)

    transport = httpx.MockTransport(handler)
    context = f"Document Q&A; owner person@example.com {key}"
    focus = f"Reduce latency; requester person@example.com {key}"
    first = lookup(workspace, imported, context, focus=focus, transport=transport)
    assert first["request"] == {
        "context": "Document Q&A; owner [REDACTED] [REDACTED]",
        "focus": "Reduce latency; requester [REDACTED] [REDACTED]",
        "limit": 5,
    }
    resumed = lookup(workspace, imported, context, focus=focus, transport=transport)
    assert resumed["cached"] and len(calls) == 1
    different_focus = lookup(workspace, imported, context, focus="Reduce cost", transport=transport)
    different_context = lookup(
        workspace, imported, "Tool assistant", focus=focus, transport=transport
    )
    focus_only = lookup(workspace, imported, focus=focus, transport=transport)
    assert (
        len({r["request_digest"] for r in [first, different_focus, different_context, focus_only]})
        == 4
    )
    assert len(calls) == 4
    refreshed = lookup(workspace, imported, context, focus=focus, refresh=True, transport=transport)
    assert not refreshed["cached"] and len(calls) == 5


def test_total_deadline_bounds_delayed_transport(workspace, imported, monkeypatch):
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")
    monkeypatch.setattr(lookup_client, "REQUEST_TIMEOUT_SECONDS", 0.01)
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(1)
        pytest.fail("The total deadline should cancel transport before it responds")

    result = lookup(
        workspace, imported, focus="Reduce latency", transport=httpx.MockTransport(handler)
    )
    assert result["status"] == "unavailable" and len(calls) == 1


@pytest.mark.parametrize("fields", [("context",), ("focus",), ("context", "focus")])
def test_cli_reads_only_supplied_files_without_saved_raw_goal(workspace, tmp_path, fields):
    audit_id = start(
        workspace,
        mode="code",
        source=None,
        project=None,
        start_time=None,
        end_time=None,
        limit=None,
        scopes=[],
        host="test",
        model="fixture",
        goal="PRIVATE RAW USER GOAL",
    )["audit_id"]
    arguments = ["--workspace", str(workspace.root), "audit", "lookup", audit_id]
    expected = {"limit": 5}
    for field in fields:
        source = tmp_path / f"{field}.txt"
        source.write_text(f"Abstract {field}", encoding="utf-8")
        arguments.extend([f"--{field}-file", str(source)])
        expected[field] = f"Abstract {field}"
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["request"] == expected and data["status"] == "missing_key"
    assert "PRIVATE RAW USER GOAL" not in result.output
    assert "PRIVATE RAW USER GOAL" not in json.dumps(workspace.read_artifact(data["receipt"]))


def test_cli_requires_new_file_options_and_valid_utf8(workspace, imported, tmp_path):
    runner = CliRunner()
    arguments = ["--workspace", str(workspace.root), "audit", "lookup", imported]
    absent = runner.invoke(main, arguments)
    assert absent.exit_code == 1 and "supply --context-file" in absent.output
    source = tmp_path / "focus.txt"
    source.write_bytes(b"\xff")
    legacy = runner.invoke(main, [*arguments, "--query-file", str(source)])
    assert legacy.exit_code == 2 and "No such option" in legacy.output
    invalid = runner.invoke(main, [*arguments, "--focus-file", str(source)])
    assert invalid.exit_code == 1 and "focus file must be UTF-8" in invalid.output
    source.write_bytes(b"x" * (24 * 1024 + 1))
    oversized = runner.invoke(main, [*arguments, "--focus-file", str(source)])
    assert oversized.exit_code == 1 and "focus file exceeds" in oversized.output
