"""Real local execution from frozen Git snapshots through verified branch delivery."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from support.experiments import (
    BENCHMARK,
    baseline,
    executions,
    git,
    passing_review,
    propose,
    verify,
)

from agentagon.cli.main import main
from agentagon.core.records import AuditError
from agentagon.experiments import engine
from agentagon.storage.config import Config


def test_baseline_executes_default_repetitions_then_requires_independent_review(
    application, specification
):
    initial = git(application.root, "rev-parse", "HEAD")
    started = engine.start(application, specification, "local")
    run_id, candidate_id = started["run_id"], started["candidate_id"]
    assert started["candidate"]["state"] == "sealed"
    with pytest.raises(AuditError, match="baseline execution"):
        engine.new(application, run_id, hypothesis="Premature proposal")
    measured = engine.run(application, run_id)
    assert measured["candidate"]["state"] == "awaiting_review"
    assert measured["candidate"]["metrics"] == {"latency": 100.0, "quality": 0.8}
    assert [entry["seed"] for entry in executions()] == [0, 1, 2]
    assert len(measured["candidate"]["trials"]) == 3
    assert measured["frontier"] == []
    with pytest.raises(AuditError):
        engine.run(application, run_id, review=measured["review_template"])
    with pytest.raises(AuditError, match="different reviewer"):
        engine.run(application, run_id, review=passing_review(measured, "baseline"))
    result = engine.run(application, run_id, review=passing_review(measured))
    assert result["candidate"]["state"] == "verified"
    assert result["frontier"] == [candidate_id]
    for artifact in result["artifacts"].values():
        assert Path(artifact).is_file()
    assert git(application.root, "rev-parse", "HEAD") == initial
    assert git(application.root, "status", "--porcelain") == ""


def test_pareto_tradeoffs_keep_original_baseline_constraints_and_retain_rejections(
    application, specification
):
    started = baseline(application, specification)
    run_id = started["run_id"]
    fast, _ = propose(application, run_id, latency=80, quality=0.8, variant="faster")
    fast_id = fast["candidate_id"]
    assert verify(application, run_id, fast_id)["frontier"] == [fast_id]

    quality, _ = propose(
        application, run_id, parent_id=fast_id, latency=100, quality=0.9, variant="quality"
    )
    quality_id = quality["candidate_id"]
    improved = verify(application, run_id, quality_id)
    assert improved["candidate"]["parent_id"] == fast_id
    assert improved["candidate"]["state"] == "verified"
    assert set(improved["frontier"]) == {fast_id, quality_id}
    gate = next(
        rule for rule in improved["candidate"]["constraints"] if rule["metric"] == "latency"
    )
    assert gate["threshold"] == 105.0  # Parent's faster 80 ms must not tighten this to 84 ms.

    infeasible, _ = propose(
        application, run_id, parent_id=fast_id, latency=70, quality=0.5, variant="infeasible"
    )
    rejected = verify(application, run_id, infeasible["candidate_id"])
    assert rejected["candidate"]["state"] == "rejected"
    assert rejected["candidate"]["metrics"]["latency"] == 70
    assert set(rejected["frontier"]) == {fast_id, quality_id}

    worse, _ = propose(
        application, run_id, parent_id=fast_id, latency=90, quality=0.7, variant="dominated"
    )
    retained = verify(application, run_id, worse["candidate_id"])
    assert retained["candidate"]["state"] == "verified"
    assert worse["candidate_id"] not in retained["frontier"]
    assert set(retained["frontier"]) == {fast_id, quality_id}


def test_candidate_scope_blocks_evaluator_changes_before_any_trial(application, specification):
    start = baseline(application, specification)
    candidate, checkout = propose(application, start["run_id"])
    (checkout / "benchmark.py").write_text("raise SystemExit(0)\n")
    with pytest.raises(AuditError, match="frozen evaluation"):
        engine.run(application, start["run_id"], candidate["candidate_id"])
    assert len(executions()) == 3
    assert (
        engine.status(application, start["run_id"], candidate["candidate_id"])["candidate"][
            "trials"
        ]
        == []
    )
    assert (application.root / "benchmark.py").read_text() == BENCHMARK


def test_foreign_stale_or_fabricated_review_cannot_admit_candidate(application, specification):
    start = baseline(application, specification)
    candidate, _ = propose(application, start["run_id"])
    run_id, candidate_id = start["run_id"], candidate["candidate_id"]
    measured = engine.run(application, run_id, candidate_id)
    original = passing_review(measured)
    for field in ("run_id", "candidate_id", "source_digest", "evaluation_digest", "inputs_digest"):
        invalid = {**original, field: "foreign-value"}
        with pytest.raises(AuditError, match="stale"):
            engine.run(application, run_id, candidate_id, review=invalid)
    with pytest.raises(AuditError, match="stale"):
        engine.run(
            application, run_id, candidate_id, review={**original, "trial_ids": ["foreign-trial"]}
        )
    with pytest.raises(AuditError, match="retained engine evidence"):
        engine.run(
            application,
            run_id,
            candidate_id,
            review={**original, "evidence": ["invented-evidence"]},
        )
    with pytest.raises(AuditError):
        engine.run(
            application, run_id, candidate_id, review={**original, "metrics": {"latency": 1}}
        )
    assert (
        engine.status(application, run_id, candidate_id)["candidate"]["state"] == "awaiting_review"
    )
    assert len(executions()) == 6
    assert (
        engine.run(application, run_id, candidate_id, review=original)["candidate"]["state"]
        == "verified"
    )


def test_replay_and_duplicate_proposal_never_execute_completed_checks_again(
    application, specification
):
    start = baseline(application, specification)
    run_id = start["run_id"]
    candidate, _ = propose(application, run_id)
    measured = engine.run(application, run_id, candidate["candidate_id"])
    review = passing_review(measured)
    engine.run(application, run_id, candidate["candidate_id"], review=review)
    trial_ids = [trial["trial_id"] for trial in measured["candidate"]["trials"]]
    for _ in range(2):
        result = engine.run(application, run_id, candidate["candidate_id"])
        engine.run(application, run_id, candidate["candidate_id"], review=review)
        assert [trial["trial_id"] for trial in result["candidate"]["trials"]] == trial_ids
    duplicate = engine.new(
        application,
        run_id,
        parent_id=candidate["candidate_id"],
        hypothesis="Unchanged candidate",
        author="another-author",
    )
    result = engine.run(application, run_id, duplicate["candidate_id"])
    assert result["candidate"]["state"] == "duplicate"
    assert result["candidate"]["duplicate_of"] == candidate["candidate_id"]
    assert result["candidate"]["trials"] == []
    assert len(executions()) == 6


def test_selected_branch_preserves_exact_binary_mode_snapshot_and_origin(
    application, specification
):
    original_revision = git(application.root, "rev-parse", "HEAD")
    original_ref = git(application.root, "symbolic-ref", "HEAD")
    start = baseline(application, specification)
    candidate, checkout = propose(application, start["run_id"])
    expected_binary = b"\x00improved\x01\xff"
    (checkout / "model.bin").write_bytes(expected_binary)
    (checkout / "tool.sh").write_text("#!/bin/sh\nprintf 'improved\\n'\n")
    (checkout / "tool.sh").chmod(0o755)
    verified = verify(application, start["run_id"], candidate["candidate_id"])
    snapshot = verified["candidate"]["source_revision"]
    # Later host edits are not allowed to silently inherit the measured commit's verification.
    (checkout / "model.bin").write_bytes(b"unmeasured later edit")
    selected = engine.select(application, start["run_id"], candidate["candidate_id"])
    branch = selected["selected_branch"]
    assert git(application.root, "rev-parse", branch) == snapshot
    assert git(application.root, "show", f"{branch}:model.bin", binary=True) == expected_binary
    assert git(application.root, "ls-tree", branch, "tool.sh").startswith("100755 ")
    assert git(application.root, "rev-parse", "HEAD") == original_revision
    assert git(application.root, "symbolic-ref", "HEAD") == original_ref
    assert git(application.root, "status", "--porcelain") == ""
    assert (application.root / "model.bin").read_bytes() == b"\x00baseline\xff"
    assert not ((application.root / "tool.sh").stat().st_mode & 0o111)
    assert (
        engine.select(application, start["run_id"], candidate["candidate_id"])["selected_branch"]
        == selected["selected_branch"]
    )


def test_frozen_declared_input_and_overlay_are_reused_even_if_original_changes(
    application, specification
):
    source = application.state / "fixture.json"
    source.write_text('{"held_out": true}\n')
    overlay = application.state / "control.txt"
    overlay.write_text("frozen check\n")
    specification["inputs"] = [{"source": ".agentagon/fixture.json", "path": "data/fixture.json"}]
    specification["overlays"] = [
        {"source": ".agentagon/control.txt", "path": "tests/control.txt", "deliver": True}
    ]
    specification["evaluation_paths"].append("tests")
    started = engine.start(application, specification, "local")
    source.write_text("changed private source")
    overlay.write_text("changed original overlay")
    verified = verify(application, started["run_id"], started["candidate_id"])
    for trial in verified["candidate"]["trials"]:
        result = application.read_artifact(trial["artifact"])
        assert "data/fixture.json" in result["source_manifest_before"]
        assert result["source_manifest_before"] == result["source_manifest_after"]
    candidate, checkout = propose(application, started["run_id"])
    assert (checkout / "tests/control.txt").read_text() == "frozen check\n"
    verified = verify(application, started["run_id"], candidate["candidate_id"])
    selected = engine.select(application, started["run_id"], candidate["candidate_id"])
    branch = selected["selected_branch"]
    assert git(application.root, "show", f"{branch}:tests/control.txt") == "frozen check"
    assert "data/fixture.json" not in git(application.root, "ls-tree", "-r", "--name-only", branch)


def test_candidate_limit_and_stop_need_explicit_continuation_without_resetting_usage(
    application, specification
):
    profile = Config().profile(application.root, "local")
    profile["limits"]["max_candidates"] = 1
    Config().update_profile("project", "local", profile, application.root)
    start = baseline(application, specification)
    candidate, _ = propose(application, start["run_id"])
    finished = verify(application, start["run_id"], candidate["candidate_id"])
    assert finished["state"] == "exhausted"
    with pytest.raises(AuditError, match="exhausted"):
        engine.new(application, start["run_id"], hypothesis="Over budget")
    extended = {**profile["limits"], "max_candidates": 2}
    continued = engine.run(
        application, start["run_id"], candidate["candidate_id"], continue_run=True, limits=extended
    )
    assert continued["usage"]["candidates"] == 1
    assert continued["usage"]["trials"] == 6
    next_candidate, _ = propose(
        application, start["run_id"], variant="after-continuation", latency=70
    )
    engine.stop(application, start["run_id"])
    with pytest.raises(AuditError, match="stopped"):
        engine.run(application, start["run_id"], next_candidate["candidate_id"])
    assert len(executions()) == 6
    resumed = engine.run(
        application, start["run_id"], next_candidate["candidate_id"], continue_run=True
    )
    assert resumed["candidate"]["state"] == "awaiting_review"
    assert resumed["usage"]["trials"] == 9


def test_invalid_metric_is_retained_but_never_enters_frontier(application, specification):
    start = baseline(application, specification)
    candidate, _ = propose(application, start["run_id"], quality=None, variant="invalid-result")
    result = engine.run(application, start["run_id"], candidate["candidate_id"])
    assert result["candidate"]["state"] == "failed"
    assert result["candidate"]["trials"][0]["artifact"]
    assert candidate["candidate_id"] not in result["frontier"]
    assert len(executions()) == 4


def test_selection_rejects_modified_retained_execution_evidence(application, specification):
    start = baseline(application, specification)
    candidate, _ = propose(application, start["run_id"])
    verified = verify(application, start["run_id"], candidate["candidate_id"])
    evidence = application.root / verified["candidate"]["trials"][0]["artifact"]
    evidence.write_text('{"metrics":{"latency":1}}')
    before = git(application.root, "branch", "--list")
    with pytest.raises(AuditError, match="checksum"):
        engine.select(application, start["run_id"], candidate["candidate_id"])
    assert git(application.root, "branch", "--list") == before


def test_unselected_snapshot_survives_git_collection_and_can_still_be_delivered(
    application, specification
):
    start = baseline(application, specification)
    candidate, _ = propose(application, start["run_id"])
    verified = verify(application, start["run_id"], candidate["candidate_id"])
    snapshot = verified["candidate"]["source_revision"]
    assert verified["selected_branch"] is None
    engine.stop(application, start["run_id"])
    # This is the disposable fixture repository, never the developer's checkout.
    git(application.root, "gc", "--prune=now")
    assert git(application.root, "cat-file", "-t", snapshot) == "commit"
    delivered = engine.select(application, start["run_id"], candidate["candidate_id"])
    assert git(application.root, "rev-parse", delivered["selected_branch"]) == snapshot
    assert git(application.root, "status", "--porcelain") == ""


@pytest.mark.parametrize("field", ["inputs_digest", "profile_digest"])
def test_foreign_runner_input_or_profile_identity_cannot_be_admitted(
    application, specification, monkeypatch, field
):
    started = engine.start(application, specification, "local")
    execute = engine.runners.execute

    def mismatched_result(*args, **kwargs):
        result = execute(*args, **kwargs)
        return {**result, field: "foreign-identity"}

    monkeypatch.setattr(engine.runners, "execute", mismatched_result)
    result = engine.run(application, started["run_id"])
    assert result["candidate"]["state"] == "failed", result["candidate"]
    assert result["frontier"] == []
    assert len(executions()) == 1


def test_cli_lifecycle_reuses_status_and_review_continuation(application, specification):
    def invoke(*arguments):
        result = CliRunner().invoke(main, ["--workspace", str(application.root), *arguments])
        assert result.exit_code == 0, result.output or repr(result.exception)
        return json.loads(result.output)

    def reviewed(run_id, candidate_id):
        measured = invoke("fix", "run", run_id, candidate_id)
        response = application.state / "review.json"
        response.write_text(json.dumps(passing_review(measured)))
        return invoke("fix", "run", run_id, candidate_id, "--review-file", str(response))

    spec_file = application.state / "specification.json"
    spec_file.write_text(json.dumps(specification))
    started = invoke("fix", "start", "--spec", str(spec_file), "--profile", "local")
    run_id = started["run_id"]
    reviewed(run_id, started["candidate_id"])
    created = invoke(
        "fix", "new", run_id, "--hypothesis", "Reduce computation", "--author", "cli-author"
    )
    candidate_id = created["candidate_id"]
    checkout = application.root / created["candidate"]["worktree"]
    (checkout / "app.json").write_text(
        json.dumps({"latency": 75, "quality": 0.85, "variant": "cli"})
    )
    assert reviewed(run_id, candidate_id)["candidate"]["state"] == "verified"
    assert invoke("status")["latest_fix_run_id"] == run_id
    assert (
        invoke("status", "--run", run_id, "--candidate", candidate_id)["candidate"]["metrics"][
            "latency"
        ]
        == 75
    )
    selected = invoke("fix", "select", run_id, candidate_id)
    assert git(application.root, "rev-parse", selected["selected_branch"])
    assert invoke("fix", "stop", run_id)["state"] == "stopped"
    assert len(executions()) == 6
    assert git(application.root, "status", "--porcelain") == ""


@pytest.mark.parametrize("untracked", [False, True])
def test_dirty_origin_rejects_before_run_or_worktree_creation(
    application, specification, untracked
):
    path = application.root / ("untracked.txt" if untracked else "app.json")
    path.write_text("pre-existing user work")
    before = git(application.root, "worktree", "list", "--porcelain")
    with pytest.raises(AuditError, match="clean checkout"):
        engine.start(application, specification, "local")
    assert path.read_text() == "pre-existing user work"
    assert git(application.root, "worktree", "list", "--porcelain") == before
    assert not (application.state / "runs").exists()
    assert executions() == []


def test_leading_space_filename_cannot_bypass_editable_scope(application, specification):
    started = baseline(application, specification)
    created, checkout = propose(application, started["run_id"])
    (checkout / " app.json").write_text("outside the declared app.json scope")
    with pytest.raises(AuditError, match="outside the declared editable scope"):
        engine.run(application, started["run_id"], created["candidate_id"])
    assert len(executions()) == 3


@pytest.mark.parametrize("explicit,custom_seeds", [(False, False), (True, True), (False, True)])
def test_saved_repetition_override_is_frozen_and_explicit_spec_wins(
    application, specification, explicit, custom_seeds
):
    profile = Config().profile(application.root, "local")
    profile["repetitions"] = 2
    Config().update_profile("project", "local", profile, application.root)
    if explicit:
        specification["repetitions"] = 1
    if custom_seeds:
        specification["seeds"] = [73] if explicit else [11, 22]
    started = engine.start(application, specification, "local")
    profile["repetitions"] = 7
    Config().update_profile("project", "local", profile, application.root)
    measured = engine.run(application, started["run_id"])
    assert measured["candidate"]["state"] == "awaiting_review"
    expected = [73] if explicit else [11, 22] if custom_seeds else [0, 1]
    assert [record["seed"] for record in executions()] == expected
