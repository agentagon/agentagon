"""Parent policy, frozen task measurements and durable candidate reservations."""

import copy
import json

import pytest
import support.experiments as fixtures
import support.recovery as recovery
import support.runners as runner_fixtures
from support.experiments import baseline, git, passing_review, propose, verify

from agentagon.core.records import AuditError, validate_record
from agentagon.experiments import engine, evaluation, runners, search
from agentagon.experiments.spec import validate_profile, validate_spec
from agentagon.experiments.store import load_run, locked, save_run
from agentagon.storage.config import Config


def search_run():
    records = {
        "baseline": {"cost": 10, "quality": 0.8},
        "accurate": {"cost": 10, "quality": 1.0},
        "balanced": {"cost": 5, "quality": 0.9},
        "cheap": {"cost": 1, "quality": 0.8},
        "dominated": {"cost": 6, "quality": 0.85},
    }
    return {
        "baseline_id": "baseline",
        "profile": {},
        "spec": {
            "metrics": {
                "cost": {"direction": "min", "unit": "USD"},
                "quality": {"direction": "max", "unit": "fraction"},
            },
            "repetitions": 3,
        },
        "candidates": {
            cid: {
                "candidate_id": cid,
                "state": "verified",
                "feasible": True,
                "metrics": metrics,
                "last_expanded": 0,
                "parent_id": None if cid == "baseline" else "baseline",
            }
            for cid, metrics in records.items()
        },
    }


def choices(data, count):
    result = []
    for index in range(count):
        decision = search.choose_parent(data)
        search.reserve(data, decision, f"child_{index}", f"operation_{index}")
        data["candidates"][decision["chosen_parent"]]["last_expanded"] = index + 1
        result.append(decision)
    return result


def test_default_pareto_preserves_balanced_compromise_and_expands_least_recently():
    data = search_run()
    selected = choices(data, 4)
    assert [d["chosen_parent"] for d in selected] == ["accurate", "balanced", "cheap", "accurate"]
    assert data["search"]["policy"] == {"strategy": "pareto", "seed": 0}
    assert data["search"]["decision_index"] == 4
    assert {entry["candidate_id"] for entry in selected[0]["eligible"]} == {
        "accurate",
        "balanced",
        "cheap",
    }
    assert selected[0]["eligible"][1]["metrics"] == {"cost": 5, "quality": 0.9}
    assert evaluation.frontier(data) == ["accurate", "balanced", "cheap"]


def test_explicit_parent_can_explore_dominated_verified_archive_without_admitting_it():
    data = search_run()
    decision = search.choose_parent(data, "dominated")
    assert decision["chosen_parent"] == "dominated"
    assert decision["explicit_parent"]
    assert "dominated" not in evaluation.frontier(data)
    data["candidates"]["dominated"]["feasible"] = False
    with pytest.raises(AuditError, match="verified and feasible"):
        search.choose_parent(data, "dominated")


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ({"strategy": "argmax", "objective": "cost"}, ["cheap"] * 3),
        ({"strategy": "argmax", "objective": "quality"}, ["accurate"] * 3),
        ({"strategy": "top_k", "objective": "cost", "k": 3}, ["cheap", "balanced", "dominated"]),
        ({"strategy": "epsilon_greedy", "objective": "cost", "epsilon": 0}, ["cheap"] * 3),
    ],
)
def test_scalar_selectors_use_explicit_direction_and_top_k_cycles_archive(policy, expected):
    data = search_run()
    search.set_policy(data, policy)
    before = evaluation.frontier(data)
    assert [d["chosen_parent"] for d in choices(data, 3)] == expected
    assert evaluation.frontier(data) == before


@pytest.mark.parametrize("strategy", ["epsilon_greedy", "softmax"])
def test_seeded_sampling_replays_identically_and_is_independent_of_dictionary_order(strategy):
    data = search_run()
    policy = {"strategy": strategy, "objective": "cost", "seed": 19}
    policy.update({"epsilon": 1} if strategy == "epsilon_greedy" else {"temperature": 100})
    search.set_policy(data, policy)
    replay = copy.deepcopy(data)
    replay["candidates"] = dict(reversed(list(replay["candidates"].items())))
    first = choices(data, 30)
    second = choices(replay, 30)
    assert [d["chosen_parent"] for d in first] == [d["chosen_parent"] for d in second]
    assert "dominated" in {d["chosen_parent"] for d in first}
    assert all(d["seed"] == 19 and d["index"] == index for index, d in enumerate(first))


def test_softmax_is_stable_for_extreme_finite_metrics():
    data = search_run()
    data["candidates"]["cheap"]["metrics"]["cost"] = -1e308
    data["candidates"]["accurate"]["metrics"]["cost"] = 1e308
    search.set_policy(data, {"strategy": "softmax", "objective": "cost", "temperature": 1e-300})
    assert search.choose_parent(data)["chosen_parent"] == "cheap"


def test_task_specialists_cycle_frozen_tasks_even_when_one_task_has_many_winners():
    data = search_run()
    data["spec"]["task_metrics"] = {
        "task_b": {"direction": "min", "unit": "ms"},
        "task_a": {"direction": "max", "unit": "fraction"},
    }
    for cid, record in data["candidates"].items():
        record["task_metrics"] = {
            "task_a": 1 if cid != "cheap" else 0,
            "task_b": 0 if cid == "cheap" else 1,
        }
    search.set_policy(data, {"strategy": "pareto_per_task"})
    selected = choices(data, 4)
    assert [d["task_id"] for d in selected] == ["task_a", "task_b", "task_a", "task_b"]
    assert [d["chosen_parent"] for d in selected] == ["accurate", "cheap", "balanced", "cheap"]
    assert "baseline" in selected[0]["selection_pool"]


def test_invalidated_lineage_is_excluded_but_exhausted_expansion_remains_on_frontier():
    data = search_run()
    data["candidates"]["accurate"]["expansion_exhausted"] = True
    assert "accurate" not in search.eligible(data)
    assert "accurate" in evaluation.frontier(data)
    data["candidates"]["cheap"]["invalidated"] = True
    data["candidates"]["balanced"]["parent_id"] = "cheap"
    assert search.eligible(data) == ["baseline", "dominated"]
    assert evaluation.frontier(data) == ["accurate", "dominated"]
    data["candidates"]["baseline"]["invalidated"] = True
    assert evaluation.frontier(data) == []
    with pytest.raises(AuditError, match="no eligible parent"):
        search.choose_parent(data)


def test_verified_infeasible_baseline_is_only_an_initial_seed():
    data = search_run()
    data["candidates"] = {"baseline": data["candidates"]["baseline"]}
    data["candidates"]["baseline"]["feasible"] = False
    assert search.choose_parent(data)["chosen_parent"] == "baseline"
    assert evaluation.frontier(data) == []
    successor = copy.deepcopy(data["candidates"]["baseline"])
    successor.update(candidate_id="repaired", feasible=True, expansion_exhausted=True)
    data["candidates"]["repaired"] = successor
    with pytest.raises(AuditError, match="no eligible parent"):
        search.choose_parent(data)


def test_policy_revisions_do_not_rewrite_prior_decisions_or_frozen_profile():
    data = search_run()
    choices(data, 1)
    previous = copy.deepcopy(data["search"]["decisions"])
    search.set_policy(data, {"strategy": "pareto"})
    assert data["search"]["revision"] == 1
    search.set_policy(data, {"strategy": "argmax", "objective": "cost"})
    assert data["search"]["revision"] == 2
    assert data["search"]["decisions"] == previous
    assert data["profile"] == {}
    assert search.choose_parent(data)["index"] == 1
    with pytest.raises(AuditError, match="stale"):
        search.reserve(data, previous[0], "stale", "stale")


@pytest.mark.parametrize(
    "policy",
    [
        {"strategy": "argmax"},
        {"strategy": "top_k", "objective": "cost", "k": 0},
        {"strategy": "top_k", "objective": "cost", "k": True},
        {"strategy": "epsilon_greedy", "objective": "cost", "epsilon": 1.1},
        {"strategy": "softmax", "objective": "cost", "temperature": 0},
        {"strategy": "softmax", "objective": "cost", "temperature": float("inf")},
        {"strategy": "pareto", "objective": "cost"},
        {"strategy": "pareto", "k": 2},
        {"strategy": "pareto", "seed": True},
        {"strategy": "unknown"},
    ],
)
def test_policy_validation_rejects_ambiguous_or_incompatible_settings(policy):
    with pytest.raises(AuditError):
        search.validate_policy(policy, search_run()["spec"])


def test_profile_search_and_tasks_validate_against_frozen_spec_before_run_creation(
    application, specification
):
    profile = Config().profile(application.root, "local")
    profile["search"] = {"strategy": "argmax", "objective": "unknown"}
    Config().update_profile("project", "local", profile, application.root)
    with pytest.raises(AuditError, match="frozen declared metric"):
        engine.start(application, specification, "local")
    assert not (application.state / "runs").exists()
    profile["search"] = {"strategy": "pareto_per_task"}
    Config().update_profile("project", "local", profile, application.root)
    with pytest.raises(AuditError, match="frozen task_metrics"):
        engine.start(application, specification, "local")
    assert not (application.state / "runs").exists()


def test_additive_profile_and_task_contracts_are_valid(application, specification):
    profile = Config().profile(application.root, "local")
    profile["search"] = {"strategy": "top_k", "objective": "latency", "seed": 4}
    profile["scans"] = {"max_scans": 3, "max_input_bytes": 4096, "scan_timeout_seconds": 30}
    normalized = validate_profile(profile)
    assert normalized["search"]["k"] == 3
    validate_record("fix-profile", normalized)
    specification["task_metrics"] = {"task_a": {"direction": "max", "unit": "fraction"}}
    normalized_spec = validate_spec(specification)
    validate_record("fix-spec", normalized_spec)
    assert normalized_spec["task_metrics"] == specification["task_metrics"]


def task_benchmark(application, specification, task_expression=None):
    task_expression = (
        task_expression
        or "{'task_a': application['latency'] + int(os.environ['AGENTAGON_SEED']), 'task_b': application['quality'], 'other': {'note': 'unscored evidence'}}"
    )
    content = fixtures.BENCHMARK.replace(
        "[{'seed': int(os.environ['AGENTAGON_SEED'])}]", task_expression
    )
    (application.root / "benchmark.py").write_text(content)
    git(application.root, "add", "benchmark.py")
    git(
        application.root,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@localhost",
        "commit",
        "-qm",
        "Add frozen task measurements",
    )
    specification["task_metrics"] = {
        "task_a": {"direction": "min", "unit": "ms"},
        "task_b": {"direction": "max", "unit": "fraction"},
    }


def test_real_repetitions_aggregate_declared_tasks_and_recheck_evidence(application, specification):
    task_benchmark(application, specification)
    started = engine.start(application, specification, "local")
    run_id, cid = started["run_id"], started["candidate_id"]
    measured = engine.run(application, run_id)
    assert measured["candidate"]["task_metrics"] == {"task_a": 101.0, "task_b": 0.8}
    assert measured["candidate"]["task_variation"]["task_a"]["samples"] == [100, 101, 102]
    trial = measured["candidate"]["trials"][0]
    output = json.loads(application.read_artifact(trial["artifact"])["benchmark_output"])
    assert output["tasks"]["other"] == {"note": "unscored evidence"}
    review = passing_review(measured)
    with locked(application, run_id):
        data = load_run(application, run_id)
        data["candidates"][cid]["task_metrics"]["task_a"] = 1
        save_run(application, data)
    with pytest.raises(AuditError, match="task summary"):
        engine.run(application, run_id, review=review)
    with locked(application, run_id):
        data = load_run(application, run_id)
        data["candidates"][cid]["task_metrics"]["task_a"] = 101.0
        save_run(application, data)
    verified = engine.run(application, run_id, review=review)
    assert verified["candidate"]["state"] == "verified"
    with locked(application, run_id):
        data = load_run(application, run_id)
        data["candidates"][cid]["trials"][0]["task_metrics"]["task_a"] = 0
        save_run(application, data)
    with pytest.raises(AuditError, match="measurement|tasks"):
        engine.run(application, run_id, review=review)


@pytest.mark.parametrize(
    "output",
    ["{'task_a': 1}", "{'task_a': 1, 'task_b': float('nan')}", "[1, 2]"],
)
def test_incomplete_or_nonfinite_frozen_tasks_fail_real_baseline(
    application, specification, output
):
    task_benchmark(application, specification, output)
    started = engine.start(application, specification, "local")
    result = engine.run(application, started["run_id"])
    assert result["candidate"]["state"] == "failed"
    assert len(result["candidate"]["trials"]) == 1
    assert result["frontier"] == []


def test_new_replays_reserved_choice_after_creation_failure_and_policy_change(
    application, specification, monkeypatch
):
    started = baseline(application, specification)
    run_id = started["run_id"]
    original_create = engine.checkouts.create

    def failed_after_creation(*args):
        original_create(*args)
        raise OSError("simulated interruption after Git worktree creation")

    with monkeypatch.context() as patch:
        patch.setattr(engine.checkouts, "create", failed_after_creation)
        with pytest.raises(OSError, match="simulated interruption"):
            engine.new(
                application,
                run_id,
                hypothesis="Reduce latency",
                author="host",
                operation_id="queued-one",
            )
    reserved = load_run(application, run_id)
    decision = copy.deepcopy(reserved["search"]["decisions"][0])
    cid = decision["candidate_id"]
    assert reserved["candidates"][cid]["creation_pending"]
    assert reserved["usage"]["candidates"] == 1
    with locked(application, run_id):
        search.set_policy(reserved, {"strategy": "softmax", "objective": "latency", "seed": 91})
        save_run(application, reserved)
    replay = engine.new(
        application, run_id, hypothesis="Reduce latency", author="host", operation_id="queued-one"
    )
    assert replay["candidate_id"] == cid
    assert not replay["candidate"]["creation_pending"]
    assert replay["search"]["decisions"] == [decision]
    assert replay["usage"]["candidates"] == 1
    path = application.root / replay["candidate"]["worktree"] / "app.json"
    path.write_text('{"quality":0.8,"latency":80,"variant":"retry"}')
    repeated = engine.new(
        application, run_id, hypothesis="Reduce latency", author="host", operation_id="queued-one"
    )
    assert repeated["candidate_id"] == cid
    assert json.loads(path.read_text())["latency"] == 80
    with pytest.raises(AuditError, match="another request"):
        engine.new(
            application,
            run_id,
            hypothesis="Different request",
            author="host",
            operation_id="queued-one",
        )
    assert verify(application, run_id, cid)["candidate"]["state"] == "verified"


def test_run_recovers_reserved_worktree_before_host_editing(
    application, specification, monkeypatch
):
    started = baseline(application, specification)
    run_id = started["run_id"]
    with monkeypatch.context() as patch:
        patch.setattr(
            engine.checkouts,
            "create",
            lambda *_: (_ for _ in ()).throw(OSError("creation interrupted")),
        )
        with pytest.raises(OSError):
            engine.new(application, run_id, hypothesis="Recover creation")
    data = load_run(application, run_id)
    cid = data["search"]["decisions"][0]["candidate_id"]
    assert not (application.root / data["candidates"][cid]["worktree"]).exists()
    recovered = engine.run(application, run_id, cid)
    assert recovered["candidate"]["state"] == "editing"
    assert not recovered["candidate"]["creation_pending"]
    assert recovered["candidate"]["trials"] == []


def test_real_archived_parent_remains_explorable_and_invalidated_lineage_cannot_run(
    application, specification
):
    started = baseline(application, specification)
    run_id, baseline_id = started["run_id"], started["candidate_id"]
    fast, _ = propose(application, run_id, latency=80)
    fast_id = fast["candidate_id"]
    assert verify(application, run_id, fast_id)["frontier"] == [fast_id]
    created = engine.new(
        application, run_id, parent_id=baseline_id, hypothesis="Try alternative baseline edit"
    )
    assert created["candidate"]["parent_id"] == baseline_id
    with locked(application, run_id):
        data = load_run(application, run_id)
        data["candidates"][baseline_id]["invalidated"] = True
        save_run(application, data)
    with pytest.raises(AuditError, match="invalidated"):
        engine.run(application, run_id, created["candidate_id"])
    with pytest.raises(AuditError, match="invalidated"):
        engine.select(application, run_id, fast_id)
    assert engine.status(application, run_id)["frontier"] == []


@pytest.mark.parametrize("kind", ["local", "ssh", "e2b"])
@pytest.mark.parametrize("supply_request", [False, True])
def test_prelaunch_cancellation_records_identity_without_process_or_remote_creation(
    tmp_path, monkeypatch, kind, supply_request
):
    profile = {"runner": {"kind": kind}, "env": {"TOKEN": "UNAVAILABLE_TEST_SECRET"}}
    monkeypatch.delenv("UNAVAILABLE_TEST_SECRET", raising=False)

    def unexpected(*args, **kwargs):
        raise AssertionError("cancelled work must not dispatch")

    monkeypatch.setattr(runners, "_transport", unexpected)
    monkeypatch.setattr(runners.subprocess, "Popen", unexpected)
    request = {**runner_fixtures.request(), "inputs_digest": "inputs", "profile_digest": "profile"}
    attempt = tmp_path / "attempt"
    result = runners.cancel(profile, attempt, request=request if supply_request else None)
    if not supply_request:
        assert result["state"] == "interrupted"
    result = runners.execute(profile, tmp_path / "source", attempt, request)
    assert result["state"] == "cancelled"
    assert result["finalized"] is True
    assert result["results"] == []
    assert result["inputs_digest"] == "inputs"
    assert result["profile_digest"] == "profile"
    assert not result["cleanup_pending"]
    assert not (attempt / "job").exists()
    assert runners.cancel(profile, attempt, request=request) == result
    assert runners.cleanup(profile, attempt)["cleanup_pending"] is False


def test_invalidated_reserved_trial_is_cancelled_without_dispatch_and_releases_capacity(
    application, specification
):
    started = baseline(application, specification)
    run_id = started["run_id"]
    created, _ = propose(application, run_id)
    cid = created["candidate_id"]
    with locked(application, run_id):
        data = load_run(application, run_id)
        candidate = data["candidates"][cid]
        engine._seal(application, data, candidate)
        trial = engine._reserve(application, data, candidate)
        candidate["invalidated"] = True
        save_run(application, data)
    result = engine.cancel_invalidated(application, run_id)
    assert result["state"] == "active"
    assert result["invalidated_active_attempts"] == []
    current = engine.status(application, run_id, cid)
    assert current["candidate"]["trials"][0]["state"] == "cancelled"
    assert not (application.root / trial["source_path"]).exists()
    receipt = application.read_artifact(current["candidate"]["trials"][0]["artifact"])
    assert receipt["attempt_id"] == trial["trial_id"]
    assert receipt["finalized"]
    assert len(fixtures.executions()) == 3  # Only the already completed baseline ran.
    next_candidate = engine.new(application, run_id, hypothesis="Replace invalidated proposal")
    assert next_candidate["candidate_id"] != cid
    assert next_candidate["usage"]["candidates"] == 2


def test_invalidation_cancels_live_owned_trial_without_stopping_other_search(
    application, specification
):
    recovery.install_gate(application, "application['variant'] == 'invalidated'")
    started = baseline(application, specification)
    run_id = started["run_id"]
    candidate, _ = propose(application, run_id, variant="invalidated")
    cid = candidate["candidate_id"]
    with recovery.invocation(application, run_id, cid) as (thread, outcome):
        recovery.wait_for_gate()
        with locked(application, run_id):
            data = load_run(application, run_id)
            data["candidates"][cid]["invalidated"] = True
            save_run(application, data)
        result = engine.cancel_invalidated(application, run_id)
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert "error" not in outcome
        assert result["state"] == "active"
        assert result["invalidated_active_attempts"] == []
        assert (
            engine.status(application, run_id, cid)["candidate"]["trials"][0]["state"]
            == "cancelled"
        )
    assert len([e for e in fixtures.executions() if e["variant"] == "invalidated"]) == 1
