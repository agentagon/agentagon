"""Import provider exports into immutable local trace revisions."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from agentagon.core.records import (
    AuditError,
    digest,
    identifier,
    load_json,
    now,
    timestamp_ns,
    validate_record,
)
from agentagon.core.signals import measure
from agentagon.storage.workspace import Workspace
from agentagon.telemetry.alignment import trace_alignment
from agentagon.telemetry.normalize import normalize, redact, unpack


def import_export(
    workspace: Workspace, audit: dict, source_path: Path, acquisition_path: Path | None
) -> dict:
    source_path = source_path.expanduser().resolve()
    if not source_path.exists():
        raise AuditError("export path does not exist")
    if audit["mode"] == "code":
        raise AuditError("code-only audits cannot import traces")
    provenance = {
        "method": "local_export",
        "tool_version": "unknown",
        "fetched_at": now(),
        "completeness": "unknown",
        "pagination_complete": False,
        "selected_trace_ids": [],
        "failed_trace_ids": [],
    }
    if acquisition_path:
        supplied = load_json(acquisition_path)
        validate_record("acquisition", supplied)
        if supplied["source"] != audit["source"] or supplied["project"] != audit["project"]:
            raise AuditError("acquisition source/project does not match audit")
        timestamp_ns(supplied["fetched_at"])
        provenance.update(redact(supplied))
    plan = audit.get("acquisition_plan")
    if plan is not None:
        if acquisition_path is None:
            raise AuditError("planned downloads require an acquisition receipt")
        if set(provenance["selected_trace_ids"]) != set(plan["selected_trace_ids"]):
            raise AuditError("acquisition selected trace IDs do not match the download plan")
        if not plan["estimate"]["inventory_complete"]:
            provenance["pagination_complete"] = False
            if provenance["completeness"] == "complete":
                provenance["completeness"] = "partial"
    paths = sorted(source_path.rglob("*")) if source_path.is_dir() else [source_path]
    files = [path for path in paths if path.is_file()]
    if any(path.is_symlink() for path in files):
        raise AuditError("export inputs must not contain symlinks")
    input_digest = digest(
        {
            "files": [
                (
                    str(path.relative_to(source_path)) if source_path.is_dir() else path.name,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                for path in files
            ],
            "acquisition": load_json(acquisition_path) if acquisition_path else None,
        }
    )
    if audit["acquisition"] and audit["acquisition"].get("input_digest") == input_digest:
        return audit["acquisition"]
    if audit["reviews"] or audit["diagnoses"] or audit["groups"]:
        raise AuditError("analysis has begun; start a new audit to change imported evidence")
    diagnostics, raw_artifacts = [], []
    rows: dict[str, dict] = {}
    conflicts = set()
    for path in files:
        if path.suffix.lower() not in {".json", ".jsonl", ".ndjson"}:
            diagnostics.append({"file": path.name, "reason": "unsupported_format"})
            continue
        original_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        for value, location, error in _documents(path):
            if error:
                diagnostics.append({"file": path.name, "locator": location, "reason": error})
                continue
            clean = redact(value)
            raw_path = workspace.artifact(clean)
            raw_artifacts.append(
                {
                    "path": raw_path,
                    "original_digest": original_digest,
                    "source_file": path.name,
                    "locator": location,
                    "redacted": clean != value,
                }
            )
            try:
                for row, locator in unpack(clean, audit["source"]):
                    try:
                        span = normalize(
                            row, audit["source"], audit["project"], raw_path + "#" + locator
                        )
                        validate_record("trace", span, definition="span")
                    except (AuditError, TypeError, KeyError, ValueError, AttributeError) as exc:
                        diagnostics.append(
                            {
                                "file": path.name,
                                "locator": location + locator,
                                "reason": type(exc).__name__,
                            }
                        )
                        continue
                    if (
                        span["id"] in rows
                        and rows[span["id"]]["source_digest"] != span["source_digest"]
                    ):
                        conflicts.add(span["trace_id"])
                        diagnostics.append(
                            {
                                "file": path.name,
                                "locator": location + locator,
                                "reason": "conflicting_span_revision",
                                "trace_id": span["trace_id"],
                            }
                        )
                    else:
                        rows[span["id"]] = span
            except (AuditError, TypeError, KeyError, ValueError, AttributeError) as exc:
                diagnostics.append(
                    {"file": path.name, "locator": location, "reason": type(exc).__name__}
                )
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for span in rows.values():
        by_trace[span["trace_id"]].append(span)
    start, end = timestamp_ns(audit["window"]["start"]), timestamp_ns(audit["window"]["end"])
    candidates = []
    explicit_ids = set(provenance["selected_trace_ids"])
    for trace_id, spans in by_trace.items():
        roots = [s for s in spans if not s["parent_span_ids"]]
        root_times = [s["started_ns"] for s in roots if s["started_ns"] is not None]
        root_time = min(root_times) if root_times else None
        if root_time is not None and not start <= root_time < end:
            if trace_id in explicit_ids:
                diagnostics.append(
                    {"trace_id": trace_id, "reason": "receipt_selection_outside_window"}
                )
            continue
        if (plan is not None or explicit_ids) and trace_id not in explicit_ids:
            continue
        if root_time is None and trace_id not in explicit_ids:
            diagnostics.append({"trace_id": trace_id, "reason": "unknown_root_start_not_selected"})
            continue
        ids = {s["span_id"] for s in spans}
        missing = sorted(
            {parent for s in spans for parent in s["parent_span_ids"] if parent not in ids}
        )
        completeness = provenance["completeness"]
        if (
            missing
            or not roots
            or trace_id in conflicts
            or _cyclic(spans)
            or any(
                s["started_ns"] is None or s["ended_ns"] is None or s["ended_ns"] < s["started_ns"]
                for s in spans
            )
        ):
            completeness = "partial"
        if diagnostics and completeness == "complete":
            completeness = "partial"
        trace = {
            "contract_version": "1",
            "id": identifier("trace", audit["source"], audit["project"], trace_id),
            "source": audit["source"],
            "project": audit["project"],
            "provider_trace_id": trace_id,
            "root_started_ns": root_time,
            "completeness": completeness,
            "missing_parents": missing,
            "spans": sorted(
                spans, key=lambda s: (s["started_ns"] is None, s["started_ns"] or 0, s["id"])
            ),
        }
        trace["digest"] = digest(trace)
        validate_record("trace", trace)
        candidates.append(trace)
    candidates.sort(
        key=lambda trace: (
            trace["root_started_ns"] is not None,
            trace["root_started_ns"] or 0,
            trace["id"],
        ),
        reverse=True,
    )
    selected = candidates if audit["limit"] == "all" else candidates[: audit["limit"]]
    snapshots = []
    for trace in selected:
        snapshots.append(
            {
                "id": trace["id"],
                "digest": trace["digest"],
                "path": workspace.artifact(trace),
                "provider_trace_id": trace["provider_trace_id"],
                "root_started_ns": trace["root_started_ns"],
                "completeness": trace["completeness"],
                "measurements": measure(trace),
                "revision_alignment": trace_alignment(
                    trace,
                    audit["snapshot"].get("revision"),
                    audit["snapshot"].get("code_scope", "full"),
                    audit["snapshot"].get("local_changes"),
                ),
            }
        )
    selected_ids = {trace["provider_trace_id"] for trace in selected}
    absent = sorted(explicit_ids - set(by_trace))
    imported = {
        "input_digest": input_digest,
        "provenance": provenance,
        "raw_artifacts": raw_artifacts,
        "diagnostics": diagnostics,
        "eligible_trace_count": len(candidates),
        "selected_trace_count": len(selected),
        "selected_trace_ids": sorted(selected_ids),
        "missing_selected_ids": absent,
        "skipped_by_limit": len(candidates) - len(selected),
        "imported_at": now(),
    }
    audit["acquisition"] = imported
    audit["traces"] = snapshots
    if audit.get("trace_alignment") is not None:
        mismatches = [
            trace["id"]
            for trace in snapshots
            if trace["revision_alignment"]["status"] == "mismatch"
        ]
        alignment = trace_alignment(
            {"spans": []},
            audit["snapshot"].get("revision"),
            audit["snapshot"].get("code_scope", "full"),
            audit["snapshot"].get("local_changes"),
        )
        audit["trace_alignment"].update(
            status="mismatch" if mismatches else alignment["status"],
            warning=alignment["warning"],
            mismatched_trace_ids=mismatches,
        )
    audit["packets"] = {}
    audit["generation"] += 1
    return imported


def _documents(path: Path):
    if path.suffix.lower() == ".json":
        try:
            yield load_json(path), "$", None
        except (AuditError, UnicodeError):
            yield None, "$", "invalid_json"
        return
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    value: Any = json.loads(
                        line, parse_constant=lambda value: _reject_constant(value)
                    )
                    yield value, f"line:{line_number}", None
                except (ValueError, AuditError):
                    yield None, f"line:{line_number}", "invalid_json"
    except UnicodeError:
        yield None, "$", "invalid_utf8"


def _reject_constant(value: str) -> None:
    raise AuditError("non-finite JSON number")


def _cyclic(spans: list[dict]) -> bool:
    parents = {span["span_id"]: set(span["parent_span_ids"]) for span in spans}
    remaining = set(parents)
    while remaining:
        ready = {span for span in remaining if not (parents[span] & remaining)}
        if not ready:
            return True
        remaining -= ready
    return False
