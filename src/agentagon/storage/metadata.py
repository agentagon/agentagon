"""Transactional local application metadata; immutable evidence stays on disk."""

import copy
import json
import os
import re
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from agentagon.core.records import AuditError, encoded, now

_PROJECT = re.compile(r"project_[a-f0-9]{24}")
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}")
_REFERENCE = re.compile(r"(?:env:[A-Za-z_][A-Za-z0-9_]*|(?:session|keyring):[a-f0-9]{32})")
_AGENTS = {"default_agent": "codex", "model": "", "concurrency": 1}
_SCHEMA = (
    """CREATE TABLE projects (
        id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1,
        payload TEXT NOT NULL)""",
    """CREATE TABLE settings (name TEXT PRIMARY KEY, payload TEXT NOT NULL)""",
    """CREATE TABLE connections (id TEXT PRIMARY KEY, payload TEXT NOT NULL)""",
    """CREATE TABLE records (
        project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL, id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0), created_at TEXT NOT NULL,
        payload TEXT NOT NULL, PRIMARY KEY (project_id, kind, id))""",
    "CREATE INDEX records_listing ON records(project_id, kind, created_at DESC, id)",
)


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise AuditError("invalid application record identifier")
    return value


def _references(record):
    credentials = record.get("credentials", {})
    if not isinstance(credentials, dict) or any(
        not isinstance(value, str) or not _REFERENCE.fullmatch(value)
        for value in credentials.values()
    ):
        raise AuditError("application credentials must be secret references")
    reference = record.get("claude_api_key_ref")
    if reference is not None and (
        not isinstance(reference, str) or not _REFERENCE.fullmatch(reference)
    ):
        raise AuditError("application credentials must be secret references")


class MetadataTransaction:
    """Project-scoped operations sharing one short SQLite transaction."""

    def __init__(self, connection):
        self.connection = connection

    def project(self, project_id):
        if not isinstance(project_id, str) or not _PROJECT.fullmatch(project_id):
            raise AuditError("invalid project identifier")
        row = self.connection.execute(
            "SELECT payload FROM projects WHERE id = ? AND active = 1", (project_id,)
        ).fetchone()
        if row is None:
            raise AuditError("project is not registered")
        return json.loads(row["payload"])

    def project_for_path(self, path):
        row = self.connection.execute(
            "SELECT payload FROM projects WHERE path = ? AND active = 1", (str(path),)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def register(self, path):
        row = self.connection.execute(
            "SELECT payload FROM projects WHERE path = ?", (str(path),)
        ).fetchone()
        if row:
            project = json.loads(row["payload"])
            self.connection.execute("UPDATE projects SET active = 1 WHERE id = ?", (project["id"],))
            return project
        project = {
            "id": "project_" + uuid.uuid4().hex[:24],
            "path": str(path),
            "name": Path(path).name,
            "created_at": now(),
        }
        self.connection.execute(
            "INSERT INTO projects(id, path, payload) VALUES (?, ?, ?)",
            (project["id"], project["path"], encoded(project)),
        )
        return project

    def read_state(self):
        projects = {
            row["id"]: json.loads(row["payload"])
            for row in self.connection.execute("SELECT id, payload FROM projects WHERE active = 1")
        }
        connections = {
            row["id"]: json.loads(row["payload"])
            for row in self.connection.execute("SELECT id, payload FROM connections")
        }
        row = self.connection.execute(
            "SELECT payload FROM settings WHERE name = 'agents'"
        ).fetchone()
        return {
            "version": 1,
            "projects": projects,
            "connections": connections,
            "agents": json.loads(row["payload"]) if row else copy.deepcopy(_AGENTS),
        }

    def replace_state(self, data):
        if not isinstance(data, dict) or set(data) != {
            "version",
            "projects",
            "connections",
            "agents",
        }:
            raise AuditError("invalid application settings")
        if data["version"] != 1:
            raise AuditError("unsupported application settings")
        projects = data["projects"]
        if not isinstance(projects, dict) or not isinstance(data["connections"], dict):
            raise AuditError("invalid application settings")
        # Registration owns IDs and paths. A settings update cannot move a checkout.
        for project_id, project in projects.items():
            saved = self.project(project_id)
            if project.get("id") != project_id or project.get("path") != saved["path"]:
                raise AuditError("registered project identity cannot be changed")
            self.connection.execute(
                "UPDATE projects SET payload = ? WHERE id = ?", (encoded(project), project_id)
            )
        for row in self.connection.execute("SELECT id FROM projects WHERE active = 1").fetchall():
            if row["id"] not in projects:
                self.connection.execute("UPDATE projects SET active = 0 WHERE id = ?", (row["id"],))
        self.connection.execute("DELETE FROM connections")
        for connection_id, connection in data["connections"].items():
            _name(connection_id)
            if connection.get("id") != connection_id:
                raise AuditError("connection identity does not match its record")
            _references(connection)
            assigned = connection.get("project_ids", [])
            if not isinstance(assigned, list) or any(
                project not in projects for project in assigned
            ):
                raise AuditError("connection assignment requires a registered project")
            self.connection.execute(
                "INSERT INTO connections(id, payload) VALUES (?, ?)",
                (connection_id, encoded(connection)),
            )
        if not isinstance(data["agents"], dict):
            raise AuditError("invalid coding-agent settings")
        _references(data["agents"])
        self.connection.execute(
            "INSERT INTO settings(name, payload) VALUES ('agents', ?) "
            "ON CONFLICT(name) DO UPDATE SET payload = excluded.payload",
            (encoded(data["agents"]),),
        )

    def get_record(self, project_id, kind, record_id):
        self.project(project_id)
        row = self.connection.execute(
            "SELECT payload FROM records WHERE project_id = ? AND kind = ? AND id = ?",
            (project_id, _name(kind), _name(record_id)),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_records(self, project_id, kind):
        self.project(project_id)
        return [
            json.loads(row["payload"])
            for row in self.connection.execute(
                "SELECT payload FROM records WHERE project_id = ? AND kind = ? "
                "ORDER BY created_at DESC, id DESC",
                (project_id, _name(kind)),
            )
        ]

    def put_record(self, project_id, kind, record_id, payload, expected_revision=None):
        self.project(project_id)
        _name(kind)
        _name(record_id)
        if not isinstance(payload, dict):
            raise AuditError("application record must be an object")
        if (
            payload.get("id", record_id) != record_id
            or payload.get("project_id", project_id) != project_id
        ):
            raise AuditError("application record belongs to another project or identifier")
        expected = payload.get("revision", 0) if expected_revision is None else expected_revision
        if type(expected) is not int or expected < 0:
            raise AuditError("invalid application record revision")
        current = self.get_record(project_id, kind, record_id)
        if (current["revision"] if current else 0) != expected:
            raise AuditError("application record changed; reload before updating")
        saved = copy.deepcopy(payload)
        saved.update(id=record_id, project_id=project_id, revision=expected + 1, updated_at=now())
        saved["created_at"] = current["created_at"] if current else saved.get("created_at", now())
        body = encoded(saved)
        if len(body.encode()) > 20_000_000:
            raise AuditError("application record exceeds the metadata size limit")
        self.connection.execute(
            "INSERT INTO records(project_id, kind, id, revision, created_at, payload) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, kind, id) DO UPDATE SET "
            "revision = excluded.revision, payload = excluded.payload",
            (project_id, kind, record_id, saved["revision"], saved["created_at"], body),
        )
        return saved

    def delete_record(self, project_id, kind, record_id, expected_revision=None):
        current = self.get_record(project_id, kind, record_id)
        if current is None:
            return False
        if expected_revision is not None and current["revision"] != expected_revision:
            raise AuditError("application record changed; reload before deleting")
        self.connection.execute(
            "DELETE FROM records WHERE project_id = ? AND kind = ? AND id = ?",
            (project_id, kind, record_id),
        )
        return True


class MetadataStore:
    """One private database, rollback journaling, and no legacy state import."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._check_paths()
        try:
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            if not stat.S_ISREG(self.path.stat().st_mode):
                raise AuditError("application database must be a regular file") from None
        else:
            os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self._transaction(write=True) as transaction:
            connection = transaction.connection
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise AuditError("unsupported application database version")
            if version == 0:
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")

    def _check_paths(self):
        paths = [self.path, self.path.with_name(self.path.name + "-journal")]
        paths += [self.path.with_name(self.path.name + suffix) for suffix in ("-wal", "-shm")]
        if any(path.is_symlink() for path in [*paths, *self.path.parents]):
            raise AuditError("application database cannot use symlinks")

    @contextmanager
    def _transaction(self, *, write):
        self._check_paths()
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield MetadataTransaction(connection)
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            raise AuditError(
                "application database operation failed; retry after active updates finish"
            ) from exc
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def transaction(self):
        return self._transaction(write=True)

    def read_state(self):
        with self._transaction(write=False) as transaction:
            return transaction.read_state()

    def project(self, project_id):
        with self._transaction(write=False) as transaction:
            return transaction.project(project_id)

    def project_for_path(self, path):
        with self._transaction(write=False) as transaction:
            return transaction.project_for_path(path)

    def get_record(self, project_id, kind, record_id):
        with self._transaction(write=False) as transaction:
            return transaction.get_record(project_id, kind, record_id)

    def list_records(self, project_id, kind):
        with self._transaction(write=False) as transaction:
            return transaction.list_records(project_id, kind)

    def put_record(self, project_id, kind, record_id, payload, expected_revision=None):
        with self.transaction() as transaction:
            return transaction.put_record(project_id, kind, record_id, payload, expected_revision)

    def delete_record(self, project_id, kind, record_id, expected_revision=None):
        with self.transaction() as transaction:
            return transaction.delete_record(project_id, kind, record_id, expected_revision)
