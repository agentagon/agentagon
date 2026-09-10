import hashlib
import subprocess

import pytest
from support.changes import git

from agentagon.core.records import AuditError
from agentagon.storage.workspace import Workspace


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init", "-q")
    (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Initial code")
    return Workspace(root)


def test_changes_can_inspect_clean_checkout_without_initializing(checkout):
    assert checkout.changes()["changes"] == []
    assert not checkout.state.exists()
    assert not (checkout.root / ".gitignore").exists()


def test_changes_capture_net_staged_unstaged_and_untracked_content(checkout):
    root = checkout.root
    (root / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    git(root, "add", "app.py")
    (root / "app.py").write_text("def run():\n    return 3\n", encoding="utf-8")
    unusual = "new file\twith\nnewline.py"
    (root / unusual).write_text("new = True\n", encoding="utf-8")
    snapshot = checkout.changes()
    changes = {change["path"]: change for change in snapshot["changes"]}
    assert set(changes) == {"app.py", unusual}
    assert changes[unusual]["change_type"] == "added"
    text = changes["app.py"]["hunks"][0]["text"]
    assert "-    return 1" in text and "+    return 3" in text
    assert "return 2" not in text
    assert git(root, "show", ":app.py") == "def run():\n    return 2"
    assert not checkout.state.exists()


def test_staged_change_reverted_in_worktree_has_no_net_change(checkout):
    path = checkout.root / "app.py"
    original = path.read_bytes()
    path.write_text("changed\n", encoding="utf-8")
    git(checkout.root, "add", "app.py")
    path.write_bytes(original)
    assert checkout.changes()["changes"] == []


def test_staged_deletion_with_untracked_replacement_uses_net_contents(checkout):
    path = checkout.root / "app.py"
    original = path.read_bytes()
    git(checkout.root, "rm", "--cached", "app.py")
    assert checkout.changes()["changes"] == []
    path.write_text("replacement\n", encoding="utf-8")
    change = checkout.changes()["changes"][0]
    assert change["change_type"] == "modified"
    assert change["old_digest"] == hashlib.sha256(original).hexdigest()
    assert "+replacement" in change["hunks"][0]["text"]


def test_unborn_checkout_uses_empty_baseline_and_handles_removed_new_file(tmp_path):
    root = tmp_path / "new"
    root.mkdir()
    git(root, "init", "-q")
    (root / "added.py").write_text("hello\n", encoding="utf-8")
    (root / "removed.py").write_text("gone\n", encoding="utf-8")
    git(root, "add", ".")
    (root / "removed.py").unlink()
    snapshot = Workspace(root).changes()
    assert snapshot["revision"] is None
    assert [change["path"] for change in snapshot["changes"]] == ["added.py"]
    assert snapshot["changes"][0]["old_digest"] is None
    full = Workspace(root).snapshot([])
    assert full["revision"] is None
    assert [file["path"] for file in full["files"]] == ["added.py"]


def test_deletion_is_reviewable_and_accepts_deleted_scope(checkout):
    (checkout.root / "app.py").unlink()
    snapshot = checkout.changes(["app.py"])
    change = snapshot["changes"][0]
    assert change["change_type"] == "deleted"
    assert change["new_path"] is None and change["new_digest"] is None
    assert change["hunks"][0]["old_count"] == 2
    assert snapshot["files"] == []
    unit = {
        "path": "app.py",
        "digest": "a hunk digest",
        "change": {**change, "revision": snapshot["revision"]},
    }
    checkout.verify_code(unit)
    (checkout.root / "app.py").write_text("restored\n", encoding="utf-8")
    with pytest.raises(AuditError, match="code input changed"):
        checkout.verify_code(unit)


def test_staged_rename_retains_both_paths_and_selected_old_scope(checkout):
    git(checkout.root, "mv", "app.py", "renamed.py")
    change = checkout.changes(["app.py"])["changes"][0]
    assert change["change_type"] == "renamed"
    assert (change["old_path"], change["new_path"]) == ("app.py", "renamed.py")
    assert change["hunks"][0]["old_count"] == change["hunks"][0]["new_count"] == 0


def test_rename_can_reuse_old_path_for_new_file(checkout):
    git(checkout.root, "mv", "app.py", "renamed.py")
    (checkout.root / "app.py").write_text("a new implementation\n", encoding="utf-8")
    snapshot = checkout.changes()
    assert {change["change_type"] for change in snapshot["changes"]} == {"added", "renamed"}
    checkout.verify_snapshot(snapshot)
    for change in snapshot["changes"]:
        checkout.verify_code({"change": {**change, "revision": snapshot["revision"]}})


def test_permission_and_empty_file_changes_have_review_units(checkout):
    (checkout.root / "app.py").chmod(0o755)
    (checkout.root / "empty.py").touch()
    changes = {change["path"]: change for change in checkout.changes()["changes"]}
    assert changes["app.py"]["change_type"] == "metadata"
    assert changes["app.py"]["old_mode"] == "100644"
    assert changes["app.py"]["new_mode"] == "100755"
    assert changes["empty.py"]["change_type"] == "added"
    assert all(change["hunks"] for change in changes.values())


def test_changed_hunks_are_bounded_and_omit_distant_unchanged_code(checkout):
    path = checkout.root / "app.py"
    path.write_text("".join(f"line {i}\n" for i in range(1000)), encoding="utf-8")
    git(checkout.root, "add", "app.py")
    git(checkout.root, "commit", "-qm", "Long source")
    path.write_text(path.read_text().replace("line 500\n", "changed\n"), encoding="utf-8")
    change = checkout.changes()["changes"][0]
    assert len(change["hunks"]) == 1
    assert "line 0\n" not in change["hunks"][0]["text"]
    assert change["hunks"][0]["old_start"] == 498
    path.write_text("new\n" * 400, encoding="utf-8")
    chunks = checkout.changes()["changes"][0]["hunks"]
    assert len(chunks) > 1
    assert all(len(chunk["text"].splitlines()) <= 101 for chunk in chunks)
    assert sum(chunk["old_count"] for chunk in chunks) == 1000
    assert sum(chunk["new_count"] for chunk in chunks) == 400


def test_hunk_split_does_not_create_context_only_units(checkout):
    path = checkout.root / "app.py"
    path.write_text("".join(f"line {i}\n" for i in range(100)), encoding="utf-8")
    git(checkout.root, "add", "app.py")
    git(checkout.root, "commit", "-qm", "Source with context")
    path.write_text("added\n" * 100 + path.read_text(), encoding="utf-8")
    chunks = checkout.changes()["changes"][0]["hunks"]
    assert all(
        any(line.startswith(("+", "-")) for line in chunk["text"].splitlines()[1:])
        for chunk in chunks
    )
    assert all(len(chunk["text"].splitlines()) <= 101 for chunk in chunks)


def test_excluded_changes_are_explicit_without_retaining_their_content(checkout):
    root = checkout.root
    (root / ".env").write_text("secret must stay private", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00private")
    (root / "large.txt").write_bytes(b"x" * 2_000_001)
    (root / "link.py").symlink_to("app.py")
    (root / "build").mkdir()
    (root / "build" / "out.py").write_text("generated\n", encoding="utf-8")
    snapshot = checkout.changes()
    assert snapshot["changes"] == []
    assert {entry["reason"] for entry in snapshot["skipped"]} == {
        "credential_file",
        "binary",
        "large",
        "symlink_or_submodule",
        "excluded_directory",
    }
    assert "secret must stay private" not in str(snapshot)


def test_conflicted_checkout_is_rejected(checkout):
    root = checkout.root
    branch = git(root, "branch", "--show-current")
    git(root, "checkout", "-qb", "conflict")
    (root / "app.py").write_text("one\n", encoding="utf-8")
    git(root, "commit", "-qam", "One")
    git(root, "checkout", "-q", branch)
    (root / "app.py").write_text("two\n", encoding="utf-8")
    git(root, "commit", "-qam", "Two")
    with pytest.raises(subprocess.CalledProcessError):
        git(root, "merge", "conflict")
    with pytest.raises(AuditError, match="conflicts"):
        checkout.changes()


def test_fingerprint_preserves_selected_scope_and_rejects_changed_inputs(checkout):
    root = checkout.root
    (root / "app.py").write_text("changed\n", encoding="utf-8")
    snapshot = checkout.changes(["app.py"])
    (root / "unrelated.py").write_text("outside scope\n", encoding="utf-8")
    checkout.verify_snapshot(snapshot)
    git(root, "add", "app.py")
    checkout.verify_snapshot(snapshot)
    (root / "app.py").write_text("changed again\n", encoding="utf-8")
    with pytest.raises(AuditError, match="start a new review"):
        checkout.verify_snapshot(snapshot)


def test_changed_baseline_invalidates_review_even_with_same_file_bytes(checkout):
    root = checkout.root
    (root / "app.py").write_text("changed\n", encoding="utf-8")
    snapshot = checkout.changes()
    git(root, "commit", "--allow-empty", "-qm", "Advance baseline")
    with pytest.raises(AuditError, match="start a new review"):
        checkout.verify_snapshot(snapshot)


def test_full_snapshot_accepts_local_changes_and_legacy_snapshot_still_reads(checkout):
    snapshot = checkout.snapshot([])
    checkout.verify_snapshot(snapshot)
    (checkout.root / "untracked.py").write_text("new\n", encoding="utf-8")
    current = checkout.snapshot([])
    assert current["local_changes"] is True
    assert [file["path"] for file in current["files"]] == ["app.py", "untracked.py"]
    checkout.verify_snapshot(current)
    with pytest.raises(AuditError, match="code input changed"):
        checkout.verify_snapshot(snapshot)
    checkout.verify_snapshot({"revision": "older", "files": []})
    checkout.verify_code(
        {
            "path": "app.py",
            "digest": hashlib.sha256((checkout.root / "app.py").read_bytes()).hexdigest(),
        }
    )


def test_init_preserves_ignore_rules_and_keeps_committed_checkout_clean(checkout):
    root = checkout.root
    exclude = root / ".git" / "info" / "exclude"
    exclude.write_text("local-only", encoding="utf-8")
    (root / ".gitignore").write_text("*.cache\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "Ignore caches")
    checkout.initialize()
    checkout.initialize()
    assert exclude.read_text() == "local-only\n/.agentagon/\n"
    assert (root / ".gitignore").read_text() == "*.cache\n"
    assert git(root, "status", "--porcelain") == ""
    assert checkout.changes()["changes"] == []


def test_init_uses_git_common_exclude_for_linked_worktree(checkout, tmp_path):
    target = tmp_path / "linked"
    git(checkout.root, "worktree", "add", "--detach", str(target), "HEAD")
    workspace = Workspace(target)
    workspace.initialize()
    assert git(target, "status", "--porcelain") == ""
    assert not (target / ".gitignore").exists()
    assert "/.agentagon/" in (checkout.root / ".git" / "info" / "exclude").read_text()


def test_init_refuses_symlinked_local_exclude(checkout, tmp_path):
    target = tmp_path / "private"
    target.write_text("do not modify", encoding="utf-8")
    exclude = checkout.root / ".git" / "info" / "exclude"
    exclude.unlink()
    exclude.symlink_to(target)
    with pytest.raises(AuditError, match="symlink"):
        checkout.initialize()
    assert target.read_text() == "do not modify"


def test_init_refuses_project_rule_that_unignores_private_state(checkout):
    root = checkout.root
    (root / ".gitignore").write_text("!/.agentagon/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "Negated ignore rule")
    with pytest.raises(AuditError, match="conflicting negation"):
        checkout.initialize()
    assert not checkout.state.exists()
    assert (root / ".gitignore").read_text() == "!/.agentagon/\n"
    assert git(root, "status", "--porcelain") == ""


def test_scope_rejects_escape_and_missing_paths(checkout):
    with pytest.raises(AuditError, match="inside the checkout"):
        checkout.changes(["../outside"])
    with pytest.raises(AuditError, match="must exist"):
        checkout.changes(["missing.py"])
