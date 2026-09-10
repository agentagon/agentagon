"""Reproducible parent selection; verification and Pareto admission stay separate."""

import copy
import math
import random

from agentagon.core.records import AuditError, digest, now
from agentagon.experiments import evaluation
from agentagon.experiments.spec import NAME, finite, object_keys

STRATEGIES = {"pareto", "argmax", "top_k", "epsilon_greedy", "softmax", "pareto_per_task"}
SCALAR = {"argmax", "top_k", "epsilon_greedy", "softmax"}


def validate_policy(policy: dict, spec: dict | None = None) -> dict:
    """Normalize policy without inventing a scalar objective for multiple metrics."""
    policy = copy.deepcopy(
        object_keys(
            policy,
            {"strategy", "seed", "objective", "k", "epsilon", "temperature"},
            set(),
            "search policy",
        )
    )
    strategy = policy.setdefault("strategy", "pareto")
    if not isinstance(strategy, str) or strategy not in STRATEGIES:
        raise AuditError("unsupported search strategy")
    policy.setdefault("seed", 0)
    if type(policy["seed"]) is not int:
        raise AuditError("search seed must be an integer")
    allowed = {"strategy", "seed"}
    if strategy in SCALAR:
        allowed.add("objective")
        objective = policy.get("objective")
        if not isinstance(objective, str) or not NAME.fullmatch(objective):
            raise AuditError("scalar search requires an explicit objective metric")
        if spec is not None and objective not in spec["metrics"]:
            raise AuditError("search objective must be a frozen declared metric")
    if strategy == "top_k":
        allowed.add("k")
        policy.setdefault("k", 3)
        if type(policy["k"]) is not int or policy["k"] < 1:
            raise AuditError("top_k requires a positive integer k")
    elif strategy == "epsilon_greedy":
        allowed.add("epsilon")
        policy["epsilon"] = finite(policy.get("epsilon", 0.1))
        if not 0 <= policy["epsilon"] <= 1:
            raise AuditError("epsilon must be between zero and one")
    elif strategy == "softmax":
        allowed.add("temperature")
        policy["temperature"] = finite(policy.get("temperature", 1.0))
        if policy["temperature"] <= 0:
            raise AuditError("softmax temperature must be positive in the objective's units")
    elif strategy == "pareto_per_task" and spec is not None and not spec.get("task_metrics"):
        raise AuditError("task-specialist search requires frozen task_metrics")
    if set(policy) - allowed:
        raise AuditError("search policy contains options for another strategy")
    return policy


def set_policy(data: dict, policy: dict) -> None:
    """Record future search policy; callers hold the run lock and persist the run."""
    normalized = validate_policy(policy, data["spec"])
    state = data.setdefault(
        "search", {"revision": 0, "revisions": [], "decision_index": 0, "decisions": []}
    )
    if state.get("policy") == normalized:
        return
    state["revision"] += 1
    state["policy"] = normalized
    state["revisions"].append(
        {"revision": state["revision"], "policy": copy.deepcopy(normalized), "at": now()}
    )


def _state(data: dict) -> dict:
    if "search" not in data:
        set_policy(data, data.get("profile", {}).get("search", {}))
    return data["search"]


def eligible(data: dict, *, frontier_only: bool = False) -> list[str]:
    """Verified, feasible archive members, with a verified initial baseline exception."""
    records = data["candidates"]
    members = [
        cid
        for cid, candidate in records.items()
        if candidate["state"] == "verified"
        and candidate.get("feasible")
        and not candidate.get("expansion_exhausted")
        and not evaluation.invalidated(data, candidate)
    ]
    if frontier_only:
        frontier = set(evaluation.frontier(data))
        members = [cid for cid in members if cid in frontier]
    # Expected defects and violated constraints must not prevent the first repair.
    # Once a feasible successor was verified, an infeasible baseline is no fallback.
    baseline_id = data["baseline_id"]
    baseline = records[baseline_id]
    has_successor = any(
        cid != baseline_id and c["state"] == "verified" and c.get("feasible")
        for cid, c in records.items()
    )
    if (
        not members
        and not has_successor
        and baseline["state"] == "verified"
        and not baseline.get("expansion_exhausted")
        and not evaluation.invalidated(data, baseline)
    ):
        members = [baseline_id]
    return sorted(members)


def choose_parent(data: dict, parent_id: str | None = None) -> dict:
    """Prepare a decision for durable reservation; sampling itself has no side effects."""
    state = _state(data)
    policy = validate_policy(state["policy"], data["spec"])
    strategy = policy["strategy"]
    members = eligible(data, frontier_only=parent_id is None and strategy == "pareto")
    if not members:
        raise AuditError("no eligible parent remains; inspect invalidation and expansion limits")
    if parent_id is not None and parent_id not in members:
        raise AuditError("parent must be verified and feasible, or the eligible initial baseline")
    records = data["candidates"]
    index = state["decision_index"]
    rng = random.Random(digest([policy["seed"], state["revision"], index]))
    ranked = members
    if strategy in SCALAR:
        objective = policy["objective"]
        direction = data["spec"]["metrics"][objective]["direction"]

        def utility(cid):
            value = finite(records[cid]["metrics"][objective])
            return value if direction == "max" else -value

        ranked = sorted(members, key=lambda cid: (-utility(cid), cid))
    selection_pool = list(members)
    selected_task = None
    task_winners = []
    if parent_id is not None:
        selection_pool = [parent_id]
        chosen = parent_id
    elif strategy == "argmax":
        selection_pool = ranked[:1]
        chosen = ranked[0]
    elif strategy == "top_k":
        selection_pool = ranked[: policy["k"]]
        offset = sum(
            d["policy_revision"] == state["revision"] and not d["explicit_parent"]
            for d in state["decisions"]
        )
        chosen = selection_pool[offset % len(selection_pool)]
    elif strategy == "epsilon_greedy":
        chosen = rng.choice(members) if rng.random() < policy["epsilon"] else ranked[0]
    elif strategy == "softmax":
        best = utility(ranked[0])
        weights = [math.exp((utility(cid) - best) / policy["temperature"]) for cid in members]
        chosen = rng.choices(members, weights=weights, k=1)[0]
    else:
        if strategy == "pareto_per_task":
            specialists = set()
            selected_task = sorted(data["spec"]["task_metrics"])[
                index % len(data["spec"]["task_metrics"])
            ]
            for task_id, definition in data["spec"]["task_metrics"].items():
                values = {
                    cid: finite(records[cid].get("task_metrics", {}).get(task_id))
                    for cid in members
                }
                best = (min if definition["direction"] == "min" else max)(values.values())
                winners = sorted(cid for cid, value in values.items() if value == best)
                specialists.update(winners)
                if task_id == selected_task:
                    task_winners = winners
            selection_pool = sorted(specialists)
        chosen = min(
            task_winners or selection_pool,
            key=lambda cid: (records[cid].get("last_expanded", 0), cid),
        )
    return {
        "index": index,
        "policy_revision": state["revision"],
        "policy": copy.deepcopy(policy),
        "seed": policy["seed"],
        "eligible": [
            {
                "candidate_id": cid,
                "metrics": copy.deepcopy(records[cid]["metrics"]),
                "task_metrics": copy.deepcopy(records[cid].get("task_metrics", {})),
                "last_expanded": records[cid].get("last_expanded", 0),
            }
            for cid in members
        ],
        "selection_pool": selection_pool,
        "task_id": selected_task,
        "chosen_parent": chosen,
        "explicit_parent": parent_id is not None,
    }


def reserve(data: dict, decision: dict, candidate_id: str, operation_id: str | None) -> None:
    """Record a prepared choice before candidate worktree creation, under the run lock."""
    state = _state(data)
    if (
        decision["index"] != state["decision_index"]
        or decision["policy_revision"] != state["revision"]
    ):
        raise AuditError("search decision became stale before reservation")
    state["decisions"].append(
        {
            **copy.deepcopy(decision),
            "candidate_id": candidate_id,
            "operation_id": operation_id,
            "at": now(),
        }
    )
    state["decision_index"] += 1
