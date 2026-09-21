"""Immutable provider imports; selected private data never becomes repository source."""

import json

from agentagon.core.records import AuditError, digest, load_json, now
from agentagon.storage.state import identifier, private_directory

MAX_IMPORT_BYTES = 20_000_000


def save(workspace, project_id, preview):
    workspace.require_initialized()
    content = {
        "version": 1,
        "project_id": project_id,
        "origin": str(workspace.root),
        "kind": preview["kind"],
        "connection_id": preview["connection_id"],
        "selection": preview["selection"],
        "items": preview["items"],
        "provenance": preview["provenance"],
        "completeness": preview["completeness"],
    }
    if len(json.dumps(content).encode()) > MAX_IMPORT_BYTES:
        raise AuditError("import exceeds 20 MB; reduce the selection")
    checksum = digest(content)
    snapshot_id = "snapshot_" + checksum[:24]
    path = private_directory(workspace, "imports") / f"{snapshot_id}.json"
    if path.exists():
        return load(workspace, snapshot_id)
    record = {
        **content,
        "id": snapshot_id,
        "digest": checksum,
        "created_at": now(),
        "state": "draft" if content["kind"] == "dataset" else "imported",
    }
    workspace.write(path, record)
    return record


def load(workspace, snapshot_id):
    identifier(snapshot_id, "snapshot")
    path = private_directory(workspace, "imports") / f"{snapshot_id}.json"
    if not path.exists():
        raise AuditError("import snapshot not found in this project")
    record = load_json(workspace.checked(path))
    content = {k: v for k, v in record.items() if k not in {"id", "digest", "created_at", "state"}}
    if digest(content) != record.get("digest") or record["id"] != snapshot_id:
        raise AuditError("import snapshot integrity check failed")
    if record.get("origin") != str(workspace.root) or (
        getattr(workspace, "project_id", None) is not None
        and record.get("project_id") != workspace.project_id
    ):
        raise AuditError("import snapshot belongs to another project")
    return record


def summary(record):
    missing = (
        sum(
            not item.get("expected_present", item.get("expected") is not None)
            for item in record["items"]
        )
        if record["kind"] == "dataset"
        else 0
    )
    trace_readiness = (
        record.get("completeness", {}).get("diagnosis_ready")
        if record["kind"] == "traces"
        else None
    )
    return {
        **{k: v for k, v in record.items() if k != "items"},
        "count": len(record["items"]),
        "missing_expectations": missing,
        **({"diagnosis_ready": trace_readiness} if record["kind"] == "traces" else {}),
        "name": record["selection"].get("dataset_id")
        or record["provenance"].get("dataset_name")
        or record["provenance"].get("project")
        or record["kind"].title(),
        "next_action": (
            (
                "Attach this reviewed case to an evaluation"
                if record["provenance"].get("expectation_status") == "reviewed" and not missing
                else "Review expectations and configure an evaluator"
            )
            if record["kind"] == "dataset"
            else (
                "Select this import for diagnosis"
                if trace_readiness is True
                else "Inspect import limitations"
            )
        ),
    }


def list_snapshots(workspace):
    records = []
    for path in sorted(private_directory(workspace, "imports").glob("*.json"), reverse=True):
        if path.is_symlink():
            continue
        candidate = load_json(workspace.checked(path))
        # Private state can outlive an application database. Evidence carrying
        # another project identity remains inaccessible, but it must not make
        # the current project's inventory or onboarding unusable.
        if candidate.get("origin") != str(workspace.root) or (
            getattr(workspace, "project_id", None) is not None
            and candidate.get("project_id") != workspace.project_id
        ):
            continue
        records.append(summary(load(workspace, path.stem)))
    return records


def input_payload(record):
    """Canonical private input retained inside a frozen evaluator package."""
    return {
        "snapshot_id": record["id"],
        "digest": record["digest"],
        "items": record["items"],
        "dataset_partition": record["provenance"].get("dataset_partition", "unsplit"),
        "split_id": record["provenance"].get("split_id"),
    }


def materialize(workspace, snapshot_id, evaluation_id):
    """Copy a selected immutable dataset only into its evaluation preparation tree."""
    from agentagon.capabilities.evaluation.datasets import assert_development

    record = load(workspace, snapshot_id)
    assert_development(workspace, record)
    return _materialize(workspace, snapshot_id, evaluation_id)


def _materialize(workspace, snapshot_id, evaluation_id):
    """Internal copier; callers authorize the dataset's development or final use."""
    from agentagon.capabilities.experiments import preparation

    record = load(workspace, snapshot_id)
    if record["kind"] != "dataset":
        raise AuditError("select a dataset snapshot")
    evaluation = preparation.load(workspace, evaluation_id)
    if evaluation["state"] == "frozen":
        raise AuditError("a frozen evaluator cannot accept new inputs; create a new version")
    root = workspace.checked(workspace.root / evaluation["worktree"])
    relative = f"agentagon-private/{snapshot_id}.json"
    target = root / relative
    if target.is_symlink() or (root / "agentagon-private").is_symlink():
        raise AuditError("private input destination cannot use symlinks")
    if not target.resolve().is_relative_to(root.resolve()):
        raise AuditError("private input destination escapes the preparation worktree")
    workspace.write(target, input_payload(record))
    return {
        "snapshot_id": snapshot_id,
        "path": relative,
        "input": {"source": str(target.relative_to(workspace.root)), "path": relative},
        "worktree": str(root),
        "private": True,
        "instruction": "Append the returned input descriptor to spec.inputs, never to deliver_paths or overlays.",
    }


def select_traces(workspace, project_id, snapshot_id, selector, cap=None):
    """Freeze an agent's newest completed roots, preserving each selected trace's spans."""
    from agentagon.capabilities.traces.normalize import normalize, unpack

    source = load(workspace, snapshot_id)
    if source["kind"] != "traces":
        raise AuditError("select a trace snapshot")
    if selector.get("connection_id") not in {None, source["connection_id"]}:
        raise AuditError("trace snapshot belongs to a different agent connection")
    project = source["selection"].get("project") or source["provenance"].get("project")
    if selector.get("project") not in {None, project}:
        raise AuditError("trace snapshot belongs to a different provider project")
    if cap is not None and (type(cap) is not int or not 1 <= cap <= 10000):
        raise AuditError("trace cap must be between 1 and 10000")
    provider = source["provenance"]["provider"]
    groups, roots, matches, invalid = {}, {}, set(), 0
    filters = dict(selector.get("filters", {}))
    if selector.get("name") and "agent_name" not in filters:
        filters["name"] = selector["name"]
    if selector.get("environment"):
        filters["environment"] = selector["environment"]
    for row, locator in unpack(source["items"], provider):
        try:
            span = normalize(row, provider, project, locator)
        except (AuditError, ValueError, TypeError, KeyError):
            invalid += 1
            continue
        trace_id = span["trace_id"]
        groups.setdefault(trace_id, []).append(row)
        values = {
            **span.get("resource", {}),
            **span.get("metadata", {}),
            **span.get("attributes", {}),
            "name": span["name"],
        }
        values.setdefault("agent_name", row.get("agent_name"))
        if all(
            values.get(key.removeprefix("metadata.")) == value for key, value in filters.items()
        ):
            matches.add(trace_id)
        if (
            not span["parent_span_ids"]
            and span["started_ns"] is not None
            and span["ended_ns"] is not None
        ):
            roots[trace_id] = span["started_ns"]
    eligible = sorted(
        (key for key in roots if key in matches), key=lambda key: (roots[key], key), reverse=True
    )
    selected = eligible[:cap] if cap else eligible
    if not selected:
        raise AuditError(
            "No completed root traces match this agent. Check its trace selector or import another snapshot."
        )
    preview = {
        key: source[key]
        for key in ("kind", "connection_id", "selection", "provenance", "completeness")
    }
    preview["items"] = [item for key in selected for item in groups[key]]
    preview["selection"] = {**source["selection"], "cap": cap or len(selected)}
    preview["provenance"] = {
        **source["provenance"],
        "source_snapshot_id": source["id"],
        "source_digest": source["digest"],
        "agent_selector": selector,
        "selected_trace_ids": selected,
        "selected_traces": len(selected),
        "selection_order": "newest_completed_roots_in_snapshot",
    }
    preview["completeness"] = {
        **source["completeness"],
        "selected_traces": len(selected),
        "eligible_completed_roots": len(eligible),
        "count": len(preview["items"]),
        "unusable_records": invalid,
        "requested_traces": cap,
        "complete": bool(source["completeness"].get("complete")) and not invalid,
    }
    return save(workspace, project_id, preview)


def select_trace(workspace, project_id, snapshot_id, trace_id):
    """Freeze one explicitly chosen trace without requiring a complete root span."""
    from agentagon.capabilities.traces.normalize import normalize, unpack

    if not isinstance(trace_id, str) or not trace_id or len(trace_id) > 2048:
        raise AuditError("choose a bounded trace identity")
    source = load(workspace, snapshot_id)
    if source["kind"] != "traces":
        raise AuditError("select a trace snapshot")
    provider = source["provenance"]["provider"]
    project = source["selection"].get("project") or source["provenance"].get("project")
    selected, invalid = [], 0
    for row, locator in unpack(source["items"], provider):
        try:
            span = normalize(row, provider, project, locator)
        except (AuditError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            invalid += 1
            continue
        if span["trace_id"] == trace_id:
            selected.append(row)
    if not selected:
        raise AuditError("trace not found in this snapshot")
    preview = {
        key: source[key]
        for key in ("kind", "connection_id", "selection", "provenance", "completeness")
    }
    preview["items"] = selected
    preview["selection"] = {**source["selection"], "trace_id": trace_id, "cap": 1}
    preview["provenance"] = {
        **source["provenance"],
        "source_snapshot_id": source["id"],
        "source_digest": source["digest"],
        "selected_trace_ids": [trace_id],
        "selected_traces": 1,
        "selection_order": "explicit_trace_identity",
    }
    preview["completeness"] = {
        **source["completeness"],
        "selected_traces": 1,
        "count": len(selected),
        "unusable_records": invalid,
        "requested_traces": 1,
        "complete": bool(source["completeness"].get("complete")) and not invalid,
    }
    return save(workspace, project_id, preview)


def select_trace_ids(workspace, project_id, snapshot_id, trace_ids):
    """Freeze the exact traces accepted by an earlier bounded selection."""
    from agentagon.capabilities.traces.normalize import normalize, unpack

    if (
        not isinstance(trace_ids, list)
        or not trace_ids
        or len(trace_ids) > 10000
        or any(not isinstance(value, str) or not value or len(value) > 2048 for value in trace_ids)
        or len(set(trace_ids)) != len(trace_ids)
    ):
        raise AuditError("choose a bounded list of distinct trace identities")
    source = load(workspace, snapshot_id)
    if source["kind"] != "traces":
        raise AuditError("select a trace snapshot")
    provider = source["provenance"]["provider"]
    project = source["selection"].get("project") or source["provenance"].get("project")
    requested = set(trace_ids)
    groups = {trace_id: [] for trace_id in trace_ids}
    invalid = 0
    for row, locator in unpack(source["items"], provider):
        try:
            span = normalize(row, provider, project, locator)
        except (AuditError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            invalid += 1
            continue
        if span["trace_id"] in requested:
            groups[span["trace_id"]].append(row)
    if any(not groups[trace_id] for trace_id in trace_ids):
        raise AuditError("selected trace is no longer present in this snapshot")
    preview = {
        key: source[key]
        for key in ("kind", "connection_id", "selection", "provenance", "completeness")
    }
    preview["items"] = [item for trace_id in trace_ids for item in groups[trace_id]]
    preview["selection"] = {**source["selection"], "cap": len(trace_ids)}
    preview["provenance"] = {
        **source["provenance"],
        "source_snapshot_id": source["id"],
        "source_digest": source["digest"],
        "selected_trace_ids": trace_ids,
        "selected_traces": len(trace_ids),
        "selection_order": "accepted_trace_identities",
    }
    preview["completeness"] = {
        **source["completeness"],
        "selected_traces": len(trace_ids),
        "count": len(preview["items"]),
        "unusable_records": invalid,
        "requested_traces": len(trace_ids),
        "complete": bool(source["completeness"].get("complete")) and not invalid,
    }
    return save(workspace, project_id, preview)
