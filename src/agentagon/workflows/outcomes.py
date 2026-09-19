"""Record workflow lessons and verified issue resolution from retained evidence."""

from agentagon.core.records import AuditError, identifier, now
from agentagon.domain.issues import binding, link_task, record_issue


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
        if not observation.get("lesson_due"):
            return
        text = "Production feedback: " + str(observation["metrics"])
        for group in application.memory.improvement_groups(
            job["project_id"], job.get("application_agent_id")
        ):
            application.memory.record(
                job["project_id"],
                group["id"],
                {
                    "key": observation_id,
                    "text": text[:24000],
                    "evidence": [observation["evidence"]],
                    "uncertainty": "Observational evidence; does not establish causation or universal recovery.",
                },
                job.get("application_agent_id"),
            )
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
    for group in application.memory.list(job["project_id"], agent, "improvement"):
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
