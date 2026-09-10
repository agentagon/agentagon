"""Issue identities and customer status are independent of audit prevalence."""

from pathlib import Path

from agentagon.core.records import (
    AuditError,
    identifier,
    load_json,
    now,
    timestamp_ns,
    validate_record,
)
from agentagon.storage.workspace import Workspace
from agentagon.telemetry.normalize import redact

SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def list_issues(workspace: Workspace) -> list[dict]:
    issues = {}
    for audit in sorted(workspace.audits(), key=lambda a: a["created_at"]):
        findings = {
            finding["id"]: finding for d in audit["diagnoses"].values() for finding in d["findings"]
        }
        for group in audit["groups"]:
            key = group["issue_id"]
            issue = issues.setdefault(
                key,
                {
                    "issue_id": key,
                    "title": group["title"],
                    "summary": group["summary"],
                    "status": "open",
                    "history": [],
                    "occurrences": [],
                    "severity": "low",
                    "confidence": 0,
                },
            )
            for finding_id in group["finding_ids"]:
                finding = findings[finding_id]
                issue["occurrences"].append(
                    {
                        "audit_id": audit["audit_id"],
                        "finding_id": finding_id,
                        "trace_ids": finding["trace_ids"],
                        "observed_ns": finding["observed_ns"],
                        "assigned_at": group["assigned_at"],
                        "basis": finding["basis"],
                    }
                )
                if SEVERITY[finding["severity"]] > SEVERITY[issue["severity"]]:
                    issue["severity"] = finding["severity"]
                issue["confidence"] = max(issue["confidence"], finding["confidence"])
    event_paths = sorted((workspace.state / "cases" / "events").glob("*.json"))
    for event in sorted(
        (load_json(workspace.checked(p)) for p in event_paths),
        key=lambda event: (event["at"], event["event_id"]),
    ):
        if event["issue_id"] in issues:
            issues[event["issue_id"]]["history"].append(event)
            issues[event["issue_id"]]["status"] = event["status"]
    for issue in issues.values():
        traces = {key for occurrence in issue["occurrences"] for key in occurrence["trace_ids"]}
        issue["historical_affected_traces"] = len(traces)
        issue["runtime_prevalence"] = None  # No common denominator across arbitrary audits.
        times = [o["observed_ns"] for o in issue["occurrences"] if o["observed_ns"] is not None]
        issue["first_seen_ns"] = min(times) if times else None
        issue["last_seen_ns"] = max(times) if times else None
        if issue["history"] and issue["status"] in {"resolved_user", "resolved_verified"}:
            resolved = issue["history"][-1]
            recurrence = next(
                (
                    o
                    for o in issue["occurrences"]
                    if o["observed_ns"] is not None
                    and o["observed_ns"] > timestamp_ns(resolved["at"])
                    and o["assigned_at"] > resolved["at"]
                ),
                None,
            )
            if recurrence:
                issue["status"] = "reopened"
                issue["history"].append(
                    {
                        "event_id": identifier(
                            "event", resolved["event_id"], recurrence["finding_id"]
                        ),
                        "issue_id": issue["issue_id"],
                        "status": "reopened",
                        "at": recurrence["assigned_at"],
                        "reason": "Later trace evidence recurred after resolution",
                        "evidence": [recurrence["finding_id"]],
                    }
                )
    return sorted(
        issues.values(),
        key=lambda issue: (
            -SEVERITY[issue["severity"]],
            -issue["historical_affected_traces"],
            -issue["confidence"],
            issue["issue_id"],
        ),
    )


def update_issue(
    workspace: Workspace,
    event: dict,
    verification: Path | None = None,
    *,
    run_id: str | None = None,
    candidate_id: str | None = None,
) -> dict:
    validate_record("issue-update", event)
    if verification is not None:
        raise AuditError("manual verification receipts cannot establish new engine verification")
    if event["status"] == "resolved_verified" and (not run_id or not candidate_id):
        raise AuditError("verified resolution requires --run and --candidate engine evidence")
    if event["status"] != "resolved_verified" and (run_id or candidate_id):
        raise AuditError("run and candidate selectors apply only to verified resolution")
    with workspace.locked():
        issues = {issue["issue_id"]: issue for issue in list_issues(workspace)}
        if event["issue_id"] not in issues:
            raise AuditError("issue not found")
        known_refs = set()
        for audit in workspace.audits():
            for diagnosis in audit["diagnoses"].values():
                for finding in diagnosis["findings"]:
                    known_refs.add(finding["id"])
                    known_refs.update(finding["evidence"])
        if any(ref not in known_refs for ref in event["evidence"]):
            raise AuditError("issue update contains unknown evidence references")
        receipt_path = None
        if event["status"] == "resolved_verified":
            from agentagon.experiments.engine import verify_origin

            receipt = verify_origin(workspace, run_id, candidate_id, event["issue_id"])
            receipt_path = workspace.artifact(receipt)
        clean_event = redact(event)
        previous = issues[event["issue_id"]]["history"]
        if (
            previous
            and all(previous[-1].get(k) == v for k, v in clean_event.items())
            and previous[-1].get("verification") == receipt_path
        ):
            return issues[event["issue_id"]]
        created = {**clean_event, "at": now(), "verification": receipt_path}
        created["event_id"] = identifier("event", created)
        workspace.write(
            workspace.state / "cases" / "events" / f"{created['event_id']}.json", created
        )
        return next(
            issue for issue in list_issues(workspace) if issue["issue_id"] == event["issue_id"]
        )
