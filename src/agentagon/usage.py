"""Anonymous product events. One entry point; delivery is bounded and optional."""

import asyncio
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from agentagon import __version__
from agentagon.core.records import AuditError, digest, load_json, validate_record
from agentagon.installation import SKILLS
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace

# Public ingestion token, not a personal or project-secret API key.
POSTHOG_PROJECT_TOKEN = "phc_CUB2gYkuZX7TsLaJmTxqUg7iCbuitCxkYW4LVgcy9HKn"
POSTHOG_URL = "https://us.i.posthog.com/batch/"
MAX_EVENTS = 1000
MAX_BYTES = 1024 * 1024
MAX_AGE = 7 * 86400
BATCH_SIZE = 20
REQUEST_SECONDS = 2
EVENTS = (
    "skill_invoked",
    "intelligence_lookup_completed",
    "knowledge_returned",
    "knowledge_investigated",
    "knowledge_cited",
)
OUTCOMES = {
    "complete",
    "missing_key",
    "missing_endpoint",
    "unauthorized",
    "rate_limited",
    "unavailable",
    "invalid_response",
}


def _token() -> str:
    value = os.environ.get("AGENTAGON_POSTHOG_PROJECT_TOKEN", POSTHOG_PROJECT_TOKEN)
    return value if re.fullmatch(r"phc_[A-Za-z0-9]{10,200}", value) else ""


def _enabled(config: Config) -> bool:
    return (
        os.environ.get("AGENTAGON_TELEMETRY_DISABLED") != "1"
        and config.effective()["telemetry"]["enabled"]
    )


def _path(config: Config) -> Path:
    directory = config.path.with_name(config.path.name + ".telemetry")
    path = directory / "queue.sqlite3"
    if directory.is_symlink() or any(
        Path(str(path) + suffix).is_symlink() for suffix in ("", "-journal", "-wal", "-shm")
    ):
        raise AuditError("unsafe telemetry storage")
    return path


def _connect(config: Config) -> sqlite3.Connection:
    path = _path(config)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    db = sqlite3.connect(path, timeout=0.1)
    try:
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery (
                id INTEGER PRIMARY KEY CHECK(id=1), destination TEXT NOT NULL DEFAULT '',
                next_attempt REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
                lease TEXT, lease_until REAL NOT NULL DEFAULT 0,
                last_status TEXT NOT NULL DEFAULT 'idle', dropped INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO delivery(id) VALUES(1);
        """)
        return db
    except Exception:
        db.close()
        raise


def _clear(db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM events")
    db.execute("""UPDATE delivery SET next_attempt=0, failures=0, lease=NULL,
        lease_until=0, last_status='disabled' WHERE id=1""")


def _purge(config: Config) -> None:
    """Called after disabling. Never create a queue just to clear it."""
    try:
        if _path(config).exists():
            with closing(_connect(config)) as db, db:
                _clear(db)
    except (AuditError, OSError, sqlite3.Error):
        # Disabled senders also clear before doing anything else; reads never send.
        return


def _summary(config: Config, settings: dict) -> dict:
    result = {
        "enabled": settings["telemetry"]["enabled"]
        and os.environ.get("AGENTAGON_TELEMETRY_DISABLED") != "1",
        "configured": bool(_token()),
        "pending": 0,
        "dropped": 0,
        "delivery": "idle",
    }
    try:
        path = _path(config)
        if path.exists():
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
                result["pending"] = db.execute("SELECT count(*) FROM events").fetchone()[0]
                status = db.execute(
                    "SELECT dropped,last_status FROM delivery WHERE id=1"
                ).fetchone()
                result.update(dropped=status[0], delivery=status[1])
    except (AuditError, OSError, sqlite3.Error, TypeError):
        result.update(pending=None, delivery="storage_unavailable")
    return result


def _payload(event: str, properties: dict) -> dict:
    event_id = str(uuid4())
    return {
        "uuid": event_id,
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
        "properties": {
            "distinct_id": event_id,
            "$process_person_profile": False,
            "$geoip_disable": True,
            "$ip": None,
            "schema_version": 1,
            "agentagon_version": __version__,
            **properties,
        },
    }


def _valid_payload(payload: dict) -> bool:
    """Recheck the closed contract on disk, including immediately before sending."""
    try:
        if set(payload) != {"uuid", "event", "timestamp", "properties"}:
            return False
        event = payload["event"]
        if event not in EVENTS or str(UUID(payload["uuid"], version=4)) != payload["uuid"]:
            return False
        at = datetime.fromisoformat(payload["timestamp"])
        if (
            at.utcoffset() is None
            or not time.time() - MAX_AGE <= at.timestamp() <= time.time() + 300
        ):
            return False
        properties = payload["properties"]
        common = {
            "distinct_id",
            "$process_person_profile",
            "$geoip_disable",
            "$ip",
            "schema_version",
            "agentagon_version",
        }
        if (
            not common <= properties.keys()
            or properties["distinct_id"] != payload["uuid"]
            or properties["$process_person_profile"] is not False
            or properties["$geoip_disable"] is not True
            or properties["$ip"] is not None
            or type(properties["schema_version"]) is not int
            or properties["schema_version"] != 1
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", properties["agentagon_version"])
        ):
            return False
        fields = properties.keys() - common
        if event == "skill_invoked":
            return (
                fields == {"skill", "host"}
                and properties["skill"] in SKILLS
                and properties["host"] in {"codex", "claude-code", "unknown"}
            )
        if event == "intelligence_lookup_completed":
            return (
                fields == {"outcome", "phase", "duration_ms", "cached", "result_count"}
                and properties["outcome"] in OUTCOMES
                and properties["phase"] in {"initial", "follow_up"}
                and type(properties["cached"]) is bool
                and type(properties["duration_ms"]) is int
                and 0 <= properties["duration_ms"] <= 300_000
                and type(properties["result_count"]) is int
                and 0 <= properties["result_count"] <= 5
            )
        return (
            fields in ({"entry_id"}, {"entry_id", "knowledge_version"})
            and re.fullmatch(r"[a-z0-9-]{1,100}", properties["entry_id"]) is not None
            and (
                "knowledge_version" not in properties
                or re.fullmatch(r"[a-f0-9]{64}", properties["knowledge_version"]) is not None
            )
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _prepare(event: str, fields: dict) -> tuple[list[dict], Workspace | None, Path | None]:
    """Construct public fields from enums and validated local records only."""
    if event not in EVENTS:
        raise AuditError("unknown telemetry event")
    if event == "skill_invoked":
        if set(fields) - {"skill", "host"} or fields.get("skill") not in SKILLS:
            raise AuditError("invalid skill event")
        host = fields.get("host", "unknown")
        if host not in {"codex", "claude-code", "unknown"}:
            raise AuditError("invalid host")
        return [{"payload": _payload(event, {"skill": fields["skill"], "host": host})}], None, None

    lookup = event == "intelligence_lookup_completed"
    owner_fields = {"audit_id": "audit", "evaluation_id": "eval", "run_id": "fix"}
    supplied = owner_fields.keys() & fields.keys()
    if len(supplied) != 1:
        raise AuditError("supply exactly one Intelligence receipt owner")
    owner_field = next(iter(supplied))
    workflow = owner_fields[owner_field]
    if event in {"knowledge_investigated", "knowledge_cited"} and workflow != "audit":
        raise AuditError("investigated and cited events require an audit")
    required = {owner_field, "receipt"} | ({"duration_ms", "cached"} if lookup else {"entry_id"})
    if event == "knowledge_cited":
        required.add("finding_id")
    if not required <= fields.keys() or fields.keys() - required - {"workspace"}:
        raise AuditError("invalid telemetry fields")
    workspace = fields.get("workspace", Path("."))
    if not isinstance(workspace, Workspace):
        workspace = Workspace(Path(workspace))
    from agentagon.lookup import owners

    audit = owners.load(workspace, workflow, fields[owner_field])
    # Match the registered path before opening any caller-selected file.
    if fields["receipt"] not in {item["path"] for item in audit.get("intelligence", [])}:
        raise AuditError(f"receipt does not belong to this {workflow}")
    receipt = workspace.read_artifact(fields["receipt"])
    if receipt["status"] not in OUTCOMES or receipt["phase"] not in {"initial", "follow_up"}:
        raise AuditError("invalid receipt metadata")
    response = receipt.get("response", {})
    if receipt["status"] == "complete":
        validate_record("intelligence-response", response)
    suggestions = response.get("suggestions", [])
    entries = {item["id"]: item for item in suggestions}
    records = []
    if lookup:
        if (
            type(fields["duration_ms"]) is not int
            or not 0 <= fields["duration_ms"] <= 300_000
            or type(fields["cached"]) is not bool
            or (fields["cached"] and receipt["status"] != "complete")
        ):
            raise AuditError("invalid lookup measurements")
        records.append(
            {
                "payload": _payload(
                    event,
                    {
                        "outcome": receipt["status"],
                        "phase": receipt["phase"],
                        "duration_ms": fields["duration_ms"],
                        "cached": fields["cached"],
                        "result_count": len(suggestions),
                    },
                )
            }
        )
        entry_ids = [] if fields["cached"] else list(entries)
        stage = "knowledge_returned"
    else:
        if receipt["status"] != "complete" or fields["entry_id"] not in entries:
            raise AuditError("entry was not returned in this receipt")
        if event == "knowledge_cited" and fields["finding_id"] not in {
            finding["id"]
            for diagnosis in audit["diagnoses"].values()
            for finding in diagnosis.get("findings", [])
        }:
            raise AuditError("finding does not belong to this audit")
        entry_ids, stage = [fields["entry_id"]], event
    for entry_id in entry_ids:
        # Authored entry IDs use this grammar. Legacy opaque versions are omitted
        # unless they match the service's SHA-256 response fingerprint.
        if not re.fullmatch(r"[a-z0-9-]{1,100}", entry_id):
            if not lookup:
                raise AuditError("unsupported knowledge ID")
            continue
        properties = {"entry_id": entry_id}
        version = response["knowledge_version"]
        if re.fullmatch(r"[a-f0-9]{64}", version):
            properties["knowledge_version"] = version
        annotation = {
            "stage": stage,
            "receipt": fields["receipt"],
            "entry_id": entry_id,
        }
        if stage == "knowledge_cited":
            annotation["finding_id"] = fields["finding_id"]
        records.append(
            {
                "key": digest([fields["receipt"], entry_id, stage]),
                "annotation": annotation,
                "payload": _payload(stage, properties),
            }
        )
    return records, workspace, owners.usage_path(workspace, workflow, fields[owner_field])


def _enqueue(config: Config, records: list[dict], token: str) -> bool:
    with closing(_connect(config)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if not _enabled(config):
            _clear(db)
            return False
        destination = digest([POSTHOG_URL, token])
        previous = db.execute("SELECT destination FROM delivery WHERE id=1").fetchone()[0]
        if previous and previous != destination:
            _clear(db)
        db.execute("UPDATE delivery SET destination=? WHERE id=1", (destination,))
        for record in records:
            payload = record["payload"]
            if not _valid_payload(payload):
                raise AuditError("invalid event payload")
            db.execute(
                "INSERT OR IGNORE INTO events VALUES(?,?,?)",
                (
                    payload["uuid"],
                    json.dumps(payload, separators=(",", ":")),
                    datetime.fromisoformat(payload["timestamp"]).timestamp(),
                ),
            )
        expired = db.execute(
            "DELETE FROM events WHERE created<?", (time.time() - MAX_AGE,)
        ).rowcount
        rows = db.execute(
            "SELECT id,length(CAST(payload AS BLOB)) AS size FROM events ORDER BY rowid DESC"
        ).fetchall()
        size, dropped = 0, []
        for index, row in enumerate(rows):
            size += row["size"]
            if index >= MAX_EVENTS or size > MAX_BYTES:
                dropped.append((row["id"],))
        db.executemany("DELETE FROM events WHERE id=?", dropped)
        db.execute("UPDATE delivery SET dropped=dropped+? WHERE id=1", (expired + len(dropped),))
    return True


def _record(config: Config, event: str, fields: dict, token: str) -> bool:
    records, workspace, path = _prepare(event, fields)
    if workspace is None:
        return _enqueue(config, records, token)
    with workspace.locked():
        if path.is_symlink():
            raise AuditError("unsafe usage annotations")
        annotations = load_json(path) if path.exists() else {"version": 1, "entries": {}}
        pending = []
        for record in records:
            if "key" not in record:
                pending.append(record)
                continue
            saved = annotations["entries"].setdefault(record["key"], {**record, "queued": False})
            if not saved["queued"]:
                pending.append(saved)
        # Save UUIDs before enqueueing. A failed write can retry the same identity.
        if any("key" in record for record in pending):
            workspace.write(path, annotations)
        queued = _enqueue(config, pending, token)
        if queued and any("key" in record for record in pending):
            for record in pending:
                if "key" in record:
                    record["queued"] = True
            workspace.write(path, annotations)
        return queued


async def _post(payloads: list[dict], token: str) -> tuple[str, int]:
    try:
        async with asyncio.timeout(REQUEST_SECONDS):
            async with httpx.AsyncClient(
                timeout=REQUEST_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST", POSTHOG_URL, json={"api_key": token, "batch": payloads}
                ) as response:
                    retry = response.headers.get("Retry-After", "")
                    retry_after = (
                        min(3600, int(retry)) if retry.isdigit() and len(retry) <= 8 else 0
                    )
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        return "retry", retry_after
                    if response.status_code != 200:
                        return "rejected", 0
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 8192:
                            return "invalid_response", 0
                    result = json.loads(body)
                    if not isinstance(result, dict):
                        return "invalid_response", 0
                    if result.get("quota_limited"):
                        return "quota_limited", 3600
                    status = result.get("status")
                    if (type(status) is int and status == 1) or status == "Ok":
                        return "accepted", 0
                    return "invalid_response", 0
    except (TimeoutError, httpx.HTTPError, ValueError):
        return "unavailable", 0


def _deliver(config: Config, token: str) -> str:
    if not token:
        return "unconfigured"
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        # Python callers inside an async application still enqueue; the next
        # synchronous invocation delivers without a thread or nested event loop.
        return "queued"
    with closing(_connect(config)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        state = db.execute("SELECT * FROM delivery WHERE id=1").fetchone()
        if state["destination"] != digest([POSTHOG_URL, token]):
            return "queued"
        if state["next_attempt"] > time.time() or state["lease_until"] > time.time():
            return "queued"
        batch = db.execute(
            "SELECT id,payload FROM events ORDER BY rowid LIMIT ?", (BATCH_SIZE,)
        ).fetchall()
        valid = []
        for row in batch:
            try:
                payload = json.loads(row["payload"])
            except ValueError:
                payload = {}
            if _valid_payload(payload) and row["id"] == payload["uuid"]:
                valid.append(row)
            else:
                db.execute("DELETE FROM events WHERE id=?", (row["id"],))
                db.execute("UPDATE delivery SET dropped=dropped+1 WHERE id=1")
        batch = valid
        if not batch:
            return "idle"
        lease = str(uuid4())
        db.execute(
            "UPDATE delivery SET lease=?,lease_until=? WHERE id=1", (lease, time.time() + 30)
        )
    if not _enabled(config):
        _purge(config)
        return "disabled"
    if _token() != token:
        return "queued"
    outcome, retry_after = asyncio.run(_post([json.loads(row["payload"]) for row in batch], token))
    with closing(_connect(config)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT lease FROM delivery WHERE id=1").fetchone()[0] != lease:
            return outcome
        if outcome in {"accepted", "rejected"}:
            db.executemany("DELETE FROM events WHERE id=?", [(row["id"],) for row in batch])
            db.execute(
                "UPDATE delivery SET failures=0,next_attempt=0,dropped=dropped+? WHERE id=1",
                (len(batch) if outcome == "rejected" else 0,),
            )
        else:
            failures = min(state["failures"] + 1, 7)
            delay = max(retry_after, min(3600, 60 * 2 ** (failures - 1)))
            db.execute(
                "UPDATE delivery SET failures=?,next_attempt=? WHERE id=1",
                (failures, time.time() + delay),
            )
        db.execute(
            "UPDATE delivery SET lease=NULL,lease_until=0,last_status=? WHERE id=1", (outcome,)
        )
    return outcome


def track(event: str, /, **fields) -> dict:
    """Validate, retain and attempt delivery; never raise a telemetry failure.

    Skill events accept skill and optional host. Knowledge/lookup events accept
    local workspace and receipt references. Lookup/returned owners are audit_id,
    evaluation_id or run_id; investigated/cited stages require audit_id.
    """
    try:
        config = Config()
        if not _enabled(config):
            _purge(config)
            return {"status": "disabled"}
        token = _token()
        if not _record(config, event, fields, token):
            return {"status": "disabled"}
        return {"status": _deliver(config, token)}
    except AuditError:
        return {"status": "invalid_event"}
    except Exception:
        # This is an optional boundary. Neither exception text nor traceback may
        # expose caller fields, credentials or private paths to logs or the host.
        return {"status": "unavailable"}
