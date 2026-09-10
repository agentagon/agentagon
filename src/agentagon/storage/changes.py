"""Read-only Git selection and bounded change evidence."""

import difflib
import hashlib
import subprocess
from pathlib import Path

from agentagon.core.records import AuditError, digest

EXCLUDED = {".git", ".agentagon", ".venv", "node_modules", "__pycache__", "dist", "build"}
MAX_FILE_BYTES = 2_000_000
HUNK_LINES = 100
GIT_TIMEOUT_SECONDS = 30


def git_bytes(root: Path, *args: str, optional: bool = False) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        if optional:
            return None
        raise AuditError("Git is required for changes reviews") from exc
    if result.returncode:
        if optional:
            return None
        raise AuditError("unable to read checkout metadata")
    return result.stdout


def revision(root: Path) -> str | None:
    value = git_bytes(root, "rev-parse", "--verify", "HEAD", optional=True)
    return value.decode().strip() if value is not None else None


def names(value: bytes) -> list[str]:
    try:
        return [name.decode("utf-8") for name in value.split(b"\0") if name]
    except UnicodeError as exc:
        raise AuditError("code paths must use UTF-8 filenames") from exc


def selected_scopes(root: Path, scopes: list[str], known: set[str]) -> list[str]:
    selected = []
    for scope in scopes or ["."]:
        path = root / scope
        if not path.resolve().is_relative_to(root):
            raise AuditError("code scope must remain inside the checkout")
        # Keep the lexical path: resolving it would turn a symlink into its target.
        relative = Path(scope)
        if relative.is_absolute():
            relative = relative.relative_to(root)
        if ".." in relative.parts:
            raise AuditError("code scope must remain inside the checkout")
        name = relative.as_posix()
        if not path.exists() and not any(under(candidate, name) for candidate in known):
            raise AuditError("code scope must exist in the checkout or the captured Git baseline")
        if name not in selected:
            selected.append(name)
    return selected


def under(name: str, scope: str) -> bool:
    return scope == "." or name == scope or name.startswith(scope.rstrip("/") + "/")


def excluded(name: str) -> str | None:
    path = Path(name)
    if any(part in EXCLUDED for part in path.parts):
        return "excluded_directory"
    if path.name.startswith(".env") or path.suffix in {".pem", ".key", ".p12"}:
        return "credential_file"
    return None


def _text(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise AuditError("binary") from exc
    if "\x00" in text:
        raise AuditError("binary")
    return text


def _old_content(root: Path, object_id: str, mode: str | None) -> bytes:
    if mode is None:
        return b""
    if mode not in {"100644", "100755"}:
        raise AuditError("symlink_or_submodule")
    if int(git_bytes(root, "cat-file", "-s", object_id)) > MAX_FILE_BYTES:
        raise AuditError("large")
    return git_bytes(root, "cat-file", "blob", object_id)


def _new_content(root: Path, name: str | None) -> tuple[bytes, str | None]:
    if name is None:
        return b"", None
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise AuditError("symlink_or_submodule")
    if not path.exists():
        return b"", None
    if not path.is_file():
        raise AuditError("symlink_or_submodule")
    stat = path.stat()
    if stat.st_size > MAX_FILE_BYTES:
        raise AuditError("large")
    return path.read_bytes(), "100755" if stat.st_mode & 0o111 else "100644"


def hunks(old: str, new: str) -> list[dict]:
    before, after = old.splitlines(keepends=True), new.splitlines(keepends=True)
    result = []
    for group in difflib.SequenceMatcher(None, before, after).get_grouped_opcodes(3):
        old_position, new_position = group[0][1], group[0][3]
        entries = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                entries.extend((" ", line, 1, 1) for line in before[i1:i2])
            else:
                if tag in {"delete", "replace"}:
                    entries.extend(("-", line, 1, 0) for line in before[i1:i2])
                if tag in {"insert", "replace"}:
                    entries.extend(("+", line, 0, 1) for line in after[j1:j2])
        offset = 0
        while offset < len(entries):
            end = min(offset + HUNK_LINES, len(entries))
            if end < len(entries) and all(entry[0] == " " for entry in entries[end:]):
                # Leave a changed line with trailing context, never a context-only unit.
                end = max(i for i in range(offset, end) if entries[i][0] != " ")
            chunk = entries[offset:end]
            old_count = sum(entry[2] for entry in chunk)
            new_count = sum(entry[3] for entry in chunk)
            old_start = old_position + bool(old_count)
            new_start = new_position + bool(new_count)
            text = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@\n"
            for prefix, line, _, _ in chunk:
                text += prefix + line
                if not line.endswith("\n"):
                    text += "\n\\ No newline at end of file\n"
            result.append(
                {
                    "old_start": old_start,
                    "old_count": old_count,
                    "new_start": new_start,
                    "new_count": new_count,
                    "text": text,
                }
            )
            old_position += old_count
            new_position += new_count
            offset = end
    return result or [
        {
            "old_start": 0,
            "old_count": 0,
            "new_start": 0,
            "new_count": 0,
            "text": "@@ -0,0 +0,0 @@\n(no textual line changes)\n",
        }
    ]


def _candidates(root: Path, head: str | None) -> list[dict]:
    if git_bytes(root, "ls-files", "--unmerged", "-z"):
        raise AuditError("resolve Git conflicts before reviewing local changes")
    result = []
    if head is not None:
        raw = git_bytes(
            root,
            "diff",
            "--raw",
            "--no-abbrev",
            "--find-renames",
            "--no-ext-diff",
            "--no-textconv",
            "-z",
            head,
            "--",
        ).split(b"\0")
        offset = 0
        while raw[offset]:
            metadata = raw[offset].decode("ascii").split()
            old_mode, new_mode, object_id, _, status = metadata
            old_name = names(raw[offset + 1])[0]
            new_name = old_name
            offset += 2
            if status.startswith("R"):
                new_name = names(raw[offset])[0]
                offset += 1
            result.append(
                {
                    "old_path": old_name if old_mode != ":000000" else None,
                    "new_path": new_name if new_mode != "000000" else None,
                    "old_mode": old_mode[1:] if old_mode != ":000000" else None,
                    "object_id": object_id,
                }
            )
    added = names(git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z"))
    if head is None:
        added += names(git_bytes(root, "ls-files", "--cached", "-z"))
    known = {entry["new_path"] for entry in result}
    deleted = {entry["old_path"]: entry for entry in result if entry["new_path"] is None}
    for name in sorted(set(added) - known):
        if name in deleted:
            # A staged deletion can leave an untracked replacement at the same path.
            deleted[name]["new_path"] = name
        else:
            result.append({"old_path": None, "new_path": name, "old_mode": None, "object_id": ""})
    return result


def snapshot_changes(root: Path, scopes: list[str]) -> dict:
    head = revision(root)
    candidates = _candidates(root, head)
    known = {entry[key] for entry in candidates for key in ("old_path", "new_path") if entry[key]}
    selected = selected_scopes(root, scopes, known)
    changes, files, skipped = [], [], []
    for candidate in candidates:
        old_path, new_path = candidate["old_path"], candidate["new_path"]
        paths = [path for path in (old_path, new_path) if path]
        if not any(under(path, scope) for path in paths for scope in selected):
            continue
        name = new_path or old_path
        reason = next((excluded(path) for path in paths if excluded(path)), None)
        try:
            if reason:
                raise AuditError(reason)
            before = _old_content(root, candidate["object_id"], candidate["old_mode"])
            after, new_mode = _new_content(root, new_path)
            old_text, new_text = _text(before), _text(after)
        except AuditError as exc:
            if str(exc) not in {
                "excluded_directory",
                "credential_file",
                "symlink_or_submodule",
                "large",
                "binary",
            }:
                raise
            skipped.append({"path": name, "reason": str(exc)})
            continue
        old_mode = candidate["old_mode"]
        if old_mode is None and new_mode is None:
            continue
        if new_mode is None:
            new_path = None
        if old_path == new_path and before == after and old_mode == new_mode:
            continue
        change_type = (
            "added"
            if old_path is None
            else "deleted"
            if new_path is None
            else "renamed"
            if old_path != new_path
            else "metadata"
            if before == after
            else "modified"
        )
        changes.append(
            {
                "path": new_path or old_path,
                "old_path": old_path,
                "new_path": new_path,
                "change_type": change_type,
                "old_digest": hashlib.sha256(before).hexdigest() if old_mode else None,
                "new_digest": hashlib.sha256(after).hexdigest() if new_mode else None,
                "old_mode": old_mode,
                "new_mode": new_mode,
                "hunks": hunks(old_text, new_text),
            }
        )
        if new_path:
            files.append(
                {
                    "path": new_path,
                    "digest": hashlib.sha256(after).hexdigest(),
                    "lines": len(new_text.splitlines()),
                }
            )
    result = {
        "code_scope": "changes",
        "revision": head,
        "files": sorted(files, key=lambda entry: entry["path"]),
        "skipped": sorted(skipped, key=lambda entry: entry["path"]),
        "scopes": selected,
        "changes": sorted(changes, key=lambda entry: entry["path"]),
    }
    result["fingerprint"] = digest(result)
    return result


def verify_change(root: Path, change: dict) -> None:
    if revision(root) != change["revision"]:
        raise AuditError("Git baseline changed; start a new review")
    current, mode = _new_content(root, change["new_path"] or change["old_path"])
    current_digest = hashlib.sha256(current).hexdigest() if mode else None
    if current_digest != change["new_digest"] or mode != change["new_mode"]:
        raise AuditError(f"code input changed: {change['path']}; start a new review")
