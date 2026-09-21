"""Local memory groups. Registry metadata and immutable entry revisions have one owner."""

import fcntl
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from agentagon.capabilities.traces.normalize import redact
from agentagon.core.records import AuditError, digest, encoded, identifier, load_json, now
from agentagon.storage.state import atomic_write

MAX_ENTRY_BYTES = 32000
MAX_ENTRIES = 2000


class MemoryStore(Protocol):
    def entries(self) -> list[dict]: ...
    def record(self, payload: dict) -> dict: ...


class FolderStore:
    def __init__(self, group):
        self.group = group
        self.root = Path(group["path"])
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise AuditError("memory folder moved or became a symlink")
        marker = self.root / ".agentagon-memory.json"
        if marker.is_symlink() or load_json(marker) != {"version": 1, "group_id": group["id"]}:
            raise AuditError("memory folder identity changed")

    @contextmanager
    def locked(self):
        with os.fdopen(
            os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w"
        ) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def revisions(self, entry_id=None):
        if entry_id is not None:
            identifier(entry_id, "memory")
        revisions = []
        for index, path in enumerate(sorted(self.root.glob("entry-*.json"))):
            if index >= MAX_ENTRIES:
                raise AuditError("memory group exceeds its 2000 revision bound")
            if path.is_symlink() or path.stat().st_size > MAX_ENTRY_BYTES * 2:
                raise AuditError("invalid memory entry file")
            record = load_json(path)
            content = {k: v for k, v in record.items() if k != "digest"}
            if record.get("group_id") != self.group["id"] or digest(content) != record.get(
                "digest"
            ):
                raise AuditError("memory entry integrity check failed")
            if entry_id is None or record["id"] == entry_id:
                revisions.append(record)
        revisions.sort(key=lambda record: (record["id"], record["version"]))
        return revisions

    def entries(self):
        latest = {}
        for record in self.revisions():
            if record["id"] not in latest or record["version"] > latest[record["id"]]["version"]:
                latest[record["id"]] = record
        return list(latest.values())

    def record(self, payload):
        if not isinstance(payload, dict) or set(payload) - {
            "key",
            "text",
            "evidence",
            "uncertainty",
            "expected_version",
            "status",
            "revision_kind",
            "revision_note",
        }:
            raise AuditError("unsupported memory entry fields")
        key, text = payload.get("key"), payload.get("text")
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 200
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise AuditError("memory requires a stable key and text")
        evidence = payload.get("evidence", [])
        if (
            not isinstance(evidence, list)
            or len(evidence) > 100
            or any(not isinstance(e, str) or len(e) > 4096 for e in evidence)
        ):
            raise AuditError("memory evidence must be bounded references")
        if not isinstance(payload.get("uncertainty", ""), str):
            raise AuditError("memory uncertainty must be text")
        status = payload.get("status", "active")
        revision_kind = payload.get("revision_kind", "recorded")
        revision_note = payload.get("revision_note", "")
        if status not in {"active", "outdated"}:
            raise AuditError("memory status must be active or outdated")
        if revision_kind not in {"recorded", "note", "outdated", "revised"}:
            raise AuditError("unsupported memory revision kind")
        if (
            not isinstance(revision_note, str)
            or len(revision_note) > 4000
            or (revision_kind != "recorded" and not revision_note.strip())
        ):
            raise AuditError("memory correction requires a bounded revision note")
        if revision_kind == "outdated" and status != "outdated":
            raise AuditError("an outdated revision must mark the memory entry outdated")
        if revision_kind == "revised" and status != "active":
            raise AuditError("a revised memory entry must be active")
        expected_version = payload.get("expected_version")
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 0
        ):
            raise AuditError("memory expected version must be a non-negative integer")
        if len(encoded(payload).encode()) > MAX_ENTRY_BYTES:
            raise AuditError("memory entry exceeds 32 KB")
        entry_id = identifier("memory", self.group["id"], key)
        with self.locked():
            existing = next((e for e in self.entries() if e["id"] == entry_id), None)
            version = existing["version"] if existing else 0
            clean = redact(
                {
                    "key": key,
                    "text": text,
                    "evidence": evidence,
                    "uncertainty": payload.get("uncertainty", ""),
                    "status": status,
                    "revision_kind": revision_kind,
                    "revision_note": revision_note.strip(),
                }
            )
            if expected_version is not None and expected_version != version:
                raise AuditError("memory entry changed; reload before updating")
            if (
                existing
                and expected_version is None
                and all(existing.get(k) == v for k, v in clean.items())
            ):
                return existing
            if sum(1 for _ in self.root.glob("entry-*.json")) >= MAX_ENTRIES:
                raise AuditError("memory group has reached its revision bound")
            entry = {
                **clean,
                "id": entry_id,
                "group_id": self.group["id"],
                "version": version + 1,
                "created_at": now(),
            }
            entry["digest"] = digest(entry)
            atomic_write(
                self.root / f"entry-{entry_id}-{entry['version']:08}-{entry['digest']}.json", entry
            )
            return entry


class MemoryGroups:
    def __init__(self, state):
        self.state = state

    def list(self, project_id, agent_id=None, purpose=None):
        self.state.project(project_id)
        groups = []
        for owner in self.state.read()["projects"]:
            for group in self.state.db.list_records(owner, "memory_groups"):
                if project_id not in group["project_ids"]:
                    continue
                if agent_id and group["agent_ids"] and agent_id not in group["agent_ids"]:
                    continue
                if purpose and purpose != group["purpose"]:
                    continue
                groups.append(group)
        return groups

    def get(self, project_id, group_id, agent_id=None, *, write=False):
        group = next((g for g in self.list(project_id, agent_id) if g["id"] == group_id), None)
        if group is None:
            raise AuditError("memory group not accessible in this scope")
        if group["agent_ids"] and agent_id not in group["agent_ids"]:
            raise AuditError("select an authorized target agent for this memory group")
        if write and project_id not in group["write_project_ids"]:
            raise AuditError("memory group is read-only in this project")
        return group

    @contextmanager
    def registry_lock(self):
        path = self.state.directory / "memory-registry.lock"
        with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def improvement_groups(self, project_id, agent_id=None):
        with self.registry_lock():
            # ``list`` without an agent intentionally returns registry metadata for
            # every group shared with the project. Entry access is narrower: an
            # agent-bound group must never be recalled by an agentless workflow.
            groups = [
                group
                for group in self.list(project_id, agent_id, "improvement")
                if not group["agent_ids"] or agent_id in group["agent_ids"]
            ]
            # Read-only shared groups are valid recall sources, but every project
            # also needs a writable improvement group for outcome recording.
            if any(project_id in group["write_project_ids"] for group in groups):
                return groups
            workspace = self.state.workspace(project_id)
            workspace.initialize()
            created = self._create(
                project_id,
                {
                    "name": "Improvement lessons",
                    "purpose": "improvement",
                    "path": str(workspace.state / "memory" / "improvement"),
                },
            )
            return [*groups, created]

    def create(self, project_id, payload):
        with self.registry_lock():
            return self._create(project_id, payload)

    def _create(self, project_id, payload):
        self.state.project(project_id)
        if not isinstance(payload, dict) or set(payload) - {
            "name",
            "path",
            "purpose",
            "project_ids",
            "write_project_ids",
            "agent_ids",
        }:
            raise AuditError("unsupported memory group fields")
        name = payload.get("name", "")
        purpose = payload.get("purpose")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 200
            or purpose not in {"improvement", "agent"}
        ):
            raise AuditError("memory requires a name and improvement or agent purpose")
        projects = payload.get("project_ids", [project_id])
        writes = payload.get("write_project_ids", [project_id])
        agents = payload.get("agent_ids", [])
        if (
            not isinstance(projects, list)
            or not isinstance(writes, list)
            or any(not isinstance(p, str) for p in projects + writes)
            or project_id not in projects
            or not set(writes) <= set(projects)
        ):
            raise AuditError("memory access requires explicit project lists including its owner")
        if (
            not isinstance(agents, list)
            or any(not isinstance(a, str) for a in agents)
            or (purpose == "agent" and not agents)
        ):
            raise AuditError("agent memory requires explicit agent bindings")
        known = set()
        for project in projects:
            self.state.project(project)
            known.update(a["id"] for a in self.state.db.list_records(project, "application_agents"))
        if not set(agents) <= known:
            raise AuditError("memory group references an unknown agent")
        raw_path = payload.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise AuditError("choose a memory folder")
        raw = Path(raw_path).expanduser().absolute()
        if raw.is_symlink():
            raise AuditError("memory folder cannot be a symlink")
        path = raw.resolve()
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise AuditError("choose an empty memory folder")
        group = {
            "id": "group_" + uuid.uuid4().hex[:24],
            "name": name.strip(),
            "path": str(path),
            "purpose": purpose,
            "provider": "local",
            "project_ids": projects,
            "write_project_ids": writes,
            "agent_ids": agents,
            "owner_project_id": project_id,
        }
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        atomic_write(path / ".agentagon-memory.json", {"version": 1, "group_id": group["id"]})
        try:
            return self.state.db.put_record(project_id, "memory_groups", group["id"], group)
        except Exception:
            (path / ".agentagon-memory.json").unlink()
            raise

    def record(self, project_id, group_id, payload, agent_id=None):
        return FolderStore(self.get(project_id, group_id, agent_id, write=True)).record(payload)

    def history(self, project_id, group_id, entry_id, agent_id=None):
        group = self.get(project_id, group_id, agent_id)
        revisions = FolderStore(group).revisions(entry_id)
        if not revisions:
            raise AuditError("lesson not found in this memory group")
        return {"group": group, "versions": revisions}

    def recall(self, project_id, group_id, query, agent_id=None, limit=10):
        group = self.get(project_id, group_id, agent_id)
        if (
            not isinstance(query, str)
            or len(query) > 4000
            or type(limit) is not int
            or not 1 <= limit <= 50
        ):
            raise AuditError("recall requires a bounded query and limit of 1–50")
        terms = set(re.findall(r"[^\W_]+", query.casefold()))
        entries = [
            entry
            for entry in FolderStore(group).entries()
            if entry.get("status", "active") == "active"
        ]
        entries.sort(
            key=lambda e: (
                -len(terms & set(re.findall(r"[^\W_]+", (e["key"] + " " + e["text"]).casefold()))),
                e["id"],
            )
        )
        selected, size = [], 0
        for entry in entries:
            if terms and not terms & set(
                re.findall(r"[^\W_]+", (entry["key"] + " " + entry["text"]).casefold())
            ):
                continue
            size += len(encoded(entry).encode())
            if size > 64000 or len(selected) == limit:
                break
            selected.append(entry)
        return {"group_id": group_id, "entries": selected, "advisory": True}

    def snapshot(self, project_id, agent_id, purpose="agent"):
        groups = []
        for group in self.list(project_id, agent_id, purpose):
            if purpose == "agent" and agent_id not in group["agent_ids"]:
                continue
            groups.append(
                {
                    "group_id": group["id"],
                    "entries": [
                        entry
                        for entry in FolderStore(group).entries()
                        if entry.get("status", "active") == "active"
                    ],
                }
            )
        value = {
            "version": 1,
            "project_id": project_id,
            "agent_id": agent_id,
            "purpose": purpose,
            "groups": groups,
        }
        if len(encoded(value).encode()) > 4_000_000:
            raise AuditError("memory snapshot exceeds 4 MB")
        workspace = self.state.workspace(project_id)
        workspace.initialize()
        return {"path": workspace.artifact(value), "digest": digest(value)}
