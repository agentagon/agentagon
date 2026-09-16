"""Optimizer stages bound real calls without fabricating scores."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from agentagon.experiments.runtime import (
    BudgetTracker,
    EvalBudgetExhausted,
    EvalServer,
    Task,
)


def test_concurrent_requests_cannot_exceed_stage_allowance():
    calls = []

    def evaluate(candidate, example, **kwargs):
        calls.append(candidate)
        return float(candidate), {"candidate": candidate}

    server = EvalServer(Task("check", "0", "increase score"), evaluate, BudgetTracker(3))

    def attempt(candidate):
        try:
            return server.evaluate(candidate)
        except EvalBudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, [str(n) for n in range(8)]))
    assert len(calls) == 3
    assert sum(result is not None for result in results) == 3
    assert server.budget.used == 3
    assert server.best_score == max(float(value) for value in calls)
    assert server.best_candidate in calls


def test_failed_evaluation_consumes_attempt_without_becoming_a_zero_score():
    def evaluate(candidate, example, **kwargs):
        if candidate == "failed":
            raise RuntimeError("execution failed")
        return -1.0, {}

    server = EvalServer(Task("check", "seed", "increase score"), evaluate, BudgetTracker(2))
    server.evaluate("measured")
    with pytest.raises(RuntimeError, match="execution failed"):
        server.evaluate("failed")
    assert server.budget.exhausted
    assert server.best_candidate == "measured"
    assert server.best_score == -1.0
    with pytest.raises(EvalBudgetExhausted):
        server.evaluate("over budget")
