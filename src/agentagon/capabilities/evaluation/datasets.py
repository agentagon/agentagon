"""Private dataset drafts and deterministic, bounded-use final partitions."""

import copy
import math
import re

from agentagon.capabilities.traces import snapshots
from agentagon.capabilities.traces.normalize import normalize, unpack
from agentagon.core.records import AuditError, digest, load_json, now
from agentagon.storage.state import identifier, private_directory

FAMILY_KEYS = ("conversation_id", "session_id", "thread_id", "task_family")
FINAL = "final_holdout"
LIMITATIONS = [
    "Partitions separate development from one bound final verification; they are not a security sandbox.",
    "A local coding host can read private workspace files. This is not a sealed or demonstrably unseen holdout.",
    "Related examples without shared family metadata may remain undetected; inspect grouping before use.",
]


def _workspace(workspace, project_id):
    if getattr(workspace, "project_id", None) != project_id:
        raise AuditError("dataset operation requires this registered project workspace")
    return workspace.metadata_store


def derive(workspace, project_id, trace_snapshot_id, selection):
    """Copy complete trace roots into examples without treating observations as labels."""
    _workspace(workspace, project_id)
    if not isinstance(selection, dict) or set(selection) - {"trace_ids", "cap"}:
        raise AuditError("choose trace_ids and an optional example cap")
    cap = selection.get("cap", 1000)
    if type(cap) is not int or not 1 <= cap <= 10000:
        raise AuditError("example cap must be between 1 and 10000")
    selected = selection.get("trace_ids")
    if selected is not None and (
        not isinstance(selected, list)
        or not selected
        or len(selected) > 10000
        or any(not isinstance(value, str) or not value or len(value) > 500 for value in selected)
    ):
        raise AuditError("trace_ids must be a bounded list of trace identifiers")
    source = snapshots.load(workspace, trace_snapshot_id)
    if source["kind"] != "traces":
        raise AuditError("derive examples from a trace snapshot")
    provider = source["provenance"]["provider"]
    project = (
        source["selection"].get("project") or source["provenance"].get("project") or project_id
    )
    roots, invalid = {}, 0
    for row, locator in unpack(source["items"], provider):
        try:
            span = normalize(row, provider, project, locator)
        except (AuditError, ValueError, TypeError, KeyError):
            invalid += 1
            continue
        if span["parent_span_ids"] or span["ended_ns"] is None or span["input"] is None:
            continue
        roots.setdefault(span["trace_id"], {})[span["source_digest"]] = span
    eligible = {
        key: next(iter(values.values())) for key, values in roots.items() if len(values) == 1
    }
    if selected is not None and set(selected) - eligible.keys():
        raise AuditError("selected trace lacks one complete root with an input")
    ordered = sorted(
        (selected if selected is not None else eligible),
        key=lambda key: (eligible[key]["started_ns"] or 0, key),
        reverse=True,
    )
    ordered = list(dict.fromkeys(ordered))[:cap]
    if not ordered:
        raise AuditError("no complete trace roots with inputs can become examples")
    items = []
    for trace_id in ordered:
        span = eligible[trace_id]
        metadata = {**span["metadata"], **span["attributes"]}
        if span.get("session_id"):
            metadata.setdefault("conversation_id", span["session_id"])
        items.append(
            {
                "id": "example_" + digest({"source": source["digest"], "trace": trace_id})[:24],
                "input": copy.deepcopy(span["input"]),
                "observed_output": copy.deepcopy(span["output"]),
                "expected": None,
                "expected_present": False,
                "metadata": metadata,
                "source": {
                    "snapshot_id": source["id"],
                    "trace_id": trace_id,
                    "span_id": span["span_id"],
                },
            }
        )
    preview = {
        "kind": "dataset",
        "connection_id": source["connection_id"],
        "selection": {"source_snapshot_id": source["id"], "trace_ids": ordered, "cap": cap},
        "provenance": {
            "provider": provider,
            "source_snapshot_id": source["id"],
            "source_digest": source["digest"],
            "derivation": "complete_trace_roots",
            "dataset_partition": "unsplit",
            "expectation_status": "unreviewed",
            "observed_outputs_are_expectations": False,
        },
        "completeness": {
            "count": len(items),
            "complete": bool(source["completeness"].get("complete")) and not invalid,
            "unusable_records": invalid,
            "missing_expectations": len(items),
        },
        "items": items,
    }
    return snapshots.summary(snapshots.save(workspace, project_id, preview))


def _groups(items, group_key):
    parents = list(range(len(items)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(left, right):
        parents[root(right)] = root(left)

    families, inputs = {}, {}
    keys = tuple(dict.fromkeys((*FAMILY_KEYS, *([group_key] if group_key else []))))
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("input") is None:
            raise AuditError("every split example needs a structured input")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise AuditError("example metadata must be an object")
        identifiers = []
        for key in keys:
            value = metadata.get(key)
            if value is None or value == "":
                continue
            if type(value) not in {str, int} or len(str(value)) > 500:
                raise AuditError("family identifiers must be bounded strings or integers")
            identifiers.append(("conversation" if key in FAMILY_KEYS[:3] else key, str(value)))
        if not identifiers or (group_key and metadata.get(group_key) in (None, "")):
            raise AuditError(
                "label every example with its conversation or task family before splitting"
            )
        for family in identifiers:
            if family in families:
                join(index, families[family])
            else:
                families[family] = index
        fingerprint = digest(item["input"])
        if fingerprint in inputs:
            join(index, inputs[fingerprint][0])
        inputs.setdefault(fingerprint, []).append(index)
    groups, conflicts = {}, 0
    for fingerprint, duplicates in sorted(inputs.items()):
        ordered = sorted(duplicates, key=lambda index: (str(items[index].get("id", "")), index))
        retained = copy.deepcopy(items[ordered[0]])
        labels = {
            digest(item.get("expected"))
            for item in (items[index] for index in ordered)
            if item.get("expected_present", item.get("expected") is not None)
        }
        if len(labels) > 1:
            retained.update(expected=None, expected_present=False)
            conflicts += 1
        elif any(
            not items[index].get("expected_present", items[index].get("expected") is not None)
            for index in ordered
        ):
            retained.update(expected=None, expected_present=False)
        if len(duplicates) > 1:
            retained.setdefault("metadata", {})["duplicate_example_ids"] = [
                items[index].get("id") for index in ordered
            ]
        groups.setdefault(root(ordered[0]), []).append((fingerprint, retained))
    return list(groups.values()), len(items) - len(inputs), conflicts


def split(state, project_id, snapshot_id, selection):
    """Freeze one split per source, merging families linked by exact duplicate inputs."""
    if not isinstance(selection, dict) or set(selection) - {"holdout_fraction", "group_key"}:
        raise AuditError("choose a holdout fraction and optional metadata group_key")
    fraction = selection.get("holdout_fraction", 0.2)
    if type(fraction) not in {int, float} or not math.isfinite(fraction) or not 0 < fraction < 1:
        raise AuditError("holdout_fraction must be a number between zero and one")
    group_key = selection.get("group_key")
    if group_key is not None and (
        not isinstance(group_key, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,79}", group_key)
    ):
        raise AuditError("group_key must name a metadata field")
    workspace = state.workspace(project_id)
    source = snapshots.load(workspace, snapshot_id)
    if source["kind"] != "dataset" or source["provenance"].get("dataset_partition") in {
        "development",
        FINAL,
    }:
        raise AuditError("split an unsplit dataset snapshot")
    if not 2 <= len(source["items"]) <= 10000:
        raise AuditError("split requires between 2 and 10000 examples")
    parameters = {
        "source_snapshot_id": source["id"],
        "source_digest": source["digest"],
        "holdout_fraction": float(fraction),
        "group_key": group_key,
    }
    split_id = "split_" + digest({"project_id": project_id, "source": source["id"]})[:24]
    existing = state.db.get_record(project_id, "dataset_splits", split_id)
    if existing is not None:
        if existing["parameters"] != parameters:
            raise AuditError(
                "this dataset already has a frozen split; reuse its existing partition"
            )
        return existing
    groups, duplicates, conflicts = _groups(source["items"], group_key)
    if len(groups) < 2:
        raise AuditError(
            "split needs at least two independent families after duplicate-input grouping"
        )
    groups.sort(
        key=lambda group: digest(
            {"source": source["digest"], "inputs": [value[0] for value in group]}
        )
    )
    holdout_count = max(1, min(len(groups) - 1, round(len(groups) * fraction)))
    partitions = {FINAL: groups[:holdout_count], "development": groups[holdout_count:]}
    saved = {}
    for partition, families in partitions.items():
        items = [item for group in families for _, item in group]
        saved[partition] = snapshots.save(
            workspace,
            project_id,
            {
                "kind": "dataset",
                "connection_id": source["connection_id"],
                "selection": {"source_snapshot_id": source["id"], "split_id": split_id},
                "provenance": {
                    **source["provenance"],
                    "source_snapshot_id": source["id"],
                    "source_digest": source["digest"],
                    "split_id": split_id,
                    "dataset_partition": partition,
                },
                "completeness": {
                    **source["completeness"],
                    "count": len(items),
                    "selected_families": len(families),
                },
                "items": items,
            },
        )
    missing = sum(
        not item.get("expected_present", item.get("expected") is not None)
        for group in groups
        for _, item in group
    )
    manifest = {
        "version": 1,
        "parameters": parameters,
        "development_snapshot_id": saved["development"]["id"],
        "holdout_snapshot_id": saved[FINAL]["id"],
        "development_digest": saved["development"]["digest"],
        "holdout_digest": saved[FINAL]["digest"],
        "counts": {
            "source": len(source["items"]),
            "development": len(saved["development"]["items"]),
            "holdout": len(saved[FINAL]["items"]),
            "families": len(groups),
            "holdout_families": holdout_count,
            "development_families": len(groups) - holdout_count,
            "duplicates_removed": duplicates,
            "missing_expectations": missing,
            "conflicting_expectations": conflicts,
        },
    }
    manifest_artifact = workspace.artifact(manifest)
    with state.db.transaction() as transaction:
        existing = transaction.get_record(project_id, "dataset_splits", split_id)
        if existing is not None:
            if existing["parameters"] != parameters:
                raise AuditError(
                    "this dataset already has a frozen split; reuse its existing partition"
                )
            return existing
        guard_path = private_directory(workspace, "dataset-splits") / f"{source['id']}.json"
        guard = {"manifest_artifact": manifest_artifact, "split_id": split_id}
        if guard_path.exists() and load_json(workspace.checked(guard_path)) != guard:
            raise AuditError(
                "this dataset already has a frozen split artifact; reuse its partition"
            )
        workspace.write(guard_path, guard)
        return transaction.put_record(
            project_id,
            "dataset_splits",
            split_id,
            {
                **manifest,
                "id": split_id,
                "digest": digest(manifest),
                "manifest_artifact": manifest_artifact,
                "state": "draft" if missing else "partitioned",
                "exposure_state": "unclaimed",
                "final_claim": None,
                "limitations": LIMITATIONS,
            },
        )


def _load_split(state, project_id, split_id):
    identifier(split_id, "split")
    record = state.db.get_record(project_id, "dataset_splits", split_id)
    if record is None:
        raise AuditError("dataset split not found in this project")
    manifest = {
        key: record[key]
        for key in (
            "version",
            "parameters",
            "development_snapshot_id",
            "holdout_snapshot_id",
            "development_digest",
            "holdout_digest",
            "counts",
        )
    }
    if digest(manifest) != record["digest"]:
        raise AuditError("dataset split integrity check failed")
    workspace = state.workspace(project_id)
    if workspace.read_artifact(record["manifest_artifact"]) != manifest:
        raise AuditError("dataset split differs from its frozen manifest artifact")
    for key in ("development", "holdout"):
        snapshot = snapshots.load(workspace, record[key + "_snapshot_id"])
        if snapshot["digest"] != record[key + "_digest"]:
            raise AuditError("dataset partition differs from its frozen split")
    return record


def assert_development(workspace, record):
    """Deny both the final partition and a split's full parent in ordinary tools."""
    if record["provenance"].get("dataset_partition") == FINAL:
        raise AuditError(
            "final holdout content is reserved for an explicitly bound final verification"
        )
    guard_path = private_directory(workspace, "dataset-splits") / f"{record['id']}.json"
    if guard_path.exists():
        guard = load_json(workspace.checked(guard_path))
        manifest = workspace.read_artifact(guard["manifest_artifact"])
        if (
            manifest["parameters"]["source_snapshot_id"] != record["id"]
            or manifest["parameters"]["source_digest"] != record["digest"]
        ):
            raise AuditError("dataset split source binding changed")
        raise AuditError(
            "this dataset has a final partition; select its development snapshot "
            + manifest["development_snapshot_id"]
        )


def assert_development_evaluator(workspace, evaluation_id):
    """A claimed final evaluator cannot become another app development objective."""
    database = getattr(workspace, "metadata_store", None)
    if database is None:
        raise AuditError("checking final evaluator use requires a registered project")
    for record in database.list_records(workspace.project_id, "dataset_splits"):
        claim = record.get("final_claim") or {}
        if claim.get("materialization_evaluation_id") == evaluation_id:
            raise AuditError("final verification evaluator cannot be used in development search")


def claim_final(state, project_id, split_id, run_binding, *, correctness_evaluation_id):
    """Reserve the final partition before its verifier starts; never silently reuse it."""
    record = _load_split(state, project_id, split_id)
    if not isinstance(run_binding, dict) or set(run_binding) != {
        "run_id",
        "candidate_id",
        "manifest_digest",
        "source_revision",
        "source_digest",
    }:
        raise AuditError("final verification requires an exact run, candidate, and suite digest")
    identifier(run_binding.get("run_id"), "run")
    identifier(run_binding.get("candidate_id"), "candidate")
    if not isinstance(run_binding.get("manifest_digest"), str) or not re.fullmatch(
        r"[a-f0-9]{64}", run_binding["manifest_digest"]
    ):
        raise AuditError("invalid final verification suite digest")
    from agentagon.capabilities.experiments import preparation, suites
    from agentagon.capabilities.experiments.store import load_run

    workspace = state.workspace(project_id)
    identifier(correctness_evaluation_id, "eval")
    correctness_digest = preparation.evaluator_identity(workspace, correctness_evaluation_id)
    run = load_run(workspace, run_binding["run_id"])
    if suites.status(workspace, run_binding["run_id"]).get("binding") != run_binding:
        raise AuditError("final verification binding does not match the retained suite")
    candidate = run["candidates"].get(run_binding["candidate_id"])
    if (
        not candidate
        or not candidate.get("source_revision")
        or any(
            run_binding[key] != candidate.get(key) for key in ("source_revision", "source_digest")
        )
    ):
        raise AuditError("final verification requires a captured candidate in this project")
    binding = {
        **run_binding,
        "split_digest": record["digest"],
        "correctness_evaluation_id": correctness_evaluation_id,
        "correctness_evaluator_digest": correctness_digest,
    }
    with state.db.transaction() as transaction:
        current = transaction.get_record(project_id, "dataset_splits", split_id)
        if current["final_claim"]:
            if current["final_claim"]["binding"] != binding:
                raise AuditError("final partition is already exposed to another verification")
            return current
        current.update(
            exposure_state="claimed", final_claim={"binding": binding, "claimed_at": now()}
        )
        return transaction.put_record(project_id, "dataset_splits", split_id, current)


def materialize_final(
    state, project_id, split_id, run_binding, evaluation_id, *, correctness_evaluation_id
):
    """Expose final inputs only after a matching bounded verification claim."""
    record = _load_split(state, project_id, split_id)
    if (
        not isinstance(run_binding, dict)
        or not record["final_claim"]
        or any(
            record["final_claim"]["binding"].get(key) != value for key, value in run_binding.items()
        )
    ):
        raise AuditError("claim this exact final verification before materializing its inputs")
    from agentagon.capabilities.experiments import preparation

    workspace = state.workspace(project_id)
    evaluation = preparation.load(workspace, evaluation_id)
    if evaluation["state"] == "frozen":
        raise AuditError("final inputs require a separate unfrozen verification evaluator")
    claimed = claim_final(
        state,
        project_id,
        split_id,
        run_binding,
        correctness_evaluation_id=correctness_evaluation_id,
    )
    with state.db.transaction() as transaction:
        current = transaction.get_record(project_id, "dataset_splits", split_id)
        destination = current["final_claim"].get("materialization_evaluation_id")
        if destination not in {None, evaluation_id}:
            raise AuditError("final inputs were already assigned to another verification evaluator")
        if destination is None:
            current["final_claim"]["materialization_evaluation_id"] = evaluation_id
            transaction.put_record(project_id, "dataset_splits", split_id, current)
    result = snapshots._materialize(workspace, claimed["holdout_snapshot_id"], evaluation_id)
    return {
        **result,
        "split_id": split_id,
        "exposure_state": "claimed",
        "final_verification": True,
        "instruction": "Declare these private inputs only in the separate final verification evaluator. Review and freeze that evaluator before bounded final execution; never feed its results into development search.",
        "limitations": LIMITATIONS,
    }
