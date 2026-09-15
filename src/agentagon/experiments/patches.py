"""Reviewed patches with actual checks, kept separate from measured experiments."""

import copy
import fcntl
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from agentagon.core.records import AuditError, digest, identifier, load_json, now, validate_record
from agentagon.experiments import checkouts, evidence, runners, store
from agentagon.experiments.spec import argv, path, text
from agentagon.storage.workspace import Workspace

ASSESSMENTS = {"correctness", "scope", "checks", "known_failures", "limitations"}
CONTEXT_FIELDS = ("origin_revision", "goal", "author", "reason_no_comparison", "plan_digest")


def directory(workspace: Workspace, patch_id: str) -> Path:
    if not isinstance(patch_id, str) or not re.fullmatch(r"patch_[0-9a-f]{24}", patch_id):
        raise AuditError("invalid patch ID")
    return workspace.checked(workspace.state / "patches" / patch_id)


@contextmanager
def locked(workspace: Workspace, patch_id: str):
    root = directory(workspace, patch_id)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(root / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield root


def load(workspace: Workspace, patch_id: str) -> dict:
    file = directory(workspace, patch_id) / "state.json"
    if not file.exists():
        raise AuditError("patch not found in this checkout")
    data = load_json(file)
    if (
        data.get("version") != 1
        or data.get("origin") != str(workspace.root)
        or data.get("patch_id") != patch_id
        or digest(data["plan"]) != data["plan_digest"]
        or digest({key: data[key] for key in CONTEXT_FIELDS}) != data.get("context_digest")
    ):
        raise AuditError("patch origin or frozen check plan changed")
    return data


def _save(workspace: Workspace, data: dict) -> None:
    data["updated_at"] = now()
    workspace.write(directory(workspace, data["patch_id"]) / "state.json", data)


def start(
    workspace: Workspace,
    plan: dict,
    *,
    goal: str,
    author: str,
    reason_no_comparison: str,
) -> dict:
    workspace.require_initialized()
    if isinstance(plan, dict) and plan.get("checks") == [] and not plan.get("no_checks_reason"):
        raise AuditError("a patch without executable checks requires no_checks_reason")
    validate_record("patch-plan", plan)
    text(goal, "patch goal")
    text(author, "patch author")
    text(reason_no_comparison, "reason comparison is unavailable")
    plan = copy.deepcopy(plan)
    plan["editable_paths"] = [path(p) for p in plan["editable_paths"]]
    for command in plan["checks"]:
        command["argv"] = argv(command["argv"])
        command["cwd"] = path(command.get("cwd", "."))
    if len({c["id"] for c in plan["checks"]}) != len(plan["checks"]):
        raise AuditError("patch check IDs must be unique")
    plan.setdefault("max_output_bytes", 262144)
    plan.setdefault("env", {})
    revision = checkouts.clean_revision(workspace.root)
    patch_id = identifier("patch", str(workspace.root), revision, uuid.uuid4().hex)
    with locked(workspace, patch_id) as root:
        editing = root / "editing"
        checkouts.create(workspace.root, editing, revision)
        data = {
            "version": 1,
            "patch_id": patch_id,
            "origin": str(workspace.root),
            "origin_revision": revision,
            "created_at": now(),
            "state": "editing",
            "goal": text(goal, "patch goal"),
            "author": text(author, "patch author"),
            "reason_no_comparison": text(reason_no_comparison, "reason comparison is unavailable"),
            "plan": plan,
            "plan_digest": digest(plan),
            "worktree": str(editing.relative_to(workspace.root)),
            "checks": [],
            "reviews": [],
        }
        data["context_digest"] = digest({key: data[key] for key in CONTEXT_FIELDS})
        _save(workspace, data)
    return status(workspace, patch_id)


def status(workspace: Workspace, patch_id: str) -> dict:
    data = load(workspace, patch_id)
    return {
        **data,
        "next_action": "deliver"
        if data["state"] == "reviewed_unmeasured"
        else "independent_review"
        if data["state"] == "awaiting_review"
        else "edit_and_check",
    }


def list_patches(workspace: Workspace) -> list[dict]:
    return sorted(
        [
            status(workspace, file.parent.name)
            for file in (workspace.state / "patches").glob("*/state.json")
        ],
        key=lambda data: (data["created_at"], data["patch_id"]),
        reverse=True,
    )


def _scope(workspace: Workspace, data: dict, revision: str) -> None:
    checkouts.validate_scope(
        workspace.root,
        data["origin_revision"],
        revision,
        {
            "editable_paths": data["plan"]["editable_paths"],
            "evaluation_paths": [],
            "inputs": [],
            "overlays": [],
        },
    )
    for name in checkouts.paths(workspace.root, revision):
        p = Path(name)
        if (
            ".agentagon" in p.parts
            or p.name == ".env"
            or p.name.startswith(".env.")
            and p.name not in {".env.example", ".env.sample"}
            or p.suffix in {".pem", ".key", ".p12"}
        ):
            raise AuditError("patch snapshot contains a private state or credential path")


def _known_failures(workspace: Workspace, source_digest: str) -> None:
    # A failed measured candidate cannot be exported under a weaker evidence label.
    for run in store.list_runs(workspace):
        for candidate in run["candidates"].values():
            if candidate.get("source_digest") == source_digest and (
                candidate["state"] in {"failed", "rejected"}
                or candidate["state"] in {"awaiting_review", "verified"}
                and (candidate.get("machine_passed") is False or candidate.get("feasible") is False)
            ):
                raise AuditError(
                    "this source has known failed experiment evidence; resolve it before delivery"
                )
    for saved in list_patches(workspace):
        if any(
            record["source_digest"] == source_digest and record["state"] == "failed"
            for record in saved["checks"]
        ) or any(
            review["source_digest"] == source_digest
            and (review["verdict"] != "pass" or not all(review["assessments"].values()))
            for review in saved["reviews"]
        ):
            raise AuditError(
                "this source has failed patch checks or a rejected review; revise it before delivery"
            )


def _passing(workspace: Workspace, data: dict, record: dict) -> dict:
    result = evidence.read_result(workspace, record["artifact"])
    expected = data["plan"]["checks"]
    if (
        result.get("attempt_id") != record["check_id"]
        or record.get("context_digest") != data["context_digest"]
        or result.get("source_digest") != record["source_digest"]
        or result.get("evaluation_digest") != data["plan_digest"]
        or result.get("state") != "completed"
        or not result.get("finalized")
        or [r["id"] for r in result["results"]] != [c["id"] for c in expected]
        or any(r["exit_code"] != 0 for r in result["results"])
        or result.get("source_manifest_before") != record["source_manifest"]
        or any(
            result["source_manifest_after"].get(k) != v
            for k, v in record["source_manifest"].items()
        )
    ):
        raise AuditError("patch checks failed, were interrupted, or changed their source")
    _known_failures(workspace, record["source_digest"])
    return result


def check(workspace: Workspace, patch_id: str) -> dict:
    with locked(workspace, patch_id) as root:
        data = load(workspace, patch_id)
        if data["state"] == "reviewed_unmeasured":
            raise AuditError("reviewed patch cannot change; start a new patch")
        if data["checks"] and data["checks"][-1]["state"] in {"running", "interrupted"}:
            # A detached worker retains the original deadline and execution ownership.
            # Resume collection before considering further edits or allocating more budget.
            _execute(workspace, data, data["checks"][-1], root)
            return status(workspace, patch_id)
        editing = workspace.checked(workspace.root / data["worktree"])
        revision = checkouts.snapshot(editing, data["origin_revision"], "Prepare checked patch")
        source_digest = checkouts.tree(workspace.root, revision)
        _scope(workspace, data, revision)
        if source_digest == checkouts.tree(workspace.root, data["origin_revision"]):
            raise AuditError("patch has no changes to check")
        if data["checks"] and data["checks"][-1]["source_digest"] == source_digest:
            # Retries neither spend the budget again nor erase a failure/rejected review.
            return status(workspace, patch_id)
        if len(data["checks"]) >= data["plan"]["max_attempts"]:
            raise AuditError("patch check budget exhausted; limits cannot expand automatically")
        check_id = identifier("check", patch_id, source_digest, len(data["checks"]))
        job = root / "checks" / check_id
        source = job / "source"
        checkouts.create(workspace.root, source, revision)
        manifest = checkouts.source_manifest(source)
        checkouts.retain(workspace.root, patch_id, check_id, revision)
        record = {
            "check_id": check_id,
            "context_digest": data["context_digest"],
            "source_revision": revision,
            "source_digest": source_digest,
            "source_manifest": manifest,
            "state": "running",
            "started_at": now(),
        }
        record["request"] = _request(data, record)
        data["checks"].append(record)
        data["state"] = "checking"
        _save(workspace, data)
        _execute(workspace, data, record, root)
    return status(workspace, patch_id)


def _request(data: dict, record: dict) -> dict:
    return {
        "attempt_id": record["check_id"],
        "source_digest": record["source_digest"],
        "evaluation_digest": data["plan_digest"],
        "commands": [{**command, "role": "check"} for command in data["plan"]["checks"]],
        "timeout_seconds": data["plan"]["timeout_seconds"],
        "deadline_at": (
            datetime.fromisoformat(record["started_at"])
            + timedelta(seconds=data["plan"]["timeout_seconds"])
        ).isoformat(),
        "seed": 0,
    }


def _execute(workspace: Workspace, data: dict, record: dict, root: Path) -> None:
    if record["request"] != _request(data, record):
        raise AuditError("frozen patch check request changed")
    job = root / "checks" / record["check_id"]
    profile = {
        "runner": {"kind": "local"},
        "env": data["plan"]["env"],
        "limits": {"max_output_bytes": data["plan"]["max_output_bytes"]},
    }
    result = runners.execute(
        profile, job / "source", job / "execution", record["request"], checks_only=True
    )
    record["artifact"] = evidence.retain_result(workspace, job / "execution", result)
    if not result.get("finalized"):
        record["state"] = data["state"] = "interrupted"
        _save(workspace, data)
        return
    record["ended_at"] = now()
    try:
        _passing(workspace, data, record)
    except AuditError as exc:
        record["state"], record["failure"] = "failed", str(exc)
        data["state"] = "check_failed"
    else:
        record["state"] = "passed"
        data["state"] = "awaiting_review"
    record["review_template"] = {
        "patch_id": data["patch_id"],
        "check_id": record["check_id"],
        "source_revision": record["source_revision"],
        "source_digest": record["source_digest"],
        "check_digest": digest(record),
        "evidence": [record["artifact"]],
        "reviewer": "",
        "verdict": "pending",
        "rationale": "",
        "assessments": dict.fromkeys(ASSESSMENTS, False),
    }
    _save(workspace, data)


def _review_evidence(workspace: Workspace, data: dict, record: dict, review: dict) -> None:
    template = record["review_template"]
    for name in (
        "patch_id",
        "check_id",
        "source_revision",
        "source_digest",
        "check_digest",
        "evidence",
    ):
        if review[name] != template[name]:
            raise AuditError("patch review is stale or references different source/evidence")
    if (
        digest({k: v for k, v in record.items() if k != "review_template"})
        != review["check_digest"]
    ):
        raise AuditError("patch check evidence changed after review")
    if review["reviewer"] == data["author"]:
        raise AuditError("patch review must be independent of its author")
    _passing(workspace, data, record)


def review(workspace: Workspace, patch_id: str, value: dict) -> dict:
    validate_record("patch-review", value)
    if value["verdict"] == "pending":
        raise AuditError("complete the independent patch review before submitting it")
    with locked(workspace, patch_id):
        data = load(workspace, patch_id)
        if data["state"] == "reviewed_unmeasured":
            if data["review"] != value:
                raise AuditError("accepted patch review cannot be replaced")
            verified(workspace, data)
            return status(workspace, patch_id)
        if data["state"] != "awaiting_review":
            raise AuditError("passing patch checks are required before independent review")
        record = data["checks"][-1]
        _review_evidence(workspace, data, record, value)
        editing = workspace.checked(workspace.root / data["worktree"])
        revision = checkouts.snapshot(editing, data["origin_revision"], "Prepare checked patch")
        if checkouts.tree(workspace.root, revision) != record["source_digest"]:
            raise AuditError("patch changed after checks; run patch check again")
        data["reviews"].append(copy.deepcopy(value))
        if value["verdict"] != "pass" or value["assessments"] != dict.fromkeys(ASSESSMENTS, True):
            data["state"] = "review_rejected"
            _save(workspace, data)
            return status(workspace, patch_id)
        branch = f"codex/ag-patch-{patch_id}"
        existing = checkouts.git(
            workspace.root, "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}"
        )
        if existing and existing != record["source_revision"]:
            raise AuditError("patch delivery branch already contains different source")
        if not existing:
            checkouts.git(
                workspace.root,
                "update-ref",
                f"refs/heads/{branch}",
                record["source_revision"],
                "0" * len(revision),
            )
        data.update(
            state="reviewed_unmeasured",
            source_revision=record["source_revision"],
            source_digest=record["source_digest"],
            branch=branch,
            review=copy.deepcopy(value),
            review_artifact=workspace.artifact(value),
        )
        _save(workspace, data)
    return status(workspace, patch_id)


def verified(workspace: Workspace, data: dict) -> dict:
    if data["state"] != "reviewed_unmeasured":
        raise AuditError("delivery requires an independently reviewed unmeasured patch")
    review = workspace.read_artifact(data["review_artifact"])
    if (
        review != data["review"]
        or review["verdict"] != "pass"
        or review["assessments"] != dict.fromkeys(ASSESSMENTS, True)
    ):
        raise AuditError("patch independent review changed or was rejected")
    record = data["checks"][-1]
    _review_evidence(workspace, data, record, review)
    if (
        data["source_revision"] != record["source_revision"]
        or data["source_digest"] != record["source_digest"]
        or checkouts.git(workspace.root, "rev-parse", f"refs/heads/{data['branch']}")
        != data["source_revision"]
        or checkouts.tree(workspace.root, data["source_revision"]) != data["source_digest"]
    ):
        raise AuditError("reviewed patch branch changed")
    _scope(workspace, data, data["source_revision"])
    return record
