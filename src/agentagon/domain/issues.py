"""Independent project issues with explicit, evidence-backed status events."""

import copy
from pathlib import Path

from agentagon.capabilities.traces.normalize import redact
from agentagon.core.records import AuditError, identifier, now, timestamp_ns, validate_record

SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def binding(workspace, *, create=False):
    import os

    from agentagon.storage.config import Config
    from agentagon.storage.state import AppState

    if getattr(workspace, "metadata_store", None) is not None:
        return workspace.metadata_store, workspace.project_id
    directory = Path(os.environ.get("AGENTAGON_APP_STATE") or Config().path.with_suffix(".app"))
    if not create and not (directory / "app.sqlite3").exists():
        return None, None
    state = AppState(directory)
    project = state.db.project_for_path(workspace.root)
    if project is None and create:
        project = state.register(str(workspace.root))
    return state.db, project["id"] if project else None


def list_issues(workspace):
    db, project = binding(workspace)
    records = db.list_records(project, "issues") if db and project else []
    return sorted(
        records,
        key=lambda issue: (
            -SEVERITY[issue["severity"]],
            -issue["historical_affected_traces"],
            issue["issue_id"],
        ),
    )


def get_issue(workspace, issue_id):
    db, project = binding(workspace)
    record = db.get_record(project, "issues", issue_id) if db and project else None
    if record is None:
        raise AuditError("issue not found in this project")
    return record


def issue_detail(workspace, issue_id):
    """Expand retained diagnoses for review without changing their source records."""
    issue = get_issue(workspace, issue_id)
    diagnoses = []
    for reference in issue["evidence"][:100]:
        if not reference.startswith(".agentagon/evidence/"):
            continue
        value = workspace.read_artifact(reference)
        diagnosis = value.get("diagnosis", value)
        if isinstance(diagnosis, dict) and diagnosis.get("key"):
            diagnoses.append(
                {
                    "reference": reference,
                    **diagnosis,
                    "evidence": [
                        text for text in diagnosis.get("evidence", []) if isinstance(text, str)
                    ],
                }
            )
    return {**issue, "diagnoses": diagnoses}


def record_issue(
    workspace,
    *,
    key,
    title,
    summary,
    occurrences,
    agent_id=None,
    issue_id=None,
    severity="medium",
    confidence=0,
    evidence=(),
):
    if not isinstance(key, str) or not key.strip() or len(key) > 500:
        raise AuditError("issue requires a bounded stable key")
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise AuditError("issue requires a title of 1–300 characters")
    if not isinstance(summary, str) or len(summary) > 8000 or severity not in SEVERITY:
        raise AuditError("invalid issue summary or severity")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise AuditError("issue confidence must be between 0 and 1")
    db, project = binding(workspace, create=True)
    explicit_id = issue_id
    issue_id = issue_id or identifier("issue", project, agent_id, key.casefold().strip())
    with db.transaction() as tx:
        if not explicit_id and agent_id:
            # An initially unowned discovery retains its identity after explicit assignment.
            matches = [
                i
                for i in tx.list_records(project, "issues")
                if i.get("agent_id") == agent_id
                and i["key"].casefold().strip() == key.casefold().strip()
            ]
            if matches:
                issue_id = matches[0]["issue_id"]
        issue = tx.get_record(project, "issues", issue_id) or {
            "issue_id": issue_id,
            "agent_id": agent_id,
            "key": key,
            "title": title,
            "summary": summary,
            "status": "open",
            "severity": severity,
            "confidence": confidence,
            "occurrences": [],
            "history": [],
            "evidence": [],
            "task_ids": [],
            "goal_ids": [],
            "verification": None,
        }
        if agent_id and issue.get("agent_id") not in (None, agent_id):
            raise AuditError("issue belongs to another agent")
        issue["agent_id"] = issue.get("agent_id") or agent_id
        existing = {o["id"] for o in issue["occurrences"]}
        for occurrence in occurrences:
            # Trace identity is scoped to its immutable evidence source; different diagnoses
            # of that trace remain separate issues, while repeated scans are idempotent.
            entry = copy.deepcopy(occurrence)
            entry.setdefault(
                "id",
                identifier(
                    "occurrence",
                    entry.get("source_id"),
                    entry.get("trace_ids"),
                    entry.get("finding_id"),
                ),
            )
            if entry["id"] in existing:
                previous = next(o for o in issue["occurrences"] if o["id"] == entry["id"])
                sources = list(
                    dict.fromkeys(
                        [
                            previous.get("source_id"),
                            *previous.get("source_ids", []),
                            entry.get("source_id"),
                        ]
                    )
                )
                previous["source_ids"] = [s for s in sources if s]
                references = list(
                    dict.fromkeys(
                        [
                            previous.get("evidence"),
                            *previous.get("evidence_versions", []),
                            entry.get("evidence"),
                        ]
                    )
                )
                previous["evidence_versions"] = [e for e in references if e]
                continue
            entry.setdefault("assigned_at", now())
            issue["occurrences"].append(entry)
            existing.add(entry["id"])
            if issue["status"] in {"resolved_user", "resolved_verified"} and issue["history"]:
                resolved = issue["history"][-1]
                observed = entry.get("observed_ns")
                if observed is not None and observed > timestamp_ns(resolved["at"]):
                    issue["status"] = "reopened"
                    issue["history"].append(
                        {
                            "event_id": identifier("event", issue_id, entry["id"]),
                            "status": "reopened",
                            "at": now(),
                            "reason": "Later trace evidence recurred after resolution",
                            "evidence": [entry.get("finding_id") or entry["id"]],
                        }
                    )
        issue["evidence"] = sorted(set(issue["evidence"]) | set(evidence))
        issue["severity"] = max((issue["severity"], severity), key=SEVERITY.get)
        issue["confidence"] = max(issue["confidence"], confidence)
        traces = {t for o in issue["occurrences"] for t in o.get("trace_ids", [])}
        issue["historical_affected_traces"] = len(traces)
        issue["runtime_prevalence"] = None
        audits = list(
            dict.fromkeys(o["audit_id"] for o in issue["occurrences"] if o.get("audit_id"))
        )
        issue["audit_ids"] = audits
        issue["latest_audit_id"] = audits[-1] if audits else None
        times = [o["observed_ns"] for o in issue["occurrences"] if o.get("observed_ns") is not None]
        issue["first_seen_ns"] = min(times) if times else None
        issue["last_seen_ns"] = max(times) if times else None
        return tx.put_record(project, "issues", issue_id, redact(issue))


def record_audit(workspace, audit):
    """Publish validated findings when an audit is written, never reconstruct on reads."""
    findings = {f["id"]: f for d in audit["diagnoses"].values() for f in d["findings"]}
    for group in audit["groups"]:
        selected = [findings[f] for f in group["finding_ids"]]
        record_issue(
            workspace,
            key=group["key"],
            issue_id=group["issue_id"] if not audit.get("agent_id") else None,
            agent_id=audit.get("agent_id"),
            title=group["title"],
            summary=group["summary"],
            severity=max((f["severity"] for f in selected), key=SEVERITY.get),
            confidence=max(f["confidence"] for f in selected),
            evidence=[ref for f in selected for ref in [f["id"], *f["evidence"]]],
            occurrences=[
                {
                    "audit_id": audit["audit_id"],
                    "source_id": audit["audit_id"],
                    "finding_id": f["id"],
                    "trace_ids": f["trace_ids"],
                    "observed_ns": f["observed_ns"],
                    "assigned_at": group["assigned_at"],
                    "basis": f["basis"],
                }
                for f in selected
            ],
        )


def assign_agent(workspace, issue_id, agent_id):
    db, project = binding(workspace)
    with db.transaction() as tx:
        issue = tx.get_record(project, "issues", issue_id)
        if not issue or issue.get("agent_id") not in (None, agent_id):
            raise AuditError("issue belongs to another agent or is unavailable")
        issue["agent_id"] = agent_id
        return tx.put_record(project, "issues", issue_id, issue)


def link_task(workspace, issue_id, task_id, agent_id=None):
    db, project = binding(workspace)
    with db.transaction() as tx:
        issue = tx.get_record(project, "issues", issue_id)
        if issue is None:
            raise AuditError("issue not found")
        if agent_id:
            if issue.get("agent_id") not in (None, agent_id):
                raise AuditError("issue belongs to another agent")
            issue["agent_id"] = agent_id
        issue["task_ids"] = list(dict.fromkeys([*issue["task_ids"], task_id]))
        return tx.put_record(project, "issues", issue_id, issue)


def update_issue(workspace, event, verification=None, *, run_id=None, candidate_id=None):
    validate_record("issue-update", event)
    if verification is not None:
        raise AuditError("manual verification receipts cannot establish new engine verification")
    if event["status"] == "resolved_verified" and (not run_id or not candidate_id):
        raise AuditError("verified resolution requires --run and --candidate engine evidence")
    if event["status"] != "resolved_verified" and (run_id or candidate_id):
        raise AuditError("run and candidate selectors apply only to verified resolution")
    issue = get_issue(workspace, event["issue_id"])
    if any(ref not in issue["evidence"] for ref in event["evidence"]):
        raise AuditError("issue update contains unknown evidence references")
    receipt = None
    if event["status"] == "resolved_verified":
        from agentagon.capabilities.experiments.engine import verify_origin

        receipt = workspace.artifact(
            verify_origin(workspace, run_id, candidate_id, event["issue_id"])
        )
    db, project = binding(workspace)
    with db.transaction() as tx:
        issue = tx.get_record(project, "issues", event["issue_id"])
        clean = redact(event)
        if (
            issue["history"]
            and all(issue["history"][-1].get(k) == v for k, v in clean.items())
            and issue["history"][-1].get("verification") == receipt
        ):
            return issue
        created = {**clean, "at": now(), "verification": receipt}
        created["event_id"] = identifier("event", created)
        issue["history"].append(created)
        issue["status"] = clean["status"]
        issue["verification"] = receipt
        return tx.put_record(project, "issues", issue["issue_id"], issue)
