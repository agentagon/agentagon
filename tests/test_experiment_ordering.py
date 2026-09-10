import copy
from itertools import permutations

import pytest

from agentagon.core.records import now
from agentagon.experiments import engine, evaluation


def specification():
    return {
        "metrics": {
            "cost": {"direction": "min", "unit": "USD"},
            "latency": {"direction": "min", "unit": "ms"},
            "quality": {"direction": "max", "unit": "score"},
        },
        "constraints": [],
        "repetitions": 3,
    }


def candidate(name, cost, latency, quality, *, state="verified", feasible=True):
    return {
        "candidate_id": name,
        "state": state,
        "feasible": feasible,
        "metrics": {"cost": cost, "latency": latency, "quality": quality},
        "trials": [],
    }


@pytest.mark.parametrize("order", list(permutations(range(4))))
def test_frontier_retains_tradeoffs_independent_of_completion_order(order):
    records = [
        candidate("baseline", 10, 100, 0.9),
        candidate("cheaper", 5, 130, 0.9),
        candidate("faster", 12, 60, 0.95),
        candidate("dominated", 14, 150, 0.85),
    ]
    data = {"spec": specification(), "candidates": {}}
    for index in order:
        record = records[index]
        data["candidates"][record["candidate_id"]] = record
        # Reading the frontier at each completion must not change later admission.
        evaluation.frontier(data)
    assert evaluation.frontier(data) == ["baseline", "cheaper", "faster"]


def test_equal_vectors_are_nondominated_and_do_not_erase_lineage():
    left = candidate("a", 5, 100, 0.95)
    right = candidate("b", 5, 100, 0.95)
    spec = specification()
    assert not evaluation.dominates(spec, left["metrics"], right["metrics"])
    assert not evaluation.dominates(spec, right["metrics"], left["metrics"])
    assert evaluation.frontier({"spec": spec, "candidates": {"b": right, "a": left}}) == [
        "a",
        "b",
    ]


def test_unreviewed_or_infeasible_candidates_cannot_remove_verified_tradeoffs():
    records = [
        candidate("baseline", 10, 100, 0.9),
        candidate("unreviewed", 1, 1, 1, state="awaiting_review"),
        candidate("constraint_failed", 1, 1, 1, feasible=False),
        candidate("rejected", 1, 1, 1, state="rejected"),
    ]
    data = {
        "spec": specification(),
        "candidates": {record["candidate_id"]: record for record in records},
    }
    assert evaluation.frontier(data) == ["baseline"]


def test_descendant_constraints_use_original_baseline_with_no_cumulative_drift():
    spec = specification()
    spec["constraints"] = [
        {"metric": "quality", "op": "gte", "bound": -0.02, "reference": "baseline_delta"},
        {"metric": "latency", "op": "lte", "bound": 1.5, "reference": "baseline_ratio"},
        {"metric": "cost", "op": "lte", "bound": 7, "reference": "absolute"},
    ]
    baseline = candidate("baseline", 10, 100, 0.9)
    parent = candidate("parent", 6, 140, 0.89)
    descendant = candidate("descendant", 5, 155, 0.875, state="sealed", feasible=False)
    descendant["parent_id"] = "parent"
    descendant["trials"] = [
        {
            "state": "completed",
            "repetition": repetition,
            "metrics": copy.deepcopy(descendant["metrics"]),
            "checks": [],
        }
        for repetition in range(spec["repetitions"])
    ]
    data = {
        "baseline_id": "baseline",
        "spec": spec,
        "candidates": {"baseline": baseline, "parent": parent, "descendant": descendant},
    }
    engine._summarize(data, descendant)
    assert [rule["threshold"] for rule in descendant["constraints"]] == [0.88, 150, 7]
    assert [rule["passed"] for rule in descendant["constraints"]] == [False, False, True]
    assert descendant["state"] == "awaiting_review"
    assert descendant["feasible"] is False


def test_aggregation_uses_all_repetitions_and_preserves_observed_spread():
    spec = specification()
    samples = [
        {"cost": 1, "latency": 120, "quality": 0.9},
        {"cost": 9, "latency": 50, "quality": 0.8},
        {"cost": 2, "latency": 100, "quality": 0.95},
    ]
    metrics, variation = evaluation.aggregate(spec, samples)
    assert metrics == {"cost": 2, "latency": 100, "quality": 0.9}
    assert variation["latency"] == {"min": 50, "max": 120, "samples": [120, 50, 100]}


def run_with_rounds(records, rounds, *, stagnation_limit=3):
    baseline = candidate("baseline", 10, 100, 0.9)
    return {
        "baseline_id": "baseline",
        "created_at": now(),
        "state": "active",
        "spec": specification(),
        "candidates": {record["candidate_id"]: record for record in [baseline, *records]},
        "rounds": [
            {"round_id": f"round_{index}", "size": len(members), "candidates": members}
            for index, members in enumerate(rounds)
        ],
        "stagnation": 0,
        "limits": {
            "max_elapsed_seconds": 600,
            "stagnation_rounds": stagnation_limit,
            "max_candidates": 100,
            "max_trials": 1000,
        },
        "usage": {"candidates": len(records), "trials": 0, "elapsed_seconds": 0},
    }


def test_equal_valued_patch_preserves_lineage_but_exhausts_stagnation_limit():
    equal = candidate("same_values", 10, 100, 0.9)
    data = run_with_rounds([equal], [["same_values"]], stagnation_limit=1)
    engine._update(data)
    assert data["frontier"] == ["baseline", "same_values"]
    assert data["stagnation"] == 1
    assert data["state"] == "exhausted"
    engine._update(data)
    assert data["stagnation"] == 1  # Repeated status saves cannot recount a round.


def test_later_round_improvement_does_not_get_credited_to_earlier_round():
    first = candidate("first", 12, 130, 0.8, state="running", feasible=False)
    second = candidate("second", 5, 100, 0.9)
    data = run_with_rounds([first, second], [["first"], ["second"]])
    engine._update(data)
    assert data["frontier"] == ["second"]
    assert all(not round_.get("completed") for round_ in data["rounds"])
    first["state"] = "rejected"
    engine._update(data)
    assert data["rounds"][0]["frontier"] == ["baseline"]
    assert data["rounds"][1]["frontier"] == ["second"]
    assert data["stagnation"] == 0


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_round_stagnation_is_independent_of_trial_completion_order(order):
    records = [
        candidate("improved", 5, 100, 0.9, state="running"),
        candidate("equal", 5, 100, 0.9, state="running"),
        candidate("failed", 1, 1, 1, state="running", feasible=False),
    ]
    data = run_with_rounds(records, [[record["candidate_id"]] for record in records])
    for index in order:
        records[index]["state"] = "failed" if index == 2 else "verified"
        engine._update(data)
    assert data["frontier"] == ["equal", "improved"]
    assert data["stagnation"] == 2
    assert [round_["frontier"] for round_ in data["rounds"]] == [
        ["improved"],
        ["equal", "improved"],
        ["equal", "improved"],
    ]


def test_cancelled_candidate_completes_round_without_admission_or_resuming_stop():
    cancelled = candidate("cancelled", 1, 1, 1, state="failed", feasible=False)
    cancelled["trials"] = [{"state": "cancelled", "cleanup_pending": True}]
    pending = candidate("pending", 1, 1, 1, state="running", feasible=False)
    data = run_with_rounds([cancelled, pending], [["cancelled", "pending"]])
    data["state"] = "stopped"
    engine._update(data)
    assert not data["rounds"][0].get("completed")
    assert data["stagnation"] == 0
    pending["state"] = "failed"
    engine._update(data)
    assert data["rounds"][0]["completed"]
    assert data["stagnation"] == 1
    assert data["frontier"] == ["baseline"]
    assert data["cleanup_pending"] is True
    assert data["state"] == "stopped"
