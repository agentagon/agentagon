"""Full audit preparation and completion preserve the fixed rubric."""

from agentagon.capabilities.traces import snapshots
from agentagon.core.records import AuditError
from agentagon.workflows.audit import operations


def prepare(workspace, job, save):
    options = job["options"]
    trace = (
        snapshots.load(workspace, options["trace_snapshot_id"])
        if options.get("trace_snapshot_id")
        else None
    )
    mode = options.get("mode", "combined" if trace else "code")
    if options.get("scope") == "traces":
        mode = "traces"
    if mode != "code" and not trace:
        raise AuditError("import and select traces before starting a trace audit")
    selection = trace["selection"] if trace else {}
    provenance = trace["provenance"] if trace else {}
    if not job.get("actual_model"):
        raise AuditError(
            "Coding host did not report its actual model; update the host or select a model explicitly."
        )
    if not job["workflow_ids"].get("audit_id"):
        audit = operations.start(
            workspace,
            mode=mode,
            source=provenance.get("provider") if mode != "code" else None,
            project=selection.get("project") or provenance.get("project")
            if mode != "code"
            else None,
            start_time=selection.get("start") if mode != "code" else None,
            end_time=selection.get("end") if mode != "code" else None,
            limit=selection.get("cap", 100) if mode != "code" else None,
            scopes=options.get("code_scopes", []),
            host=job["agent"],
            model=job["actual_model"],
            goal=job["goal"],
            request_id=job["id"],
            agent_id=job.get("application_agent_id"),
            code_scope="changes" if options.get("scope") == "changes" else "full",
        )
        job["workflow_ids"]["audit_id"] = audit["audit_id"]
        save(job)
    if trace and mode != "code" and not job.get("trace_imported"):
        saved = workspace.read_audit(job["workflow_ids"]["audit_id"])
        if not saved.get("acquisition"):
            exported = workspace.artifact(trace["items"])
            operations.import_traces(workspace, saved["audit_id"], workspace.root / exported)
        job["trace_imported"] = True
        save(job)


def validate(workspace, job, candidate, result):
    record = workspace.read_audit(candidate)
    state = operations.status(workspace, candidate)["state"]
    complete = state in {"complete", "complete_with_limits"}
    return record, state, complete
