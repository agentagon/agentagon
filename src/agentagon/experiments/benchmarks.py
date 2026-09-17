"""Content-pinned dataset assessments that do not require executable Git inputs.

The coding host supplies quality judgments. This module records their evidence and
keeps draft readiness separate from the independently reviewed evaluation gates.
"""

import copy
import hashlib
import re
import uuid
from pathlib import Path

from agentagon.core.records import (
    AuditError,
    digest,
    encoded,
    identifier,
    load_json,
    now,
    validate_record,
)
from agentagon.experiments import checkouts, preparation
from agentagon.experiments.evidence import DEFAULT_LIMITS
from agentagon.experiments.spec import object_keys, path, text
from agentagon.storage.changes import revision
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace

ASSESSMENTS = {
    "coverage",
    "label_correctness",
    "duplicates",
    "representativeness",
    "provenance",
    "leakage",
    "sensitivity",
}
MAX_FILES = 500
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 20_000_000


def _directory(workspace: Workspace, benchmark_id: str) -> Path:
    if not isinstance(benchmark_id, str) or not re.fullmatch(
        r"benchmark_[0-9a-f]{24}", benchmark_id
    ):
        raise AuditError("invalid benchmark draft ID")
    return workspace.checked(workspace.state / "benchmarks" / benchmark_id)


def _input(workspace: Workspace, name: str) -> Path:
    name = path(name, "benchmark input")
    parts = Path(name).parts
    if any(
        part.lower() == ".agentagon"
        or part.startswith(".env")
        or Path(part).suffix in {".pem", ".key", ".p12"}
        for part in parts
    ):
        raise AuditError("benchmark inputs cannot contain private state or credential files")
    file = checkouts.checked_file(workspace.root, name)
    if any(parent.is_symlink() for parent in file.parents if parent != workspace.root):
        raise AuditError("benchmark inputs cannot use symlink directories")
    return file


def _assessment(value: dict) -> dict:
    if len(encoded(value).encode()) > 1_000_000:
        raise AuditError("dataset assessment exceeds the 1 MB limit")
    required = {
        "goal",
        "author",
        "dataset_paths",
        "entrypoint_paths",
        "assessments",
        "proposed_cases",
    }
    value = copy.deepcopy(
        object_keys(value, required | {"issue_ids", "plan"}, required, "dataset assessment")
    )
    text(value["goal"], "assessment goal")
    text(value["author"], "assessment author")
    names = []
    for field in ("dataset_paths", "entrypoint_paths"):
        if not isinstance(value[field], list):
            raise AuditError(f"{field} must be an array of existing file paths")
        value[field] = [path(name) for name in value[field]]
        names.extend(value[field])
    if len(names) != len(set(names)) or len(names) > MAX_FILES:
        raise AuditError(f"benchmark inputs must be distinct and name at most {MAX_FILES} files")
    value.setdefault("issue_ids", [])
    for field in ("issue_ids", "proposed_cases"):
        if not isinstance(value[field], list) or len(value[field]) > MAX_FILES:
            raise AuditError(f"{field} must be a bounded array")
        for item in value[field]:
            text(item, field)
    value["issue_ids"] = list(dict.fromkeys(value["issue_ids"]))
    if not value["dataset_paths"] and not value["proposed_cases"]:
        raise AuditError("a missing dataset requires proposed cases, not invented dataset evidence")
    assessments = object_keys(value["assessments"], ASSESSMENTS, ASSESSMENTS, "dataset assessments")
    for topic, assessment in assessments.items():
        keys = {"status", "rationale", "evidence"}
        object_keys(assessment, keys, keys, topic)
        if assessment["status"] not in {"supported", "concern", "unknown"}:
            raise AuditError("assessment status must be supported, concern or unknown")
        text(assessment["rationale"], "assessment rationale")
        evidence = assessment["evidence"]
        if not isinstance(evidence, list) or len(evidence) > MAX_FILES:
            raise AuditError("assessment evidence must be a bounded array")
        if assessment["status"] != "unknown" and not evidence:
            raise AuditError("supported assessments and concerns require pinned evidence")
        for reference in evidence:
            object_keys(reference, {"path", "detail"}, {"path", "detail"}, "assessment evidence")
            reference["path"] = path(reference["path"])
            if reference["path"] not in names:
                raise AuditError("assessment evidence must reference a declared benchmark input")
            text(reference["detail"], "evidence detail")
    if value.get("plan") is not None:
        value["plan"] = preparation._plan(value["plan"], value)
        spec = value["plan"]["spec"]
        if any(
            not any(checkouts.under(name, scope) for scope in spec["evaluation_paths"])
            for name in names
        ):
            raise AuditError("the evaluation plan must protect every declared benchmark input")
    validate_record("benchmark-assessment", value)
    return value


def draft(
    workspace: Workspace, assessment: dict, *, audit_id: str | None = None, new: bool = False
) -> dict:
    """Save a reproducible inventory without changing the audited dataset or running it."""
    workspace.require_initialized()
    assessment = _assessment(assessment)
    from agentagon.storage.issues import list_issues

    if set(assessment["issue_ids"]) - {issue["issue_id"] for issue in list_issues(workspace)}:
        raise AuditError("dataset assessment references unknown saved issues")
    audit = workspace.read_audit(audit_id) if audit_id else None
    if audit and set(assessment["issue_ids"]) - {group["issue_id"] for group in audit["groups"]}:
        raise AuditError("dataset assessment issues must belong to the selected audit")
    files, total = [], 0
    for kind in ("dataset", "entrypoint"):
        for name in assessment[f"{kind}_paths"]:
            file = _input(workspace, name)
            # Read a bounded amount even if the input grows during capture.
            with file.open("rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            total += len(content)
            if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise AuditError("benchmark inputs exceed the 2 MB/file or 20 MB total draft limit")
            files.append(
                {
                    "path": name,
                    "kind": kind,
                    "digest": hashlib.sha256(content).hexdigest(),
                    "artifact": workspace.blob(content, ".input"),
                    "bytes": len(content),
                    "mode": file.stat().st_mode & 0o777,
                }
            )
    snapshot = {
        "assessment": assessment,
        "files": files,
        "audit_id": audit_id,
        "audit_snapshot_digest": digest(audit["snapshot"]) if audit else None,
        "source_revision": revision(workspace.root) if workspace.is_git else None,
    }
    identity = [str(workspace.root), snapshot]
    if new:
        identity.append(uuid.uuid4().hex)
    benchmark_id = identifier("benchmark", *identity)
    with workspace.locked():
        record_path = _directory(workspace, benchmark_id) / "state.json"
        if not record_path.exists():
            workspace.write(
                record_path,
                {
                    "version": 1,
                    "benchmark_id": benchmark_id,
                    "origin": str(workspace.root),
                    "created_at": now(),
                    "state": "draft",
                    "snapshot": snapshot,
                    "snapshot_digest": digest(snapshot),
                    "evaluations": [],
                },
            )
    return status(workspace, benchmark_id)


def load(workspace: Workspace, benchmark_id: str) -> dict:
    workspace.require_initialized()
    file = _directory(workspace, benchmark_id) / "state.json"
    if not file.exists():
        raise AuditError("benchmark draft not found in this checkout")
    data = load_json(file)
    if data.get("origin") != str(workspace.root) or data.get("benchmark_id") != benchmark_id:
        raise AuditError("benchmark draft origin mismatch")
    if digest(data["snapshot"]) != data["snapshot_digest"]:
        raise AuditError("benchmark draft evidence changed")
    if "source_revision" not in data["snapshot"]:
        raise AuditError("benchmark draft is missing its source revision; create a new draft")
    for entry in data["snapshot"]["files"]:
        file = workspace.checked(workspace.root / entry["artifact"])
        if hashlib.sha256(file.read_bytes()).hexdigest() != entry["digest"]:
            raise AuditError("benchmark input evidence changed")
    return data


def _readiness(
    workspace: Workspace,
    data: dict,
    profile_name: str | None,
    budget: dict | None,
    *,
    origin_revision: str | None = None,
) -> list[dict]:
    assessment = data["snapshot"]["assessment"]
    missing = []
    for field, code, action in (
        ("dataset_paths", "dataset_missing", "Use Fix to create the proposed dataset cases."),
        ("entrypoint_paths", "entrypoint_missing", "Identify the existing evaluation entrypoint."),
        (
            "plan",
            "execution_plan_missing",
            "Prepare a plan with checks, provenance and sensitivity cases.",
        ),
    ):
        if not assessment.get(field):
            missing.append({"code": code, "action": action})
    try:
        current_revision = checkouts.clean_revision(workspace.root)
        expected_revision = origin_revision or data["snapshot"]["source_revision"]
        if expected_revision is not None and current_revision != expected_revision:
            missing.append(
                {
                    "code": "source_changed",
                    "action": "Create a new benchmark draft for the changed application revision.",
                }
            )
    except AuditError as exc:
        missing.append({"code": "source_not_ready", "action": str(exc)})
    for entry in data["snapshot"]["files"]:
        try:
            file = _input(workspace, entry["path"])
            with file.open("rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            unchanged = (
                hashlib.sha256(content).hexdigest() == entry["digest"]
                and file.stat().st_mode & 0o777 == entry["mode"]
            )
        except (AuditError, OSError):
            unchanged = False
        if not unchanged:
            missing.append(
                {
                    "code": "input_changed",
                    "path": entry["path"],
                    "action": "Audit the changed input in a new benchmark draft.",
                }
            )
    if profile_name:
        try:
            Config().profile(workspace.root, profile_name)
        except AuditError as exc:
            missing.append({"code": "profile_not_ready", "action": str(exc)})
    else:
        missing.append({"code": "profile_missing", "action": "Select an execution profile."})
    if budget is None:
        missing.append(
            {"code": "budget_missing", "action": "Declare a bounded preparation budget."}
        )
    else:
        preparation._budget(budget)
    return missing


def _same_settings(workspace: Workspace, evaluation: dict, profile_name: str, budget: dict) -> bool:
    try:
        profile = Config().profile(workspace.root, profile_name)
    except AuditError:
        return False
    profile.setdefault("evidence", dict(DEFAULT_LIMITS))
    return (
        evaluation["profile_name"] == profile_name
        and evaluation["profile_digest"] == digest(profile)
        and evaluation["budget"] == budget
    )


def _new_preparation() -> dict:
    return {
        "code": "preparation_changed",
        "action": "Create an explicit new benchmark draft with benchmark draft --new before changing preparation settings, author or limits.",
    }


def status(
    workspace: Workspace,
    benchmark_id: str,
    *,
    profile_name: str | None = None,
    budget: dict | None = None,
) -> dict:
    data = load(workspace, benchmark_id)
    linked = [preparation.status(workspace, item["evaluation_id"]) for item in data["evaluations"]]
    latest = linked[-1] if linked else None
    profile_name = profile_name or (latest["profile_name"] if latest else None)
    budget = budget if budget is not None else (latest["budget"] if latest else None)
    missing = _readiness(
        workspace,
        data,
        profile_name,
        budget,
        origin_revision=latest["origin_revision"] if latest else None,
    )
    if latest and not _same_settings(workspace, latest, profile_name, budget):
        missing.append(_new_preparation())
    result = {
        **data,
        "readiness": {
            "state": "blocked" if missing else "ready_for_preparation",
            "missing": missing,
        },
        "next_action": "resolve_readiness" if missing else "prepare_evaluation",
        "measurement_status": "not_measured",
    }
    result["evaluations"] = [
        {**item, "state": evaluation["state"]}
        for item, evaluation in zip(data["evaluations"], linked, strict=True)
    ]
    if latest:
        result["state"] = "frozen" if latest["state"] == "frozen" else "preparing"
        result["next_action"] = "resolve_readiness" if missing else latest["next_action"]
        result["readiness"] = {
            "state": "blocked" if missing else latest["state"],
            "missing": missing,
        }
        if latest["state"] == "frozen":
            result["measurement_status"] = "historical_baseline" if missing else "baseline_recorded"
            result["measurement_revision"] = latest["origin_revision"]
    return result


def list_drafts(workspace: Workspace) -> list[dict]:
    """Read saved assessments and evaluation linkage without executing any work."""
    workspace.require_initialized()
    root = workspace.checked(workspace.state / "benchmarks")
    return [status(workspace, file.parent.name) for file in sorted(root.glob("*/state.json"))]


def prepare(
    workspace: Workspace, benchmark_id: str, profile_name: str, budget: dict, *, author: str
) -> dict:
    """Connect an unchanged draft to the existing check/review/freeze workflow."""
    with workspace.locked():
        data = load(workspace, benchmark_id)
        text(author, "benchmark author")
        linked = [
            (item, preparation.status(workspace, item["evaluation_id"]))
            for item in data["evaluations"]
        ]
        latest = linked[-1][1] if linked else None
        missing = _readiness(
            workspace,
            data,
            profile_name,
            budget,
            origin_revision=latest["origin_revision"] if latest else None,
        )
        matching = next(
            (
                (item, value)
                for item, value in reversed(linked)
                if value["author"] == author
                and _same_settings(workspace, value, profile_name, budget)
            ),
            None,
        )
        if linked and matching is None:
            missing.append(_new_preparation())
        if missing:
            return {"benchmark_id": benchmark_id, "state": "not_ready", "missing": missing}
        if matching:
            item, started = matching
            return {
                **started,
                "benchmark_id": benchmark_id,
                "plan_artifact": item["plan_artifact"],
                "measurement_status": "baseline_recorded"
                if started["state"] == "frozen"
                else "not_measured",
                "reused": True,
            }
        assessment = data["snapshot"]["assessment"]
        started = preparation.start(
            workspace,
            profile_name,
            budget,
            author=author,
            goal=assessment["goal"],
            issue_ids=assessment["issue_ids"],
            benchmark_id=benchmark_id,
        )
        plan_artifact = workspace.artifact(assessment["plan"])
        data["evaluations"].append(
            {"evaluation_id": started["evaluation_id"], "plan_artifact": plan_artifact}
        )
        workspace.write(_directory(workspace, benchmark_id) / "state.json", data)
        return {
            **started,
            "benchmark_id": benchmark_id,
            "plan_artifact": plan_artifact,
            "measurement_status": "baseline_recorded"
            if started["state"] == "frozen"
            else "not_measured",
            "reused": started.get("reused", False),
        }
