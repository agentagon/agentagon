"""Accept bounded trace diagnoses and retain independent issue records."""

from agentagon.capabilities.traces import snapshots
from agentagon.capabilities.traces.normalize import normalize, unpack
from agentagon.core.records import AuditError, encoded, identifier
from agentagon.domain.issues import record_issue


def trace_context(workspace, snapshot_id):
    snapshot = snapshots.load(workspace, snapshot_id)
    if snapshot["kind"] != "traces":
        raise AuditError("select a trace snapshot")
    provider = snapshot["provenance"]["provider"]
    spans, errors = [], 0
    for row, locator in unpack(snapshot["items"], provider):
        try:
            spans.append(normalize(row, provider, snapshot["provenance"].get("project"), locator))
        except (AuditError, ValueError, TypeError, KeyError):
            errors += 1
    return snapshot, spans, errors


def accept(workspace, job, result):
    if result.get("needs_input"):
        return "needs_input", result, str(result["needs_input"])
    findings = result.get("issues")
    if (
        not isinstance(findings, list)
        or len(findings) > 100
        or len(encoded(findings).encode()) > 256000
    ):
        raise AuditError("discovery requires a bounded issues list")
    snapshot, spans, invalid = trace_context(workspace, job["options"]["trace_snapshot_id"])
    traces = {}
    for span in spans:
        traces.setdefault(span["trace_id"], []).append(span)
    prepared = []
    for issue in findings:
        if not isinstance(issue, dict):
            raise AuditError("issue must be an object")
        ids = issue.get("trace_ids")
        if (
            not isinstance(ids, list)
            or not ids
            or not all(isinstance(t, str) and t in traces for t in ids)
        ):
            raise AuditError("discovered issues must cite traces in the selected snapshot")
        explanations = issue.get("evidence")
        if (
            not isinstance(explanations, list)
            or not explanations
            or any(not isinstance(e, str) or not e.strip() or len(e) > 8000 for e in explanations)
        ):
            raise AuditError("issue requires supporting evidence explanations")
        # Validate the full response before any records are accepted.
        for field, maximum in (("key", 500), ("title", 300), ("summary", 8000)):
            if (
                not isinstance(issue.get(field), str)
                or not issue[field].strip()
                or len(issue[field]) > maximum
            ):
                raise AuditError(f"invalid issue {field}")
        if (
            issue.get("severity") not in {"low", "medium", "high", "critical"}
            or type(issue.get("confidence")) not in {float, int}
            or not 0 <= issue["confidence"] <= 1
        ):
            raise AuditError("invalid issue severity or confidence")
        prepared.append((issue, ids))
    saved = []
    for issue, ids in prepared:
        agent_id = job.get("application_agent_id")
        if agent_id is None:
            bindings = job.get("preparation", {}).get("trace_agents", {})
            owners = {bindings.get(trace_id) for trace_id in ids}
            if len(owners) == 1 and None not in owners:
                agent_id = owners.pop()
        evidence_ref = workspace.artifact(
            {"snapshot_id": snapshot["id"], "task_id": job["id"], "diagnosis": issue}
        )
        record = record_issue(
            workspace,
            key=issue["key"],
            title=issue["title"],
            summary=issue["summary"],
            severity=issue["severity"],
            confidence=issue["confidence"],
            agent_id=agent_id,
            evidence=[evidence_ref],
            occurrences=[
                {
                    "id": identifier(
                        "occurrence",
                        snapshot["provenance"]["provider"],
                        snapshot["connection_id"],
                        snapshot["provenance"].get("project"),
                        tid,
                    ),
                    "source_id": snapshot["id"],
                    "trace_ids": [tid],
                    "observed_ns": max((s.get("started_ns") or 0 for s in traces[tid]), default=0)
                    or None,
                    "task_id": job["id"],
                    "basis": "trace",
                    "evidence": evidence_ref,
                }
                for tid in sorted(set(ids))
            ],
        )
        saved.append(record["issue_id"])
    prepared_evidence = workspace.read_artifact(evidence(workspace, snapshot["id"]))
    limited = (
        invalid
        or not spans
        or not snapshot["completeness"].get("complete", False)
        or any(t["completeness"] != "complete" for t in prepared_evidence["traces"])
    )
    return (
        "completed_with_limits" if limited else "completed",
        {
            "summary": str(result.get("summary", "Trace discovery completed")),
            "issue_ids": list(dict.fromkeys(saved)),
            "invalid_spans": invalid,
            "snapshot_id": snapshot["id"],
            "repairs_started": False,
        },
        None,
    )


def evidence(workspace, snapshot_id):
    """Normalize and extract existing deterministic signals without running a full audit."""
    from agentagon.capabilities.traces.ingest import _cyclic
    from agentagon.core.records import digest
    from agentagon.core.signals import measure
    from agentagon.domain.issues import list_issues

    snapshot, spans, invalid = trace_context(workspace, snapshot_id)
    grouped = {}
    for span in spans:
        grouped.setdefault(span["trace_id"], []).append(span)
    traces = []
    for trace_id, items in grouped.items():
        ids = {span["span_id"] for span in items}
        missing = sorted(
            {parent for span in items for parent in span["parent_span_ids"] if parent not in ids}
        )
        complete = (
            bool(snapshot["completeness"].get("complete"))
            and not invalid
            and not missing
            and not _cyclic(items)
            and any(not span["parent_span_ids"] for span in items)
        )
        trace = {
            "id": identifier(
                "trace",
                snapshot["provenance"]["provider"],
                snapshot["provenance"].get("project"),
                trace_id,
            ),
            "provider_trace_id": trace_id,
            "completeness": "complete" if complete else "partial",
            "missing_parents": missing,
            "spans": items,
        }
        trace["digest"] = digest(trace)
        traces.append({**trace, "signals": measure(trace)})
    return workspace.artifact(
        {
            "snapshot_id": snapshot_id,
            "provenance": snapshot["provenance"],
            "traces": traces,
            "invalid_spans": invalid,
            "existing_issues": [
                {k: i[k] for k in ("issue_id", "agent_id", "key", "title", "summary", "status")}
                for i in list_issues(workspace)
            ][:500],
        }
    )
