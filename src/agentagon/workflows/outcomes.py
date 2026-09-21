"""Record workflow lessons and verified issue resolution from retained evidence."""

from agentagon.core.records import AuditError, identifier, now
from agentagon.domain.issues import binding, link_task, record_issue

CLASSIFIED_PRODUCTION_OUTCOMES = {"improved", "regressed", "no_material_change"}
PRODUCTION_UNCERTAINTY = (
    "Observational production evidence does not establish causation or universal recovery; "
    "the retained Deployment and Observation records remain authoritative."
)


def _latest_lesson(application, project_id, group_id, key, agent_id):
    entry_id = identifier("memory", group_id, key)
    try:
        return application.memory.history(project_id, group_id, entry_id, agent_id)["versions"][-1]
    except AuditError as exc:
        if str(exc) != "lesson not found in this memory group":
            raise
        return None


def _references(existing, additions):
    retained = list((existing or {}).get("evidence", []))
    for reference in additions:
        if isinstance(reference, str) and reference and reference not in retained:
            retained.append(reference)
    # Earlier immutable versions retain any references displaced by the store bound.
    return retained[-100:]


def _uncertainty(existing):
    retained = (existing or {}).get("uncertainty", "").strip()
    if PRODUCTION_UNCERTAINTY in retained:
        return retained
    return "\n".join(value for value in (retained, PRODUCTION_UNCERTAINTY) if value)


def _append_feedback(existing, marker, feedback):
    retained = (existing or {}).get("text", "").strip()
    if marker in retained:
        return retained
    addition = f"{marker}: {feedback}".strip()
    if not retained:
        return addition[:24000]
    available = max(0, 24000 - len(addition) - 2)
    return f"{retained[:available]}\n\n{addition}"


def _metric_summary(metrics):
    summaries = []
    for metric in sorted(metrics, key=lambda item: (str(item.get("name", "")), str(item))):
        summary = f"{metric.get('name', 'measurement')} was {str(metric.get('status', 'unknown')).replace('_', ' ')}"
        values = []
        for label, key in (
            ("reference", "reference"),
            ("current", "current"),
            ("delta", "delta"),
            ("reference samples", "reference_count"),
            ("current samples", "count"),
        ):
            if metric.get(key) is not None:
                values.append(f"{label}={metric[key]}")
        if values:
            summary += " (" + ", ".join(values) + ")"
        summaries.append(summary)
    return "; ".join(summaries) or "the retained production assessment changed"


def _production_link(application, project_id, observation, metric):
    deployment_id = metric.get("deployment_id")
    if not isinstance(deployment_id, str) or not deployment_id:
        return None, "the classified measurement did not identify a deployment"
    deployment = application.state.db.get_record(project_id, "deployments", deployment_id)
    if not deployment:
        return None, "the classified measurement referenced an unavailable deployment"
    if (
        deployment.get("agent_id") != observation.get("agent_id")
        or deployment.get("exact_tested_revision") is not True
    ):
        return None, "the deployment was not an exact tested-revision match for this agent"
    improvement_id = deployment.get("improvement_id")
    if not isinstance(improvement_id, str) or not improvement_id:
        return None, "the deployment did not identify a verified improvement"
    improvement = application.state.db.get_record(project_id, "improvements", improvement_id)
    if (
        not improvement
        or improvement.get("id") != improvement_id
        or improvement.get("agent_id") != observation.get("agent_id")
        or improvement.get("workflow") not in {"fix", "optimize"}
        or improvement.get("evaluation_state") != "verified"
        or not isinstance(improvement.get("task_id"), str)
        or not improvement["task_id"]
    ):
        return None, "the deployment could not be bound to a verified Fix or Optimize task"
    if deployment.get("tested_revision") != improvement.get("tested_revision"):
        return None, "the deployment and improvement tested revisions differ"
    task = application.state.db.get_record(project_id, "tasks", improvement["task_id"])
    if (
        not task
        or task.get("kind") != improvement.get("workflow")
        or task.get("application_agent_id") != observation.get("agent_id")
    ):
        return None, "the verified improvement's originating task is unavailable or inconsistent"
    return (improvement, deployment), None


def _record_production_outcome(application, job, observation):
    project_id = job["project_id"]
    agent_id = observation.get("agent_id") or job.get("application_agent_id")
    metrics = observation.get("metrics") if isinstance(observation.get("metrics"), list) else []
    classified = [
        metric
        for metric in metrics
        if isinstance(metric, dict) and metric.get("status") in CLASSIFIED_PRODUCTION_OUTCOMES
    ]
    linked = {}
    unlinked = []
    reasons = []
    for metric in classified:
        relationship, reason = _production_link(application, project_id, observation, metric)
        if relationship:
            improvement, deployment = relationship
            key = (improvement["task_id"], improvement["id"], deployment["id"])
            linked.setdefault(
                key, {"improvement": improvement, "deployment": deployment, "metrics": []}
            )["metrics"].append(metric)
        else:
            unlinked.append(metric)
            reasons.append(reason)

    evidence = observation.get("evidence")
    groups = [
        group
        for group in application.memory.improvement_groups(project_id, agent_id)
        if project_id in group["write_project_ids"]
    ]
    marker = f"Production observation {observation['id']}"
    for relationship in linked.values():
        improvement = relationship["improvement"]
        deployment = relationship["deployment"]
        feedback = (
            f"{_metric_summary(relationship['metrics'])}. This observation is linked through "
            f"deployment {deployment['id']} to verified {improvement['workflow'].title()} "
            f"improvement {improvement['id']} from task {improvement['task_id']}."
        )
        for group in groups:
            existing = _latest_lesson(
                application, project_id, group["id"], improvement["task_id"], agent_id
            )
            application.memory.record(
                project_id,
                group["id"],
                {
                    "key": improvement["task_id"],
                    "text": _append_feedback(existing, marker, feedback),
                    "evidence": _references(
                        existing,
                        [
                            improvement.get("verification"),
                            evidence,
                            observation["id"],
                            deployment["id"],
                            improvement["id"],
                        ],
                    ),
                    "uncertainty": _uncertainty(existing),
                    "status": (existing or {}).get("status", "active"),
                },
                agent_id,
            )

    # Keep the previous descriptive lesson behavior for baselines and issue-only
    # changes. A classified metric falls back here only when its operational link
    # is incomplete; never invent a relationship from names, time, or revisions.
    if not classified or unlinked:
        limitation = (
            "; ".join(dict.fromkeys(reason for reason in reasons if reason))
            if unlinked
            else "no accepted material production outcome identified a deployment"
        )
        feedback = (
            f"Unlinked production feedback: {_metric_summary(unlinked or metrics)}. "
            f"No originating improvement was attached because {limitation}."
        )
        for group in groups:
            existing = _latest_lesson(
                application, project_id, group["id"], observation["id"], agent_id
            )
            application.memory.record(
                project_id,
                group["id"],
                {
                    "key": observation["id"],
                    "text": _append_feedback(existing, marker, feedback),
                    "evidence": _references(existing, [evidence, observation["id"]]),
                    "uncertainty": _uncertainty(existing),
                    "status": (existing or {}).get("status", "active"),
                },
                agent_id,
            )


def resolve_repair(workspace, job, result):
    if not result.get("verification"):
        raise AuditError("repair did not retain verification evidence")
    receipt = workspace.read_artifact(result["verification"])
    if receipt.get("provenance") != "agentagon-engine" or receipt.get("run_id") != result.get(
        "run_id"
    ):
        raise AuditError("repair verification binding changed")
    issue_id = job["options"].get("issue_id")
    if not issue_id:
        diagnosis = result.pop("diagnosis", None)
        if (
            not isinstance(diagnosis, dict)
            or not isinstance(diagnosis.get("expected_behavior"), str)
            or not diagnosis["expected_behavior"].strip()
        ):
            raise AuditError("repair must record its diagnosis and established expected behavior")
        if job["options"].get("trace_snapshot_id"):
            from agentagon.workflows.discover.handler import accept

            _, discovered, _ = accept(workspace, job, {"issues": [diagnosis]})
            issue_id = discovered["issue_ids"][0]
        else:
            issue_id = record_issue(
                workspace,
                key=diagnosis.get("key"),
                title=diagnosis.get("title"),
                summary=diagnosis.get("summary"),
                agent_id=job.get("application_agent_id"),
                occurrences=[],
                evidence=[workspace.artifact(diagnosis)],
            )["issue_id"]
    link_task(workspace, issue_id, job["id"], job.get("application_agent_id"))
    db, project = binding(workspace)
    with db.transaction() as tx:
        issue = tx.get_record(project, "issues", issue_id)
        event_id = identifier("event", job["id"], result["verification"])
        if not any(event["event_id"] == event_id for event in issue["history"]):
            issue["history"].append(
                {
                    "event_id": event_id,
                    "at": now(),
                    "status": "resolved_verified",
                    "reason": "Regression reproduced and repaired on the tested revision",
                    "task_id": job["id"],
                    "verification": result["verification"],
                }
            )
        issue.update(
            status="resolved_verified",
            verification=result["verification"],
            tested_revision=receipt["source_revision"],
            production_recovery_verified=False,
        )
        tx.put_record(project, "issues", issue_id, issue)
    return {**result, "issue_id": issue_id}


def record_outcome(application, workspace, job):
    """A failed attempt is a lesson too. This never grants verified status."""
    if job["state"] not in {"completed", "completed_with_limits", "failed", "cancelled"}:
        return
    result = job.get("result") or {}
    if job["kind"] == "observe":
        observation_id = result.get("observation_id")
        if not observation_id:
            return
        observation = application.state.db.get_record(
            job["project_id"], "observations", observation_id
        )
        if observation is None:
            raise AuditError("production observation is unavailable for lesson recording")
        if not observation.get("lesson_due"):
            return
        _record_production_outcome(application, job, observation)
        return
    references = [
        value for key, value in job.get("workflow_ids", {}).items() if key.endswith("_id")
    ]
    if result.get("verification"):
        references.append(result["verification"])
    text = f"{job['kind']} task {job['id']}: {job['state']}. Objective: {job['goal']}. " + str(
        result.get("summary") or job.get("next_action") or "No verified outcome was produced."
    )
    text += " Reported learning: " + str(result.get("learning", {}))
    text += " Lessons considered: " + str(result.get("lessons", []))
    text += " Context: " + str(
        {
            "agent": job.get("application_agent_id"),
            "goal": job.get("goal_id"),
            "issue": job["options"].get("issue_id"),
        }
    )
    agent = job.get("application_agent_id")
    for group in application.memory.improvement_groups(job["project_id"], agent):
        if job["project_id"] not in group["write_project_ids"]:
            continue
        application.memory.record(
            job["project_id"],
            group["id"],
            {
                "key": job["id"],
                "text": text[:24000],
                "evidence": references,
                "uncertainty": "Production recovery is not established. Lessons are advisory; inspect referenced evidence.",
            },
            agent,
        )
    if issue_id := job["options"].get("issue_id"):
        link_task(workspace, issue_id, job["id"], job.get("application_agent_id"))
