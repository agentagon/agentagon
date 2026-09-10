"""Plan a bounded download from provider root metadata before fetching trace bodies."""

from pathlib import Path

from agentagon.core.records import AuditError, load_json, now, timestamp_ns, validate_record
from agentagon.storage.workspace import Workspace


def plan_acquisition(workspace: Workspace, audit_id: str, inventory_path: Path) -> dict:
    if inventory_path.stat().st_size > 32 * 1024 * 1024:
        raise AuditError("root inventory exceeds 32 MiB; narrow the trace window")
    inventory = load_json(inventory_path)
    validate_record("selection", inventory)
    with workspace.locked():
        audit = workspace.read_audit(audit_id)
        if audit["mode"] == "code":
            raise AuditError("code-only audits do not acquire traces")
        if audit["acquisition"] or audit["reviews"] or audit["diagnoses"] or audit["groups"]:
            raise AuditError(
                "acquisition or analysis has begun; start a new audit to change selection"
            )
        if inventory["source"] != audit["source"] or inventory["project"] != audit["project"]:
            raise AuditError("root inventory source/project does not match audit")
        start, end = (timestamp_ns(audit["window"][key]) for key in ("start", "end"))
        if (timestamp_ns(inventory["from"]), timestamp_ns(inventory["to"])) != (start, end):
            raise AuditError("root inventory window does not match audit")
        roots = {}
        for root in inventory["roots"]:
            started = timestamp_ns(root["started_at"])
            if started is None:
                raise AuditError("root inventory requires known start times")
            if root["id"] in roots and roots[root["id"]] != started:
                raise AuditError("conflicting root start times in inventory")
            roots[root["id"]] = started
        eligible = sorted(
            (key for key, started in roots.items() if start <= started < end),
            key=lambda key: (roots[key], key),
            reverse=True,
        )
        selected = eligible if audit["limit"] == "all" else eligible[: audit["limit"]]
        plan = {
            "created_at": now(),
            "inventory": workspace.artifact(inventory),
            "selected_trace_ids": selected,
            "estimate": {
                "eligible_traces": len(eligible),
                "selected_traces": len(selected),
                "requested_limit": audit["limit"],
                "inventory_complete": inventory["complete"],
                "eligible_count_kind": "exact" if inventory["complete"] else "lower_bound",
                "selection": "newest_roots_in_inventory",
                "span_count": None,
                "bytes": None,
            },
        }
        audit["acquisition_plan"] = plan
        workspace.save_audit(audit)
        return {
            "audit_id": audit_id,
            **plan,
            "next_action": (
                "Present the trace count and completeness to the user, then fetch the selected "
                "IDs and every available descendant. Span count and download size are unknown."
            ),
        }
