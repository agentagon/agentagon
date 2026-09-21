"""SQLite authority, project isolation, and atomic application updates."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentagon.core.records import AuditError
from agentagon.memory.store import MemoryGroups
from agentagon.storage.state import AppState


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


def test_registration_identity_survives_a_fresh_application_database(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()

    first = AppState(tmp_path / "first-application").register(str(root))
    second = AppState(tmp_path / "second-application").register(str(root))

    assert second["id"] == first["id"]


def test_fresh_application_database_reuses_built_in_improvement_memory(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    first_state = AppState(tmp_path / "first-application")
    first_project = first_state.register(str(root))["id"]
    first_memory = MemoryGroups(first_state)
    group = first_memory.improvement_groups(first_project)[0]
    entry = first_memory.record(
        first_project,
        group["id"],
        {"key": "verified-fix", "text": "Retain the regression check."},
    )

    second_state = AppState(tmp_path / "second-application")
    second_project = second_state.register(str(root))["id"]
    adopted = MemoryGroups(second_state).improvement_groups(second_project)

    assert second_project == first_project
    assert [item["id"] for item in adopted] == [group["id"]]
    assert MemoryGroups(second_state).recall(
        second_project, group["id"], "regression"
    )["entries"] == [entry]


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
            transaction.put_record(project["id"], "goals", "goal_one", {"name": "Quality"})
            transaction.put_record("project_" + "f" * 24, "goals", "goal_two", {})
    assert state.db.list_records(project["id"], "goals") == []
    with state.db.transaction() as transaction:
        one = transaction.put_record(project["id"], "goals", "goal_one", {"name": "Quality"})
        two = transaction.put_record(project["id"], "suites", "suite_one", {"goal_id": one["id"]})
    assert state.db.get_record(project["id"], "suites", two["id"])["goal_id"] == one["id"]


def test_domain_records_are_scoped_and_stale_revisions_fail(registered, tmp_path):
    state, project = registered
    root = tmp_path / "second"
    root.mkdir()
    second = state.register(str(root))
    record = state.db.put_record(project["id"], "goals", "goal_one", {"name": "Quality"})
    assert state.db.get_record(second["id"], "goals", record["id"]) is None
    assert state.db.list_records(second["id"], "goals") == []
    with pytest.raises(AuditError, match="another project"):
        state.db.put_record(second["id"], "goals", record["id"], record)
    other = AppState(state.directory)
    updated = other.db.put_record(
        project["id"], "goals", record["id"], {**record, "name": "Latency"}
    )
    assert updated["revision"] == 2
    with pytest.raises(AuditError, match="changed"):
        state.db.put_record(project["id"], "goals", record["id"], record)
    assert state.db.get_record(project["id"], "goals", record["id"])["name"] == "Latency"


def test_settings_and_connection_owner_commit_together_and_only_store_references(registered):
    state, project = registered
    connection = {
        "id": "connection_one",
        "project_id": project["id"],
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
    assert state.read()["connections"] == {}


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


@pytest.mark.parametrize("owner", [None, [], "project_" + "0" * 24])
def test_connections_require_one_registered_project_owner(registered, owner):
    state, _ = registered
    with pytest.raises(AuditError, match="one registered project owner"):
        with state.locked() as data:
            data["connections"]["connection_one"] = {"id": "connection_one", "project_id": owner}
    assert state.read()["connections"] == {}


def test_unsupported_saved_connection_format_fails_on_read_without_rewriting(registered):
    state, project = registered
    payload = json.dumps({"id": "connection_one", "project_ids": [project["id"]]})
    with state.db.transaction() as transaction:
        transaction.connection.execute(
            "INSERT INTO connections(id, payload) VALUES (?, ?)", ("connection_one", payload)
        )
    with pytest.raises(AuditError, match="unsupported saved connection format"):
        AppState(state.directory).read()
    with state.db.transaction() as transaction:
        assert (
            transaction.connection.execute("SELECT payload FROM connections").fetchone()["payload"]
            == payload
        )
