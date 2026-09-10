"""Anonymous usage boundaries and observable queue/delivery behavior."""

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import httpx
import pytest
from click.testing import CliRunner
from support.audit import finish

from agentagon import usage
from agentagon.cli.main import main
from agentagon.core.records import AuditError
from agentagon.lookup.client import lookup
from agentagon.storage.config import Config
from agentagon.usage import track

TOKEN = "phc_syntheticTelemetryProject12345"
RESPONSE = {
    "knowledge_version": "a" * 64,
    "suggestions": [
        {"id": "retry-001", "title": "Check retries", "suggestion": "Inspect retry evidence."},
        {"id": "retry-002", "title": "Check timeouts", "suggestion": "Inspect timeout evidence."},
    ],
}


@pytest.fixture
def posthog(monkeypatch):
    state = {"requests": [], "response": httpx.Response(200, json={"status": 1})}
    original = httpx.AsyncClient

    def handler(request):
        assert str(request.url) == "https://us.i.posthog.com/batch/"
        state["requests"].append(request)
        return state["response"]

    def client(**kwargs):
        if "transport" not in kwargs:
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            kwargs["transport"] = httpx.MockTransport(handler)
        return original(**kwargs)

    monkeypatch.setattr(usage, "POSTHOG_PROJECT_TOKEN", TOKEN)
    monkeypatch.setattr(usage.httpx, "AsyncClient", client)
    return state


def pending():
    path = usage._path(Config())
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as db:
        return [
            json.loads(row[0]) for row in db.execute("SELECT payload FROM events ORDER BY rowid")
        ]


def retry_now():
    with closing(sqlite3.connect(usage._path(Config()))) as db, db:
        db.execute("UPDATE delivery SET next_attempt=0,lease_until=0")


def received(posthog):
    return [
        event for request in posthog["requests"] for event in json.loads(request.content)["batch"]
    ]


def receipt_for(workspace, audit_id, monkeypatch):
    Config().update("user", values={"intelligence.endpoint": "https://guidance.example"})
    monkeypatch.setenv("AGENTAGON_API_KEY", "agi_privateCustomerKey")
    with monkeypatch.context() as isolated:
        isolated.setenv("AGENTAGON_TELEMETRY_DISABLED", "1")
        return lookup(
            workspace,
            audit_id,
            "Private customer context person@example.com",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=RESPONSE)),
        )["receipt"]


def test_defaults_and_inspection_have_no_side_effects(posthog):
    assert Config().summary()["telemetry"] == {
        "enabled": True,
        "configured": True,
        "pending": 0,
        "dropped": 0,
        "delivery": "idle",
    }
    runner = CliRunner()
    assert runner.invoke(main, ["setup", "--scope", "user"]).exit_code == 0
    assert runner.invoke(main, ["--help"]).exit_code == 0
    assert not Config().path.parent.exists()
    assert posthog["requests"] == []


def test_skill_payload_contains_only_anonymous_fields(posthog, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://person:password@private.example")
    monkeypatch.setenv("AGENTAGON_API_KEY", "agi_privateCustomerKey")
    result = track("skill_invoked", skill="audit", host="codex")
    assert result == {"status": "accepted"} and pending() == []
    request = posthog["requests"][0]
    envelope = json.loads(request.content)
    assert envelope["api_key"] == TOKEN
    event = envelope["batch"][0]
    assert set(event) == {"uuid", "event", "timestamp", "properties"}
    assert event["properties"] == {
        "distinct_id": event["uuid"],
        "$process_person_profile": False,
        "$geoip_disable": True,
        "$ip": None,
        "schema_version": 1,
        "agentagon_version": usage.__version__,
        "skill": "audit",
        "host": "codex",
    }
    assert not {"authorization", "cookie", "x-forwarded-for"} & set(request.headers)
    assert b"private" not in request.content
    assert Config().summary()["telemetry"]["pending"] == 0
    assert usage._path(Config()).stat().st_mode & 0o777 == 0o600
    assert usage._path(Config()).parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "event,fields",
    [
        ("arbitrary_event", {}),
        ("skill_invoked", {"skill": "audit", "prompt": "secret"}),
        ("skill_invoked", {"skill": "private-project"}),
        ("skill_invoked", {"skill": "audit", "host": "person@example.com"}),
        ("knowledge_returned", {"entry_id": "retry-001", "knowledge_version": "a" * 64}),
        ("intelligence_lookup_completed", {"outcome": "complete", "duration_ms": float("nan")}),
    ],
)
def test_invalid_events_never_enter_storage_or_transport(event, fields, posthog):
    assert track(event, **fields)["status"] == "invalid_event"
    assert not usage._path(Config()).exists()
    assert posthog["requests"] == []


def test_disable_clears_queue_and_prevents_backfill(posthog):
    posthog["response"] = httpx.Response(503)
    assert track("skill_invoked", skill="audit")["status"] == "retry"
    assert len(pending()) == 1
    original_id = pending()[0]["uuid"]
    result = CliRunner().invoke(
        main, ["setup", "--scope", "user", "--set", "telemetry.enabled", "false"]
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["telemetry"]["pending"] == 0
    assert track("skill_invoked", skill="fix") == {"status": "disabled"}
    assert len(posthog["requests"]) == 1
    Config().update("user", values={"telemetry.enabled": True})
    posthog["response"] = httpx.Response(200, json={"status": 1})
    assert track("skill_invoked", skill="review")["status"] == "accepted"
    assert [event["properties"]["skill"] for event in received(posthog)] == ["audit", "review"]
    assert received(posthog)[-1]["uuid"] != original_id


def test_environment_disable_overrides_enabled_config_and_purges(posthog, monkeypatch):
    posthog["response"] = httpx.Response(503)
    track("skill_invoked", skill="audit")
    monkeypatch.setenv("AGENTAGON_TELEMETRY_DISABLED", "1")
    assert not Config().summary()["telemetry"]["enabled"]
    assert track("skill_invoked", skill="fix")["status"] == "disabled"
    assert pending() == [] and len(posthog["requests"]) == 1


def test_user_wide_setting_rejects_project_override(workspace):
    with pytest.raises(AuditError, match="user settings"):
        Config().update("project", workspace.root, {"telemetry.enabled": True})
    for value in ("false", 1, None):
        with pytest.raises(AuditError, match="boolean"):
            Config().update("user", values={"telemetry.enabled": value})


def test_missing_or_secret_token_never_sends(monkeypatch):
    assert track("skill_invoked", skill="setup") == {"status": "unconfigured"}
    assert len(pending()) == 1
    for prefix in ("phx_", "phs_"):
        monkeypatch.setenv("AGENTAGON_POSTHOG_PROJECT_TOKEN", prefix + "secret" * 8)
        assert track("skill_invoked", skill="setup") == {"status": "unconfigured"}
        assert not Config().summary()["telemetry"]["configured"]


def test_retry_keeps_identity_and_applies_global_backoff(posthog):
    posthog["response"] = httpx.Response(429, headers={"Retry-After": "120"})
    assert track("skill_invoked", skill="audit")["status"] == "retry"
    first = received(posthog)[0]
    assert track("skill_invoked", skill="fix")["status"] == "queued"
    assert len(posthog["requests"]) == 1 and len(pending()) == 2
    with closing(sqlite3.connect(usage._path(Config()))) as db:
        due = db.execute("SELECT next_attempt FROM delivery").fetchone()[0]
    assert 115 <= due - time.time() <= 121
    retry_now()
    posthog["response"] = httpx.Response(200, json={"status": 1})
    assert track("skill_invoked", skill="review")["status"] == "accepted"
    assert received(posthog)[1] == first
    assert pending() == []


@pytest.mark.parametrize(
    "response,expected,retained",
    [
        (
            httpx.Response(200, json={"status": 1, "quota_limited": ["events"]}),
            "quota_limited",
            True,
        ),
        (httpx.Response(200, json={"status": 0}), "invalid_response", True),
        (httpx.Response(200, json={"status": True}), "invalid_response", True),
        (httpx.Response(200, content=b"invalid"), "unavailable", True),
        (httpx.Response(200, content=b"x" * 8193), "invalid_response", True),
        (httpx.Response(400), "rejected", False),
        (httpx.Response(302, headers={"Location": "https://private.example"}), "rejected", False),
        (httpx.Response(200, json={"status": "Ok"}), "accepted", False),
    ],
)
def test_response_classification(response, expected, retained, posthog):
    posthog["response"] = response
    assert track("skill_invoked", skill="audit")["status"] == expected
    assert bool(pending()) is retained
    assert len(posthog["requests"]) == 1


def test_streaming_request_has_a_total_deadline(monkeypatch):
    original = httpx.AsyncClient

    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"status": 1})

    monkeypatch.setattr(usage, "REQUEST_SECONDS", 0.02)
    monkeypatch.setattr(usage, "POSTHOG_PROJECT_TOKEN", TOKEN)
    monkeypatch.setattr(
        usage.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(slow), **kw)
    )
    before = time.monotonic()
    assert track("skill_invoked", skill="audit")["status"] == "unavailable"
    assert time.monotonic() - before < 0.5 and len(pending()) == 1


def test_async_python_caller_enqueues_without_nested_loop(posthog):
    async def call():
        return track("skill_invoked", skill="audit")

    assert asyncio.run(call()) == {"status": "queued"}
    assert posthog["requests"] == [] and len(pending()) == 1
    assert track("skill_invoked", skill="review")["status"] == "accepted"
    assert len(received(posthog)) == 2


def test_queue_count_bytes_expiry_and_one_batch_limit(posthog, monkeypatch):
    posthog["response"] = httpx.Response(503)
    monkeypatch.setattr(usage, "MAX_EVENTS", 25)
    for _ in range(30):
        track("skill_invoked", skill="audit")
    assert len(pending()) == 25
    assert Config().summary()["telemetry"]["dropped"] == 5
    with closing(sqlite3.connect(usage._path(Config()))) as db, db:
        db.execute(
            "UPDATE events SET created=? WHERE rowid=(SELECT min(rowid) FROM events)",
            (time.time() - usage.MAX_AGE - 1,),
        )
    track("skill_invoked", skill="fix")
    assert len(pending()) == 25 and Config().summary()["telemetry"]["dropped"] == 6
    retry_now()
    posthog["response"] = httpx.Response(200, json={"status": 1})
    track("skill_invoked", skill="review")
    assert len(json.loads(posthog["requests"][-1].content)["batch"]) == 20
    assert len(pending()) == 5
    monkeypatch.setattr(usage, "MAX_BYTES", 1000)
    posthog["response"] = httpx.Response(503)
    track("skill_invoked", skill="setup")
    assert (
        sum(len(json.dumps(event, separators=(",", ":")).encode()) for event in pending()) <= 1000
    )


def test_concurrent_writers_do_not_lose_events_or_hold_database_during_send(posthog, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    async def blocked(payloads, token):
        entered.set()
        assert release.wait(2)
        return "accepted", 0

    monkeypatch.setattr(usage, "_post", blocked)
    with ThreadPoolExecutor(max_workers=8) as pool:
        first = pool.submit(track, "skill_invoked", skill="audit")
        assert entered.wait(2)
        later = [pool.submit(track, "skill_invoked", skill="review") for _ in range(12)]
        assert all(future.result()["status"] == "queued" for future in later)
        assert len(pending()) == 13
        release.set()
        assert first.result()["status"] == "accepted"
    assert len(pending()) == 12


def test_tampered_queue_is_not_forwarded(posthog):
    posthog["response"] = httpx.Response(503)
    track("skill_invoked", skill="audit")
    event = pending()[0]
    event["properties"]["prompt"] = "private customer content"
    with closing(sqlite3.connect(usage._path(Config()))) as db, db:
        db.execute("UPDATE events SET payload=?", (json.dumps(event),))
    retry_now()
    posthog["response"] = httpx.Response(200, json={"status": 1})
    track("skill_invoked", skill="review")
    assert len(received(posthog)) == 2
    assert b"private customer content" not in posthog["requests"][-1].content
    assert Config().summary()["telemetry"]["dropped"] == 1


def test_unavailable_and_symlinked_storage_never_interrupts(tmp_path, posthog, monkeypatch):
    path = usage._path(Config())
    path.parent.mkdir(parents=True)
    target = tmp_path / "private"
    target.write_text("private contents", encoding="utf-8")
    path.symlink_to(target)
    assert track("skill_invoked", skill="audit")["status"] == "invalid_event"
    assert target.read_text() == "private contents" and posthog["requests"] == []
    path.unlink()
    path.write_text("not sqlite", encoding="utf-8")
    assert track("skill_invoked", skill="audit")["status"] == "unavailable"
    assert Config().summary()["telemetry"]["delivery"] == "storage_unavailable"


def test_lookup_emits_returns_once_in_one_request_and_tracks_cache(
    workspace, imported, posthog, monkeypatch
):
    Config().update("user", values={"intelligence.endpoint": "https://guidance.example"})
    monkeypatch.setenv("AGENTAGON_API_KEY", "agi_privateCustomerKey")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=RESPONSE))
    first = lookup(workspace, imported, "private project summary", transport=transport)
    assert len(posthog["requests"]) == 1
    assert [event["event"] for event in received(posthog)] == [
        "intelligence_lookup_completed",
        "knowledge_returned",
        "knowledge_returned",
    ]
    before = workspace.read_artifact(first["receipt"])
    second = lookup(workspace, imported, "private project summary", transport=transport)
    assert second["cached"]
    assert len(received(posthog)) == 4 and received(posthog)[-1]["properties"]["cached"]
    assert workspace.read_artifact(first["receipt"]) == before
    outbound = b"".join(request.content for request in posthog["requests"])
    for private in (
        imported,
        str(workspace.root),
        first["receipt"],
        "private project summary",
        "agi_privateCustomerKey",
    ):
        assert private.encode() not in outbound


def test_investigated_and_cited_require_local_evidence_and_deduplicate(
    workspace, imported, posthog, monkeypatch
):
    receipt = receipt_for(workspace, imported, monkeypatch)
    immutable = (workspace.root / receipt).read_bytes()
    args = {
        "workspace": workspace,
        "audit_id": imported,
        "receipt": receipt,
        "entry_id": "retry-001",
    }
    assert track("knowledge_investigated", **args)["status"] == "accepted"
    assert track("knowledge_investigated", **args)["status"] == "idle"
    assert track("knowledge_cited", **args, finding_id="invented")["status"] == "invalid_event"
    assert (
        track("knowledge_investigated", **{**args, "entry_id": "invented"})["status"]
        == "invalid_event"
    )
    assert (
        track("knowledge_investigated", **{**args, "receipt": "/etc/passwd"})["status"]
        == "invalid_event"
    )
    finish(workspace, imported)
    finding = next(iter(workspace.read_audit(imported)["diagnoses"].values()))["findings"][0]["id"]
    assert track("knowledge_cited", **args, finding_id=finding)["status"] == "accepted"
    assert track("knowledge_cited", **args, finding_id=finding)["status"] == "idle"
    assert [event["event"] for event in received(posthog)] == [
        "knowledge_investigated",
        "knowledge_cited",
    ]
    assert (workspace.root / receipt).read_bytes() == immutable
    annotations = json.loads(workspace.audit_path(imported).with_name("usage.json").read_text())
    cited = next(
        row
        for row in annotations["entries"].values()
        if row["annotation"]["stage"] == "knowledge_cited"
    )
    assert cited["annotation"]["finding_id"] == finding
    assert all(finding.encode() not in request.content for request in posthog["requests"])


def test_cli_is_one_hook_and_rejects_free_text(posthog, tmp_path):
    runner = CliRunner()
    good = runner.invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "telemetry",
            "skill_invoked",
            "--data",
            '{"skill":"eval","host":"claude-code"}',
        ],
    )
    assert good.exit_code == 0 and json.loads(good.output)["status"] == "accepted"
    for data in (
        "null",
        "not json",
        '{"workspace":"/private"}',
        '{"skill":"eval","text":"private"}',
    ):
        result = runner.invoke(main, ["telemetry", "skill_invoked", "--data", data])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"status": "invalid_event"}
        assert "private" not in result.output
    assert len(posthog["requests"]) == 1


def test_workspace_cli_validates_knowledge(posthog, workspace, imported, monkeypatch):
    receipt = receipt_for(workspace, imported, monkeypatch)
    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(workspace.root),
            "telemetry",
            "knowledge_investigated",
            "--data",
            json.dumps({"audit_id": imported, "receipt": receipt, "entry_id": "retry-001"}),
        ],
    )
    assert result.exit_code == 0 and json.loads(result.output)["status"] == "accepted"
    assert received(posthog)[0]["properties"]["entry_id"] == "retry-001"


def test_inspection_does_not_drain_existing_queue(posthog, workspace):
    posthog["response"] = httpx.Response(503)
    track("skill_invoked", skill="audit")
    retry_now()
    assert CliRunner().invoke(main, ["--workspace", str(workspace.root), "status"]).exit_code == 0
    assert Config().summary()["telemetry"]["pending"] == 1
    assert len(posthog["requests"]) == 1


def test_annotation_enqueue_failure_retries_same_event(workspace, imported, posthog, monkeypatch):
    receipt = receipt_for(workspace, imported, monkeypatch)
    fields = {
        "workspace": workspace,
        "audit_id": imported,
        "receipt": receipt,
        "entry_id": "retry-001",
    }

    def fail(*args):
        raise sqlite3.OperationalError("private database path")

    with monkeypatch.context() as isolated:
        isolated.setattr(usage, "_enqueue", fail)
        assert track("knowledge_investigated", **fields) == {"status": "unavailable"}
    annotations = json.loads(workspace.audit_path(imported).with_name("usage.json").read_text())
    staged = next(iter(annotations["entries"].values()))["payload"]
    assert track("knowledge_investigated", **fields)["status"] == "accepted"
    assert received(posthog) == [staged]


def test_annotation_symlink_is_rejected(workspace, imported, posthog, monkeypatch, tmp_path):
    receipt = receipt_for(workspace, imported, monkeypatch)
    outside = tmp_path / "private.json"
    outside.write_text("private", encoding="utf-8")
    workspace.audit_path(imported).with_name("usage.json").symlink_to(outside)
    assert (
        track(
            "knowledge_investigated",
            workspace=workspace,
            audit_id=imported,
            receipt=receipt,
            entry_id="retry-001",
        )["status"]
        == "invalid_event"
    )
    assert outside.read_text() == "private" and posthog["requests"] == []


def test_changed_token_never_sends_old_queue_to_new_project(posthog, monkeypatch):
    posthog["response"] = httpx.Response(503)
    track("skill_invoked", skill="audit")
    old_id = pending()[0]["uuid"]
    monkeypatch.setenv("AGENTAGON_POSTHOG_PROJECT_TOKEN", "phc_newTelemetryProject12345")
    posthog["response"] = httpx.Response(200, json={"status": 1})
    assert track("skill_invoked", skill="review")["status"] == "accepted"
    last = json.loads(posthog["requests"][-1].content)
    assert last["api_key"] == "phc_newTelemetryProject12345"
    assert len(last["batch"]) == 1 and last["batch"][0]["uuid"] != old_id
