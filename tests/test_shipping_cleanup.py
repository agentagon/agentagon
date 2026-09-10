"""Fresh cleanup verification cannot silently substitute a regression or stale source."""

import json

import pytest
from support.experiments import git, passing_review, verify

from agentagon.core.records import AuditError
from agentagon.experiments import cleanup, delivery, engine, store


def candidate(selected, latency=80, quality=0.8):
    workspace, run_id = selected["workspace"], selected["run_id"]
    started = cleanup.start(workspace, run_id, "ship-cleanup", "cleanup-author")
    path = workspace.root / started["candidate"]["worktree"]
    (path / "app.json").write_text(
        json.dumps({"latency": latency, "quality": quality, "variant": "clean"}) + "\n"
    )
    return started, path


def test_cleanup_selects_only_after_fresh_execution_and_independent_review(selected):
    workspace, run_id = selected["workspace"], selected["run_id"]
    before = store.load_run(workspace, run_id)
    started, _ = candidate(selected)
    cid = started["candidate_id"]
    assert cleanup.start(workspace, run_id, "ship-cleanup", "cleanup-author")["candidate_id"] == cid
    assert cleanup.finish(workspace, run_id, cid)["state"] == "pending_verification"
    with pytest.raises(AuditError, match="finish cleanup verification"):
        delivery.ship(workspace, run_id)
    measured = engine.run(workspace, run_id, cid)
    with pytest.raises(AuditError, match="different reviewer"):
        engine.run(
            workspace, run_id, cid, review=passing_review(measured, reviewer="cleanup-author")
        )
    engine.run(workspace, run_id, cid, review=passing_review(measured))
    outcome = cleanup.finish(workspace, run_id, cid)
    assert outcome["state"] == "selected_cleanup"
    assert all(item["no_worse"] for item in outcome["comparisons"])
    after = store.load_run(workspace, run_id)
    assert after["usage"]["trials"] == before["usage"]["trials"] + 1
    assert after["usage"]["candidates"] == before["usage"]["candidates"] + 1
    assert after["selected"]["candidate_id"] == cid
    original = after["candidates"][selected["candidate_id"]]
    for key in ("source_revision", "trials", "review_artifact", "metrics"):
        assert original[key] == before["candidates"][selected["candidate_id"]][key]
    assert git(workspace.root, "rev-parse", selected["branch"]) == selected["revision"]
    assert cleanup.finish(workspace, run_id, cid) == outcome
    prepared = delivery.ship(workspace, run_id)
    assert (
        prepared["candidate_id"] == cid
        and prepared["source_revision"] == after["candidates"][cid]["source_revision"]
    )
    summary = json.loads((workspace.root / prepared["artifacts"]["summary"]).read_text())
    assert summary["cleanup"]["original_candidate_id"] == selected["candidate_id"]
    assert all(item["no_worse"] for item in summary["cleanup"]["comparisons"])


@pytest.mark.parametrize(
    "latency,quality,expected",
    [
        (90, 0.8, "selection_required"),
        (70, 0.7, "selection_required"),
        (80, 0.2, "retained_original"),
    ],
)
def test_regression_or_changed_tradeoff_preserves_original_selection(
    selected, latency, quality, expected
):
    workspace, run_id = selected["workspace"], selected["run_id"]
    started, _ = candidate(selected, latency, quality)
    measured = engine.run(workspace, run_id, started["candidate_id"])
    if measured["candidate"]["state"] == "awaiting_review":
        engine.run(workspace, run_id, started["candidate_id"], review=passing_review(measured))
    outcome = cleanup.finish(workspace, run_id, started["candidate_id"])
    assert outcome["state"] == expected
    assert store.load_run(workspace, run_id)["selected"]["candidate_id"] == selected["candidate_id"]


def test_cleanup_cannot_change_frozen_checks(selected):
    workspace, run_id = selected["workspace"], selected["run_id"]
    started, path = candidate(selected)
    (path / "checks.py").write_text("pass\n")
    with pytest.raises(AuditError, match="frozen evaluation"):
        engine.run(workspace, run_id, started["candidate_id"])
    assert store.load_run(workspace, run_id)["selected"]["candidate_id"] == selected["candidate_id"]


def test_cleanup_uses_existing_budget_and_rejects_changed_delivery_base(selected):
    workspace, run_id = selected["workspace"], selected["run_id"]
    with store.locked(workspace, run_id):
        data = store.load_run(workspace, run_id)
        data["limits"]["max_trials"] = data["usage"]["trials"]
        store.save_run(workspace, data)
    with pytest.raises(AuditError, match="budget exhausted"):
        cleanup.start(workspace, run_id, "no-budget", "cleanup-author")
    assert store.load_run(workspace, run_id)["usage"]["candidates"] == 1
    data = store.load_run(workspace, run_id)
    limits = {**data["limits"], "max_trials": 10}
    engine.run(workspace, run_id, selected["candidate_id"], continue_run=True, limits=limits)
    started, _ = candidate(selected)
    verify(workspace, run_id, started["candidate_id"])
    cleanup.finish(workspace, run_id, started["candidate_id"])
    git(
        workspace.root,
        "update-ref",
        f"refs/remotes/origin/{selected['base']}",
        selected["revision"],
    )
    with pytest.raises(AuditError, match="base differs"):
        delivery.ship(workspace, run_id)
