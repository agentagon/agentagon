import json
from pathlib import Path

import pytest
import support.delivery as delivery_fixtures
from click.testing import CliRunner

from agentagon.cli.main import main
from agentagon.experiments import delivery, engine
from agentagon.storage.config import Config


def invoke(root, *arguments):
    return CliRunner().invoke(main, ["--workspace", str(root), *arguments])


def profile():
    return {
        "runner": {"kind": "local"},
        "env": {},
        "setup": [],
        "limits": {
            "max_candidates": 3,
            "max_trials": 24,
            "max_elapsed_seconds": 600,
            "parallel_candidates": 1,
            "parallel_trials": 1,
            "trial_timeout_seconds": 30,
        },
    }


def test_setup_saves_named_profile_outside_checkout_and_project_override(workspace, tmp_path):
    source = tmp_path / "profile.json"
    settings = profile()
    source.write_text(json.dumps(settings))
    result = invoke(
        tmp_path, "setup", "--scope", "user", "--profile", "local", "--profile-file", str(source)
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["settings"]["profiles"]["local"]["limits"]["max_trials"] == 24
    settings["limits"]["max_trials"] = 12
    source.write_text(json.dumps(settings))
    result = invoke(workspace.root, "setup", "--profile", "local", "--profile-file", str(source))
    assert result.exit_code == 0, result.output
    assert Config().profile(workspace.root, "local")["limits"]["max_trials"] == 12
    assert Config().profile(None, "local")["limits"]["max_trials"] == 24


@pytest.mark.parametrize(
    "arguments",
    [
        ["setup", "--profile", "local"],
        ["status", "--candidate", "candidate_any"],
        ["status", "--audit", "audit_any", "--run", "run_any"],
        ["dashboard", "audit_any", "--run", "run_any"],
        [
            "audit",
            "issues",
            "update",
            "issue_any",
            "--status",
            "resolved_verified",
            "--reason",
            "verified",
            "--run",
            "run_any",
        ],
    ],
)
def test_ambiguous_or_incomplete_selection_is_an_actionable_json_error(workspace, arguments):
    result = invoke(workspace.root, *arguments)
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["state"] == "error"
    assert payload["error"]


def test_setup_rejects_profile_and_settings_in_one_invocation(workspace, tmp_path):
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile()))
    result = invoke(
        workspace.root,
        "setup",
        "--profile",
        "local",
        "--profile-file",
        str(source),
        "--set",
        "traces.project",
        "demo",
    )
    assert result.exit_code == 1
    assert "cannot be combined" in json.loads(result.output)["error"]
    assert not Config().path.exists()


def test_manual_verification_file_cannot_mark_issue_verified(workspace, tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}")
    result = invoke(
        workspace.root,
        "audit",
        "issues",
        "update",
        "issue_any",
        "--status",
        "resolved_verified",
        "--reason",
        "reported success",
        "--verification",
        str(receipt),
    )
    assert result.exit_code == 1
    assert "manual verification receipts" in json.loads(result.output)["error"]
    assert not (workspace.state / "cases").exists()


def test_fix_input_json_is_rejected_before_run_creation(workspace, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text("{invalid")
    result = invoke(workspace.root, "fix", "start", "--spec", str(spec), "--profile", "local")
    assert result.exit_code == 1
    assert "invalid UTF-8 JSON" in json.loads(result.output)["error"]
    assert not (workspace.state / "runs").exists()


def test_fix_steer_rejects_invalid_json_before_loading_a_run(workspace, tmp_path):
    source = tmp_path / "control.json"
    source.write_text("{invalid")
    result = invoke(
        workspace.root, "fix", "steer", "run_" + "0" * 24, "--control-file", str(source)
    )
    assert result.exit_code == 1
    assert "invalid UTF-8 JSON" in json.loads(result.output)["error"]
    assert not (workspace.state / "runs").exists()


def test_fix_new_forwards_the_same_operation_identity_on_retry(workspace, monkeypatch):
    calls = []

    def new(target, run_id, **options):
        calls.append((target.root, run_id, options))
        return {"candidate_id": "candidate_" + "1" * 24, "operation_id": options["operation_id"]}

    monkeypatch.setattr(engine, "new", new)
    run_id = "run_" + "0" * 24
    parent_id = "candidate_" + "2" * 24
    arguments = [
        "fix",
        "new",
        run_id,
        "--parent",
        parent_id,
        "--hypothesis",
        "Cache repeated retrieval without changing quality",
        "--author",
        "active-host",
        "--round",
        "round_000002",
        "--operation-id",
        "reservation-with-stable-identity",
    ]
    first = invoke(workspace.root, *arguments)
    repeated = invoke(workspace.root, *arguments)
    assert first.exit_code == repeated.exit_code == 0
    assert json.loads(first.output) == json.loads(repeated.output)
    assert (
        calls
        == [
            (
                workspace.root,
                run_id,
                {
                    "parent_id": parent_id,
                    "hypothesis": "Cache repeated retrieval without changing quality",
                    "author": "active-host",
                    "round_id": "round_000002",
                    "operation_id": "reservation-with-stable-identity",
                },
            )
        ]
        * 2
    )


def test_fix_ship_cli_prepares_the_selected_candidate_without_network(selected, monkeypatch):
    original = delivery._command

    def local_only(root, argv, **options):
        assert argv[0] != "gh" and "push" not in argv and "ls-remote" not in argv
        return original(root, argv, **options)

    monkeypatch.setattr(delivery, "_command", local_only)
    result = invoke(selected["workspace"].root, "fix", "ship", selected["run_id"])
    assert result.exit_code == 0, result.output
    prepared = json.loads(result.output)
    assert prepared["state"] == "prepared"
    assert prepared["candidate_id"] == selected["candidate_id"]
    assert prepared["source_revision"] == selected["revision"]
    assert prepared["remote"] == "origin"
    assert prepared["base"] == selected["base"]
    assert all(Path(path).is_file() for path in prepared["artifacts"].values())
    assert "pr" not in prepared


def test_fix_ship_cli_publishes_explicit_options_and_reconciles_a_retry(selected, github):
    arguments = [
        "fix",
        "ship",
        selected["run_id"],
        "--candidate",
        selected["candidate_id"],
        "--remote",
        "origin",
        "--base",
        selected["base"],
        "--publish",
    ]
    first = invoke(selected["workspace"].root, *arguments)
    repeated = invoke(selected["workspace"].root, *arguments)
    assert first.exit_code == 0, first.output
    assert repeated.exit_code == 0, repeated.output
    published = json.loads(first.output)
    recovered = json.loads(repeated.output)
    assert published["state"] == recovered["state"] == "published"
    assert published["delivery_id"] == recovered["delivery_id"]
    assert published["candidate_id"] == selected["candidate_id"]
    assert published["pr"]["is_draft"] is True
    assert published["pr"]["url"] == recovered["pr"]["url"]
    assert len(delivery_fixtures.pushes(github)) == len(delivery_fixtures.creates(github)) == 1
