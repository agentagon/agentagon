"""Explicit bounded imports from local JSON or a connected provider trace."""

import json
import uuid

from agentagon.capabilities.traces import snapshots
from agentagon.capabilities.traces.detail import trace_usability
from agentagon.capabilities.traces.normalize import normalize, redact, unpack
from agentagon.core.records import PROVIDERS, AuditError, digest, encoded, validate_record
from agentagon.workflows.runtime import operation_id


def _decode(raw):
    if len(encoded(raw).encode()) > snapshots.MAX_IMPORT_BYTES:
        raise AuditError("trace import exceeds 20 MB")
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        try:
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        except ValueError as exc:
            raise AuditError("paste supported JSON or JSONL trace data") from exc


def _local_preview(raw, provider):
    items, trace_ids, invalid = [], set(), 0
    for index, (row, locator) in enumerate(unpack(raw, provider)):
        if index >= 10000:
            raise AuditError("trace import exceeds 10000 spans")
        try:
            span = normalize(row, provider, "local", locator)
            validate_record("trace", span, definition="span")
        except (
            AuditError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RecursionError,
        ):
            invalid += 1
            continue
        trace_ids.add(span["trace_id"])
        if len(trace_ids) > 100:
            raise AuditError("select at most 100 traces")
        items.append(redact(row))
    record = {
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
    return record, trace_usability(record)


def _format_score(raw, provider):
    """Prefer an unmistakable export shape when generic adapters also parse it."""

    score = 0
    inspected = 0
    pending = [raw]
    while pending and inspected < 200:
        value = pending.pop()
        inspected += 1
        if isinstance(value, list):
            pending.extend(value[: 200 - inspected])
            continue
        if not isinstance(value, dict):
            continue
        keys = set(value)
        if provider == "otlp":
            score += 12 if "resourceSpans" in keys else 0
            score += (
                6
                if {"traceId", "spanId"} <= keys
                and ("startTimeUnixNano" in keys or "endTimeUnixNano" in keys)
                else 0
            )
        elif provider == "braintrust":
            score += (
                8
                if "span_id" in keys
                and keys
                & {
                    "root_span_id",
                    "span_parents",
                    "span_attributes",
                    "metrics",
                }
                else 0
            )
        elif provider == "langsmith":
            score += (
                8
                if "id" in keys
                and keys
                & {
                    "run_type",
                    "parent_run_id",
                    "parent_run_ids",
                    "start_time",
                    "inputs",
                    "outputs",
                }
                else 0
            )
        elif provider == "langfuse":
            score += (
                8
                if {"id", "traceId"} <= keys
                and keys
                & {
                    "parentObservationId",
                    "startTime",
                    "usageDetails",
                    "providedModelName",
                }
                else 0
            )
        elif provider == "phoenix":
            context = value.get("context")
            score += (
                10
                if isinstance(context, dict)
                and {
                    "trace_id",
                    "span_id",
                }
                <= set(context)
                else 0
            )
            attributes = value.get("attributes")
            score += (
                4
                if isinstance(attributes, dict)
                and any(str(key).startswith("openinference.") for key in attributes)
                else 0
            )
        pending.extend(child for child in value.values() if isinstance(child, (dict, list)))
    return score


def _preview_projection(preview_id, record, usability, *, detected, candidates):
    return {
        "preview_id": preview_id,
        "state": usability["state"],
        "diagnosis_ready": usability["diagnosis_ready"],
        "provider": record["provenance"].get("provider"),
        "provider_detected": detected,
        "provider_candidates": candidates,
        "trace_count": usability["trace_count"],
        "normalized_spans": usability["normalized_spans"],
        "inspected_records": usability["inspected_records"],
        "unusable_records": (
            usability["reported_unusable_records"]
            + usability["normalization_failures"]
            + usability["conflicting_records"]
        ),
        "incomplete_traces": usability["incomplete_traces"],
        "redaction": "Recognized secrets are redacted before this evidence is retained.",
        "blockers": usability["blockers"],
        "limitations": usability["limitations"],
        "source_complete": bool(record["completeness"].get("complete")),
    }


def preview_trace(application, project_id, payload):
    """Validate a trace selection without writing immutable project evidence."""

    if not isinstance(payload, dict) or set(payload) - {
        "provider",
        "data",
        "connection_id",
        "trace_id",
        "selection",
    }:
        raise AuditError("unsupported trace preview fields")
    application.state.workspace(project_id)
    detected = False
    candidates = []
    if payload.get("connection_id"):
        if "data" in payload or "provider" in payload:
            raise AuditError("choose local data or a provider reference")
        selection = payload.get("selection", {})
        if not isinstance(selection, dict):
            raise AuditError("trace selection must be an object")
        selection = dict(selection)
        if payload.get("trace_id"):
            selection["trace_id"] = payload["trace_id"]
        result = application.preview(
            project_id,
            {
                "connection_id": payload["connection_id"],
                "kind": "traces",
                "selection": selection,
            },
        )
        preview_id = result["preview_id"]
        record = {key: value for key, value in result.items() if key != "preview_id"}
        usability = trace_usability(record)
        candidates = [record["provenance"].get("provider")]
    else:
        raw = _decode(payload.get("data"))
        requested = payload.get("provider")
        if requested not in {*PROVIDERS, "auto", None}:
            raise AuditError("choose a supported trace format")
        attempts = []
        for provider in PROVIDERS if requested in {"auto", None} else (requested,):
            try:
                record, usability = _local_preview(raw, provider)
            except (AuditError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
                continue
            if usability["normalized_spans"]:
                attempts.append((record, usability, _format_score(raw, provider)))
        if not attempts:
            return {
                "preview_id": None,
                "state": "unsupported",
                "diagnosis_ready": False,
                "provider": None,
                "provider_detected": False,
                "provider_candidates": [],
                "trace_count": 0,
                "normalized_spans": 0,
                "inspected_records": 0,
                "unusable_records": 0,
                "incomplete_traces": 0,
                "redaction": "No data was retained.",
                "blockers": [
                    {
                        "code": "unsupported_payload",
                        "message": "No supported trace spans were recognized. Check the export format.",
                    }
                ],
                "limitations": [],
                "source_complete": False,
            }
        attempts.sort(
            key=lambda item: (
                item[1]["normalized_spans"],
                -item[1]["normalization_failures"],
                item[2],
                item[0]["provenance"]["provider"],
            ),
            reverse=True,
        )
        best_score = (
            attempts[0][1]["normalized_spans"],
            -attempts[0][1]["normalization_failures"],
            attempts[0][2],
        )
        tied = [
            item
            for item in attempts
            if (
                item[1]["normalized_spans"],
                -item[1]["normalization_failures"],
                item[2],
            )
            == best_score
        ]
        candidates = [item[0]["provenance"]["provider"] for item in attempts]
        if requested in {"auto", None} and len(tied) > 1:
            return {
                "preview_id": None,
                "state": "needs_format",
                "diagnosis_ready": False,
                "provider": None,
                "provider_detected": False,
                "provider_candidates": sorted(candidates),
                "trace_count": attempts[0][1]["trace_count"],
                "normalized_spans": attempts[0][1]["normalized_spans"],
                "inspected_records": attempts[0][1]["inspected_records"],
                "unusable_records": attempts[0][1]["normalization_failures"],
                "incomplete_traces": attempts[0][1]["incomplete_traces"],
                "redaction": "No data was retained until you choose the matching format.",
                "blockers": [
                    {
                        "code": "ambiguous_format",
                        "message": "Several formats matched. Choose the source format and preview again.",
                    }
                ],
                "limitations": [],
                "source_complete": False,
            }
        record, usability, _ = attempts[0]
        detected = requested in {"auto", None}
        preview_id = "preview_" + uuid.uuid4().hex[:24]
        record["project_id"] = project_id
        with application.lock:
            if len(application.previews) >= 12:
                application.previews.pop(next(iter(application.previews)))
            application.previews[preview_id] = record
    return _preview_projection(
        preview_id, record, usability, detected=detected, candidates=sorted(set(candidates))
    )


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
            provenance = existing["provenance"]
            confirmed = provenance.get("import_operation") or {}
            existing_operation = provenance.get("operation_id") or confirmed.get("operation_id")
            if existing_operation != operation:
                continue
            if (
                provenance.get("operation_id") == operation
                and provenance.get("request_digest") == binding
            ):
                return existing
            raise AuditError("operation_id already belongs to another import")
        if payload.get("connection_id"):
            if "data" in payload or "provider" in payload:
                raise AuditError("choose local data or a provider reference")
            selection = payload.get("selection", {})
            if not isinstance(selection, dict):
                raise AuditError("trace selection must be an object")
            selection = dict(selection)
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
            preview, usability = _local_preview(_decode(payload.get("data")), provider)
            if not usability["normalized_spans"]:
                raise AuditError("no supported trace spans found")
        usability = trace_usability(preview)
        if not usability["diagnosis_ready"]:
            raise AuditError("no supported trace spans found")
        preview["completeness"].update(
            diagnosis_ready=True,
            normalized_spans=usability["normalized_spans"],
            normalization_failures=usability["normalization_failures"],
            unusable_records=(
                usability["reported_unusable_records"]
                + usability["normalization_failures"]
                + usability["conflicting_records"]
            ),
        )
        if usability["state"] != "ready":
            preview["completeness"]["complete"] = False
        preview["provenance"].update(operation_id=operation, request_digest=binding)
        return snapshots.summary(snapshots.save(workspace, project_id, preview))
