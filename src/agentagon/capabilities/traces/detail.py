"""Bounded public projections for immutable trace snapshots."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from agentagon.capabilities.traces import snapshots
from agentagon.capabilities.traces.normalize import normalize, redact, unpack
from agentagon.core.records import PROVIDERS, AuditError, validate_record

DEFAULT_DETAIL_SPANS = 100
MAX_DETAIL_SPANS = 200
MAX_TRACE_INDEX = 100
MAX_NORMALIZED_RECORDS = 10_000
MAX_DIAGNOSTICS = 50
MAX_ID_CHARS = 2_048
MAX_PARENT_IDS = 50
MAX_SUMMARY_IDS = 100
MAX_ERRORS = 100
MAX_SPAN_CONTENT_BYTES = 24_000
MAX_VALUE_DEPTH = 6
MAX_VALUE_ITEMS = 50
MAX_VALUE_NODES = 500
MAX_STRING_BYTES = 8_000


@dataclass
class _ContentBudget:
    remaining: int = MAX_SPAN_CONTENT_BYTES
    nodes: int = MAX_VALUE_NODES
    truncated: bool = False
    omitted_items: int = 0
    redacted_values: int = 0


_OMITTED = object()


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _message(exc: Exception) -> str:
    if isinstance(exc, AuditError):
        value = str(exc)
        return value[:300] if value else "unsupported normalized record"
    return "unsupported normalized record"


def _diagnostic(diagnostics: list[dict], locator: str, code: str, message: str) -> None:
    if len(diagnostics) < MAX_DIAGNOSTICS:
        diagnostics.append(
            {
                "locator": locator[:500],
                "code": code,
                "message": message[:300],
            }
        )


def _reported_unusable(record: dict) -> int:
    value = _object(record.get("completeness")).get("unusable_records", 0)
    return value if type(value) is int and value >= 0 else 0


def _project_name(record: dict) -> tuple[str, bool]:
    value = _object(record.get("selection")).get("project") or _object(
        record.get("provenance")
    ).get("project")
    if isinstance(value, str) and value.strip() and len(value) <= MAX_ID_CHARS:
        return value, False
    return "unknown", True


def _normalize_snapshot(record: dict) -> dict:
    provider = _object(record.get("provenance")).get("provider")
    diagnostics: list[dict] = []
    groups: dict[str, dict[str, dict]] = defaultdict(dict)
    duplicate_records = 0
    conflicting_records = 0
    conflicting_trace_ids: set[str] = set()
    inspected_records = 0
    redacted_values = 0
    limit_reached = False
    payload_failure = False
    project, missing_project = _project_name(record)

    if provider not in PROVIDERS:
        _diagnostic(
            diagnostics,
            "$",
            "unsupported_provider",
            "The snapshot does not name a supported trace provider.",
        )
        return {
            "provider": provider,
            "project": project,
            "missing_project": missing_project,
            "groups": groups,
            "diagnostics": diagnostics,
            "inspected_records": 0,
            "normalization_failures": 0,
            "duplicate_records": 0,
            "conflicting_records": 0,
            "conflicting_trace_ids": set(),
            "redacted_values": 0,
            "limit_reached": False,
            "unsupported_provider": True,
        }

    try:
        rows = unpack(record.get("items", []), provider)
        for index, (row, locator) in enumerate(rows):
            if index >= MAX_NORMALIZED_RECORDS:
                limit_reached = True
                break
            inspected_records += 1
            try:
                clean = redact(row)
                redacted_values += _redaction_count(clean)
                span = normalize(clean, provider, project, locator)
                validate_record("trace", span, definition="span")
                if (
                    len(span["trace_id"]) > MAX_ID_CHARS
                    or len(span["span_id"]) > MAX_ID_CHARS
                    or len(span["parent_span_ids"]) > MAX_PARENT_IDS
                    or any(len(parent) > MAX_ID_CHARS for parent in span["parent_span_ids"])
                ):
                    raise AuditError("trace and span IDs exceed the public projection limit")
            except (
                AuditError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RecursionError,
            ) as exc:
                _diagnostic(diagnostics, locator, "unsupported_record", _message(exc))
                continue
            previous = groups[span["trace_id"]].get(span["span_id"])
            if previous is None:
                groups[span["trace_id"]][span["span_id"]] = span
            elif previous["source_digest"] == span["source_digest"]:
                duplicate_records += 1
            else:
                conflicting_records += 1
                conflicting_trace_ids.add(span["trace_id"])
                _diagnostic(
                    diagnostics,
                    locator,
                    "conflicting_span_revision",
                    "The snapshot contains different records for the same span ID.",
                )
    except (AuditError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        payload_failure = True
        _diagnostic(diagnostics, "$", "unsupported_payload", _message(exc))

    normalized_spans = sum(len(spans) for spans in groups.values())
    failures = (
        inspected_records
        - normalized_spans
        - duplicate_records
        - conflicting_records
        + int(payload_failure)
    )
    return {
        "provider": provider,
        "project": project,
        "missing_project": missing_project,
        "groups": groups,
        "diagnostics": diagnostics,
        "inspected_records": inspected_records,
        "normalization_failures": max(0, failures),
        "duplicate_records": duplicate_records,
        "conflicting_records": conflicting_records,
        "conflicting_trace_ids": conflicting_trace_ids,
        "redacted_values": redacted_values,
        "limit_reached": limit_reached,
        "unsupported_provider": False,
        "normalized_spans": normalized_spans,
    }


def _redaction_count(value: Any) -> int:
    count = 0
    pending = [value]
    visited = 0
    while pending and visited < 100_000:
        current = pending.pop()
        visited += 1
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, str) and "[REDACTED]" in current:
            count += 1
    return count


def _limitation(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _trace_structure(spans: list[dict], *, conflict: bool = False) -> dict:
    by_id = {span["span_id"]: span for span in spans}
    roots = [span for span in spans if not span["parent_span_ids"]]
    missing_parents = sorted(
        {parent for span in spans for parent in span["parent_span_ids"] if parent not in by_id}
    )
    invalid_timing = sum(
        span["started_ns"] is None
        or span["ended_ns"] is None
        or span["ended_ns"] < span["started_ns"]
        for span in spans
    )
    complete_roots = sum(
        span["started_ns"] is not None
        and span["ended_ns"] is not None
        and span["ended_ns"] >= span["started_ns"]
        for span in roots
    )
    cycle = _cyclic(spans)
    return {
        "roots": roots,
        "missing_parents": missing_parents,
        "invalid_timing": invalid_timing,
        "complete_roots": complete_roots,
        "cycle": cycle,
        "complete": bool(roots)
        and complete_roots == len(roots)
        and not missing_parents
        and not invalid_timing
        and not cycle
        and not conflict,
    }


def _usability(record: dict, analysis: dict) -> dict:
    normalized = analysis.get("normalized_spans", 0)
    reported_unusable = _reported_unusable(record)
    failures = analysis["normalization_failures"]
    unsupported = analysis["unsupported_provider"]
    limitations: list[dict] = []
    blockers: list[dict] = []

    if unsupported:
        blockers.append(
            _limitation("unsupported_provider", "Choose a supported trace format and import again.")
        )
    elif not normalized:
        blockers.append(
            _limitation(
                "no_usable_spans",
                "No records satisfy the supported provider-neutral span contract.",
            )
        )
    if not _object(record.get("completeness")).get("complete", False):
        limitations.append(
            _limitation(
                "partial_acquisition",
                "The source reports incomplete acquisition or trace details.",
            )
        )
    if reported_unusable or failures:
        limitations.append(
            _limitation(
                "unusable_records",
                "Some source records could not be normalized and are excluded.",
            )
        )
    if analysis["conflicting_records"]:
        limitations.append(
            _limitation(
                "conflicting_span_revisions",
                "Conflicting records for the same span ID were excluded.",
            )
        )
    incomplete_traces = sum(
        not _trace_structure(
            list(spans.values()), conflict=trace_id in analysis["conflicting_trace_ids"]
        )["complete"]
        for trace_id, spans in analysis["groups"].items()
    )
    if incomplete_traces:
        limitations.append(
            _limitation(
                "incomplete_trace_structure",
                "Some traces have missing roots, parents, timing, or conflicting evidence.",
            )
        )
    if analysis["limit_reached"]:
        limitations.append(
            _limitation(
                "normalization_limit",
                f"Only the first {MAX_NORMALIZED_RECORDS} source records were inspected.",
            )
        )
    if analysis["missing_project"]:
        limitations.append(
            _limitation(
                "missing_provider_project",
                "The source did not retain a provider project identity.",
            )
        )

    diagnosis_ready = bool(normalized) and not unsupported
    if not diagnosis_ready:
        state = "unsupported" if unsupported else "empty"
    elif limitations:
        state = "partial"
    else:
        state = "ready"
    return {
        "state": state,
        "diagnosis_ready": diagnosis_ready,
        "blockers": blockers,
        "limitations": limitations,
    }


def trace_usability(record: dict) -> dict:
    """Return bounded normalization coverage before an immutable import is accepted."""
    if not isinstance(record, dict) or record.get("kind") != "traces":
        raise AuditError("select a trace snapshot")
    analysis = _normalize_snapshot(record)
    readiness = _usability(record, analysis)
    return {
        **readiness,
        "normalized_spans": analysis.get("normalized_spans", 0),
        "trace_count": len(analysis["groups"]),
        "inspected_records": analysis["inspected_records"],
        "normalization_failures": analysis["normalization_failures"],
        "reported_unusable_records": _reported_unusable(record),
        "duplicate_records": analysis["duplicate_records"],
        "conflicting_records": analysis["conflicting_records"],
        "incomplete_traces": sum(
            not _trace_structure(
                list(spans.values()), conflict=trace_id in analysis["conflicting_trace_ids"]
            )["complete"]
            for trace_id, spans in analysis["groups"].items()
        ),
        "normalization_limit_reached": analysis["limit_reached"],
    }


def _safe_scalar(value: Any, maximum: int = 1_000) -> Any:
    if isinstance(value, str):
        value = redact(value)
        raw = value.encode("utf-8")
        if len(raw) > maximum:
            return raw[: maximum - 3].decode("utf-8", errors="ignore") + "..."
        return value
    return value if value is None or type(value) in {bool, int, float} else None


def _bounded(value: Any, budget: _ContentBudget, depth: int = 0) -> Any:
    if budget.nodes <= 0 or budget.remaining <= 0:
        budget.truncated = True
        budget.omitted_items += 1
        return _OMITTED
    budget.nodes -= 1
    if depth > MAX_VALUE_DEPTH:
        budget.truncated = True
        budget.omitted_items += 1
        return _OMITTED
    if value is None or type(value) in {bool, int, float}:
        return value
    if isinstance(value, str):
        if "[REDACTED]" in value:
            budget.redacted_values += 1
        raw = value.encode("utf-8")
        allowed = min(MAX_STRING_BYTES, budget.remaining)
        if len(raw) > allowed:
            budget.truncated = True
            budget.omitted_items += 1
            suffix = "." * min(3, allowed)
            value = raw[: max(0, allowed - len(suffix))].decode("utf-8", errors="ignore") + suffix
            raw = value.encode("utf-8")
        budget.remaining -= len(raw)
        return value
    if isinstance(value, list):
        result = []
        for item in value[:MAX_VALUE_ITEMS]:
            bounded = _bounded(item, budget, depth + 1)
            if bounded is _OMITTED:
                break
            result.append(bounded)
        omitted = len(value) - len(result)
        if omitted:
            budget.truncated = True
            budget.omitted_items += omitted
        return result
    if isinstance(value, dict):
        result = {}
        items = list(value.items())
        for key, item in items[:MAX_VALUE_ITEMS]:
            key = str(key)
            bounded_key = _bounded(key, budget, depth + 1)
            bounded = _bounded(item, budget, depth + 1)
            if bounded_key is _OMITTED or bounded is _OMITTED:
                break
            result[bounded_key] = bounded
        omitted = len(items) - len(result)
        if omitted:
            budget.truncated = True
            budget.omitted_items += omitted
        return result
    budget.truncated = True
    budget.omitted_items += 1
    return _OMITTED


def _span_projection(span: dict, *, depth: int | None) -> dict:
    budget = _ContentBudget()
    content = {}
    for key in (
        "error",
        "input",
        "output",
        "messages",
        "metadata",
        "attributes",
        "resource",
        "scope",
        "events",
        "links",
    ):
        value = _bounded(redact(span.get(key)), budget)
        if value is not _OMITTED:
            content[key] = value
    started, ended = span["started_ns"], span["ended_ns"]
    duration = (
        ended - started if started is not None and ended is not None and ended >= started else None
    )
    return {
        "id": span["id"],
        "span_id": span["span_id"],
        "parent_span_ids": span["parent_span_ids"],
        "root": not span["parent_span_ids"],
        "depth": depth,
        "name": _safe_scalar(span["name"]),
        "kind": span["kind"],
        "operation": _safe_scalar(span["operation"]),
        "status": span["status"],
        "started_ns": started,
        "ended_ns": ended,
        "duration_ns": duration,
        "model": _safe_scalar(span["model"]),
        "session_id": _safe_scalar(span["session_id"]),
        "tool_call_id": _safe_scalar(span["tool_call_id"]),
        "usage": span["usage"],
        **content,
        "content_truncated": budget.truncated,
        "omitted_content_items": budget.omitted_items,
        "redacted_value_count": budget.redacted_values,
        "record_locator": span["raw_ref"][:500],
    }


def _span_depth(span_id: str, by_id: dict[str, dict]) -> int | None:
    seen = {span_id}
    depth = 0
    current = by_id[span_id]
    while current["parent_span_ids"]:
        parent = current["parent_span_ids"][0]
        if parent not in by_id or parent in seen:
            return None
        seen.add(parent)
        current = by_id[parent]
        depth += 1
    return depth


def _cyclic(spans: list[dict]) -> bool:
    parents = {span["span_id"]: set(span["parent_span_ids"]) for span in spans}
    remaining = set(parents)
    while remaining:
        ready = {span for span in remaining if not (parents[span] & remaining)}
        if not ready:
            return True
        remaining -= ready
    return False


def _flatten_context(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    if depth > 3 or not isinstance(value, dict):
        return []
    result = []
    for key, item in list(value.items())[:100]:
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            result.extend(_flatten_context(item, name, depth + 1))
        elif isinstance(item, (str, int, float, bool)):
            result.append((name.lower(), item))
    return result


def _context(spans: list[dict], candidates: tuple[str, ...]) -> Any:
    roots = [span for span in spans if not span["parent_span_ids"]]
    for span in roots + [span for span in spans if span["parent_span_ids"]]:
        values = []
        for key in ("metadata", "attributes", "resource"):
            values.extend(_flatten_context(span.get(key, {})))
        for candidate in candidates:
            for name, value in values:
                if name == candidate or name.endswith("." + candidate):
                    return _safe_scalar(value, 500)
    return None


def _trace_summary(trace_id: str, spans: list[dict], conflict: bool = False) -> dict:
    structure = _trace_structure(spans, conflict=conflict)
    roots = structure["roots"]
    all_missing_parents = structure["missing_parents"]
    missing_parents = all_missing_parents[:MAX_SUMMARY_IDS]
    started = [span["started_ns"] for span in spans if span["started_ns"] is not None]
    ended = [span["ended_ns"] for span in spans if span["ended_ns"] is not None]
    return {
        "trace_id": trace_id,
        "trace_key": spans[0]["trace_key"],
        "name": _safe_scalar((roots or spans)[0]["name"]),
        "root_span_ids": [span["span_id"] for span in roots[:MAX_SUMMARY_IDS]],
        "root_span_count": len(roots),
        "root_span_ids_omitted": max(0, len(roots) - MAX_SUMMARY_IDS),
        "span_count": len(spans),
        "error_count": sum(
            span["status"] == "error" or span["error"] is not None for span in spans
        ),
        "started_ns": min(started) if started else None,
        "ended_ns": max(ended) if ended else None,
        "environment": _context(
            spans,
            ("environment", "deployment.environment", "service.environment", "env"),
        ),
        "release": _context(
            spans,
            (
                "release",
                "revision",
                "git.commit.sha",
                "service.version",
                "deployment.version",
            ),
        ),
        "complete_root_count": structure["complete_roots"],
        "missing_parent_ids": missing_parents,
        "missing_parent_count": len(all_missing_parents),
        "missing_parent_ids_omitted": max(0, len(all_missing_parents) - MAX_SUMMARY_IDS),
        "invalid_timing_spans": structure["invalid_timing"],
        "cyclic_parentage": structure["cycle"],
        "complete": structure["complete"],
    }


def _trace_sort(summary: dict) -> tuple:
    started = summary["started_ns"]
    return (started is not None, started or 0, summary["trace_id"])


def _source(record: dict, analysis: dict) -> dict:
    provenance = _object(record.get("provenance"))
    selection = _object(record.get("selection"))
    public_selection = {}
    for key in (
        "trace_id",
        "start",
        "end",
        "start_time",
        "end_time",
        "environment",
        "cap",
        "limit",
        "filters",
    ):
        if key in selection:
            budget = _ContentBudget(remaining=4_000, nodes=100)
            value = _bounded(redact(selection[key]), budget)
            if value is not _OMITTED:
                public_selection[key] = value
    return {
        "snapshot_id": _safe_scalar(record.get("id")),
        "snapshot_digest": _safe_scalar(record.get("digest")),
        "created_at": _safe_scalar(record.get("created_at")),
        "provider": _safe_scalar(analysis["provider"]),
        "provider_project": _safe_scalar(analysis["project"]),
        "connection_id": _safe_scalar(record.get("connection_id")),
        "source": _safe_scalar(provenance.get("source") or provenance.get("method")),
        "acquired_at": _safe_scalar(provenance.get("acquired_at") or provenance.get("fetched_at")),
        "source_snapshot_id": _safe_scalar(provenance.get("source_snapshot_id")),
        "source_digest": _safe_scalar(provenance.get("source_digest")),
        "selection": public_selection,
    }


def project_trace_snapshot(
    record: dict, trace_id: str | None = None, *, max_spans: int = DEFAULT_DETAIL_SPANS
) -> dict:
    """Project one immutable snapshot without returning raw provider records."""
    if not isinstance(record, dict) or record.get("kind") != "traces":
        raise AuditError("select a trace snapshot")
    if type(max_spans) is not int or not 1 <= max_spans <= MAX_DETAIL_SPANS:
        raise AuditError(f"max_spans must be between 1 and {MAX_DETAIL_SPANS}")
    if trace_id is not None and (
        not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > MAX_ID_CHARS
    ):
        raise AuditError("trace_id must be a nonempty bounded string")

    analysis = _normalize_snapshot(record)
    readiness = _usability(record, analysis)
    summaries = [
        _trace_summary(
            key,
            list(by_id.values()),
            conflict=key in analysis["conflicting_trace_ids"],
        )
        for key, by_id in analysis["groups"].items()
    ]
    summaries.sort(key=_trace_sort, reverse=True)
    trace_index_truncated = len(summaries) > MAX_TRACE_INDEX
    public_summaries = summaries[:MAX_TRACE_INDEX]

    if trace_id is not None and trace_id not in analysis["groups"]:
        raise AuditError("trace not found in this snapshot")
    selected = trace_id or (summaries[0]["trace_id"] if len(summaries) == 1 else None)
    selected_summary = next(
        (summary for summary in summaries if summary["trace_id"] == selected), None
    )
    timeline_spans: list[dict] = []
    errors: list[dict] = []
    omitted_errors = 0
    selected_total = 0
    selected_omitted = 0
    content_truncated = False
    redacted_in_projection = 0
    if selected is not None:
        by_id = analysis["groups"][selected]
        spans = sorted(
            by_id.values(),
            key=lambda span: (
                span["started_ns"] is None,
                span["started_ns"] or 0,
                span["span_id"],
            ),
        )
        selected_total = len(spans)
        selected_omitted = max(0, len(spans) - max_spans)
        for span in spans[:max_spans]:
            projected = _span_projection(span, depth=_span_depth(span["span_id"], by_id))
            timeline_spans.append(projected)
            content_truncated = content_truncated or projected["content_truncated"]
            redacted_in_projection += projected["redacted_value_count"]
        error_spans = [
            span for span in spans if span["status"] == "error" or span["error"] is not None
        ]
        omitted_errors = max(0, len(error_spans) - MAX_ERRORS)
        for span in error_spans[:MAX_ERRORS]:
            budget = _ContentBudget(remaining=4_000, nodes=100)
            error = _bounded(redact(span["error"]), budget)
            errors.append(
                {
                    "span_id": span["span_id"],
                    "name": _safe_scalar(span["name"]),
                    "status": span["status"],
                    "error": None if error is _OMITTED else error,
                    "content_truncated": budget.truncated,
                }
            )

    limitations = list(readiness["limitations"])
    if trace_index_truncated:
        limitations.append(
            _limitation(
                "trace_index_limit",
                f"Only the first {MAX_TRACE_INDEX} trace identities are shown.",
            )
        )
    if selected_omitted:
        limitations.append(
            _limitation(
                "span_projection_limit",
                f"{selected_omitted} spans are omitted from this bounded response.",
            )
        )
    if content_truncated:
        limitations.append(
            _limitation(
                "content_projection_limit",
                "One or more span payloads were truncated for public display.",
            )
        )
    if selected_summary and not selected_summary["complete"]:
        limitations.append(
            _limitation(
                "incomplete_trace",
                "The selected trace has missing roots, parents, timing, or conflicting evidence.",
            )
        )

    return {
        "projection_version": 1,
        "snapshot_id": _safe_scalar(record.get("id")),
        "source": _source(record, analysis),
        "readiness": {
            **readiness,
            "limitations": limitations,
            "selection_required": len(summaries) > 1 and selected is None,
            "viewer_ready": selected is not None,
        },
        "coverage": {
            "source_records": len(record.get("items", []))
            if isinstance(record.get("items"), list)
            else None,
            "inspected_records": analysis["inspected_records"],
            "normalized_spans": analysis.get("normalized_spans", 0),
            "trace_count": len(summaries),
            "incomplete_traces": sum(not summary["complete"] for summary in summaries),
            "reported_unusable_records": _reported_unusable(record),
            "normalization_failures": analysis["normalization_failures"],
            "duplicate_records": analysis["duplicate_records"],
            "conflicting_records": analysis["conflicting_records"],
            "normalization_limit": MAX_NORMALIZED_RECORDS,
            "normalization_limit_reached": analysis["limit_reached"],
            "trace_index_returned": len(public_summaries),
            "trace_index_omitted": max(0, len(summaries) - len(public_summaries)),
            "selected_trace_spans": selected_total,
            "returned_spans": len(timeline_spans),
            "omitted_spans": selected_omitted,
            "source_complete": bool(_object(record.get("completeness")).get("complete", False)),
            "source_limit_reason": _safe_scalar(_object(record.get("completeness")).get("reason")),
        },
        "handling": {
            "redaction": {
                "applied_to_projection": True,
                "policy": "common credential keys and token patterns",
                "redacted_value_count": max(analysis["redacted_values"], redacted_in_projection),
                "limitation": "This is not a general PII detector.",
            },
            "normalization": {
                "contract": "provider-neutral span v1",
                "raw_provider_records_exposed": False,
                "limitation": "Only supported normalized fields are exposed; unknown provider fields may be omitted.",
            },
        },
        "traces": public_summaries,
        "selected_trace_id": selected,
        "trace": selected_summary,
        "timeline": {
            "root_span_ids": selected_summary["root_span_ids"] if selected_summary else [],
            "spans": timeline_spans,
            "total_spans": selected_total,
            "returned_spans": len(timeline_spans),
            "omitted_spans": selected_omitted,
        },
        "errors": errors,
        "omitted_errors": omitted_errors,
        "diagnostics": analysis["diagnostics"],
    }


def trace_detail(
    workspace,
    snapshot_id: str,
    trace_id: str | None = None,
    *,
    max_spans: int = DEFAULT_DETAIL_SPANS,
) -> dict:
    """Load, integrity-check, and publicly project one private trace snapshot."""
    return project_trace_snapshot(
        snapshots.load(workspace, snapshot_id), trace_id=trace_id, max_spans=max_spans
    )
