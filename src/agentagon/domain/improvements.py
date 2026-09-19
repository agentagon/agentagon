"""Verified changes and declared deployments; engine evidence remains authoritative."""

from agentagon.core.records import AuditError, digest, identifier, now, timestamp_ns


def retain(application, workspace, job, result):
    if job["kind"] not in {"fix", "optimize"} or not result.get("run_id"):
        return result
    from agentagon.capabilities.experiments import engine
    from agentagon.capabilities.experiments.store import load_run

    run = load_run(workspace, result["run_id"])
    comparison = result.get("comparison") or {}
    choices = ([comparison["winner"]] if comparison.get("winner") else []) + comparison.get(
        "alternatives", []
    )
    ids = [c["candidate_id"] for c in choices]
    if job["kind"] == "fix" and run.get("selected"):
        ids = [run["selected"]["candidate_id"]]
    retained = []
    for candidate_id in dict.fromkeys(ids):
        if candidate_id == run["baseline_id"]:
            continue
        candidate = run["candidates"][candidate_id]
        engine._verified_evidence(workspace, run, candidate)
        record_id = identifier("improvement", job["project_id"], run["run_id"], candidate_id)
        record = {
            "id": record_id,
            "agent_id": job["application_agent_id"],
            "task_id": job["id"],
            "goal_id": job.get("goal_id"),
            "issue_id": result.get("issue_id") or job["options"].get("issue_id"),
            "run_id": run["run_id"],
            "candidate_id": candidate_id,
            "tested_revision": candidate["source_revision"],
            "evaluation_state": "verified",
            "verification": result.get("verification"),
            "summary": result.get("summary", ""),
            "lessons": result.get("lessons", []),
            "created_at": now(),
        }
        if not application.state.db.get_record(job["project_id"], "improvements", record_id):
            application.state.db.put_record(job["project_id"], "improvements", record_id, record)
        retained.append(record_id)
    return {**result, "improvement_ids": retained}


def list_improvements(application, project_id):
    from agentagon.capabilities.experiments.store import load_run

    workspace = application.state.workspace(project_id)
    records = application.state.db.list_records(project_id, "improvements")
    for record in records:
        try:
            selected = load_run(workspace, record["run_id"]).get("selected") or {}
            record["selected"] = selected.get("candidate_id") == record["candidate_id"]
        except (AuditError, OSError):
            record["selected"] = False
    return records


def deploy(application, project_id, payload):
    from agentagon.workflows.runtime import operation_id

    if not isinstance(payload, dict) or set(payload) - {
        "operation_id",
        "improvement_id",
        "release",
        "environment",
        "revision",
        "deployed_at",
    }:
        raise AuditError("unsupported deployment fields")
    op = operation_id(payload.get("operation_id"))
    record_id = identifier("deployment", project_id, op)
    request_digest = digest(payload)
    previous = application.state.db.get_record(project_id, "deployments", record_id)
    if previous:
        if previous["request_digest"] != request_digest:
            raise AuditError("operation_id was already used for another deployment")
        return previous
    improvement = next(
        (
            r
            for r in list_improvements(application, project_id)
            if r["id"] == payload.get("improvement_id")
        ),
        None,
    )
    if not improvement or not improvement["selected"]:
        raise AuditError("select a verified improvement before recording deployment")
    for field in ("release", "environment", "revision", "deployed_at"):
        if not isinstance(payload.get(field), str) or not 1 <= len(payload[field]) <= 200:
            raise AuditError(f"deployment requires {field}")
    if timestamp_ns(payload["deployed_at"]) > timestamp_ns(now()):
        raise AuditError("deployment time cannot be in the future")
    record = {
        **{k: v for k, v in payload.items() if k != "revision"},
        "deployed_revision": payload["revision"],
        "id": record_id,
        "agent_id": improvement["agent_id"],
        "request_digest": request_digest,
        "linkage": "user_declared",
        "tested_revision": improvement["tested_revision"],
        "exact_tested_revision": payload["revision"] == improvement["tested_revision"],
        "created_at": now(),
    }
    return application.state.db.put_record(project_id, "deployments", record_id, record)


def retain_reported_deployments(application, project_id, policy, samples):
    """Retain exact release/revision reports without inventing a deployment time."""
    selected = {
        i["tested_revision"]: i
        for i in list_improvements(application, project_id)
        if i["agent_id"] == policy["agent_id"] and i["selected"]
    }
    for sample in samples:
        improvement = selected.get(sample["revision"])
        if not improvement or not isinstance(sample.get("release"), str) or not sample["release"]:
            continue
        record_id = identifier(
            "deployment",
            project_id,
            policy["agent_id"],
            policy["selector"]["environment"],
            sample["release"],
            sample["revision"],
        )
        if application.state.db.get_record(project_id, "deployments", record_id):
            continue
        application.state.db.put_record(
            project_id,
            "deployments",
            record_id,
            {
                "id": record_id,
                "agent_id": policy["agent_id"],
                "improvement_id": improvement["id"],
                "environment": policy["selector"]["environment"],
                "release": sample["release"],
                "deployed_revision": sample["revision"],
                "tested_revision": improvement["tested_revision"],
                "exact_tested_revision": True,
                "linkage": "trace_reported",
                "deployed_at": None,
                "evidence": {"snapshot_id": sample["snapshot_id"], "trace_id": sample["trace_id"]},
                "first_seen_at": now(),
            },
        )
