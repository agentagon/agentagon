"""Immutable evidence and atomic audit records in a local source directory."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agentagon.core.records import CONTRACT_VERSION, AuditError, encoded, load_json, now
from agentagon.storage.changes import (
    EXCLUDED,
    GIT_TIMEOUT_SECONDS,
    git_bytes,
    revision,
    snapshot_changes,
    verify_change,
)

SAFE_ID = re.compile(r"^[a-z]+_[0-9a-f]{24}$")


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if result.returncode:
        raise AuditError(
            "a local Git checkout is required"
            if args[0] == "rev-parse"
            else "unable to read checkout metadata"
        )
    return result.stdout


class Workspace:
    def __init__(self, path: Path) -> None:
        path = path.expanduser().resolve()
        if not path.is_dir():
            raise AuditError("workspace directory does not exist")
        root = git_bytes(path, "rev-parse", "--show-toplevel", optional=True)
        self.is_git = root is not None
        self.root = Path(os.fsdecode(root).strip()).resolve() if self.is_git else path
        self.state = self.root / ".agentagon"
        if self.state.is_symlink():
            raise AuditError(".agentagon must not be a symlink")

    def initialize(self) -> dict:
        if (self.state / "config.json").exists():
            raise AuditError(
                "unsupported workspace: legacy .agentagon/config.json; start with a fresh .agentagon directory"
            )
        if self.is_git:
            self._exclude_state_from_git()
        if not self.state.exists():
            self.state.mkdir(mode=0o700)
        config = self.state / "workspace.json"
        if not config.exists():
            self.write(config, {"contract_version": CONTRACT_VERSION, "created_at": now()})
        self.require_initialized()
        return {
            "workspace": str(self.root),
            "state_path": str(self.state),
            "contract_version": CONTRACT_VERSION,
        }

    def _exclude_state_from_git(self) -> None:
        if git(self.root, "ls-files", "--", ".agentagon").strip():
            raise AuditError(
                ".agentagon is already tracked by Git; remove private state from the index before initializing"
            )
        ignore = Path(git(self.root, "rev-parse", "--git-path", "info/exclude").strip())
        if not ignore.is_absolute():
            ignore = self.root / ignore
        if ignore.is_symlink():
            raise AuditError("Git local exclude must not be a symlink")
        if ignore.parent.is_symlink():
            raise AuditError("Git local exclude directory must not be a symlink")
        existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        if "/.agentagon/" not in existing.splitlines():
            ignore.parent.mkdir(parents=True, exist_ok=True)
            ignore.write_text(
                existing
                + ("\n" if existing and not existing.endswith("\n") else "")
                + "/.agentagon/\n",
                encoding="utf-8",
            )
        ignored = subprocess.run(
            ["git", "-C", str(self.root), "check-ignore", "-q", "--", ".agentagon/"],
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        if ignored.returncode:
            raise AuditError(
                "Git ignore rules do not exclude .agentagon/; remove the conflicting "
                "negation from .gitignore before initializing private state"
            )

    def require_initialized(self) -> None:
        if (self.state / "config.json").exists():
            raise AuditError(
                "unsupported workspace: legacy .agentagon/config.json; start with a fresh .agentagon directory"
            )
        config = self.state / "workspace.json"
        if not config.exists():
            raise AuditError("run agentagon init first")
        if load_json(self.checked(config)).get("contract_version") != CONTRACT_VERSION:
            raise AuditError("unsupported workspace contract version")

    def checked(self, path: Path) -> Path:
        if not path.resolve().is_relative_to(self.state.resolve()):
            raise AuditError("artifact escapes .agentagon")
        return path

    def write(self, path: Path, value: Any) -> None:
        self.write_bytes(path, (encoded(value) + "\n").encode())

    def write_bytes(self, path: Path, data: bytes) -> None:
        self.checked(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()

    def artifact(self, value: Any, suffix: str = ".json") -> str:
        data = (encoded(value) + "\n").encode()
        return self.blob(data, suffix)

    def blob(self, data: bytes, suffix: str = ".txt") -> str:
        path = self.state / "evidence" / (hashlib.sha256(data).hexdigest() + suffix)
        if not path.exists():
            self.write_bytes(path, data)
        elif path.read_bytes() != data:
            raise AuditError("immutable evidence was modified")
        return str(path.relative_to(self.root))

    def read_artifact(self, relative: str) -> Any:
        path = self.checked(self.root / relative)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != path.stem:
            raise AuditError("evidence checksum changed")
        return json.loads(data)

    def audit_path(self, audit_id: str) -> Path:
        if not SAFE_ID.fullmatch(audit_id) or not audit_id.startswith("audit_"):
            raise AuditError("invalid audit ID")
        return self.checked(self.state / "audits" / audit_id / "state.json")

    def read_audit(self, audit_id: str) -> dict:
        self.require_initialized()
        path = self.audit_path(audit_id)
        if not path.exists():
            raise AuditError("audit not found")
        state = load_json(path)
        if state.get("contract_version") != CONTRACT_VERSION:
            raise AuditError("incompatible audit contract version")
        if "goal" not in state:
            raise AuditError("incompatible audit state: goal is missing; start a new audit")
        return state

    def save_audit(self, state: dict) -> None:
        self.write(self.audit_path(state["audit_id"]), state)

    def audits(self) -> list[dict]:
        self.require_initialized()
        return [
            self.read_audit(path.parent.name)
            for path in sorted((self.state / "audits").glob("*/state.json"))
        ]

    @contextmanager
    def locked(self):
        """Serialize local writers. A stale lock is explicit, never stolen."""
        self.require_initialized()
        path = self.checked(self.state / "write.lock")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise AuditError(
                "another writer holds .agentagon/write.lock; if its process exited, remove the stale lock and retry"
            ) from exc
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(str(os.getpid()))
            yield
        finally:
            path.unlink(missing_ok=True)

    def changes(self, scopes: list[str] | None = None) -> dict:
        """Inspect local changes before initialization without writing state."""
        if not self.is_git:
            raise AuditError(
                "changes reviews require a Git checkout; use ag:audit for this codebase"
            )
        return snapshot_changes(self.root, scopes or [])

    def _source_names(self) -> list[str]:
        if self.is_git:
            return git(
                self.root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
            ).split("\0")
        names = []

        def unreadable(error: OSError) -> None:
            raise error

        for directory, subdirs, files in os.walk(self.root, onerror=unreadable):
            parent = Path(directory)
            names.extend(
                str((parent / name).relative_to(self.root))
                for name in subdirs
                if name not in EXCLUDED and (parent / name).is_symlink()
            )
            subdirs[:] = [
                name
                for name in subdirs
                if name not in EXCLUDED and not (parent / name).is_symlink()
            ]
            names.extend(str((parent / name).relative_to(self.root)) for name in files)
        return names

    def snapshot(self, scopes: list[str], code_scope: str = "full") -> dict:
        if code_scope == "changes":
            return self.changes(scopes)
        if code_scope != "full":
            raise AuditError("code scope must be full or changes")
        head = revision(self.root) if self.is_git else None
        local_changes = (
            bool(
                git_bytes(
                    self.root,
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                    "--ignore-submodules=none",
                )
            )
            if self.is_git
            else None
        )
        names = self._source_names()
        selected = [self.root / scope for scope in scopes] if scopes else [self.root]
        for path in selected:
            if not path.resolve().is_relative_to(self.root) or not path.exists():
                raise AuditError("code scope must exist inside the workspace")
        files, skipped = [], []
        for name in sorted(set(names)):
            if not name:
                continue
            path = self.root / name
            if any(part in EXCLUDED for part in path.relative_to(self.root).parts):
                continue
            if not any(path == scope or path.is_relative_to(scope) for scope in selected):
                continue
            if path.name.startswith(".env") or path.suffix in {".pem", ".key", ".p12"}:
                skipped.append({"path": name, "reason": "credential_file"})
                continue
            if (
                not path.is_file()
                or any(
                    parent.is_symlink()
                    for parent in (path, *path.parents)
                    if parent.is_relative_to(self.root)
                )
                or not path.resolve().is_relative_to(self.root)
                or path.stat().st_size > 2_000_000
            ):
                skipped.append({"path": name, "reason": "missing_symlink_or_large"})
                continue
            data = path.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeError:
                skipped.append({"path": name, "reason": "binary"})
                continue
            if "\x00" in text:
                skipped.append({"path": name, "reason": "binary"})
                continue
            files.append(
                {
                    "path": name,
                    "digest": hashlib.sha256(data).hexdigest(),
                    "lines": len(text.splitlines()),
                }
            )
        if self.is_git and revision(self.root) != head:
            raise AuditError("Git baseline changed while capturing code; start a new audit")
        return {
            "code_scope": "full",
            "revision": head,
            "local_changes": local_changes,
            "files": files,
            "skipped": skipped,
            "scopes": scopes or ["."],
        }

    def verify_snapshot(self, snapshot: dict) -> None:
        if snapshot.get("code_scope") == "full":
            current = self.snapshot(snapshot["scopes"])
            if current["revision"] != snapshot["revision"]:
                raise AuditError("Git baseline changed; start a new audit")
            if (
                current["files"] != snapshot["files"]
                or current["skipped"] != snapshot["skipped"]
                or current["local_changes"]
                != snapshot.get("local_changes", current["local_changes"])
            ):
                raise AuditError("code input changed; start a new audit")
        elif snapshot.get("code_scope") == "changes":
            current = self.changes(snapshot["scopes"])
            if current["fingerprint"] != snapshot["fingerprint"]:
                raise AuditError("local changes changed; start a new review")

    def verify_code(self, evidence: dict) -> None:
        if "change" in evidence:
            verify_change(self.root, evidence["change"])
            return
        path = self.root / evidence["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(self.root) or not path.is_file():
            raise AuditError("code evidence no longer exists in the checkout; start a new audit")
        if hashlib.sha256(path.read_bytes()).hexdigest() != evidence["digest"]:
            raise AuditError(f"code input changed: {evidence['path']}; start a new audit")
