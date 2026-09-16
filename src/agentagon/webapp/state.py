"""User-local SQLite registration and private application artifact paths."""

import copy
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from agentagon.core.records import AuditError, encoded, load_json
from agentagon.storage.config import Config
from agentagon.storage.metadata import MetadataStore
from agentagon.storage.workspace import Workspace


def identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[a-f0-9]{24}", value):
        raise AuditError(f"invalid {prefix} identifier")
    return value


def atomic_write(path, value):
    path = Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise AuditError("application state cannot use symlinks")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class AppState:
    def __init__(self, directory=None):
        config = Config().path
        self.directory = (
            Path(directory or os.environ.get("AGENTAGON_APP_STATE") or config.with_suffix(".app"))
            .expanduser()
            .absolute()
        )
        # Resolve normal macOS /tmp aliases, but reject a linked state directory itself.
        if self.directory.is_symlink():
            raise AuditError("application state cannot be a symlink")
        self.directory = self.directory.parent.resolve() / self.directory.name
        self.path = self.directory / "app.sqlite3"
        self.db = MetadataStore(self.path)

    def read(self):
        return self.db.read_state()

    @contextmanager
    def locked(self):
        with self.db.transaction() as transaction:
            data = transaction.read_state()
            yield data
            transaction.replace_state(data)

    def register(self, path):
        if not isinstance(path, str) or not path.strip() or len(path) > 4096:
            raise AuditError("provide an existing local project directory")
        workspace = Workspace(Path(path))
        with self.db.transaction() as transaction:
            project = transaction.register(workspace.root)
        return copy.deepcopy(project)

    def project_id(self, root):
        project = self.db.project_for_path(Workspace(Path(root)).root)
        if project is None:
            raise AuditError("project is not registered")
        return project["id"]

    def project(self, project_id):
        identifier(project_id, "project")
        return self.db.project(project_id)

    def workspace(self, project_id):
        project = self.project(project_id)
        workspace = Workspace(Path(project["path"]))
        if str(workspace.root) != project["path"]:
            raise AuditError("project root changed; register its new location explicitly")
        workspace.project_id = project_id
        workspace.metadata_store = self.db
        return workspace

    def remove(self, project_id):
        self.project(project_id)
        with self.locked() as data:
            del data["projects"][project_id]
            for connection in data["connections"].values():
                connection["project_ids"] = [
                    key for key in connection["project_ids"] if key != project_id
                ]
        return {"removed": project_id, "evidence_preserved": True}


def private_directory(workspace, name):
    directory = workspace.checked(workspace.state / "webapp" / name)
    if directory.is_symlink():
        raise AuditError("application records cannot use symlinks")
    return directory


def list_records(workspace, name):
    directory = private_directory(workspace, name)
    return [
        load_json(workspace.checked(path))
        for path in sorted(directory.glob("*.json"), reverse=True)
        if not path.is_symlink()
    ]
