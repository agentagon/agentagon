"""Observable HTTP behavior for the checkout-scoped, read-only dashboard."""

import json

import pytest
from support.dashboard import make_audit, request, running

from agentagon.core.records import AuditError
from agentagon.dashboard import create_server


def test_dashboard_latest_includes_in_progress_and_explicit_selection(workspace):
    older = make_audit(workspace, created_at="2026-08-10T00:00:00Z")
    newer = make_audit(workspace, goal="Investigate recovery", created_at="2026-08-11T00:00:00Z")
    with running(workspace) as server:
        status, _, body = request(server)
        result = json.loads(body)
        assert status == 200
        assert result["selected_audit_id"] == newer
        assert [audit["audit_id"] for audit in result["audits"]] == [newer, older]
        status, _, body = request(server, "/api/audits/" + newer)
        detail = json.loads(body)
        assert status == 200
        assert detail["goal"] == "Investigate recovery"
        assert detail["state"] == "awaiting_evidence"
        assert detail["pending_action"] == "evidence"
        assert detail["coverage"]["code_units"] == 1
        assert detail["issues"] == []
    with running(workspace, older) as server:
        assert json.loads(request(server)[2])["selected_audit_id"] == older


def test_dashboard_empty_checkout_is_read_only(workspace):
    # The viewer must also work before the first audit/init operation.
    for path in workspace.state.iterdir():
        path.unlink()
    workspace.state.rmdir()
    with running(workspace) as server:
        status, _, body = request(server)
        assert status == 200
        assert json.loads(body)["audits"] == []
        assert json.loads(body)["selected_audit_id"] is None
        status, headers, body = request(server, "/")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert b"ag:audit" in body
        assert b"/ag:audit" not in body
        assert b"Your first audit" in body
    assert not workspace.state.exists()


def test_dashboard_explicit_unknown_audit_has_no_fallback(workspace):
    make_audit(workspace)
    unknown = "audit_" + "f" * 24
    with pytest.raises(AuditError, match="not found in this checkout"):
        create_server(workspace, unknown)
    with running(workspace) as server:
        status, _, body = request(server, "/api/audits/" + unknown)
        assert status == 404
        assert "not found" in json.loads(body)["error"]
        assert request(server, "/api/audits/../../config.json")[0] == 404


def test_dashboard_rejects_foreign_hosts_and_mutation(workspace):
    make_audit(workspace)
    with running(workspace) as server:
        assert server.server_address[0] == "127.0.0.1"
        for host in ["attacker.example", f"attacker.example:{server.server_port}", "127.0.0.1"]:
            assert request(server, headers={"Host": host})[0] == 403
        for method in ["POST", "PUT", "PATCH", "DELETE"]:
            assert request(server, method=method)[0] == 405
        for path in ["/.agentagon/config.json", "/app.py", "/../app.py", "/%2e%2e/app.py"]:
            assert request(server, path)[0] == 404


def test_dashboard_untrusted_text_stays_data_and_artifacts_stay_private(workspace):
    injected = '</script><img src=x onerror="alert(1)">'
    audit_id = make_audit(workspace, goal=injected)
    audit = workspace.read_audit(audit_id)
    audit["private_payload"] = {"api_key": "must-never-be-served"}
    workspace.save_audit(audit)
    before = {path.relative_to(workspace.state) for path in workspace.state.rglob("*")}
    with running(workspace) as server:
        status, headers, body = request(server, "/api/audits/" + audit_id)
        assert status == 200
        assert json.loads(body)["goal"] == injected
        assert b"must-never-be-served" not in body
        assert "application/json" in headers["Content-Type"]
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        status, _, html = request(server, "/")
        assert status == 200
        assert injected.encode() not in html
        javascript = request(server, "/app.js")[2]
        assert b"textContent" in javascript
        assert b"innerHTML" not in javascript
        assert b"insertAdjacentHTML" not in javascript
    assert {path.relative_to(workspace.state) for path in workspace.state.rglob("*")} == before


def test_dashboard_stays_within_one_checkout(workspace, tmp_path):
    import subprocess

    from agentagon.storage.workspace import Workspace

    root = tmp_path / "other"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "app.py").write_text("print('other')\n", encoding="utf-8")
    other = Workspace(root)
    other.initialize()
    subprocess.run(["git", "-C", str(root), "add", "app.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "test: establish application baseline",
        ],
        check=True,
    )
    local_id = make_audit(workspace)
    foreign_id = make_audit(other, goal="Foreign checkout goal")
    with running(workspace) as server:
        result = json.loads(request(server)[2])
        assert [audit["audit_id"] for audit in result["audits"]] == [local_id]
        assert request(server, "/api/audits/" + foreign_id)[0] == 404
        assert (
            request(server, "/api/audits?workspace=" + str(other.root))[2].find(
                b"Foreign checkout goal"
            )
            == -1
        )
    with pytest.raises(AuditError, match="not found in this checkout"):
        create_server(workspace, foreign_id)


def test_dashboard_trace_detail_excludes_raw_payloads(workspace, imported):
    with running(workspace) as server:
        result = json.loads(request(server, "/api/audits/" + imported)[2])
        assert result["coverage"]["selected_traces"] == 1
        assert "acquisition" not in result
        assert "analysis" not in result
        assert "intelligence" not in result
        assert "traces" not in result
    assert not (workspace.state / "reports").exists()


def test_dashboard_exposes_incomplete_acquisition_limits_without_raw_receipt(
    workspace, imported, fixtures, tmp_path
):
    from agentagon.operations import import_traces

    exports = tmp_path / "partial-export"
    exports.mkdir()
    (exports / "traces.json").write_bytes((fixtures / "braintrust.json").read_bytes())
    (exports / "incomplete.json").write_text("{", encoding="utf-8")
    receipt = tmp_path / "partial-acquisition.json"
    receipt.write_text(
        json.dumps(
            {
                "source": "braintrust",
                "project": "demo",
                "method": "fixture",
                "tool_version": "fixture-v1",
                "fetched_at": "2026-08-12T00:00:00Z",
                "completeness": "unknown",
                "pagination_complete": False,
                "selected_trace_ids": ["root", "private-missing-trace"],
                "failed_trace_ids": ["private-failed-trace"],
            }
        ),
        encoding="utf-8",
    )
    import_traces(workspace, imported, exports, receipt)

    with running(workspace) as server:
        status, _, body = request(server, "/api/audits/" + imported)
        assert status == 200
        detail = json.loads(body)
        assert detail["coverage"]["selected_traces"] == 1
        assert "Acquisition completeness: unknown; pagination complete: False." in detail["limits"]
        assert (
            "Import diagnostics: 1; missing selected traces: 1; failed fetches: 1."
            in detail["limits"]
        )
        assert "acquisition" not in detail
        assert "acquisition_plan" not in detail
        assert b"private-missing-trace" not in body
        assert b"private-failed-trace" not in body
    assert not (workspace.state / "reports").exists()


def test_dashboard_grouped_issue_includes_evidence_and_status_history(workspace, imported):
    from support.audit import finish

    from agentagon.storage.issues import list_issues, update_issue

    finish(workspace, imported)
    issue = list_issues(workspace)[0]
    update_issue(
        workspace,
        {
            "issue_id": issue["issue_id"],
            "status": "in_progress",
            "reason": "Investigating the upstream timeout.",
            "evidence": [],
        },
    )
    with running(workspace) as server:
        status, _, body = request(server, "/api/audits/" + imported)
        assert status == 200
        detail = json.loads(body)
        assert detail["state"] == "complete"
        displayed = detail["issues"][0]
        assert displayed["title"] == "Weather requests time out"
        assert displayed["severity"] == "high"
        assert displayed["status"] == "in_progress"
        assert displayed["findings"][0]["source_references"]
        assert displayed["history"][0]["reason"] == "Investigating the upstream timeout."
        assert displayed["history"][0]["at"]
    assert not (workspace.state / "reports").exists()


def test_dashboard_review_shows_change_recommendations_without_diff_content(workspace):
    from support.audit import finish_change_review

    audit_id = finish_change_review(workspace)
    audit = workspace.read_audit(audit_id)
    audit["snapshot"]["changes"][0]["hunks"][0]["text"] = "private diff payload"
    workspace.save_audit(audit)
    with running(workspace) as server:
        history = json.loads(request(server)[2])["audits"][0]
        assert history["workflow"] == "review"
        assert history["code_scope"] == "changes"
        status, _, body = request(server, "/api/audits/" + audit_id)
        assert status == 200
        detail = json.loads(body)
        assert detail["workflow"] == "review"
        assert detail["revision"] == history["revision"]
        assert detail["changes"][0]["old_path"] == "app.py"
        assert detail["changes"][0]["new_path"] is None
        assert b"private diff payload" not in body
        assert b"upstream unavailable" not in body
        categories = {item["category"] for item in detail["issues"][0]["findings"]}
        assert categories == {"Improvement", "Eval recommendation"}
        source = next(iter(detail["issues"][0]["findings"][0]["source_references"].values()))
        assert source["change"]["old_start"] == 1
        assert source["change"]["old_count"] == 2
        assert source["change"]["new_path"] is None


def test_dashboard_progress_and_findings_use_one_report_snapshot(workspace, imported, monkeypatch):
    from support.audit import finish

    from agentagon import dashboard
    from agentagon.reporting import build_report

    pending_audit = workspace.read_audit(imported)
    finish(workspace, imported)
    completed_report = build_report(workspace, imported)
    completed_audit = workspace.read_audit(imported)
    snapshots = iter([pending_audit, completed_audit])
    monkeypatch.setattr(workspace, "read_audit", lambda _: next(snapshots))
    monkeypatch.setattr(dashboard, "build_report", lambda *_: completed_report)

    detail = dashboard._detail(workspace, imported)

    assert detail["state"] == "complete"
    assert detail["pending_action"] is None
    assert detail["coverage"]["findings"] == len(detail["issues"][0]["findings"]) == 1
    assert detail["coverage"]["issue_groups"] == len(detail["issues"]) == 1
    assert next(snapshots) is pending_audit  # No second read can mix an earlier/later generation.
