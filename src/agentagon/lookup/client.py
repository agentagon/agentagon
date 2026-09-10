"""Bounded, redacted guidance requests with private local receipts."""

import asyncio
import json
import re
import time

import httpx

from agentagon.core.records import AuditError, digest, now, validate_record
from agentagon.lookup import owners
from agentagon.storage.config import Config, credential
from agentagon.storage.workspace import Workspace
from agentagon.usage import track

REQUEST_TIMEOUT_SECONDS = 30

PATTERNS = (
    r"(?i)\b(?:bearer\s+\S+|(?:sk-|agi_)[A-Za-z0-9_\-]{16,})",
    r"(?i)\b(?:api[_ -]?key|password|secret|access[_ -]?token)\s*[:=]\s*[^\s,;]+",
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    r"(?i)https?://[^\s<>]+",
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
    r"(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)",
    r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)[^\s]+",
)


def redact_query(value: str, secrets: tuple[str, ...]) -> str:
    if "PRIVATE KEY" in value or "```" in value:
        raise AuditError("submit an abstract summary without credentials or code blocks")
    for key in secrets:
        if key:
            value = value.replace(key, "[REDACTED]")
    for pattern in PATTERNS:
        value = re.sub(pattern, "[REDACTED]", value)
    return value.strip()


def service_url(settings: dict, workflow: str = "audit") -> str | None:
    owners.validate_workflow(workflow)
    value = settings["intelligence"]["endpoint"]
    if not value:
        return None
    return value + f"/v1/{workflow}"


async def _request(
    url: str, key: str, payload: dict, transport: httpx.AsyncBaseTransport | None
) -> dict:
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                transport=transport,
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST", url, headers={"Authorization": f"Bearer {key}"}, json=payload
                ) as response:
                    if response.status_code != 200:
                        names = {401: "unauthorized", 403: "unauthorized", 429: "rate_limited"}
                        result = {
                            "status": names.get(response.status_code, "unavailable"),
                            "http_status": response.status_code,
                        }
                        retry = response.headers.get("Retry-After", "")
                        if retry.isdigit() and len(retry) <= 8:
                            result["retry_after"] = int(retry)
                        return result
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 128 * 1024:
                            return {"status": "invalid_response"}
                    data = json.loads(body)
                    validate_record("intelligence-response", data)
                    suggestions = data["suggestions"]
                    if len(suggestions) > payload["limit"] or len(
                        {s["id"] for s in suggestions}
                    ) != len(suggestions):
                        return {"status": "invalid_response"}
                    if key in json.dumps(data):
                        return {"status": "invalid_response"}
                    request_id = response.headers.get("X-Request-ID", "")
                    return {
                        "status": "complete",
                        "response": data,
                        "request_id": request_id
                        if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id)
                        else None,
                    }
    except (TimeoutError, httpx.HTTPError):
        return {"status": "unavailable"}
    except (ValueError, UnicodeError):
        return {"status": "invalid_response"}


def _validate_request(payload: dict, workflow: str = "audit") -> None:
    contract = "intelligence-request" if workflow == "audit" else f"intelligence-{workflow}-request"
    validate_record(contract, payload)
    names = ("context", "goal") if workflow == "eval" else ("context", "focus")
    text_fields = [payload[field] for field in names if field in payload]
    try:
        for value in text_fields:
            value.encode("utf-8")
    except UnicodeError as exc:
        raise AuditError(f"{' and '.join(names)} must be valid UTF-8") from exc
    if sum(len(value) for value in text_fields) > 4000:
        raise AuditError(f"{' and '.join(names)} together must be at most 4,000 characters")


def lookup(
    workspace: Workspace,
    audit_id: str,
    context: str | None = None,
    *,
    workflow: str = "audit",
    focus: str | None = None,
    goal: str | None = None,
    limit: int = 5,
    phase: str = "initial",
    refresh: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Consult guidance for an audit, evaluation or fix run using explicit text only.

    ``audit_id`` remains the positional owner ID for compatibility with audit callers.
    """
    started = time.monotonic()
    owners.validate_workflow(workflow)
    if phase not in {"initial", "follow_up"}:
        raise AuditError("lookup phase must be initial or follow_up")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise AuditError("lookup limit must be a positive integer")
    payload = {"limit": min(limit, 5)}
    for field, value in (("context", context), ("focus", focus), ("goal", goal)):
        if value is not None:
            payload[field] = value
    _validate_request(payload, workflow)
    settings = Config().effective(workspace.root)
    key = credential(settings, "intelligence")
    secrets = (
        key,
        credential(settings, "traces"),
        credential(settings, "traces", "public_key_env"),
    )
    for field in ("context", "focus", "goal"):
        if field in payload:
            payload[field] = redact_query(payload[field], secrets)
    _validate_request(payload, workflow)
    endpoint = service_url(settings, workflow)
    request_digest = digest([endpoint, phase, payload])
    cached = None
    with owners.locked(workspace, workflow, audit_id):
        audit = owners.load(workspace, workflow, audit_id)
        for receipt in reversed(audit.get("intelligence", [])):
            if (
                receipt["request_digest"] == request_digest
                and receipt["status"] == "complete"
                and not refresh
            ):
                cached = {
                    **workspace.read_artifact(receipt["path"]),
                    "receipt": receipt["path"],
                    "cached": True,
                }
                break
        generation = owners.binding(workflow, audit)
    owner_field = {"audit": "audit_id", "eval": "evaluation_id", "fix": "run_id"}[workflow]
    if cached is not None:
        track(
            "intelligence_lookup_completed",
            workspace=workspace,
            **{owner_field: audit_id},
            receipt=cached["receipt"],
            cached=True,
            duration_ms=min(300_000, round((time.monotonic() - started) * 1000)),
        )
        return cached
    if not key:
        outcome = {"status": "missing_key"}
    elif endpoint is None:
        outcome = {"status": "missing_endpoint"}
    else:
        outcome = asyncio.run(_request(endpoint, key, payload, transport))
    record = {
        "phase": phase,
        "request_digest": request_digest,
        "at": now(),
        "endpoint": endpoint,
        "request": payload,
        **outcome,
    }
    with owners.locked(workspace, workflow, audit_id):
        audit = owners.load(workspace, workflow, audit_id)
        if owners.binding(workflow, audit) != generation:
            raise AuditError(f"{workflow} inputs changed during lookup; repeat for current inputs")
        path = workspace.artifact(record)
        audit.setdefault("intelligence", []).append(
            {
                "phase": phase,
                "request_digest": request_digest,
                "status": record["status"],
                "path": path,
            }
        )
        owners.save(workspace, workflow, audit)
    track(
        "intelligence_lookup_completed",
        workspace=workspace,
        **{owner_field: audit_id},
        receipt=path,
        cached=False,
        duration_ms=min(300_000, round((time.monotonic() - started) * 1000)),
    )
    return {**record, "receipt": path, "cached": False}
