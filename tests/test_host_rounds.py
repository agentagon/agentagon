"""Durable multi-branch authoring through independent measurement and review."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from support.experiments import passing_review, verify

from agentagon.core.records import AuditError
from agentagon.experiments import engine, learning, orchestration
from agentagon.experiments.store import load_run, locked, save_run
from agentagon.storage.config import Config


def configured(workspace, spec, **settings):
    profile = Config().profile(workspace.root, "local")
    profile["limits"]["parallel_candidates"] = 3
    profile["orchestration"] = {
        **orchestration.DEFAULT_SETTINGS,
        "round_width": 3,
        "host_capacity": 3,
        "branch_depth": 2,
        "resource_slots": 3,
        **settings,
    }
    Config().update_profile("project", "rounds", profile, workspace.root)
    started = engine.start(workspace, spec, "rounds")
    verify(workspace, started["run_id"], started["candidate_id"])
    return started


def brief(parent, label, **kwargs):
    return {
        "parent_id": parent,
        "hypothesis": f"Try {label}",
        "author": f"author-{label}",
        "editable_paths": ["app.json"],
        "evidence": [f"candidate:{parent}"],
        **kwargs,
    }


def request(branches, operation="round-1", **kwargs):
    return {
        "operation_id": operation,
        "host_id": "codex-session",
        "host_capacity": 3,
        "branches": branches,
        **kwargs,
    }


def edit(workspace, run_id, cid, value=80):
    candidate = load_run(workspace, run_id)["candidates"][cid]
    path = workspace.root / candidate["worktree"] / "app.json"
    path.write_text(json.dumps({"latency": value, "quality": 0.8, "variant": cid}))


def test_multiple_branches_from_one_parent_and_round_closes_after_every_branch(
    application, specification
):
    initial = configured(application, specification)
    run_id, parent = initial["run_id"], initial["candidate_id"]
    args = request([brief(parent, "a"), brief(parent, "b"), brief(parent, "c")])
    reserved = orchestration.reserve_round(application, run_id, args)
    assert len(set(reserved["candidate_ids"])) == 3
    state = load_run(application, run_id)
    assert state["rounds"][0]["size"] == 3
    assert {state["candidates"][cid]["parent_id"] for cid in reserved["candidate_ids"]} == {parent}
    assert state["usage"]["candidates"] == 3
    assert (
        orchestration.reserve_round(application, run_id, args)["candidate_ids"]
        == reserved["candidate_ids"]
    )
    assert load_run(application, run_id)["usage"]["candidates"] == 3
    for index, cid in enumerate(reserved["candidate_ids"]):
        orchestration.assign(application, run_id, cid, "codex-session", f"agent-{index}")
        edit(application, run_id, cid, 80 - index)
        verify(application, run_id, cid)
        assert bool(load_run(application, run_id)["rounds"][0].get("completed")) == (index == 2)


def test_concurrent_host_retries_reserve_one_set_of_candidates(application, specification):
    initial = configured(application, specification)
    run_id, parent = initial["run_id"], initial["candidate_id"]
    args = request([brief(parent, "a"), brief(parent, "b")])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: orchestration.reserve_round(application, run_id, args), range(2))
        )
    assert results[0]["candidate_ids"] == results[1]["candidate_ids"]
    data = load_run(application, run_id)
    assert data["usage"]["candidates"] == 2
    assert len(data["round_operations"]) == 1 and len(data["rounds"]) == 1


def test_depth_requires_verified_tip_and_does_not_close_round_prematurely(
    application, specification
):
    initial = configured(application, specification)
    run_id, parent = initial["run_id"], initial["candidate_id"]
    reserved = orchestration.reserve_round(
        application, run_id, request([brief(parent, "first", depth_limit=2)])
    )
    first = reserved["candidate_ids"][0]
    branch = load_run(application, run_id)["rounds"][0]["branches"][0]
    successor = request(
        [brief(first, "next", branch_id=branch["branch_id"])],
        "round-next",
        round_id=reserved["round_id"],
    )
    with pytest.raises(AuditError, match="independently review"):
        orchestration.reserve_round(application, run_id, successor)
    edit(application, run_id, first)
    measured = engine.run(application, run_id, first)
    with pytest.raises(AuditError, match="independently review"):
        orchestration.reserve_round(application, run_id, successor)
    engine.run(application, run_id, first, review=passing_review(measured))
    assert not load_run(application, run_id)["rounds"][0].get("completed")
    assert any(
        w["action"] == "next_branch_hypothesis"
        for w in orchestration.next_packet(application, run_id)["work"]
    )
    second = orchestration.reserve_round(application, run_id, successor)["candidate_ids"][0]
    edit(application, run_id, second, 70)
    verify(application, run_id, second)
    round_ = load_run(application, run_id)["rounds"][0]
    assert round_["completed"] and round_["branches"][0]["finished"]
    assert len(round_["candidates"]) == 2 and round_["size"] == 1
    with pytest.raises(AuditError, match="already complete"):
        orchestration.reserve_round(
            application,
            run_id,
            request(
                [brief(second, "excess", branch_id=branch["branch_id"])],
                "excess",
                round_id=reserved["round_id"],
            ),
        )


def test_resource_and_budget_capacity_are_independent_of_selection_pool(application, specification):
    specification["resources"] = {"slots": 2}
    initial = configured(application, specification, resource_slots=2)
    run_id, parent = initial["run_id"], initial["candidate_id"]
    assert orchestration.next_packet(application, run_id)["capacity"] == 1
    with pytest.raises(AuditError, match="capacity"):
        orchestration.reserve_round(
            application, run_id, request([brief(parent, "a"), brief(parent, "b")])
        )
    assert not load_run(application, run_id)["rounds"]
    assert load_run(application, run_id)["usage"]["candidates"] == 0


def test_reservation_survives_checkout_failure_before_any_dispatch(
    application, specification, monkeypatch
):
    initial = configured(application, specification)
    run_id, parent = initial["run_id"], initial["candidate_id"]
    args = request([brief(parent, "a"), brief(parent, "b")])
    original = engine._finish_creation
    monkeypatch.setattr(
        engine,
        "_finish_creation",
        lambda *_: (_ for _ in ()).throw(AuditError("checkout unavailable")),
    )
    with pytest.raises(AuditError, match="checkout unavailable"):
        orchestration.reserve_round(application, run_id, args)
    state = load_run(application, run_id)
    ids = [cid for cid in state["candidates"] if cid != parent]
    assert len(ids) == 2 and state["usage"]["candidates"] == 2
    monkeypatch.setattr(engine, "_finish_creation", original)
    retry = orchestration.reserve_round(application, run_id, args)
    assert set(retry["candidate_ids"]) == set(ids)
    assert all(
        not load_run(application, run_id)["candidates"][cid]["creation_pending"] for cid in ids
    )
    with pytest.raises(AuditError, match="another request"):
        orchestration.reserve_round(application, run_id, {**args, "host_id": "other-host"})


def test_finish_cancel_and_read_only_next_preserve_reservations(application, specification):
    initial = configured(application, specification)
    run_id, parent = initial["run_id"], initial["candidate_id"]
    reserved = orchestration.reserve_round(
        application, run_id, request([brief(parent, "a", depth_limit=2)])
    )
    state = load_run(application, run_id)
    before = (application.state / "runs" / run_id / "state.json").read_bytes()
    first = orchestration.next_packet(application, run_id)
    second = orchestration.next_packet(application, run_id)
    assert first["progress_digest"] == second["progress_digest"]
    assert (application.state / "runs" / run_id / "state.json").read_bytes() == before
    finish = {
        "operation_id": "finish",
        "host_id": "codex-session",
        "host_capacity": 3,
        "round_id": reserved["round_id"],
        "finish_branches": [
            {
                "branch_id": state["rounds"][0]["branches"][0]["branch_id"],
                "reason": "Hypothesis no longer supported",
            }
        ],
    }
    orchestration.reserve_round(application, run_id, finish)
    assert load_run(application, run_id)["rounds"][0]["completed"]
    assert (
        load_run(application, run_id)["candidates"][reserved["candidate_ids"][0]]["state"]
        == "cancelled"
    )
    engine.stop(application, run_id)
    assert not orchestration.next_packet(application, run_id)["actionable"]


def test_stagnation_requests_bounded_alternatives_before_stopping(application, specification):
    profile = Config().profile(application.root, "local")
    profile["limits"]["stagnation_rounds"] = 1
    Config().update_profile("project", "local", profile, application.root)
    initial = configured(application, specification, ideation_passes=1)
    run_id, parent = initial["run_id"], initial["candidate_id"]
    reserved = orchestration.reserve_round(application, run_id, request([brief(parent, "no-gain")]))
    cid = reserved["candidate_ids"][0]
    edit(application, run_id, cid, 100)
    verify(application, run_id, cid)
    packet = orchestration.next_packet(application, run_id)
    assert packet["state"] == "awaiting_ideation"
    ideation = next(w["packet"] for w in packet["work"] if w["action"] == "ideate")
    response = {
        "packet_digest": ideation["packet_digest"],
        "author": "hypothesis-agent",
        "hypotheses": [],
        "unsuccessful": [
            {
                "hypothesis": "Changing the variant did not improve latency",
                "evidence": [f"candidate:{cid}"],
            }
        ],
    }
    result = orchestration.ideate(application, run_id, response)
    assert result["state"] == "exhausted"
    assert load_run(application, run_id)["ideations"][0]["response"]["unsuccessful"]


def test_parallel_scan_shards_share_one_scan_and_input_budget(application, specification):
    profile = Config().profile(application.root, "local")
    profile["scans"] = {"max_scans": 2, "max_input_bytes": 65536, "scan_timeout_seconds": 60}
    Config().update_profile("project", "local", profile, application.root)
    initial = configured(application, specification, scan_workers=3)
    run_id, parent = initial["run_id"], initial["candidate_id"]
    reserved = orchestration.reserve_round(
        application, run_id, request([brief(parent, "a"), brief(parent, "b"), brief(parent, "c")])
    )
    for index, cid in enumerate(reserved["candidate_ids"]):
        edit(application, run_id, cid, 80 - index)
        verify(application, run_id, cid)
    with locked(application, run_id):
        data = load_run(application, run_id)
        scan = learning.prepare(application, data)
        save_run(application, data)
    assert len(scan["shards"]) == 3
    assert scan["shard_input_bytes"] <= 65536
    assert len(load_run(application, run_id)["scans"]) == 1
    assert {cid for shard in scan["shards"] for cid in shard["candidate_ids"]} == set(
        reserved["candidate_ids"]
    )
    with locked(application, run_id):
        data = load_run(application, run_id)
        completed = learning.accept(
            application,
            data,
            {**scan["response_template"], "author": "coordinator", "insights": []},
        )
        save_run(application, data)
    assert completed["state"] == "completed"
