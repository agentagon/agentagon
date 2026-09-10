"""Executable Git snapshots, independent of the text-only audit snapshots."""

import hashlib
import os
import subprocess
from pathlib import Path

from agentagon.core.records import AuditError
from agentagon.experiments import worker


def git(root: Path, *args: str, data: bytes | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Agentagon",
        "GIT_AUTHOR_EMAIL": "agentagon@localhost",
        "GIT_COMMITTER_NAME": "Agentagon",
        "GIT_COMMITTER_EMAIL": "agentagon@localhost",
    }
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
        input=data,
        capture_output=True,
        check=False,
        timeout=60,
        env=env,
    )
    if result.returncode:
        raise AuditError("Git snapshot operation failed; check checkout and worktree permissions")
    # NUL-delimited file lists must preserve leading spaces and embedded newlines.
    return result.stdout.decode("utf-8", errors="strict").rstrip("\n")


def clean_revision(root: Path) -> str:
    if git(root, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"):
        raise AuditError("fix requires a clean checkout; commit or move existing work first")
    revision = git(root, "rev-parse", "--verify", "HEAD")
    for line in git(root, "ls-tree", "-r", "-z", revision).split("\0"):
        if not line:
            continue
        mode, _, name = line.partition("\t")
        if mode.startswith("160000"):
            raise AuditError("execution snapshots do not yet support Git submodules")
        p = Path(name)
        if (
            ".agentagon" in p.parts
            or p.name == ".env"
            or p.name.startswith(".env.")
            and p.name not in (".env.example", ".env.sample")
            or p.suffix in (".pem", ".key", ".p12")
        ):
            raise AuditError(
                "tracked private state or credential files cannot be staged for execution"
            )
    return revision


def tree(root: Path, revision: str) -> str:
    return git(root, "rev-parse", f"{revision}^{{tree}}")


def retain(root: Path, run_id: str, candidate_id: str, revision: str) -> None:
    """Anchor sealed commits so ordinary Git garbage collection cannot prune evidence."""
    ref = f"refs/agentagon/fix/{run_id}/{candidate_id}"
    existing = git(root, "for-each-ref", "--format=%(objectname)", ref)
    if existing and existing != revision:
        raise AuditError("retained snapshot reference already identifies different source")
    if not existing:
        git(root, "update-ref", ref, revision, "0" * len(revision))


def create(root: Path, destination: Path, revision: str) -> None:
    if destination.exists():
        if (
            not (destination / ".git").is_file()
            or git(destination, "rev-parse", "HEAD") != revision
        ):
            raise AuditError("existing candidate checkout does not match its snapshot")
        return
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    git(root, "worktree", "add", "--detach", str(destination), revision)


def snapshot(root: Path, parent: str, message: str) -> str:
    git(root, "add", "--all", "--", ".")
    source_tree = git(root, "write-tree")
    if source_tree == tree(root, parent):
        return parent
    return git(root, "commit-tree", source_tree, "-p", parent, data=(message + "\n").encode())


def paths(root: Path, revision: str) -> list[str]:
    return git(root, "ls-tree", "-r", "--name-only", "-z", revision).split("\0")


def under(name: str, scope: str) -> bool:
    return scope == "." or name == scope or name.startswith(scope.rstrip("/") + "/")


def changes(root: Path, baseline: str, candidate: str) -> list[str]:
    raw = git(root, "diff", "--name-only", "--no-renames", "-z", baseline, candidate)
    return [name for name in raw.split("\0") if name]


def validate_scope(root: Path, baseline: str, revision: str, spec: dict) -> None:
    protected = spec["evaluation_paths"] + [
        e["path"] for k in ("overlays", "inputs") for e in spec[k]
    ]
    for name in changes(root, baseline, revision):
        if any(under(name, p) for p in protected):
            raise AuditError("candidate changed frozen evaluation files or inputs")
        if not any(under(name, p) for p in spec["editable_paths"]):
            raise AuditError("candidate changed files outside the declared editable scope")
        if ".agentagon" in Path(name).parts or name == ".gitignore":
            raise AuditError("candidate cannot change private state or Git ignore policy")


def checked_file(root: Path, relative: str) -> Path:
    candidate = root / relative
    if (
        candidate.is_symlink()
        or not candidate.resolve().is_relative_to(root.resolve())
        or not candidate.is_file()
    ):
        raise AuditError("declared evaluation inputs must be regular files inside the checkout")
    return candidate


def copy_frozen(root: Path, destination: Path, entries: list[dict]) -> None:
    for entry in entries:
        source = root / entry["artifact"]
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["digest"]:
            raise AuditError("frozen evaluation input changed")
        target = destination / entry["path"]
        if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
            raise AuditError("evaluation input destination escapes its checkout")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(entry["mode"])


def remove(root: Path, destination: Path) -> bool:
    if not destination.exists():
        return True
    try:
        git(root, "worktree", "remove", "--force", str(destination))
    except AuditError:
        return False
    return True


def source_manifest(root: Path) -> dict:
    """Read executable inputs; runtime-created files are excluded by caller comparison."""
    try:
        return worker.manifest(root)
    except worker.EscapingSourceLinkError as exc:
        raise AuditError("execution snapshot contains an escaping symlink") from exc
    except ValueError as exc:
        raise AuditError(str(exc)) from exc
