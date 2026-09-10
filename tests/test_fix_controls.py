"""Run steering is explicit, replayable, and shares the engine's admission rules."""

import json
from datetime import datetime, timedelta

import pytest
from click.testing import CliRunner
from support.controls import request, submit
from support.experiments import baseline, executions, propose, verify

from agentagon.cli.main import main
from agentagon.core.records import AuditError
from agentagon.experiments import controls, engine
from agentagon.experiments.store import load_run


@pytest.fixture
def active_run(application, specification):
    specification["repetitions"] = 1
    return application, baseline(application, specification)["run_id"]


def acknowledge(workspace, run_id, operation_id):
    return submit(workspace, run_id, "ack", target_operation_id=operation_id, host_id="active-host")


def test_directive_waits_for_host_and_repeated_operations_do_not_apply_twice(active_run):
    workspace, run_id = active_run
    command = request(workspace, run_id, "directive", text="Explore retrieval before prompts")
    before = len(executions())
    queued = controls.submit(workspace, run_id, command)
    assert queued["operation"]["state"] == "queued"
    revision = queued["revision"]
    assert controls.submit(workspace, run_id, command) == queued
    assert load_run(workspace, run_id)["revision"] == revision
    acknowledged = acknowledge(workspace, run_id, command["operation_id"])
    assert acknowledged["result"]["directive"] == command["text"]
    assert controls.projection(load_run(workspace, run_id))["controls"][0]["state"] == "applied"
    assert len(executions()) == before


def test_stale_or_reused_operation_identity_and_frozen_changes_are_rejected(active_run):
    workspace, run_id = active_run
    stale = request(workspace, run_id, "stop")
    submitted = request(workspace, run_id, "directive", text="Inspect retry failures")
    controls.submit(workspace, run_id, submitted)
    with pytest.raises(controls.ControlConflict, match="refresh"):
        controls.submit(workspace, run_id, stale)
    with pytest.raises(controls.ControlConflict, match="different request"):
        controls.submit(workspace, run_id, {**submitted, "text": "Different request"})
    before = load_run(workspace, run_id)
    for field in ("spec", "metrics", "runner", "argv", "constraints"):
        command = request(workspace, run_id, "stop", **{field: {}})
        with pytest.raises(AuditError, match="fix-control"):
            controls.submit(workspace, run_id, command)
    assert load_run(workspace, run_id) == before


def test_cancelled_request_cannot_be_acknowledged(active_run):
    workspace, run_id = active_run
    queued = submit(workspace, run_id, "directive", text="Try a cache")
    target = queued["operation"]["operation_id"]
    submit(workspace, run_id, "cancel", target_operation_id=target)
    with pytest.raises(AuditError, match="cancelled"):
        acknowledge(workspace, run_id, target)


def test_parent_request_creates_exactly_one_worktree_only_when_host_acknowledges(active_run):
    workspace, run_id = active_run
    data = load_run(workspace, run_id)
    existing = workspace.root / "existing-user-work.txt"
    existing.write_text("leave this unrelated edit intact")
    queued = submit(
        workspace,
        run_id,
        "expand",
        parent_id=data["baseline_id"],
        hypothesis="Remove redundant retrieval",
    )
    assert load_run(workspace, run_id)["usage"]["candidates"] == 0
    ack = request(
        workspace,
        run_id,
        "ack",
        target_operation_id=queued["operation"]["operation_id"],
        host_id="active-host",
    )
    first = controls.submit(workspace, run_id, ack)
    assert first["operation"]["state"] == "applied", first
    assert set(first["result"]["result"]) == {"run_id", "candidate_id", "state", "worktree"}
    assert controls.submit(workspace, run_id, ack) == first
    created = load_run(workspace, run_id)
    assert created["usage"]["candidates"] == 1
    candidate = created["candidates"][first["result"]["result"]["candidate_id"]]
    assert candidate["parent_id"] == data["baseline_id"]
    assert candidate["author"] == "active-host"
    assert existing.read_text() == "leave this unrelated edit intact"


def test_stop_is_native_and_continuation_requires_ack_without_resetting_usage(active_run):
    workspace, run_id = active_run
    before = load_run(workspace, run_id)["usage"]["trials"]
    assert submit(workspace, run_id, "stop")["result"]["state"] == "stopped"
    queued = submit(workspace, run_id, "continue")
    assert load_run(workspace, run_id)["state"] == "stopped"
    acknowledge(workspace, run_id, queued["operation"]["operation_id"])
    assert load_run(workspace, run_id)["state"] == "active"
    assert load_run(workspace, run_id)["usage"]["trials"] == before


def test_search_policy_changes_future_choices_without_changing_evaluation(active_run):
    workspace, run_id = active_run
    before = load_run(workspace, run_id)
    submit(workspace, run_id, "policy", policy={"strategy": "argmax", "objective": "latency"})
    after = load_run(workspace, run_id)
    assert after["search"]["policy"]["strategy"] == "argmax"
    assert after["evaluation_digest"] == before["evaluation_digest"]
    assert after["profile_digest"] == before["profile_digest"]
    assert after["usage"]["trials"] == before["usage"]["trials"]
    with pytest.raises(AuditError):
        submit(workspace, run_id, "policy", policy={"strategy": "argmax"})


def test_invalidation_excludes_descendants_but_keeps_the_archive(active_run):
    workspace, run_id = active_run
    parent, _ = propose(workspace, run_id)
    verify(workspace, run_id, parent["candidate_id"])
    child, _ = propose(workspace, run_id, latency=60, variant="child")
    verify(workspace, run_id, child["candidate_id"])
    invalid = submit(
        workspace,
        run_id,
        "invalidate",
        candidate_id=parent["candidate_id"],
        reason="The optimization assumes a cache contract that the application does not provide",
    )
    assert set(invalid["result"]["invalidated"]) == {parent["candidate_id"], child["candidate_id"]}
    data = load_run(workspace, run_id)
    assert data["candidates"][child["candidate_id"]]["trials"]
    assert child["candidate_id"] not in data["frontier"]
    with pytest.raises(AuditError):
        engine.select(workspace, run_id, child["candidate_id"])


def test_exhausted_branch_is_still_shippable_but_cannot_be_expanded(active_run):
    workspace, run_id = active_run
    candidate, _ = propose(workspace, run_id)
    verify(workspace, run_id, candidate["candidate_id"])
    submit(
        workspace, run_id, "exhaust", candidate_id=candidate["candidate_id"], reason="No more ideas"
    )
    assert candidate["candidate_id"] in load_run(workspace, run_id)["frontier"]
    selected = submit(workspace, run_id, "select", candidate_id=candidate["candidate_id"])
    assert selected["result"]["selected_branch"]
    with pytest.raises(AuditError, match="eligible"):
        submit(workspace, run_id, "expand", parent_id=candidate["candidate_id"], hypothesis="Retry")


def test_cli_steer_uses_same_contract_and_reports_revision(active_run):
    workspace, run_id = active_run
    file = workspace.state / "steer.json"
    file.write_text(json.dumps(request(workspace, run_id, "directive", text="Preserve latency")))
    result = CliRunner().invoke(
        main,
        ["--workspace", str(workspace.root), "fix", "steer", run_id, "--control-file", str(file)],
    )
    assert result.exit_code == 0, result.output or repr(result.exception)
    output = json.loads(result.output)
    assert output["operation"]["state"] == "queued"
    assert output["revision"] == load_run(workspace, run_id)["revision"]
    projection = controls.projection(load_run(workspace, run_id))
    assert "request" not in projection["controls"][0]
    assert "profile" not in projection


def test_other_host_cannot_consume_acknowledged_work(active_run):
    workspace, run_id = active_run
    queued = submit(workspace, run_id, "directive", text="Study failures")
    target = queued["operation"]["operation_id"]
    acknowledge(workspace, run_id, target)
    with pytest.raises(controls.ControlConflict, match="another host"):
        submit(workspace, run_id, "ack", target_operation_id=target, host_id="different-host")


def test_acknowledgment_recovers_after_candidate_created_before_response_saved(
    active_run, monkeypatch
):
    workspace, run_id = active_run
    data = load_run(workspace, run_id)
    queued = submit(
        workspace, run_id, "expand", parent_id=data["baseline_id"], hypothesis="Try batching"
    )
    command = request(
        workspace,
        run_id,
        "ack",
        target_operation_id=queued["operation"]["operation_id"],
        host_id="active-host",
    )
    create = engine.new

    def interrupted(*args, **kwargs):
        create(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(engine, "new", interrupted)
    with pytest.raises(KeyboardInterrupt):
        controls.submit(workspace, run_id, command)
    assert load_run(workspace, run_id)["usage"]["candidates"] == 1
    monkeypatch.setattr(engine, "new", create)
    result = controls.submit(workspace, run_id, command)
    assert result["operation"]["state"] == "applied"
    assert load_run(workspace, run_id)["usage"]["candidates"] == 1


@pytest.mark.parametrize("fresh_ack", [False, True])
def test_git_creation_failure_retries_the_same_reserved_candidate(
    active_run, monkeypatch, fresh_ack
):
    workspace, run_id = active_run
    data = load_run(workspace, run_id)
    queued = submit(
        workspace, run_id, "expand", parent_id=data["baseline_id"], hypothesis="Try batching"
    )
    fields = {"target_operation_id": queued["operation"]["operation_id"], "host_id": "active-host"}
    command = request(workspace, run_id, "ack", **fields)
    create = engine.checkouts.create

    def unavailable(*args, **kwargs):
        raise AuditError("Git worktree creation temporarily unavailable")

    monkeypatch.setattr(engine.checkouts, "create", unavailable)
    failed = controls.submit(workspace, run_id, command)
    assert failed["operation"]["state"] == "failed"
    assert failed["operation"]["retryable"] is True
    reserved = load_run(workspace, run_id)
    candidate_id = next(cid for cid in reserved["candidates"] if cid != reserved["baseline_id"])
    assert reserved["usage"]["candidates"] == 1
    monkeypatch.setattr(engine.checkouts, "create", create)
    if fresh_ack:
        command = request(workspace, run_id, "ack", **fields)
    recovered = controls.submit(workspace, run_id, command)
    assert recovered["operation"]["state"] == "applied"
    assert "error" not in recovered["operation"]
    assert recovered["result"]["result"]["candidate_id"] == candidate_id
    assert load_run(workspace, run_id)["usage"]["candidates"] == 1


def test_invalidating_parent_with_editing_child_does_not_strand_authoring_capacity(active_run):
    workspace, run_id = active_run
    parent, _ = propose(workspace, run_id)
    verify(workspace, run_id, parent["candidate_id"])
    child, _ = propose(workspace, run_id, latency=60, variant="editing-child")
    result = submit(
        workspace,
        run_id,
        "invalidate",
        candidate_id=parent["candidate_id"],
        reason="Invalid premise",
    )
    assert result["operation"]["state"] == "applied"
    assert result["result"]["invalidated_active_attempts"] == []
    replacement = engine.new(workspace, run_id, hypothesis="Different approach", author="new-host")
    assert replacement["candidate_id"] != child["candidate_id"]
    assert replacement["candidate"]["parent_id"] == load_run(workspace, run_id)["baseline_id"]


def test_continuation_refreshes_elapsed_exhaustion_without_a_prior_write(active_run, monkeypatch):
    workspace, run_id = active_run
    data = load_run(workspace, run_id)
    aged = datetime.fromisoformat(data["created_at"]) + timedelta(seconds=601)
    monkeypatch.setattr(engine, "now", lambda: aged.isoformat())
    assert engine.status(workspace, run_id)["state"] == "exhausted"
    assert load_run(workspace, run_id)["state"] == "active"
    limits = {**data["limits"], "max_elapsed_seconds": 1200}
    queued = submit(workspace, run_id, "continue", limits=limits)
    acknowledged = acknowledge(workspace, run_id, queued["operation"]["operation_id"])
    assert acknowledged["operation"]["state"] == "applied"
    assert load_run(workspace, run_id)["state"] == "active"


def test_stopped_unfinished_candidate_can_be_invalidated_without_more_execution(active_run):
    workspace, run_id = active_run
    draft, _ = propose(workspace, run_id)
    before = len(executions())
    engine.stop(workspace, run_id)
    result = submit(
        workspace,
        run_id,
        "invalidate",
        candidate_id=draft["candidate_id"],
        reason="Abandoned approach",
    )
    assert result["operation"]["state"] == "applied"
    assert load_run(workspace, run_id)["candidates"][draft["candidate_id"]]["invalidated"] is True
    assert len(executions()) == before
