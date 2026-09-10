"""Exercise local preparation and retry-safe publication against a real bare remote."""

import json
import subprocess
import traceback
from pathlib import Path

import pytest
from support.delivery import creates, pushes
from support.experiments import git, propose, verify

from agentagon.core.records import AuditError
from agentagon.experiments import delivery, engine, store


def ship(selected, **kwargs):
    return delivery.ship(selected["workspace"], selected["run_id"], **kwargs)


def test_preparation_is_local_repeatable_and_preserves_unrelated_work(selected, monkeypatch):
    work = selected["workspace"]
    original_head = git(work.root, "rev-parse", "HEAD")
    (work.root / "app.json").write_text("uncommitted user edit\n")
    (work.root / "private-notes.txt").write_text("untracked user notes\n")
    before = git(work.root, "status", "--porcelain")
    original_command = delivery._command

    def local_only(root, argv, **kwargs):
        assert argv[0] != "gh" and "ls-remote" not in argv and "push" not in argv
        return original_command(root, argv, **kwargs)

    monkeypatch.setattr(delivery, "_command", local_only)
    monkeypatch.setattr(delivery.shutil, "which", lambda name: None)
    first = ship(selected)
    second = ship(selected)
    assert first["delivery_id"] == second["delivery_id"]
    assert first["state"] == "prepared"
    assert len(store.load_run(work, selected["run_id"])["deliveries"]) == 1
    assert git(work.root, "rev-parse", "HEAD") == original_head
    assert git(work.root, "status", "--porcelain") == before
    assert (work.root / "app.json").read_text() == "uncommitted user edit\n"
    assert (work.root / "private-notes.txt").read_text() == "untracked user notes\n"
    assert git(selected["remote"], "for-each-ref", f"refs/heads/{selected['branch']}") == ""
    assert all(Path(path).is_file() for path in first["artifacts"].values())
    patch = Path(first["artifacts"]["diff"]).read_text()
    assert "diff --git a/app.json b/app.json" in patch
    assert '+{"latency": 80' in patch
    assert "uncommitted user edit" not in patch
    assert "app.json" in Path(first["artifacts"]["diffstat"]).read_text()
    summary = json.loads(Path(first["artifacts"]["summary"]).read_text())
    assert summary["metrics"]["latency"]["baseline"] == 100
    assert summary["metrics"]["latency"]["candidate"] == 80


def test_preparation_omits_private_prose_commands_and_evidence(selected):
    work = selected["workspace"]
    with store.locked(work, selected["run_id"]):
        data = store.load_run(work, selected["run_id"])
        data["goal"] = "private-goal-secret"
        data["candidates"][selected["candidate_id"]]["hypothesis"] = "private-hypothesis-secret"
        data["candidates"][selected["candidate_id"]]["private"] = "raw-trace-secret"
        store.save_run(work, data)
    record = ship(selected)
    body = Path(record["artifacts"]["pr_body"]).read_text()
    summary = Path(record["artifacts"]["summary"]).read_text()
    for output in (body, summary):
        for private in (
            "private-goal-secret",
            "private-hypothesis-secret",
            "raw-trace-secret",
            "EXECUTION_LOG",
        ):
            assert private not in output
    assert "quality-control" in body
    assert "baseline_ratio" in body
    assert selected["revision"] in body
    assert "not confidence intervals" in body


def test_exported_ranges_are_derived_from_actual_retained_trials(selected):
    work = selected["workspace"]
    with store.locked(work, selected["run_id"]):
        data = store.load_run(work, selected["run_id"])
        data["candidates"][selected["candidate_id"]]["variation"]["latency"] = {
            "min": 1,
            "max": 999,
        }
        store.save_run(work, data)
    result = ship(selected)
    summary = json.loads(Path(result["artifacts"]["summary"]).read_text())
    assert summary["metrics"]["latency"]["min"] == summary["metrics"]["latency"]["max"] == 80


def test_requires_existing_selection_and_matching_candidate(selected):
    with pytest.raises(AuditError, match="chosen with fix select"):
        ship(selected, candidate_id="candidate_" + "0" * 24)
    work = selected["workspace"]
    with store.locked(work, selected["run_id"]):
        data = store.load_run(work, selected["run_id"])
        data["selected"] = None
        store.save_run(work, data)
    with pytest.raises(AuditError, match="chosen with fix select"):
        ship(selected)


def test_changed_local_branch_is_not_rewritten(selected):
    work = selected["workspace"]
    revision = git(work.root, "rev-parse", "HEAD")
    git(work.root, "update-ref", f"refs/heads/{selected['branch']}", revision)
    with pytest.raises(AuditError, match="selected branch changed"):
        ship(selected)
    assert git(work.root, "rev-parse", selected["branch"]) == revision


def test_dominated_selection_and_changed_review_are_rejected(selected):
    work = selected["workspace"]
    better, _ = propose(work, selected["run_id"], latency=60, quality=0.9)
    verify(work, selected["run_id"], better["candidate_id"])
    with pytest.raises(AuditError, match="no longer on.*frontier"):
        ship(selected)
    engine.select(work, selected["run_id"], better["candidate_id"])
    data = store.load_run(work, selected["run_id"])
    review_path = work.root / data["candidates"][better["candidate_id"]]["review_artifact"]
    review_path.write_text("{}")
    with pytest.raises(AuditError, match="checksum changed"):
        ship(selected)


def test_ambiguous_remote_and_missing_default_require_explicit_settings(selected):
    work = selected["workspace"]
    git(work.root, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    with pytest.raises(AuditError):
        ship(selected)
    assert ship(selected, base=selected["base"])["state"] == "prepared"
    git(work.root, "config", "--add", "remote.origin.pushurl", str(selected["remote"]))
    git(work.root, "config", "--add", "remote.origin.pushurl", "https://other.invalid/org/repo.git")
    with pytest.raises(AuditError, match="unambiguous"):
        ship(selected, base=selected["base"])


def test_publish_requires_gh_but_saves_prepared_result(selected, monkeypatch):
    monkeypatch.setattr(delivery.shutil, "which", lambda name: None)
    with pytest.raises(AuditError, match="install and authenticate GitHub CLI"):
        ship(selected, publish=True)
    records = store.load_run(selected["workspace"], selected["run_id"])["deliveries"]
    assert [value["state"] for value in records.values()] == ["prepared"]


def test_truthy_text_does_not_authorize_publication(selected, github):
    with pytest.raises(AuditError, match="explicit boolean"):
        ship(selected, publish="false")
    assert not github["calls"]


def test_publish_creates_one_exact_branch_and_one_draft_pr(selected, github):
    record = ship(selected, publish=True)
    repeated = ship(selected, publish=True)
    assert record["delivery_id"] == repeated["delivery_id"]
    assert record["state"] == "published"
    assert record["pr"]["url"] == "https://github.com/example/project/pull/7"
    assert record["pr"]["is_draft"] is True
    assert git(selected["remote"], "rev-parse", selected["branch"]) == selected["revision"]
    assert len(pushes(github)) == len(creates(github)) == 1
    assert f"--force-with-lease=refs/heads/{selected['branch']}:" in pushes(github)[0]
    assert "--force" not in pushes(github)[0]


@pytest.mark.parametrize("phase", ["push", "pr"])
def test_retry_recovers_lost_success_acknowledgement(selected, github, monkeypatch, phase):
    crashed = False

    def lose_response(root, argv, **kwargs):
        nonlocal crashed
        output = github["command"](root, argv, **kwargs)
        target = "push" in argv if phase == "push" else argv[:3] == ["gh", "pr", "create"]
        if target and not crashed:
            crashed = True
            raise AuditError("simulated lost response")
        return output

    monkeypatch.setattr(delivery, "_command", lose_response)
    with pytest.raises(AuditError, match="lost response"):
        ship(selected, publish=True)
    result = ship(selected, publish=True)
    assert result["state"] == "published"
    assert len(pushes(github)) == len(creates(github)) == 1


def test_changed_remote_base_requires_new_verification(selected, github):
    ship(selected)
    work = selected["workspace"]
    # Updating only the remote leaves local tracking metadata stale, exercising the live check.
    git(
        work.root,
        "push",
        "-q",
        str(selected["remote"]),
        f"{selected['revision']}:refs/heads/{selected['base']}",
    )
    with pytest.raises(AuditError, match="remote base changed"):
        ship(selected, publish=True)
    assert not pushes(github) and not creates(github)


def test_published_receipt_survives_later_base_changes_without_republishing(selected, github):
    first = ship(selected, publish=True)
    work = selected["workspace"]
    git(work.root, "push", "-q", "origin", f"{selected['revision']}:refs/heads/{selected['base']}")
    github["prs"][0]["state"] = "MERGED"
    repeated = ship(selected, publish=True)
    assert repeated["delivery_id"] == first["delivery_id"]
    assert repeated["pr"]["state"] == "MERGED"
    assert len(pushes(github)) == len(creates(github)) == 1


def test_prepared_delivery_with_updated_tracking_base_cannot_publish(selected, github):
    ship(selected)
    work = selected["workspace"]
    git(work.root, "push", "-q", "origin", f"{selected['revision']}:refs/heads/{selected['base']}")
    with pytest.raises(AuditError, match="verify a new run"):
        ship(selected)
    with pytest.raises(AuditError, match="remote base changed"):
        ship(selected, publish=True)
    assert not pushes(github) and not creates(github)


def test_base_moving_after_push_stops_before_pr_creation(selected, github, monkeypatch):
    moved = False

    def move_base(root, argv, **kwargs):
        nonlocal moved
        output = github["command"](root, argv, **kwargs)
        if argv[0] == "git" and "push" in argv and not moved:
            moved = True
            git(
                selected["remote"],
                "update-ref",
                f"refs/heads/{selected['base']}",
                selected["revision"],
            )
        return output

    monkeypatch.setattr(delivery, "_command", move_base)
    with pytest.raises(AuditError, match="remote base changed"):
        ship(selected, publish=True)
    assert len(pushes(github)) == 1
    assert not creates(github)


def test_existing_remote_branch_with_other_source_is_never_overwritten(selected, github):
    work = selected["workspace"]
    origin = git(work.root, "rev-parse", "HEAD")
    git(
        work.root,
        "push",
        "-q",
        str(selected["remote"]),
        f"{origin}:refs/heads/{selected['branch']}",
    )
    with pytest.raises(AuditError, match="different source"):
        ship(selected, publish=True)
    assert git(selected["remote"], "rev-parse", selected["branch"]) == origin
    assert not pushes(github) and not creates(github)


def test_remote_branch_creation_race_does_not_even_fast_forward(selected, github, monkeypatch):
    work = selected["workspace"]
    origin = git(work.root, "rev-parse", "HEAD")

    def racing_push(root, argv, **kwargs):
        if argv[0] == "git" and "push" in argv:
            git(
                work.root,
                "push",
                "-q",
                str(selected["remote"]),
                f"{origin}:refs/heads/{selected['branch']}",
            )
        return github["command"](root, argv, **kwargs)

    monkeypatch.setattr(delivery, "_command", racing_push)
    with pytest.raises(AuditError, match="command failed"):
        ship(selected, publish=True)
    assert git(selected["remote"], "rev-parse", selected["branch"]) == origin
    assert not creates(github)


def test_conflicting_pr_is_not_modified_or_duplicated(selected, github):
    result = ship(selected, publish=True)
    github["prs"][0]["body"] = "unrelated PR"
    with pytest.raises(AuditError, match="existing PR does not match"):
        ship(selected, publish=True)
    assert len(creates(github)) == 1
    assert result["pr"]["url"] == github["prs"][0]["url"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/repo.git", "github.com/owner/repo"),
        ("git@github.example:owner/repo.git", "github.example/owner/repo"),
        ("ssh://git@github.example/owner/repo.git", "github.example/owner/repo"),
    ],
)
def test_configured_github_repository_parsing(url, expected):
    assert delivery._repository(url) == expected


@pytest.mark.parametrize(
    "url", ["/tmp/repo.git", "https://github.com/a/b?token=secret", "https://["]
)
def test_invalid_publication_destinations_fail_without_echoing_credentials(url):
    with pytest.raises(AuditError) as caught:
        delivery._repository(url)
    assert "secret" not in str(caught.value)


def test_delivery_timeout_does_not_expose_authenticated_arguments(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git", "https://private-token@example.invalid/repo"], 60)

    monkeypatch.setattr(delivery.subprocess, "run", timeout)
    with pytest.raises(AuditError) as caught:
        delivery._command(tmp_path, ["git", "status"])
    assert "private-token" not in "".join(traceback.format_exception(caught.value))
