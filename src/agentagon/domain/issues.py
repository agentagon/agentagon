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
    normalized_key = key.casefold().strip()
    issue_id = issue_id or identifier("issue", project, agent_id, normalized_key)
    with db.transaction() as tx:
        if not explicit_id:
            # Ownership is refined independently from issue identity.  Prefer an
            # already-owned record, then reuse the single deterministic unowned
            # record created before code/trace ownership was known.
            matches = [
                i
                for i in tx.list_records(project, "issues")
                if i.get("agent_id") in {None, agent_id}
                and i["key"].casefold().strip() == normalized_key
            ]
            if matches:
                matches.sort(key=lambda item: item.get("agent_id") != agent_id)
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
            # A later occurrence supersedes a user's unverified assertion that the
            # issue was resolved.  Engine verification remains an independent
            # facet: a later trace does not invalidate the exact tested revision or
            # establish production recurrence without deployment/environment
            # evidence.
            if issue["status"] == "resolved_user" and issue["history"]:
                resolved = issue["history"][-1]
                observed = entry.get("observed_ns")
                resolved_at = _time(resolved.get("at"))
                if observed is not None and resolved_at >= 0 and observed > resolved_at:
                    issue["status"] = "reopened"
                    issue["history"].append(
                        {
                            "event_id": identifier("event", issue_id, entry["id"]),
                            "status": "reopened",
                            "at": now(),
                            "reason": "Later trace evidence recurred after user-reported resolution",
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


def triage_issue(workspace, issue_id, payload):
    """Append a user triage/expectation decision without rewriting evidence."""

    if not isinstance(payload, dict) or set(payload) - {
        "action",
        "expected_revision",
        "reason",
        "agent_id",
        "expected_behavior",
    }:
        raise AuditError("unsupported issue update fields")
    action = payload.get("action")
    if action not in {"dismiss", "reopen", "assign", "set_expectation"}:
        raise AuditError("choose dismiss, reopen, assign, or set_expectation")
    allowed = {
        "dismiss": {"action", "expected_revision", "reason"},
        "reopen": {"action", "expected_revision", "reason"},
        "assign": {"action", "expected_revision", "reason", "agent_id"},
        "set_expectation": {
            "action",
            "expected_revision",
            "reason",
            "expected_behavior",
        },
    }[action]
    if set(payload) - allowed:
        raise AuditError(f"unsupported fields for issue {action}")
    expected = payload.get("expected_revision")
    if type(expected) is not int or expected < 1:
        raise AuditError("issue update requires its current revision")
    reason = payload.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 2000:
        raise AuditError("issue update reason must be bounded text")
    if action in {"dismiss", "set_expectation"} and not reason.strip():
        raise AuditError("explain this issue decision")
    agent_id = payload.get("agent_id")
    if action == "assign" and (not isinstance(agent_id, str) or not agent_id.startswith("agent_")):
        raise AuditError("choose an application agent")
    expected_behavior = payload.get("expected_behavior")
    if action == "set_expectation" and (
        not isinstance(expected_behavior, str)
        or not expected_behavior.strip()
        or len(expected_behavior) > 8000
    ):
        raise AuditError("expected behavior must be 1–8000 characters")

    db, project = binding(workspace)
    with db.transaction() as tx:
        issue = tx.get_record(project, "issues", issue_id)
        if issue is None:
            raise AuditError("issue not found in this project")
        if issue["revision"] != expected:
            raise AuditError("issue changed; reload before updating")
        if action == "assign":
            if issue.get("agent_id") not in {None, agent_id}:
                raise AuditError("issue already belongs to another agent")
            issue["agent_id"] = agent_id
        elif action == "dismiss":
            issue["status"] = "dismissed"
        elif action == "reopen":
            issue["status"] = "reopened"
        else:
            issue["expected_behavior"] = expected_behavior.strip()
        event = {
            "event_id": identifier(
                "event",
                issue_id,
                expected,
                action,
                agent_id,
                expected_behavior,
                reason.strip(),
            ),
            "at": now(),
            "kind": {
                "dismiss": "triage",
                "reopen": "triage",
                "assign": "ownership",
                "set_expectation": "expectation",
            }[action],
            "action": action,
            "status": issue["status"],
            "reason": reason.strip(),
        }
        if agent_id:
            event["agent_id"] = agent_id
        if action == "set_expectation":
            event["expected_behavior"] = issue["expected_behavior"]
        issue["history"].append(redact(event))
        return tx.put_record(
            project,
            "issues",
            issue_id,
            issue,
            expected_revision=expected,
        )


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


def _time(value):
    if not isinstance(value, str):
        return -1
    try:
        return timestamp_ns(value)
    except (TypeError, ValueError):
        return -1


def _task_projection(application, project_id, issue):
    linked = set(issue.get("task_ids", []))
    tasks = []
    for task in application.runtime.list(project_id):
        if (
            task["id"] not in linked
            and task.get("options", {}).get("issue_id") != issue["issue_id"]
        ):
            continue
        tasks.append(
            {
                "id": task["id"],
                "workflow": task["kind"],
                "state": task["state"],
                "updated_at": task.get("updated_at"),
                "run_id": (task.get("result") or {}).get("run_id")
                or task.get("workflow_ids", {}).get("run_id"),
                "limited": task["state"] == "completed_with_limits",
            }
        )
    tasks.sort(key=lambda task: (_time(task.get("updated_at")), task["id"]), reverse=True)
    active = next(
        (task for task in tasks if task["state"] in {"needs_input", "paused", "interrupted"}),
        None,
    )
    if active:
        state = "needs_input"
    else:
        active = next((task for task in tasks if task["state"] in {"queued", "running"}), None)
        if active:
            state = "running"
        elif tasks and tasks[0]["state"] in {"failed", "cancelled"}:
            state = "failed"
        elif tasks:
            state = "completed"
        else:
            state = "no_attempt"
    return {
        "state": state,
        "active_task_id": active["id"] if active else None,
        "attempts": tasks,
        "failed_attempt_ids": [
            task["id"] for task in tasks if task["state"] in {"failed", "cancelled"}
        ],
        "unavailable_task_ids": sorted(linked - {task["id"] for task in tasks}),
    }


def _verification_receipt(workspace, reference):
    if not isinstance(reference, str):
        return None
    try:
        receipt = workspace.read_artifact(reference)
    except (AuditError, OSError, ValueError, TypeError):
        return None
    if (
        not isinstance(receipt, dict)
        or receipt.get("provenance") != "agentagon-engine"
        or not isinstance(receipt.get("source_revision"), str)
    ):
        return None
    return receipt


def _test_projection(workspace, issue, work, improvements):
    changes = []
    for event in issue.get("history", []):
        if event.get("status") != "resolved_verified":
            continue
        reference = event.get("verification")
        receipt = _verification_receipt(workspace, reference)
        if receipt:
            changes.append(
                {
                    "kind": "issue_verification",
                    "verified_at": event.get("at"),
                    "tested_revision": receipt["source_revision"],
                    "verification": reference,
                    "run_id": receipt.get("run_id"),
                    "candidate_id": receipt.get("candidate_id"),
                    "task_id": event.get("task_id"),
                }
            )
    for improvement in improvements:
        if improvement.get("evaluation_state") != "verified":
            continue
        changes.append(
            {
                "kind": "improvement",
                "improvement_id": improvement["id"],
                "verified_at": improvement.get("created_at"),
                "tested_revision": improvement["tested_revision"],
                "verification": improvement.get("verification"),
                "run_id": improvement["run_id"],
                "candidate_id": improvement["candidate_id"],
                "task_id": improvement.get("task_id"),
            }
        )
    unique = {}
    for change in changes:
        identity = (
            change.get("run_id"),
            change.get("candidate_id"),
            change["tested_revision"],
        )
        unique.setdefault(identity, change)
    changes = sorted(
        unique.values(),
        key=lambda change: (_time(change.get("verified_at")), change["tested_revision"]),
        reverse=True,
    )
    invalid_verification = bool(issue.get("verification")) and not changes
    attempted = bool(work["attempts"])
    if changes:
        state = "verified"
    elif invalid_verification or attempted:
        state = "incomplete"
    else:
        state = "none"
    latest = changes[0] if changes else {}
    return {
        "state": state,
        "tested_revision": latest.get("tested_revision"),
        "verified_at": latest.get("verified_at"),
        "verification": latest.get("verification"),
        "changes": changes,
        "evidence_available": not invalid_verification,
    }


def _delivery_projection(application, project_id, improvements):
    from agentagon.capabilities.experiments.store import load_run

    selected = [item for item in improvements if item.get("selected_by_user")]
    deliveries = []
    workspace = application.state.workspace(project_id)
    for improvement in selected:
        try:
            run = load_run(workspace, improvement["run_id"])
        except (AuditError, OSError, KeyError, ValueError):
            continue
        for delivery in run.get("deliveries", {}).values():
            if delivery.get("candidate_id") != improvement["candidate_id"]:
                continue
            deliveries.append(
                {
                    "id": delivery["delivery_id"],
                    "state": delivery["state"],
                    "improvement_id": improvement["id"],
                    "source_revision": delivery.get("source_revision"),
                    "created_at": delivery.get("created_at"),
                    "url": delivery.get("url"),
                }
            )
    deliveries.sort(key=lambda item: (_time(item.get("created_at")), item["id"]), reverse=True)
    if any(item["state"] == "published" for item in deliveries):
        state = "published_draft"
    elif deliveries:
        state = "prepared_locally"
    elif selected:
        state = "selected"
    else:
        state = "not_selected"
    decisions = [item.get("decision") for item in improvements if item.get("decision")]
    return {
        "state": state,
        "selected_improvement_ids": [item["id"] for item in selected],
        "deliveries": deliveries,
        "decision": decisions[0] if decisions else None,
    }


def _occurrence_environment(occurrence):
    metadata = occurrence.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return occurrence.get("environment") or metadata.get("environment")


def _matches_deployment(occurrence, deployment):
    if occurrence.get("deployment_id"):
        return occurrence["deployment_id"] == deployment["id"]
    environment = _occurrence_environment(occurrence)
    if not environment or environment != deployment.get("environment"):
        return False
    if occurrence.get("release") and occurrence["release"] != deployment.get("release"):
        return False
    revision = occurrence.get("revision")
    if revision and revision != deployment.get("deployed_revision"):
        return False
    return True


def _later_occurrences(issue, deployment, observed_after=None):
    if not deployment or not deployment.get("deployed_at"):
        return []
    threshold = timestamp_ns(deployment["deployed_at"])
    if observed_after:
        threshold = max(threshold, _time(observed_after))
    return [
        {
            "id": occurrence["id"],
            "observed_ns": occurrence.get("observed_ns"),
            "trace_ids": occurrence.get("trace_ids", []),
            "environment": _occurrence_environment(occurrence),
            "deployment_id": deployment["id"],
            "source_ids": list(
                dict.fromkeys([occurrence.get("source_id"), *occurrence.get("source_ids", [])])
            ),
        }
        for occurrence in issue.get("occurrences", [])
        if occurrence.get("observed_ns") is not None
        and occurrence["observed_ns"] > threshold
        and _matches_deployment(occurrence, deployment)
    ]


def _production_projection(application, project_id, issue, verification, improvements):
    improvement_ids = {item["id"] for item in improvements}
    deployments = [
        deployment
        for deployment in application.state.db.list_records(project_id, "deployments")
        if deployment.get("improvement_id") in improvement_ids
    ]
    deployment_by_id = {item["id"]: item for item in deployments}
    observations = []
    for observation in application.state.db.list_records(project_id, "observations"):
        if issue.get("agent_id") and observation.get("agent_id") != issue["agent_id"]:
            continue
        for metric in observation.get("metrics", []):
            definition = metric.get("definition", {})
            if (
                definition.get("metric") != "issue_recurrence"
                or definition.get("issue_id") != issue["issue_id"]
            ):
                continue
            observations.append(
                {
                    "observation_id": observation["id"],
                    "observed_at": observation.get("window", {}).get("end")
                    or observation.get("updated_at"),
                    "evidence": observation.get("evidence"),
                    "metric": copy.deepcopy(metric),
                }
            )
    observations.sort(
        key=lambda item: (_time(item.get("observed_at")), item["observation_id"]),
        reverse=True,
    )
    latest = observations[0] if observations else None
    metric = latest["metric"] if latest else None
    deployed = [item for item in deployments if item.get("deployed_at")]
    latest_deployment = (
        max(deployed, key=lambda item: timestamp_ns(item["deployed_at"])) if deployed else None
    )
    deployment = deployment_by_id.get(metric.get("deployment_id")) if metric else None
    deployment = deployment or latest_deployment
    later = _later_occurrences(
        issue,
        deployment,
        latest.get("observed_at") if latest else None,
    )
    if later:
        state = "recurring"
    elif metric is None:
        state = "not_observed"
    elif metric.get("status") == "regressed":
        state = "regressed"
    elif isinstance(metric.get("current"), (int, float)) and metric["current"] > 0:
        state = "recurring"
    elif (
        metric.get("status") == "improved"
        and metric.get("current") == 0
        and deployment
        and deployment.get("exact_tested_revision")
    ):
        state = "recovery_supported"
    elif metric.get("status") == "no_material_change":
        state = "no_material_change"
    elif metric.get("status") == "not_comparable":
        state = "not_comparable"
    else:
        state = "insufficient_evidence"
    return {
        "state": state,
        "latest_observation_id": latest["observation_id"] if latest else None,
        "metric": metric,
        "deployment_id": deployment["id"] if deployment else None,
        "deployments": [
            {
                key: deployment.get(key)
                for key in (
                    "id",
                    "improvement_id",
                    "release",
                    "environment",
                    "deployed_revision",
                    "tested_revision",
                    "exact_tested_revision",
                    "linkage",
                    "deployed_at",
                )
            }
            for deployment in deployments
        ],
        "later_occurrences": later,
        "observational": True,
    }


def _triage_projection(issue):
    status = issue["status"]
    if status == "dismissed":
        state, provenance = "dismissed", "user_decision"
    elif status == "resolved_user":
        state, provenance = "resolved", "user_report"
    elif status == "resolved_verified":
        state, provenance = "resolved", "test_verification"
    else:
        state = "open"
        provenance = "recurrence" if status == "reopened" else "issue_evidence"
    event = issue.get("history", [])[-1] if issue.get("history") else None
    return {
        "state": state,
        "disposition": status,
        "provenance": provenance,
        "event": copy.deepcopy(event),
    }


def _next_actions(issue, facets):
    if facets["triage"]["state"] == "dismissed":
        return ["reopen_issue"]
    actions = []
    if not issue.get("agent_id"):
        actions.append("assign_agent")
    work = facets["work"]
    if work["state"] in {"running", "needs_input"}:
        actions.append("inspect_task")
        return actions
    if facets["production"]["state"] in {"recurring", "regressed"}:
        actions.append("investigate_recurrence")
    if facets["test_verification"]["state"] != "verified":
        actions.append("start_fix")
        return list(dict.fromkeys(actions))
    delivery = facets["delivery"]["state"]
    if delivery == "not_selected":
        actions.append("review_verified_change")
    elif delivery == "selected":
        actions.append("prepare_local_delivery")
    elif not facets["production"]["deployments"]:
        actions.append("record_deployment")
    elif facets["production"]["state"] in {
        "not_observed",
        "insufficient_evidence",
        "not_comparable",
    }:
        actions.append("observe_production")
    return list(dict.fromkeys(actions))


def lifecycle_projection(application, project_id, issue_id):
    """Compose independent issue facets from their authoritative evidence.

    This is a read projection only.  It does not rewrite the legacy ``status``
    field, task state, engine verification, result decision, delivery,
    deployment, or observation records.
    """

    from agentagon.domain.improvements import list_improvements

    workspace = application.state.workspace(project_id)
    issue = get_issue(workspace, issue_id)
    improvements = [
        item
        for item in list_improvements(application, project_id)
        if item.get("issue_id") == issue_id
    ]
    work = _task_projection(application, project_id, issue)
    verification = _test_projection(workspace, issue, work, improvements)
    facets = {
        "triage": _triage_projection(issue),
        "work": work,
        "test_verification": verification,
        "delivery": _delivery_projection(application, project_id, improvements),
        "production": _production_projection(
            application, project_id, issue, verification, improvements
        ),
    }
    return {
        "id": issue["issue_id"],
        "issue_id": issue["issue_id"],
        "agent_id": issue.get("agent_id"),
        "title": issue["title"],
        "summary": issue["summary"],
        "severity": issue["severity"],
        "confidence": issue["confidence"],
        "status": issue["status"],
        "revision": issue["revision"],
        "historical_affected_traces": issue["historical_affected_traces"],
        "first_seen_ns": issue.get("first_seen_ns"),
        "last_seen_ns": issue.get("last_seen_ns"),
        "occurrences": [
            {
                key: copy.deepcopy(occurrence.get(key))
                for key in (
                    "id",
                    "source_id",
                    "source_ids",
                    "trace_ids",
                    "observed_ns",
                    "basis",
                    "evidence",
                    "evidence_versions",
                    "environment",
                    "deployment_id",
                    "release",
                    "revision",
                    "metadata",
                )
                if occurrence.get(key) is not None
            }
            for occurrence in issue["occurrences"]
        ],
        "facets": facets,
        "next_actions": _next_actions(issue, facets),
    }
