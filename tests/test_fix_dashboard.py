"""Fix reports expose results while execution inputs and artifacts stay private."""

import copy
import json

import pytest
from support.dashboard import make_audit, request, running

from agentagon import dashboard
from agentagon.core.records import AuditError, digest
from agentagon.reporting import build_fix_report, render_fix_markdown


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


def test_fix_dashboard_audit_default_and_explicit_run_selection(workspace, fixture_runs):
    run_id = next(iter(fixture_runs))
    audit_id = make_audit(workspace)
    with running(workspace) as server:
        audits = json.loads(request(server, "/api/audits")[2])
        assert audits["selected_view"] == "audit"
        assert audits["selected_audit_id"] == audit_id
        status, _, body = request(server, "/api/runs")
        listing = json.loads(body)
        assert status == 200
        assert listing["selected_run_id"] == run_id
        assert listing["runs"][0]["run_id"] == run_id
        assert "spec" not in listing["runs"][0]
    with running(workspace, run_id=run_id) as server:
        assert json.loads(request(server, "/api/audits")[2])["selected_view"] == "fix"
        status, _, body = request(server, "/api/runs/" + run_id)
        assert status == 200
        assert json.loads(body)["selected_branch"] == "codex/fix-tradeoff"
        assert b"must-never-be-served" not in body
        assert b"trial-with-secret" not in body
    with pytest.raises(AuditError, match="not both"):
        dashboard.create_server(workspace, audit_id=audit_id, run_id=run_id)


def test_fix_dashboard_missing_run_and_read_only_boundaries(workspace, fixture_runs):
    unknown = "run_" + "f" * 24
    with pytest.raises(AuditError, match="fix run not found"):
        dashboard.create_server(workspace, run_id=unknown)
    before = {path: path.read_bytes() for path in workspace.state.rglob("*") if path.is_file()}
    with running(workspace) as server:
        for path in [
            "/api/runs/" + unknown,
            "/api/runs/../../workspace.json",
            "/api/runs/%2e%2e/state.json",
        ]:
            assert request(server, path)[0] == 404
        assert request(server, "/api/runs", headers={"Host": "attacker.example"})[0] == 403
        for method in ["POST", "PUT", "PATCH", "DELETE"]:
            assert request(server, "/api/runs", method=method)[0] == 405
        assert b"Fix runs" in request(server, "/")[2]
        js = request(server, "/app.js")[2]
        assert b"innerHTML" not in js
        assert b"insertAdjacentHTML" not in js
    assert {
        path: path.read_bytes() for path in workspace.state.rglob("*") if path.is_file()
    } == before


def test_fix_dashboard_explicit_older_run_and_invalid_origin(workspace, fixture_runs):
    latest_id, latest = next(iter(fixture_runs.items()))
    older = copy.deepcopy(latest)
    older.update(run_id="run_" + "b" * 24, created_at="2026-09-06T00:00:00Z")
    path = workspace.state / "runs" / older["run_id"] / "state.json"
    workspace.write(path, older)
    with running(workspace, run_id=older["run_id"]) as server:
        listing = json.loads(request(server, "/api/runs")[2])
        assert listing["selected_run_id"] == older["run_id"]
        assert [run["run_id"] for run in listing["runs"]] == [latest_id, older["run_id"]]
    older["origin"] = "/private/another-checkout"
    workspace.write(path, older)
    with running(workspace) as server:
        status, _, body = request(server, "/api/runs")
        assert status == 500
        assert b"another-checkout" not in body


def test_fix_dashboard_empty_before_initialization_does_not_write(workspace):
    for path in workspace.state.iterdir():
        path.unlink()
    workspace.state.rmdir()
    with running(workspace) as server:
        status, _, body = request(server, "/api/runs")
        assert status == 200
        assert json.loads(body)["runs"] == []
        assert json.loads(body)["selected_run_id"] is None
    assert not workspace.state.exists()
