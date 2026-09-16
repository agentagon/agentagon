"""SQLite authority, project isolation, and atomic application updates."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentagon.core.records import AuditError
from agentagon.webapp.state import AppState


@pytest.fixture
def registered(tmp_path):
    state = AppState(tmp_path / "application")
    root = tmp_path / "checkout"
    root.mkdir()
    project = state.register(str(root))
    return state, project


def test_database_is_private_sole_authority_and_ignores_old_json(tmp_path):
    directory = tmp_path / "application"
    directory.mkdir()
    legacy = directory / "state.json"
    legacy.write_text('{"version":999,"projects":{"legacy":{}}}')
    state = AppState(directory)
    assert state.read()["projects"] == {}
    assert state.path.read_bytes().startswith(b"SQLite format 3")
    assert state.path.stat().st_mode & 0o777 == 0o600
    assert legacy.read_text() == '{"version":999,"projects":{"legacy":{}}}'
    with sqlite3.connect(state.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert not (directory / "state.lock").exists()


def test_registration_canonical_path_restarts_and_reactivation_keep_id(registered):
    state, project = registered
    alias = state.directory.parent / "alias"
    alias.symlink_to(project["path"], target_is_directory=True)
    assert state.register(str(alias))["id"] == project["id"]
    restarted = AppState(state.directory)
    assert restarted.project_id(alias) == project["id"]
    state.remove(project["id"])
    assert restarted.read()["projects"] == {}
    assert restarted.register(project["path"])["id"] == project["id"]
    workspace = restarted.workspace(project["id"])
    assert workspace.project_id == project["id"]
    assert workspace.metadata_store is restarted.db


def test_database_rejects_previous_format_without_migration(registered):
    state, _ = registered
    with sqlite3.connect(state.path) as connection:
        connection.execute("PRAGMA user_version = 1")
    with pytest.raises(AuditError, match="choose a new AGENTAGON_APP_STATE directory"):
        AppState(state.directory)


def test_concurrent_registration_has_one_opaque_identity(registered):
    state, project = registered
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(
            pool.map(lambda _: AppState(state.directory).register(project["path"]), range(8))
        )
    assert {record["id"] for record in records} == {project["id"]}
    assert len(state.read()["projects"]) == 1


def test_git_worktrees_are_distinct_checkouts(registered):
    import subprocess

    state, project = registered
    root = project["path"]
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            root,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "Initial",
        ],
        check=True,
        capture_output=True,
    )
    worktree = state.directory.parent / "worktree"
    subprocess.run(
        ["git", "-C", root, "worktree", "add", "--detach", str(worktree)],
        check=True,
        capture_output=True,
    )
    second = state.register(str(worktree))
    assert second["id"] != project["id"]
    assert state.register(root)["id"] == project["id"]


def test_domain_transaction_rolls_back_all_updates(registered):
    state, project = registered
    with pytest.raises(AuditError, match="not registered"):
        with state.db.transaction() as transaction:
            transaction.put_record(project["id"], "focuses", "focus_one", {"name": "Quality"})
            transaction.put_record("project_" + "f" * 24, "focuses", "focus_two", {})
    assert state.db.list_records(project["id"], "focuses") == []
    with state.db.transaction() as transaction:
        one = transaction.put_record(project["id"], "focuses", "focus_one", {"name": "Quality"})
        two = transaction.put_record(project["id"], "suites", "suite_one", {"focus_id": one["id"]})
    assert state.db.get_record(project["id"], "suites", two["id"])["focus_id"] == one["id"]


def test_domain_records_are_scoped_and_stale_revisions_fail(registered, tmp_path):
    state, project = registered
    root = tmp_path / "second"
    root.mkdir()
    second = state.register(str(root))
    record = state.db.put_record(project["id"], "focuses", "focus_one", {"name": "Quality"})
    assert state.db.get_record(second["id"], "focuses", record["id"]) is None
    assert state.db.list_records(second["id"], "focuses") == []
    with pytest.raises(AuditError, match="another project"):
        state.db.put_record(second["id"], "focuses", record["id"], record)
    other = AppState(state.directory)
    updated = other.db.put_record(
        project["id"], "focuses", record["id"], {**record, "name": "Latency"}
    )
    assert updated["revision"] == 2
    with pytest.raises(AuditError, match="changed"):
        state.db.put_record(project["id"], "focuses", record["id"], record)
    assert state.db.get_record(project["id"], "focuses", record["id"])["name"] == "Latency"


def test_settings_and_assignments_commit_together_and_only_store_references(registered):
    state, project = registered
    connection = {
        "id": "connection_one",
        "project_ids": [project["id"]],
        "credentials": {"api_key": "env:PROVIDER_KEY"},
    }
    with state.locked() as data:
        data["agents"]["models"]["codex"] = "selected-model"
        data["connections"][connection["id"]] = connection
    saved = state.read()
    with pytest.raises(AuditError, match="secret references"):
        with state.locked() as data:
            data["agents"]["models"]["codex"] = "should-roll-back"
            data["connections"][connection["id"]]["credentials"]["api_key"] = "plaintext-secret"
    assert state.read() == saved
    assert b"plaintext-secret" not in state.path.read_bytes()
    state.remove(project["id"])
    assert state.read()["connections"][connection["id"]]["project_ids"] == []


def test_settings_cannot_move_registered_checkout(registered):
    state, project = registered
    with pytest.raises(AuditError, match="identity cannot be changed"):
        with state.locked() as data:
            data["projects"][project["id"]]["path"] = str(state.directory)
    assert state.project(project["id"])["path"] == project["path"]


def test_database_symlink_rejected_without_touching_target(tmp_path):
    target = tmp_path / "private.json"
    target.write_text(json.dumps({"keep": True}))
    directory = tmp_path / "application"
    directory.mkdir()
    (directory / "app.sqlite3").symlink_to(target)
    with pytest.raises(AuditError, match="symlink"):
        AppState(directory)
    assert json.loads(target.read_text()) == {"keep": True}
