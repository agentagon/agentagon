"""Explicit bounded imports from local JSON or a connected provider trace."""

import json

from agentagon.capabilities.traces import snapshots
from agentagon.capabilities.traces.normalize import normalize, redact, unpack
from agentagon.core.records import PROVIDERS, AuditError, digest, encoded
from agentagon.workflows.runtime import operation_id


def import_trace(application, project_id, payload):
    if not isinstance(payload, dict) or set(payload) - {
        "operation_id",
        "provider",
        "data",
        "connection_id",
        "trace_id",
        "selection",
    }:
        raise AuditError("unsupported trace import fields")
    operation = operation_id(payload.get("operation_id"))
    binding = digest(payload)
    workspace = application.state.workspace(project_id)
    workspace.initialize()
    with application.lock:
        for existing in snapshots.list_snapshots(workspace):
            if existing["provenance"].get("operation_id") == operation:
                if existing["provenance"].get("request_digest") != binding:
                    raise AuditError("operation_id already belongs to another import")
                return existing
        if payload.get("connection_id"):
            if "data" in payload or "provider" in payload:
                raise AuditError("choose local data or a provider reference")
            selection = dict(payload.get("selection", {}))
            if payload.get("trace_id"):
                selection["trace_id"] = payload["trace_id"]
            preview = application.preview(
                project_id,
                {
                    "connection_id": payload["connection_id"],
                    "kind": "traces",
                    "selection": selection,
                },
            )
        else:
            provider = payload.get("provider")
            if provider not in PROVIDERS:
                raise AuditError("choose a supported trace format")
            raw = payload.get("data")
            if len(encoded(raw).encode()) > snapshots.MAX_IMPORT_BYTES:
                raise AuditError("trace import exceeds 20 MB")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    try:
                        raw = [json.loads(line) for line in raw.splitlines() if line.strip()]
                    except ValueError as exc:
                        raise AuditError("paste supported JSON or JSONL trace data") from exc
            items, trace_ids, invalid = [], set(), 0
            for index, (row, locator) in enumerate(unpack(raw, provider)):
                if index >= 10000:
                    raise AuditError("trace import exceeds 10000 spans")
                try:
                    span = normalize(row, provider, "local", locator)
                except (AuditError, ValueError, TypeError, KeyError):
                    invalid += 1
                    continue
                trace_ids.add(span["trace_id"])
                if len(trace_ids) > 100:
                    raise AuditError("select at most 100 traces")
                items.append(redact(row))
            if not items:
                raise AuditError("no supported trace spans found")
            preview = {
                "kind": "traces",
                "connection_id": None,
                "selection": {"project": "local"},
                "items": items,
                "provenance": {"provider": provider, "project": "local", "source": "supplied_data"},
                "completeness": {
                    "complete": not invalid,
                    "unusable_records": invalid,
                    "count": len(items),
                    "selected_traces": len(trace_ids),
                    "scope": "supplied_data_only",
                },
            }
        preview["provenance"].update(operation_id=operation, request_digest=binding)
        return snapshots.summary(snapshots.save(workspace, project_id, preview))
