"""Read-only dashboard projections over retained checkout evidence."""

import re
from urllib.parse import urlsplit

from agentagon.capabilities.reporting import build_fix_report, build_report
from agentagon.core.records import timestamp_ns
from agentagon.storage.workspace import Workspace
from agentagon.workflows.audit.operations import progress


def _audits(workspace: Workspace) -> list[dict]:
    if not workspace.state.exists():
        return []
    return sorted(
        workspace.audits(),
        key=lambda audit: (timestamp_ns(audit["created_at"]), audit["audit_id"]),
        reverse=True,
    )


def _summary(audit: dict) -> dict:
    return {
        **{key: audit[key] for key in ("audit_id", "created_at", "mode", "goal")},
        **{
            key: value
            for key, value in progress(audit).items()
            if key in {"state", "pending_action", "coverage", "workflow", "code_scope", "revision"}
        },
    }


def _change_metadata(change: dict) -> dict:
    fields = {
        "path",
        "old_path",
        "new_path",
        "change_type",
        "revision",
        "old_digest",
        "new_digest",
        "old_mode",
        "new_mode",
        "old_start",
        "old_count",
        "new_start",
        "new_count",
    }
    result = {key: value for key, value in change.items() if key in fields}
    if "hunks" in change:
        result["hunks"] = [_change_metadata(hunk) for hunk in change["hunks"]]
    return result


def _display_finding(finding: dict) -> dict:
    return {
        **finding,
        "source_references": {
            key: {
                **{field: value for field, value in source.items() if field != "change"},
                **({"change": _change_metadata(source["change"])} if source.get("change") else {}),
            }
            for key, source in finding.get("source_references", {}).items()
        },
    }


def _detail(workspace: Workspace, audit_id: str) -> dict:
    # Build a deliberate display projection: never expose raw trace payloads,
    # credential configuration, provider receipts, or intelligence requests.
    report = build_report(workspace, audit_id)
    issue_keys = {
        "issue_id",
        "title",
        "summary",
        "rationale",
        "status",
        "severity",
        "confidence",
        "affected_trace_ids",
        "reviewed_trace_denominator",
        "reviewed_sample_rate",
        "findings",
    }
    issues = []
    for issue in report["issues"]:
        displayed = {key: value for key, value in issue.items() if key in issue_keys}
        displayed["findings"] = [_display_finding(finding) for finding in issue["findings"]]
        displayed["history"] = [
            {key: event[key] for key in ("at", "status", "reason")} for event in issue["history"]
        ]
        issues.append(displayed)
    alignment = report.get("trace_alignment")
    if alignment:
        alignment = {
            **{key: alignment.get(key) for key in ("status", "revision", "scope", "warning")},
            "mismatched_traces": len(alignment.get("mismatched_trace_ids", [])),
        }
    return {
        **{
            key: report[key]
            for key in (
                "audit_id",
                "created_at",
                "mode",
                "goal",
                "state",
                "pending_action",
                "coverage",
                "workflow",
                "revision",
            )
        },
        "host": report["host"],
        "model": report["model"],
        "window": report["window"],
        "code_scope": report["code_scope"].get("code_scope"),
        "code_scopes": report["code_scope"]["scopes"],
        "changes": [_change_metadata(change) for change in report["code_scope"].get("changes", [])],
        "skipped_code_files": len(report["code_scope"]["skipped"]),
        "skipped_code": [
            {"path": item["path"], "reason": item["reason"]}
            for item in report["code_scope"]["skipped"]
        ],
        "trace_alignment": alignment,
        "issues": issues,
        "ungrouped_findings": [
            _display_finding(finding) for finding in report["ungrouped_findings"]
        ],
        "limits": report["limits"],
    }


def _runs(workspace: Workspace) -> list[dict]:
    if not (workspace.state / "runs").exists():
        return []
    from agentagon.capabilities.experiments.store import list_runs

    return sorted(
        list_runs(workspace),
        key=lambda run: (run.get("created_at") or "", run["run_id"]),
        reverse=True,
    )


def _run_summary(run: dict) -> dict:
    return {key: run.get(key) for key in ("run_id", "created_at", "updated_at", "goal", "state")}


def _run_detail(workspace: Workspace, run_id: str) -> dict:
    from agentagon.capabilities.experiments import orchestration
    from agentagon.capabilities.experiments.store import load_run

    data = load_run(workspace, run_id)
    result = build_fix_report(workspace, data)
    if "orchestration" in data.get("profile", {}):
        packet = orchestration.next_packet(workspace, run_id)
        result.update(
            work=packet["work"],
            work_reason=packet["reason"],
            lesson_context=packet["lesson_context"],
        )
    return result


def _benchmark_drafts(workspace: Workspace) -> list[dict]:
    if not (workspace.state / "benchmarks").exists():
        return []
    from agentagon.capabilities.experiments import benchmarks

    result = []
    for draft in sorted(
        benchmarks.list_drafts(workspace),
        key=lambda item: (item["created_at"], item["benchmark_id"]),
        reverse=True,
    ):
        snapshot = draft["snapshot"]
        assessment = snapshot["assessment"]
        result.append(
            {
                **{
                    key: draft[key]
                    for key in ("benchmark_id", "created_at", "state", "measurement_status")
                },
                "audit_id": snapshot.get("audit_id"),
                "goal": assessment["goal"],
                "readiness": {
                    "state": draft["readiness"]["state"],
                    "missing": [
                        {key: item[key] for key in ("code", "action", "path") if key in item}
                        for item in draft["readiness"]["missing"]
                    ],
                },
                "validation_label": {
                    "baseline_recorded": "Frozen benchmark; baseline recorded",
                    "historical_baseline": "Historical baseline; current inputs need preparation",
                }.get(draft["measurement_status"], "Draft; not measured"),
                "assessment_counts": {
                    status: sum(
                        item["status"] == status for item in assessment["assessments"].values()
                    )
                    for status in ("supported", "concern", "unknown")
                },
                "dataset_files": len(assessment["dataset_paths"]),
                "entrypoint_files": len(assessment["entrypoint_paths"]),
                "evaluation_ids": [item["evaluation_id"] for item in draft.get("evaluations", [])],
                "report_url": f"/api/benchmarks/{draft['benchmark_id']}",
            }
        )
    return result


DELIVERY_FILES = {
    "summary": "summary.json",
    "pr_body": "pull-request.md",
    "diff": "diff.patch",
    "diffstat": "diffstat.txt",
}


def _patch_artifact_path(workspace: Workspace, patch: dict, delivery_id: str, name: str):
    from agentagon.capabilities.experiments import patches

    if not re.fullmatch(r"delivery_[0-9a-f]{24}", delivery_id) or name not in DELIVERY_FILES:
        return None
    record = patch.get("deliveries", {}).get(delivery_id, {})
    expected = (
        patches.directory(workspace, patch["patch_id"])
        / "deliveries"
        / delivery_id
        / DELIVERY_FILES[name]
    )
    if record.get("artifacts", {}).get(name) != str(expected.relative_to(workspace.root)):
        return None
    # Links may name only generated files in this owner's delivery directory.
    # A modified receipt cannot turn the endpoint into a private-state file server.
    if any(
        part.is_symlink()
        for part in (expected, *expected.parents)
        if part.is_relative_to(workspace.state)
    ):
        return None
    return expected if workspace.checked(expected).is_file() else None


def _patch_deliveries(workspace: Workspace, patch: dict) -> list[dict]:
    result = []
    for delivery_id, record in patch.get("deliveries", {}).items():
        url = record.get("pr", {}).get("url")
        parsed = urlsplit(url) if isinstance(url, str) else None
        result.append(
            {
                "delivery_id": delivery_id,
                "state": record.get("state"),
                "pr_url": url
                if parsed
                and parsed.scheme == "https"
                and parsed.netloc
                and not parsed.username
                and not parsed.password
                else None,
                "artifacts": {
                    name: f"/api/patches/{patch['patch_id']}/deliveries/{delivery_id}/artifacts/{name}"
                    for name in DELIVERY_FILES
                    if _patch_artifact_path(workspace, patch, delivery_id, name) is not None
                },
            }
        )
    return result


def _reviewed_patches(workspace: Workspace) -> list[dict]:
    from agentagon.capabilities.experiments import patches

    result = []
    for patch in patches.list_patches(workspace):
        current = patch["checks"][-1] if patch["checks"] else {}
        check_count = len(patch["plan"]["checks"])
        result.append(
            {
                **{
                    key: patch.get(key)
                    for key in (
                        "patch_id",
                        "goal",
                        "state",
                        "created_at",
                        "updated_at",
                        "next_action",
                        "reason_no_comparison",
                        "branch",
                    )
                },
                "check_state": current.get("state") if check_count else "not_run",
                "check_count": check_count,
                "executable_checks_run": bool(
                    check_count and current.get("state") in {"passed", "failed"}
                ),
                "review_verdict": patch.get("review", {}).get("verdict")
                or (patch["reviews"][-1].get("verdict") if patch["reviews"] else None),
                "measurement_status": "not_measured",
                "validation_label": "Reviewed; unmeasured"
                if patch["state"] == "reviewed_unmeasured"
                else "Unmeasured patch",
                "deliveries": _patch_deliveries(workspace, patch),
                "report_url": f"/api/patches/{patch['patch_id']}",
            }
        )
    return result
