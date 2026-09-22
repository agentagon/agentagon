"""Unmeasured patches retain actual checks without weakening verified experiment gates."""

import copy
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from support.experiments import git

from agentagon.capabilities.experiments import delivery, patches, runners, store
from agentagon.cli.internal import main
from agentagon.core.records import AuditError
from agentagon.workflows.service import Application


def start(workspace, *, checks=None, **updates):
    plan = {
        "editable_paths": ["app.py"],
        "checks": checks
        if checks is not None
        else [
            {
                "id": "behavior",
                "argv": [
                    sys.executable,
                    "-c",
                    "from app import weather; assert weather('Paris') == 'Paris'; print('checked')",
                ],
            }
        ],
        "timeout_seconds": 5,
        "max_attempts": 3,
        **updates,
    }
    return patches.start(
        workspace,
        plan,
        goal="Return the requested city",
        author="author",
        reason_no_comparison="Production credentials are unavailable; private-reason",
    )


def change(workspace, started, content="def weather(city):\n    return city\n"):
    (workspace.root / started["worktree"] / "app.py").write_text(content)


def passing_review(checked):
    review = copy.deepcopy(checked["checks"][-1]["review_template"])
    review.update(
        reviewer="independent",
        verdict="pass",
        rationale="Inspected exact patch, recorded command output and comparison limitations.",
    )
    review["assessments"] = dict.fromkeys(review["assessments"], True)
    return review


def reviewed(workspace):
    started = start(workspace)
    change(workspace, started)
    checked = patches.check(workspace, started["patch_id"])
    return patches.review(workspace, started["patch_id"], passing_review(checked))


def test_checked_patch_delivers_locally_without_remote_and_preserves_origin(workspace):
    origin = git(workspace.root, "rev-parse", "HEAD")
    result = reviewed(workspace)
    assert result["state"] == "reviewed_unmeasured"
    assert not store.list_runs(workspace)
    record = result["checks"][-1]
    observed = workspace.read_artifact(record["artifact"])
    assert observed["results"][0]["stdout"] == "checked\n"
    assert observed["results"][0]["exit_code"] == 0
    (workspace.root / "notes.txt").write_text("unrelated user notes")
    before = git(workspace.root, "status", "--porcelain")
    shipped = delivery.deliver(workspace, patch_id=result["patch_id"])
    assert (
        delivery.deliver(workspace, patch_id=result["patch_id"])["delivery_id"]
        == shipped["delivery_id"]
    )
    assert shipped["state"] == "prepared"
    assert git(workspace.root, "status", "--porcelain") == before
    assert git(workspace.root, "rev-parse", "HEAD") == origin
    summary = json.loads(Path(shipped["artifacts"]["summary"]).read_text())
    assert summary["status"] == "reviewed_unmeasured" and summary["comparison"] is None
    assert "private-reason" not in Path(shipped["artifacts"]["pr_body"]).read_text()
    assert "+    return city" in Path(shipped["artifacts"]["diff"]).read_text()


def test_application_exposes_only_reviewed_patch_summary_and_delivery(workspace, tmp_path):
    result = reviewed(workspace)
    app = Application(tmp_path / "application-state", execute=lambda *_: {})
    try:
        project = app.register(str(workspace.root))
        shown = app.result(project["id"], "patch", result["patch_id"])
        assert shown["state"] == "reviewed_unmeasured"
        assert shown["allowed_actions"] == ["prepare_local_delivery"]
        assert "env" not in str(shown) and "argv" not in str(shown)
        prepared = app.deliver(
            project["id"],
            {"kind": "patch", "source_id": result["patch_id"], "publish": False},
        )
        assert prepared["state"] == "prepared"
        reloaded = app.result(project["id"], "patch", result["patch_id"])
        retained = reloaded["deliveries"][0]
        assert retained["delivery_id"] == prepared["delivery_id"]
        assert set(retained["artifact_urls"]) == {"summary", "pr_body", "diff", "diffstat"}
        assert app.delivery_artifact(project["id"], prepared["delivery_id"], "diff")
    finally:
        app.close()


def test_patch_requires_clean_origin_and_declared_scope(workspace):
    (workspace.root / "notes.txt").write_text("user work")
    tracked = workspace.root / "app.py"
    original = tracked.read_text()
    tracked.write_text("uncommitted user work")
    with pytest.raises(AuditError, match="tracked files"):
        start(workspace)
    tracked.write_text(original)
    started = start(workspace)
    assert (workspace.root / "notes.txt").read_text() == "user work"
    assert not (workspace.root / started["worktree"] / "notes.txt").exists()
    change(workspace, started)
    (workspace.root / started["worktree"] / "extra.py").write_text("pass")
    with pytest.raises(AuditError, match="outside.*scope"):
        patches.check(workspace, started["patch_id"])
    assert not patches.load(workspace, started["patch_id"])["checks"]


def test_failures_retries_budget_and_independent_reviews_cannot_be_relabelled(workspace):
    started = start(workspace, max_attempts=2)
    change(workspace, started, "# still broken\n")
    failed = patches.check(workspace, started["patch_id"])
    assert failed["state"] == "check_failed"
    assert len(patches.check(workspace, started["patch_id"])["checks"]) == 1
    with pytest.raises(AuditError, match="passing patch checks"):
        patches.review(workspace, started["patch_id"], passing_review(failed))
    with pytest.raises(AuditError, match="independently reviewed"):
        delivery.deliver(workspace, patch_id=started["patch_id"])
    change(workspace, started)
    checked = patches.check(workspace, started["patch_id"])
    review = passing_review(checked)
    with pytest.raises(AuditError, match="independent"):
        patches.review(workspace, started["patch_id"], {**review, "reviewer": "author"})
    rejected = patches.review(workspace, started["patch_id"], {**review, "verdict": "fail"})
    assert rejected["state"] == "review_rejected"
    assert patches.check(workspace, started["patch_id"])["state"] == "review_rejected"
    with pytest.raises(AuditError, match="passing patch checks"):
        patches.review(workspace, started["patch_id"], review)
    change(workspace, started, "def weather(city):\n    return city\n# adjusted\n")
    with pytest.raises(AuditError, match="budget exhausted"):
        patches.check(workspace, started["patch_id"])


@pytest.mark.parametrize(
    "command",
    [
        "import time; time.sleep(30)",
        "from pathlib import Path; Path('app.py').write_text('changed')",
    ],
)
def test_timeout_and_check_mutation_fail(workspace, command):
    started = start(
        workspace,
        checks=[{"id": "bounded", "argv": [sys.executable, "-c", command]}],
        timeout_seconds=1,
    )
    change(workspace, started)
    result = patches.check(workspace, started["patch_id"])
    assert result["state"] == "check_failed"
    assert (workspace.root / started["worktree"] / "app.py").read_text().endswith("return city\n")


def test_changed_source_and_tampered_evidence_block_review_and_delivery(workspace):
    started = start(workspace)
    change(workspace, started)
    checked = patches.check(workspace, started["patch_id"])
    review = passing_review(checked)
    with pytest.raises(AuditError, match="stale"):
        patches.review(workspace, started["patch_id"], {**review, "source_digest": "different"})
    change(workspace, started, "# changed after check\n")
    with pytest.raises(AuditError, match="changed after checks"):
        patches.review(workspace, started["patch_id"], review)
    change(workspace, started)
    accepted = patches.review(workspace, started["patch_id"], review)
    (workspace.root / accepted["checks"][-1]["artifact"]).write_text("{}")
    with pytest.raises(AuditError, match="checksum changed"):
        delivery.deliver(workspace, patch_id=started["patch_id"])


def test_changed_reviewed_branch_is_not_rewritten(workspace):
    result = reviewed(workspace)
    git(workspace.root, "update-ref", f"refs/heads/{result['branch']}", result["origin_revision"])
    with pytest.raises(AuditError, match="branch changed"):
        delivery.deliver(workspace, patch_id=result["patch_id"])


def test_empty_checks_require_recorded_reason_and_explicit_delivery_limit(workspace):
    with pytest.raises(AuditError, match="no_checks_reason"):
        start(workspace, checks=[])
    started = start(
        workspace,
        checks=[],
        no_checks_reason="Only a prose clarification is possible without runtime inputs.",
    )
    change(workspace, started, "# Clarify the expected timeout behavior\n")
    checked = patches.check(workspace, started["patch_id"])
    accepted = patches.review(workspace, started["patch_id"], passing_review(checked))
    shipped = delivery.deliver(workspace, patch_id=accepted["patch_id"])
    assert "No executable checks run" in Path(shipped["artifacts"]["pr_body"]).read_text()
    assert (
        json.loads(Path(shipped["artifacts"]["summary"]).read_text())["executable_checks_run"]
        is False
    )


def test_failed_patch_cannot_restart_under_a_no_checks_label(workspace):
    failed = start(workspace)
    change(workspace, failed, "# still broken\n")
    assert patches.check(workspace, failed["patch_id"])["state"] == "check_failed"
    renamed = start(workspace, checks=[], no_checks_reason="Unavailable evaluator")
    change(workspace, renamed, "# still broken\n")
    result = patches.check(workspace, renamed["patch_id"])
    assert result["state"] == "check_failed"
    assert "failed patch checks" in result["checks"][-1]["failure"]


def test_check_logs_are_bounded_and_credentials_are_redacted(workspace, monkeypatch):
    monkeypatch.setenv("PATCH_TEST_TOKEN", "private-credential")
    started = start(
        workspace,
        checks=[
            {
                "id": "output",
                "argv": [
                    sys.executable,
                    "-c",
                    "import os; print(os.environ['PATCH_TEST_TOKEN']); print('x' * 5000)",
                ],
            }
        ],
        max_output_bytes=128,
        env={"PATCH_TEST_TOKEN": "PATCH_TEST_TOKEN"},
    )
    change(workspace, started)
    checked = patches.check(workspace, started["patch_id"])
    output = workspace.read_artifact(checked["checks"][-1]["artifact"])["results"][0]
    assert "private-credential" not in output["stdout"]
    assert "[REDACTED]" in output["stdout"]
    assert len(output["stdout"].encode()) <= 128
    assert output["stdout_truncated"] is True


def test_interrupted_check_collects_same_durable_attempt_once(workspace, tmp_path, monkeypatch):
    counter = tmp_path / "counter.txt"
    command = f"from pathlib import Path; p=Path({str(counter)!r}); p.write_text(p.read_text() + 'x' if p.exists() else 'x')"
    started = start(
        workspace, checks=[{"id": "once", "argv": [sys.executable, "-c", command]}], max_attempts=1
    )
    change(workspace, started)
    execute = runners.execute

    def interrupted(*args, **kwargs):
        execute(*args, **kwargs)
        raise AuditError("simulated host interruption after worker completed")

    monkeypatch.setattr(runners, "execute", interrupted)
    with pytest.raises(AuditError, match="host interruption"):
        patches.check(workspace, started["patch_id"])
    assert patches.status(workspace, started["patch_id"])["state"] == "checking"
    monkeypatch.setattr(runners, "execute", execute)
    result = patches.check(workspace, started["patch_id"])
    assert result["state"] == "awaiting_review"
    assert len(result["checks"]) == 1
    assert counter.read_text() == "x"
    assert (
        patches.review(workspace, started["patch_id"], passing_review(result))["state"]
        == "reviewed_unmeasured"
    )


def test_check_only_runner_rejects_benchmarks_without_weakening_default_validation():
    request = {
        "timeout_seconds": 5,
        "commands": [
            {"id": "benchmark", "role": "benchmark", "argv": [sys.executable, "-c", "pass"]}
        ],
    }
    with pytest.raises(AuditError, match="only check commands"):
        runners._validate(request, checks_only=True)
    request["commands"][0]["role"] = "check"
    with pytest.raises(AuditError, match="exactly one benchmark"):
        runners._validate(request)
    runners._validate(request, checks_only=True)


def test_review_reconciles_branch_creation_before_lost_state_save(workspace, monkeypatch):
    started = start(workspace)
    change(workspace, started)
    checked = patches.check(workspace, started["patch_id"])
    value = passing_review(checked)
    save = patches._save

    def interrupted(workspace, data):
        if data["state"] == "reviewed_unmeasured":
            raise AuditError("simulated lost review save")
        save(workspace, data)

    monkeypatch.setattr(patches, "_save", interrupted)
    with pytest.raises(AuditError, match="lost review save"):
        patches.review(workspace, started["patch_id"], value)
    monkeypatch.setattr(patches, "_save", save)
    assert patches.review(workspace, started["patch_id"], value)["state"] == "reviewed_unmeasured"


def test_rejected_measured_source_cannot_be_exported_as_unmeasured(selected):
    workspace = selected["workspace"]
    data = store.load_run(workspace, selected["run_id"])
    data["candidates"][selected["candidate_id"]]["state"] = "rejected"
    store.save_run(workspace, data)
    with pytest.raises(AuditError, match="known failed experiment"):
        patches._known_failures(
            workspace,
            selected["candidate"]["source_digest"]
            if "candidate" in selected
            else data["candidates"][selected["candidate_id"]]["source_digest"],
        )


def test_cli_patch_and_delivery_registration(workspace, tmp_path):
    result = reviewed(workspace)
    prefix = ["--workspace", str(workspace.root), "fix"]
    runner = CliRunner()
    status = runner.invoke(main, [*prefix, "patch", "status", result["patch_id"]])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["state"] == "reviewed_unmeasured"
    delivered = runner.invoke(main, [*prefix, "deliver", "--patch", result["patch_id"]])
    assert delivered.exit_code == 0, delivered.output
    assert json.loads(delivered.output)["state"] == "prepared"
