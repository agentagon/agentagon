"""Fix reports expose results while execution inputs and artifacts stay private."""

import copy
import json

import pytest

from agentagon.capabilities.reporting import build_fix_report, render_fix_markdown
from agentagon.core.records import digest
from agentagon.dashboard import evidence as dashboard


@pytest.fixture
def fixture_runs(workspace, fix_run):
    """Use real read-only storage without executing any experiment."""
    fix_run.update(
        version=1,
        origin=str(workspace.root),
        evaluation_digest=digest(fix_run["spec"]),
        profile_digest=digest(fix_run["profile"]),
    )
    workspace.write(workspace.state / "runs" / fix_run["run_id"] / "state.json", fix_run)
    return {fix_run["run_id"]: fix_run}


def test_fix_report_projects_results_and_keeps_execution_state_private(workspace, fix_run):
    before = copy.deepcopy(fix_run)
    report = build_fix_report(workspace, fix_run)
    candidate = report["candidates"][1]
    assert candidate["metrics"] == {"cost_usd": 1.4, "latency_ms": 120.0, "quality": 0.96}
    assert candidate["parent_id"] == "candidate_baseline"
    assert candidate["constraints"][0]["passed"] is True
    assert candidate["constraints"][0]["op"] == ">="
    assert candidate["constraints"][0]["reference"] == "baseline_delta"
    assert candidate["checks"] == [
        {"id": "correctness", "passed": True, "issue_ids": ["issue_one"]}
    ]
    assert candidate["review_verdict"] == "pass"
    assert report["selected_branch"] == "codex/fix-tradeoff"
    assert report["runner_kind"] == "ssh"
    assert report["cleanup_pending"] is True
    assert report["usage"]["elapsed_seconds"] == 300.5
    serialized = json.dumps(report)
    assert "must-never-be-served" not in serialized
    assert "trial-with-secret" not in serialized
    assert "123456789" not in serialized
    assert "samples" not in serialized
    assert "trials" not in candidate
    assert fix_run == before
    assert not (workspace.state / "runs").exists()


def test_fix_report_defaults_and_nonfinite_metrics(workspace):
    run = {
        "run_id": "run_empty",
        "candidates": {
            "candidate_one": {
                "metrics": {
                    "missing": None,
                    "nan": float("nan"),
                    "infinite": float("inf"),
                    "boolean": True,
                    "valid": 0.0,
                    "private": {"secret": "hidden"},
                },
            }
        },
    }
    report = build_fix_report(workspace, run)
    assert report["candidates"][0]["metrics"] == {"valid": 0.0}
    assert report["candidates"][0]["checks"] == []
    assert report["selected_branch"] is None
    assert report["frontier"] == []
    assert report["limits"] == {}
    assert report["cleanup_pending"] is False
    assert "No branch has been selected" in render_fix_markdown(report)


def test_fix_markdown_includes_all_dimensions_and_baseline_deltas(workspace, fix_run):
    fix_run["candidates"]["candidate_tradeoff"]["metrics"]["memory_mb"] = 300
    text = render_fix_markdown(build_fix_report(workspace, fix_run))
    assert "cost_usd | latency_ms | memory_mb | quality" in text
    assert "1.4 (-0.6)" in text
    assert "120 (+20)" in text
    assert "codex/fix-tradeoff" in text
    assert "Reuse context \\| avoid duplicate calls" in text
    assert "Workspace cleanup is pending" in text
    assert "must-never-be-served" not in text
    table_lines = [line for line in text.splitlines() if line.startswith("|")]
    assert len(table_lines) == 4


def _save_reviewed_patch(workspace, *, no_checks=False):
    """Write a stored display fixture without executing checks or creating a branch."""
    from agentagon.capabilities.experiments import patches

    patch_id = "patch_" + "a" * 24
    plan = {"checks": [] if no_checks else [{"id": "unit", "argv": ["private-command"]}]}
    delivery_id = "delivery_" + "b" * 24
    data = {
        "version": 1,
        "origin": str(workspace.root),
        "patch_id": patch_id,
        "created_at": "2026-09-12T00:00:00Z",
        "updated_at": "2026-09-12T01:00:00Z",
        "goal": "Fix timeout handling",
        "origin_revision": "0" * 40,
        "author": "patch-host",
        "state": "reviewed_unmeasured",
        "reason_no_comparison": "No executable baseline is available.",
        "plan": plan,
        "plan_digest": digest(plan),
        "branch": "codex/reviewed-patch",
        "checks": [{"state": "passed", "stdout": "private-check-output"}],
        "reviews": [{"verdict": "pass", "private": "private-review-material"}],
        "review": {"verdict": "pass", "private": "private-review-material"},
        "intelligence": {"request": "private-intelligence-payload"},
        "deliveries": {
            delivery_id: {
                "state": "prepared",
                "artifacts": {},
                "pr": {"url": "javascript:alert('must not serve')"},
            }
        },
    }
    data["context_digest"] = digest({key: data[key] for key in patches.CONTEXT_FIELDS})
    for name, filename in dashboard.DELIVERY_FILES.items():
        path = patches.directory(workspace, patch_id) / "deliveries" / delivery_id / filename
        workspace.write_bytes(path, f"retained-{name}".encode())
        data["deliveries"][delivery_id]["artifacts"][name] = str(path.relative_to(workspace.root))
    workspace.write(patches.directory(workspace, patch_id) / "state.json", data)
    return data


def test_reviewed_patch_projection_preserves_private_inputs(workspace):
    source = _save_reviewed_patch(workspace)
    projected = dashboard._reviewed_patches(workspace)[0]
    assert projected["patch_id"] == source["patch_id"]
    assert projected["validation_label"] == "Reviewed; unmeasured"
    assert "private-command" not in json.dumps(projected)
