"""Read-only, bounded projections of candidate and evaluation evidence."""

import base64
import copy
import hashlib
import json
import subprocess

from agentagon.core.records import AuditError
from agentagon.experiments import engine, learning, preparation
from agentagon.experiments.store import load_run, run_dir

DIFF_LIMIT = 262144


def _trial(workspace, data: dict, candidate: dict, trial: dict) -> dict:
    result = workspace.read_artifact(trial["artifact"]) if trial.get("artifact") else {}
    evidence = result.get("evidence")
    if not evidence:
        progress = (
            run_dir(workspace, data["run_id"]) / "attempts" / trial["trial_id"] / "progress.json"
        )
        progress = engine._owned(workspace, data, str(progress.relative_to(workspace.root)))
        if progress.exists() and not progress.is_symlink():
            with progress.open("rb") as stream:
                raw = stream.read(4194305)
            if len(raw) <= 4194304:
                evidence = json.loads(raw)
    if evidence:
        expected = {
            "run_id": data["run_id"],
            "candidate_id": candidate["candidate_id"],
            "attempt_id": trial["trial_id"],
            "source_digest": trial["request"]["source_digest"],
            "evaluation_digest": data["evaluation_digest"],
        }
        if evidence.get("identity") != expected:
            raise AuditError("task evidence identity does not match the selected trial")
        evidence = {
            **evidence,
            "artifacts": [
                {k: v for k, v in entry.items() if k != "content_base64"}
                for entry in evidence["artifacts"]
            ],
        }
    return {
        "trial_id": trial["trial_id"],
        "state": trial["state"],
        "error": trial.get("error"),
        "metrics": trial.get("metrics", {}),
        "checks": trial.get("checks", []),
        "repetition": trial.get("repetition"),
        "started_at": trial.get("started_at"),
        "ended_at": trial.get("ended_at"),
        "evidence": evidence,
        "skipped_commands": result.get("skipped_commands", []),
        "commands": [
            {
                key: entry[key]
                for key in (
                    "id",
                    "role",
                    "exit_code",
                    "stdout",
                    "stderr",
                    "stdout_truncated",
                    "stderr_truncated",
                    "timed_out",
                )
                if key in entry
            }
            for entry in result.get("results", [])
        ],
    }


def _diff(workspace, data: dict, candidate: dict) -> dict:
    revision = candidate.get("source_revision")
    if not revision:
        return {
            "text": "Candidate has not been sealed. Its assigned worktree may still contain edits.",
            "truncated": False,
            "sealed": False,
        }
    parent = candidate.get("parent_revision", data["origin_revision"])
    process = subprocess.Popen(
        [
            "git",
            "-C",
            str(workspace.root),
            "-c",
            "core.hooksPath=/dev/null",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            parent,
            revision,
            "--",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        content = process.stdout.read(DIFF_LIMIT + 1)
        if len(content) > DIFF_LIMIT:
            process.terminate()
        code = process.wait(timeout=10)
        if code and len(content) <= DIFF_LIMIT:
            raise AuditError("candidate diff is unavailable")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    return {
        "text": content[:DIFF_LIMIT].decode("utf-8", errors="replace"),
        "truncated": len(content) > DIFF_LIMIT,
        "sealed": True,
        "parent_revision": parent,
        "source_revision": revision,
    }


def candidate(workspace, run_id: str, candidate_id: str) -> dict:
    data = load_run(workspace, run_id)
    value = engine._candidate(data, candidate_id)
    fields = (
        "candidate_id",
        "parent_id",
        "state",
        "hypothesis",
        "author",
        "source_revision",
        "source_digest",
        "metrics",
        "variation",
        "constraints",
        "checks",
        "brief",
        "assignment",
        "round_id",
    )
    return {
        "run_id": run_id,
        "candidate": {k: copy.deepcopy(value[k]) for k in fields if k in value},
        "diff": _diff(workspace, data, value),
        "trials": [_trial(workspace, data, value, trial) for trial in value["trials"]],
        "review": workspace.read_artifact(value["review_artifact"])
        if value.get("review_artifact")
        else None,
        "review_template": engine._review_template(data, value)
        if value["state"] == "awaiting_review"
        else None,
        "lesson_context": learning.context(workspace, data),
    }


def artifact(workspace, run_id: str, candidate_id: str, trial_id: str, index: int) -> bytes:
    data = load_run(workspace, run_id)
    value = engine._candidate(data, candidate_id)
    trial = next((t for t in value["trials"] if t["trial_id"] == trial_id), None)
    if not trial or not trial.get("artifact") or type(index) is not int or index < 0:
        raise AuditError("artifact is not retained by this candidate trial")
    _trial(workspace, data, value, trial)
    result = workspace.read_artifact(trial["artifact"])
    entries = (result.get("evidence") or {}).get("artifacts", [])
    if index >= len(entries):
        raise AuditError("artifact is not retained by this candidate trial")
    entry = entries[index]
    try:
        content = base64.b64decode(entry["content_base64"], validate=True)
    except (ValueError, KeyError) as exc:
        raise AuditError("artifact content is invalid") from exc
    if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
        raise AuditError("artifact checksum changed")
    return content


def evaluation_summary(data: dict) -> dict:
    current = next(
        (c for c in data["checks"] if c["validation_id"] == data.get("current_validation")), None
    )
    plan = current["plan"] if current else None
    metric_case_ids = {case["id"] for case in plan.get("metric_cases", [])} if plan else set()
    return {
        **{
            k: data.get(k)
            for k in (
                "evaluation_id",
                "goal",
                "state",
                "created_at",
                "updated_at",
                "from_id",
                "budget",
                "usage",
                "package_digest",
            )
        },
        "coverage": plan["coverage"] if plan else None,
        "metrics": plan["spec"]["metrics"] if plan else {},
        "review_branch": data.get("package", {}).get("review_branch"),
        "review_verdict": data.get("review", {}).get("verdict"),
        "metric_comparisons": current.get("metric_comparisons", []) if current else [],
        "metric_comparison_error": current.get("metric_comparison_error") if current else None,
        "trials": [
            {
                **{
                    k: t[k]
                    for k in ("trial_id", "case_id", "state", "repetition", "outcome", "error")
                    if k in t
                },
                "kind": "baseline"
                if t["case_id"] == "baseline"
                else "metric"
                if t["case_id"] in metric_case_ids
                else "negative",
            }
            for t in current["trials"]
        ]
        if current
        else [],
    }


def evaluations(workspace) -> list[dict]:
    return sorted(
        [
            evaluation_summary(preparation.load(workspace, file.parent.name))
            for file in (workspace.state / "evaluations").glob("*/state.json")
        ],
        key=lambda data: (data["created_at"], data["evaluation_id"]),
        reverse=True,
    )
