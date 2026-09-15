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


def _save_reviewed_patch(workspace, *, no_checks=False):
    """Write a stored display fixture without executing checks or creating a branch."""
    from agentagon.experiments import patches

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


@pytest.mark.parametrize("no_checks", [False, True])
def test_reviewed_patch_dashboard_keeps_measurement_limits_and_downloads_owned_artifacts(
    workspace, monkeypatch, no_checks
):
    from agentagon.experiments import patches, worker

    source = _save_reviewed_patch(workspace, no_checks=no_checks)
    before = {path: path.read_bytes() for path in workspace.state.rglob("*") if path.is_file()}
    monkeypatch.setattr(
        patches, "verified", lambda *args, **kwargs: pytest.fail("dashboard revalidated delivery")
    )
    monkeypatch.setattr(worker, "run", lambda *args, **kwargs: pytest.fail("dashboard ran checks"))
    with running(workspace) as server:
        for endpoint in ("/api/runs", "/api/patches"):
            status, _, body = request(server, endpoint)
            assert status == 200
            patch = json.loads(body)["reviewed_patches"][0]
            assert patch["patch_id"] == source["patch_id"]
            assert patch["validation_label"] == "Reviewed; unmeasured"
            assert patch["measurement_status"] == "not_measured"
            assert patch["reason_no_comparison"] == source["reason_no_comparison"]
            assert patch["next_action"] == "deliver"
            assert patch["review_verdict"] == "pass"
            assert patch["check_count"] == (0 if no_checks else 1)
            assert patch["check_state"] == ("not_run" if no_checks else "passed")
            assert patch["executable_checks_run"] is not no_checks
            assert patch["deliveries"][0]["pr_url"] is None
            assert b"private-" not in body
            assert json.loads(request(server, patch["report_url"])[2]) == patch
            for name, url in patch["deliveries"][0]["artifacts"].items():
                status, headers, content = request(server, url)
                assert status == 200 and content == f"retained-{name}".encode()
                assert headers["Content-Disposition"].startswith("attachment;")
                assert headers["Content-Type"] == "application/octet-stream"
                assert request(server, url, headers={"Host": "attacker.example"})[0] == 403
        assert request(server, "/api/patches/patch_" + "f" * 24)[0] == 404
    assert {
        path: path.read_bytes() for path in workspace.state.rglob("*") if path.is_file()
    } == before


def test_patch_delivery_links_reject_changed_paths_and_symlinks(workspace):
    from agentagon.experiments import patches

    source = _save_reviewed_patch(workspace)
    patch_id = source["patch_id"]
    delivery_id, delivery = next(iter(source["deliveries"].items()))
    delivery["artifacts"]["summary"] = str(
        (workspace.state / "workspace.json").relative_to(workspace.root)
    )
    diff = workspace.root / delivery["artifacts"]["diff"]
    diff.unlink()
    diff.symlink_to(workspace.state / "workspace.json")
    workspace.write(patches.directory(workspace, patch_id) / "state.json", source)
    with running(workspace) as server:
        status, _, body = request(server, "/api/patches")
        assert status == 200
        artifacts = json.loads(body)["reviewed_patches"][0]["deliveries"][0]["artifacts"]
        assert set(artifacts) == {"pr_body", "diffstat"}
        for suffix in ("summary", "diff", "state.json", "../../state.json"):
            url = f"/api/patches/{patch_id}/deliveries/{delivery_id}/artifacts/{suffix}"
            assert request(server, url)[0] == 404
