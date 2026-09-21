"""Verified changes, explicit user decisions, and declared deployments.

The experiment engine remains authoritative for verification and for the branch it
prepared internally.  A separate durable receipt records whether a user actually
chose that candidate.  Keeping these facts separate prevents an automatic Fix
selection from being presented as user approval.
"""

from agentagon.core.records import (
    AuditError,
    digest,
    identifier,
    now,
    timestamp_ns,
)

DECISIONS = {"select_candidate", "keep_current"}
TERMINAL_TASKS = {"completed", "completed_with_limits"}
IDENTITY_FIELDS = (
    "run_id",
    "candidate_id",
    "baseline_id",
    "source_revision",
    "source_digest",
    "evaluation_digest",
    "inputs_digest",
    "profile_digest",
)


def _linked_jobs(application, project_id, run_id):
    return [
        job
        for job in application.runtime.list(project_id)
        if run_id
        in {
            job.get("workflow_ids", {}).get("run_id"),
            job.get("result", {}).get("run_id"),
        }
    ]


def _validate_kind(application, project_id, workflow, run_id, *, require_complete=False):
    if workflow not in {"fix", "optimize"}:
        raise AuditError("user decisions apply only to Fix or Optimize results")
    jobs = _linked_jobs(application, project_id, run_id)
    kinds = {job["kind"] for job in jobs if job.get("kind") in {"fix", "optimize"}}
    if kinds and kinds != {workflow}:
        raise AuditError("result workflow does not match its managed task")
    if jobs and not any(job.get("kind") == workflow for job in jobs):
        raise AuditError("result does not belong to this workflow")
    if require_complete and any(
        job.get("kind") == workflow and job.get("state") not in TERMINAL_TASKS for job in jobs
    ):
        raise AuditError("finish this task's required verification before choosing a result")
    return [job for job in jobs if job.get("kind") == workflow]


def _identity(data, candidate_id):
    baseline_id = data["baseline_id"]
    evidence_id = candidate_id or baseline_id
    candidate = data["candidates"].get(evidence_id)
    if not candidate:
        raise AuditError("decision candidate is missing from this result")
    return {
        "run_id": data["run_id"],
        "candidate_id": candidate_id,
        "baseline_id": baseline_id,
        "source_revision": candidate["source_revision"],
        "source_digest": candidate["source_digest"],
        "evaluation_digest": data["evaluation_digest"],
        "inputs_digest": data["inputs_digest"],
        "profile_digest": data["profile_digest"],
    }


def _validate_run_kind(data, workflow):
    if data.get("focused_repair") and workflow != "fix":
        raise AuditError("focused repair result must be inspected as Fix")
    if data.get("optimizer_configured") and workflow != "optimize":
        raise AuditError("optimizer result must be inspected as Optimize")


def _public_decision(record, data=None):
    if not record:
        return None
    value = {
        key: record.get(key)
        for key in (
            "id",
            "operation_id",
            "decision",
            "candidate_id",
            "baseline_id",
            "source_revision",
            "source_digest",
            "evaluation_digest",
            "inputs_digest",
            "profile_digest",
            "decided_at",
            "revision",
        )
    }
    if data is not None:
        try:
            identity = _identity(data, record.get("candidate_id"))
            value["current"] = all(record.get(key) == identity.get(key) for key in IDENTITY_FIELDS)
            if record["decision"] == "select_candidate":
                value["current"] = value["current"] and (
                    (data.get("selected") or {}).get("candidate_id") == record.get("candidate_id")
                )
        except (AuditError, KeyError, TypeError):
            value["current"] = False
    return value


def _operation_current(record, data):
    request = record.get("request") or {}
    identity = request.get("identity")
    try:
        current = identity == _identity(data, request.get("candidate_id"))
        if (
            current
            and record.get("workflow") == "fix"
            and request.get("decision") == "select_candidate"
        ):
            current = (data.get("selected") or {}).get("candidate_id") == request.get(
                "candidate_id"
            )
        return current
    except (AuditError, KeyError, TypeError):
        return False


def _public_operation(record, data):
    if not record:
        return None
    request = record.get("request") or {}
    current = _operation_current(record, data)
    persisted_state = record.get("state")
    state = "stale_pending" if persisted_state == "pending" and not current else persisted_state
    next_action = {
        "pending": "retry_pending_decision",
        "stale_pending": "reconcile_stale_decision",
        "stale": "review_and_choose_again",
    }.get(state)
    return {
        "operation_id": record.get("operation_id"),
        "state": state,
        "persisted_state": persisted_state,
        "expected_revision": request.get("expected_revision"),
        "decision": request.get("decision"),
        "candidate_id": request.get("candidate_id"),
        "current": current,
        "next_action": next_action,
        "reason": record.get("reason"),
        "updated_at": record.get("updated_at"),
    }


def _stale_operation(application, project_id, operation_record_id, request_binding, reason):
    with application.state.db.transaction() as tx:
        record = tx.get_record(project_id, "result_decision_operations", operation_record_id)
        if not record or record.get("request_binding") != request_binding:
            raise AuditError("result decision operation is unavailable; reload the result")
        if record.get("state") != "pending":
            return record
        return tx.put_record(
            project_id,
            "result_decision_operations",
            operation_record_id,
            {**record, "state": "stale", "reason": reason, "stale_at": now()},
            expected_revision=record["revision"],
        )


def _decision_record(application, project_id, workflow, run_id):
    record_id = identifier("result_decision", project_id, workflow, run_id)
    return application.state.db.get_record(project_id, "result_decisions", record_id)


def selection_projection(
    application,
    project_id,
    workflow,
    run_id,
    data=None,
    eligible_candidate_ids=None,
):
    """Project task recommendation and explicit user choice as separate facts."""
    from agentagon.capabilities.experiments.store import load_run

    _validate_kind(application, project_id, workflow, run_id)
    data = data or load_run(application.state.workspace(project_id), run_id)
    _validate_run_kind(data, workflow)
    jobs = _linked_jobs(application, project_id, run_id)
    recommended = None
    for job in sorted(jobs, key=lambda item: item.get("updated_at", ""), reverse=True):
        comparison = job.get("result", {}).get("comparison") or {}
        winner = comparison.get("winner") or {}
        if isinstance(winner, dict) and isinstance(winner.get("candidate_id"), str):
            recommended = winner["candidate_id"]
            break
    if recommended is None:
        recommended = (data.get("selected") or {}).get("candidate_id")
    decision_record = _decision_record(application, project_id, workflow, run_id)
    decision = _public_decision(decision_record, data)
    if eligible_candidate_ids is None:
        from agentagon.capabilities.reporting import build_fix_report

        eligible_candidate_ids = {
            item["id"]
            for item in build_fix_report(application.state.workspace(project_id), data)[
                "comparisons"
            ]["alternatives"]
        }
    else:
        eligible_candidate_ids = set(eligible_candidate_ids)
    operations = [
        item
        for item in application.state.db.list_records(project_id, "result_decision_operations")
        if item.get("workflow") == workflow and item.get("run_id") == run_id
    ]
    pending = next((item for item in operations if item.get("state") == "pending"), None)
    stale = next(
        (
            item
            for item in operations
            if item.get("state") == "stale"
            and (
                decision_record is None
                or item.get("updated_at", "") > decision_record.get("updated_at", "")
            )
        ),
        None,
    )
    operation = _public_operation(pending or stale, data)
    if operation and operation["persisted_state"] == "pending":
        allowed = [operation["next_action"]]
    elif jobs and any(job.get("state") not in TERMINAL_TASKS for job in jobs):
        allowed = []
    else:
        allowed = ["keep_current"]
        if eligible_candidate_ids:
            allowed.insert(0, "select_candidate")
        if decision and decision["decision"] == "select_candidate" and decision["current"]:
            allowed.insert(0, "prepare_local_delivery")
    return {
        "recommended_candidate_id": recommended,
        "engine_selected_candidate_id": (data.get("selected") or {}).get("candidate_id"),
        "decision": decision,
        "decision_operation": operation,
        "expected_revision": decision_record["revision"] if decision_record else 0,
        "allowed_actions": allowed,
    }


def decide(application, project_id, workflow, run_id, payload):
    """Record one idempotent user decision and reconcile engine selection safely."""
    from agentagon.capabilities.experiments import engine, suites
    from agentagon.capabilities.experiments.store import load_run
    from agentagon.workflows.runtime import operation_id

    if not isinstance(payload, dict) or set(payload) - {
        "operation_id",
        "expected_revision",
        "decision",
        "candidate_id",
    }:
        raise AuditError("unsupported result decision fields")
    op = operation_id(payload.get("operation_id"))
    expected = payload.get("expected_revision")
    if type(expected) is not int or expected < 0:
        raise AuditError("expected_revision must be a nonnegative integer")
    choice = payload.get("decision")
    if choice not in DECISIONS:
        raise AuditError("choose select_candidate or keep_current")
    candidate_id = payload.get("candidate_id")
    if choice == "select_candidate" and not isinstance(candidate_id, str):
        raise AuditError("select_candidate requires candidate_id")
    if choice == "keep_current" and candidate_id is not None:
        raise AuditError("keep_current cannot include candidate_id")

    semantic_request = {
        "workflow": workflow,
        "run_id": run_id,
        "operation_id": op,
        "expected_revision": expected,
        "decision": choice,
        "candidate_id": candidate_id,
    }
    request_binding = digest(semantic_request)
    operation_record_id = identifier("result_decision_operation", project_id, op)
    head_id = identifier("result_decision", project_id, workflow, run_id)
    workspace = application.state.workspace(project_id)
    with application.lock:
        jobs = _validate_kind(application, project_id, workflow, run_id, require_complete=True)
        data = load_run(workspace, run_id)
        _validate_run_kind(data, workflow)
        existing = application.state.db.get_record(
            project_id, "result_decision_operations", operation_record_id
        )
        if existing:
            if existing.get("request_binding") != request_binding:
                raise AuditError("operation_id was already used for a different result decision")
            if existing.get("state") == "completed":
                return _public_decision(existing["receipt"], data)
            if existing.get("state") == "stale":
                return _public_operation(existing, data)
        else:
            if candidate_id == data["baseline_id"]:
                raise AuditError("use keep_current to retain the baseline")
            identity = _identity(data, candidate_id)
            if choice == "select_candidate":
                candidate = data["candidates"][candidate_id]
                engine._verified_evidence(workspace, data, candidate)
                if workflow == "fix" and (
                    (data.get("selected") or {}).get("candidate_id") != candidate_id
                ):
                    raise AuditError("Fix decisions must use the task's verified repair")
                for job in jobs:
                    manifest = job.get("options", {}).get("suite_manifest")
                    if manifest:
                        suites.verify_selection(workspace, run_id, candidate_id)
            request = {**semantic_request, "identity": identity}
            with application.state.db.transaction() as tx:
                existing = tx.get_record(
                    project_id, "result_decision_operations", operation_record_id
                )
                if existing:
                    if existing.get("request_binding") != request_binding:
                        raise AuditError(
                            "operation_id was already used for a different result decision"
                        )
                else:
                    head = tx.get_record(project_id, "result_decisions", head_id)
                    if (head["revision"] if head else 0) != expected:
                        raise AuditError("result decision changed; reload before choosing")
                    pending = next(
                        (
                            item
                            for item in tx.list_records(project_id, "result_decision_operations")
                            if item.get("workflow") == workflow
                            and item.get("run_id") == run_id
                            and item.get("state") == "pending"
                        ),
                        None,
                    )
                    if pending:
                        raise AuditError(
                            "another result decision is pending; resume its saved operation"
                        )
                    existing = tx.put_record(
                        project_id,
                        "result_decision_operations",
                        operation_record_id,
                        {
                            "id": operation_record_id,
                            "operation_id": op,
                            "workflow": workflow,
                            "run_id": run_id,
                            "state": "pending",
                            "request_binding": request_binding,
                            "request": request,
                        },
                    )

        if existing.get("state") == "completed":
            return _public_decision(existing["receipt"], data)
        if existing.get("state") == "stale":
            return _public_operation(existing, data)

        # The retained request, including its frozen evidence identity, is the only
        # request that may resume. A retry never silently binds the user's choice to
        # evidence that appeared after the operation was persisted.
        request = existing["request"]
        identity = request["identity"]
        candidate_id = request.get("candidate_id")
        choice = request["decision"]
        current = load_run(workspace, run_id)
        if not _operation_current(existing, current):
            stale = _stale_operation(
                application,
                project_id,
                operation_record_id,
                request_binding,
                "result_evidence_changed",
            )
            return _public_operation(stale, current)

        if choice == "select_candidate":
            try:
                candidate = current["candidates"][candidate_id]
                engine._verified_evidence(workspace, current, candidate)
                for job in jobs:
                    manifest = job.get("options", {}).get("suite_manifest")
                    if manifest:
                        suites.verify_selection(workspace, run_id, candidate_id)
                if workflow == "optimize":
                    engine.select(workspace, run_id, candidate_id)
            except AuditError:
                stale = _stale_operation(
                    application,
                    project_id,
                    operation_record_id,
                    request_binding,
                    "candidate_no_longer_eligible",
                )
                return _public_operation(stale, load_run(workspace, run_id))

        current = load_run(workspace, run_id)
        if identity != _identity(current, candidate_id):
            stale = _stale_operation(
                application,
                project_id,
                operation_record_id,
                request_binding,
                "result_evidence_changed",
            )
            return _public_operation(stale, current)
        if choice == "select_candidate" and (
            (current.get("selected") or {}).get("candidate_id") != candidate_id
        ):
            stale = _stale_operation(
                application,
                project_id,
                operation_record_id,
                request_binding,
                "candidate_selection_not_established",
            )
            return _public_operation(stale, current)

        decided_at = now()
        receipt = {
            "id": head_id,
            "operation_id": request["operation_id"],
            "workflow": workflow,
            "decision": choice,
            **identity,
            "decided_at": decided_at,
        }
        with application.state.db.transaction() as tx:
            saved_operation = tx.get_record(
                project_id, "result_decision_operations", operation_record_id
            )
            if not saved_operation or saved_operation.get("request_binding") != request_binding:
                raise AuditError("result decision receipt is unavailable; reload the result")
            if saved_operation.get("state") == "completed":
                return _public_decision(saved_operation["receipt"], current)
            if saved_operation.get("state") == "stale":
                return _public_operation(saved_operation, current)
            head = tx.get_record(project_id, "result_decisions", head_id)
            if (head["revision"] if head else 0) != request["expected_revision"]:
                stale = tx.put_record(
                    project_id,
                    "result_decision_operations",
                    operation_record_id,
                    {
                        **saved_operation,
                        "state": "stale",
                        "reason": "result_decision_changed",
                        "stale_at": decided_at,
                    },
                    expected_revision=saved_operation["revision"],
                )
                return _public_operation(stale, current)
            saved = tx.put_record(
                project_id,
                "result_decisions",
                head_id,
                receipt,
                expected_revision=request["expected_revision"],
            )
            tx.put_record(
                project_id,
                "result_decision_operations",
                operation_record_id,
                {**saved_operation, "state": "completed", "receipt": saved},
                expected_revision=saved_operation["revision"],
            )
            attention_id = identifier("improvement_attention", project_id, run_id)
            attention = tx.get_record(project_id, "attention", attention_id)
            if attention:
                tx.put_record(
                    project_id,
                    "attention",
                    attention_id,
                    {**attention, "active": False, "resolved_at": decided_at},
                    expected_revision=attention["revision"],
                )
        return _public_decision(saved, current)


def require_user_selection(application, project_id, workflow, run_id):
    """Return the current exact selection receipt or reject measured delivery."""
    from agentagon.capabilities.experiments.store import load_run

    data = load_run(application.state.workspace(project_id), run_id)
    selection = selection_projection(application, project_id, workflow, run_id, data)
    decision = selection["decision"]
    if not decision or decision["decision"] != "select_candidate":
        raise AuditError("choose a verified candidate before preparing measured delivery")
    if not decision["current"]:
        raise AuditError("chosen candidate or evidence changed; review and choose again")
    return decision


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
            "workflow": job["kind"],
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
    if retained and not _decision_record(
        application, job["project_id"], job["kind"], run["run_id"]
    ):
        attention_id = identifier("improvement_attention", job["project_id"], run["run_id"])
        if not application.state.db.get_record(job["project_id"], "attention", attention_id):
            application.state.db.put_record(
                job["project_id"],
                "attention",
                attention_id,
                {
                    "id": attention_id,
                    "active": True,
                    "kind": "result_decision",
                    "agent_id": job["application_agent_id"],
                    "task_id": job["id"],
                    "run_id": run["run_id"],
                    "workflow": job["kind"],
                    "message": "Verified result is ready for your decision",
                    "at": now(),
                },
            )
    return {**result, "improvement_ids": retained}


def list_improvements(application, project_id):
    from agentagon.capabilities.experiments.store import load_run

    workspace = application.state.workspace(project_id)
    records = application.state.db.list_records(project_id, "improvements")
    for record in records:
        try:
            run = load_run(workspace, record["run_id"])
            selection = selection_projection(
                application, project_id, record["workflow"], record["run_id"], run
            )
            record["recommended_by_task"] = (
                selection["recommended_candidate_id"] == record["candidate_id"]
            )
            record["selected_by_user"] = bool(
                selection["decision"]
                and selection["decision"]["decision"] == "select_candidate"
                and selection["decision"]["current"]
                and selection["decision"]["candidate_id"] == record["candidate_id"]
            )
            record["decision"] = selection["decision"]
            record["next_action"] = (
                "prepare_local_delivery" if record["selected_by_user"] else "review_decision"
            )
        except (AuditError, OSError):
            record.update(
                recommended_by_task=False,
                selected_by_user=False,
                decision=None,
                next_action="inspect_evidence",
            )
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
    if not improvement or not improvement["selected_by_user"]:
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
        "user_decision_id": improvement["decision"]["id"],
        "user_decision_revision": improvement["decision"]["revision"],
        "exact_tested_revision": payload["revision"] == improvement["tested_revision"],
        "created_at": now(),
    }
    return application.state.db.put_record(project_id, "deployments", record_id, record)


def retain_reported_deployments(application, project_id, policy, samples):
    """Retain exact release/revision reports without inventing a deployment time."""
    verified = {
        i["tested_revision"]: i
        for i in list_improvements(application, project_id)
        if i["agent_id"] == policy["agent_id"] and i["evaluation_state"] == "verified"
    }
    for sample in samples:
        improvement = verified.get(sample["revision"])
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
                "selected_by_user": improvement["selected_by_user"],
                "deployed_at": None,
                "evidence": {"snapshot_id": sample["snapshot_id"], "trace_id": sample["trace_id"]},
                "first_seen_at": now(),
            },
        )
