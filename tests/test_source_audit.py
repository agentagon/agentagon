"""Audit current files without requiring a committed, clean Git checkout."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from support.audit import finish, respond, review_unknown
from support.changes import git

from agentagon.cli.main import main
from agentagon.core.records import AuditError, load_json
from agentagon.dashboard import _detail
from agentagon.operations import import_traces, prepare, start
from agentagon.reporting import report
from agentagon.storage.workspace import Workspace


@pytest.fixture(params=["directory", "unborn", "dirty", "clean"])
def source(request, tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    return 1\n")
    if request.param != "directory":
        git(root, "init", "-q")
        if request.param != "unborn":
            git(root, "add", ".")
            git(root, "commit", "-qm", "Baseline")
        if request.param == "dirty":
            (root / "app.py").write_text("def run():\n    return 2\n")
    workspace = Workspace(root)
    workspace.initialize()
    return workspace, request.param


def begin(workspace, mode):
    return start(
        workspace,
        mode=mode,
        source="braintrust" if mode != "code" else None,
        project="demo" if mode != "code" else None,
        start_time="2026-08-10T00:00:00Z" if mode != "code" else None,
        end_time="2026-08-11T00:00:00Z" if mode != "code" else None,
        limit="all" if mode != "code" else None,
        scopes=[],
        host="test",
        model="fixture",
    )


@pytest.mark.parametrize("mode", ["code", "traces", "combined"])
def test_audit_modes_accept_current_source_and_retain_alignment_limits(source, fixtures, mode):
    workspace, kind = source
    result = begin(workspace, mode)
    audit_id = result["audit_id"]
    if mode == "code":
        assert result["trace_alignment"] is None
        respond(workspace, audit_id, "evidence", review_unknown)

        def no_findings(response, packet):
            for item in response["items"]:
                item.update(status="reviewed", summary="No supported findings in this fixture.")

        result, _ = respond(workspace, audit_id, "diagnosis", no_findings)
        assert result["state"] == "complete"
    else:
        alignment = result["trace_alignment"]
        assert alignment["status"] == ("assumed" if kind == "clean" else "unverified")
        warning = alignment["warning"]
        assert bool(warning) == (kind != "clean")
        acquisition = workspace.state / "acquisition.json"
        acquisition.write_text(
            json.dumps(
                {
                    "source": "braintrust",
                    "project": "demo",
                    "method": "fixture",
                    "tool_version": "fixture-v1",
                    "fetched_at": "2026-08-12T00:00:00Z",
                    "completeness": "complete",
                    "pagination_complete": True,
                    "selected_trace_ids": ["root"],
                    "failed_trace_ids": [],
                }
            )
        )
        result = import_traces(workspace, audit_id, fixtures / "braintrust.json", acquisition)
        assert result["trace_alignment"]["warning"] == warning
        prepared = prepare(workspace, audit_id, "evidence")
        assert load_json(Path(prepared["packet"]))["trace_alignment"]["warning"] == warning
        if mode == "traces":
            result, _ = finish(workspace, audit_id)
            assert result["state"] == ("complete" if kind == "clean" else "complete_with_limits")
        generated = report(workspace, audit_id)
        payload = load_json(Path(generated["json_report"]))
        assert payload["trace_alignment"]["warning"] == warning
        if warning:
            assert "incomplete or incorrect" in warning
            assert warning in Path(generated["report"]).read_text()
            assert warning in _detail(workspace, audit_id)["limits"]
            assert _detail(workspace, audit_id)["trace_alignment"]["warning"] == warning
    if kind == "directory":
        assert not (workspace.root / ".git").exists()
        assert not (workspace.root / ".gitignore").exists()


def test_no_git_executable_cli_can_initialize_configure_audit_and_report(tmp_path, monkeypatch):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "app.py").write_text("value = 1\n")
    monkeypatch.setenv("PATH", "")
    runner = CliRunner()

    def cli(*arguments):
        result = runner.invoke(main, ["--workspace", str(root), *arguments])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    cli("init")
    cli("setup", "--scope", "project", "--set", "traces.state", "disabled")
    audit = cli("audit", "start", "--mode", "code")
    assert audit["revision"] is None
    assert audit["coverage"]["code_units"] == 1
    assert cli("status", "--audit", audit["audit_id"])["audit_id"] == audit["audit_id"]
    cli("audit", "prepare", audit["audit_id"], "--stage", "evidence")
    cli("audit", "report", audit["audit_id"])
    assert not (root / ".git").exists()
    with pytest.raises(AuditError, match="changes reviews require a Git checkout"):
        Workspace(root).changes()


def test_current_files_include_staged_unstaged_and_untracked_content(workspace):
    root = workspace.root
    (root / "app.py").write_text("staged = True\n")
    git(root, "add", "app.py")
    (root / "app.py").write_text("unstaged = True\n")
    (root / "new.py").write_text("new = True\n")
    result = begin(workspace, "code")
    audit = workspace.read_audit(result["audit_id"])
    content = {
        unit["path"]: workspace.read_artifact(unit["content_path"])["text"]
        for unit in audit["code_units"]
    }
    assert content == {"app.py": "unstaged = True", "new.py": "new = True"}
    assert git(root, "show", ":app.py") == "staged = True"


def test_edit_during_capture_cannot_label_dirty_code_as_clean(workspace, monkeypatch):
    source_names = workspace._source_names

    def edit_after_status():
        (workspace.root / "app.py").write_text("changed during capture\n")
        return source_names()

    monkeypatch.setattr(workspace, "_source_names", edit_after_status)
    with pytest.raises(AuditError, match="code input changed"):
        begin(workspace, "combined")
    assert workspace.audits() == []


@pytest.mark.parametrize("mutation", ["edit", "add", "delete"])
def test_snapshot_detects_changes_after_capture(source, mutation):
    workspace, _ = source
    snapshot = workspace.snapshot([])
    if mutation == "edit":
        (workspace.root / "app.py").write_text("changed\n")
    elif mutation == "add":
        (workspace.root / "new.py").write_text("new\n")
    else:
        (workspace.root / "app.py").unlink()
    with pytest.raises(AuditError, match="code input changed"):
        workspace.verify_snapshot(snapshot)


def test_directory_capture_excludes_private_state_dependencies_and_symlinks(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "app.py").write_text("value = 1\n")
    (root / ".env.local").write_text("SECRET=private\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dependency.js").write_text("dependency\n")
    outside = tmp_path / "outside.py"
    outside.write_text("private outside source\n")
    (root / "link.py").symlink_to(outside)
    (root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    workspace = Workspace(root)
    workspace.initialize()
    snapshot = workspace.snapshot([])
    assert [file["path"] for file in snapshot["files"]] == ["app.py"]
    assert {file["path"] for file in snapshot["skipped"]} == {".env.local", "link.py", "linked-dir"}
    with pytest.raises(AuditError, match="inside the workspace"):
        workspace.snapshot([str(tmp_path)])
