"""Small in-process subset of GEPA's unreleased optimizer orchestration.

Adapted from GEPA 0632cdb5dcc052e690eab439e1b4a7e3e9cfe407 (MIT): oa/budget.py,
oa/config.py, oa/task.py, oa/engine.py, oa/eval_server.py, oa/ensemble.py and
oa/engines/gepa.py.
Copyright © 2025 Lakshya A Agrawal. See THIRD_PARTY_NOTICES.txt.

Only Agentagon's single-candidate, native-host path is retained. The search
algorithm comes from the published gepa dependency. No HTTP server, external
host launcher, dataset routing, registry or upstream example is bundled.

TODO: Remove this module when a PyPI GEPA release provides the required oa APIs
and passes test_optimizer.py and test_optimize_run.py, including replay,
concurrency, repeated-trial budgets, failure preservation and target stopping.
See docs/contributing/gepa-compatibility.md for the removal checklist.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


class EvalBudgetExhausted(Exception):
    """The current optimizer stage has used its evaluation allowance."""


@dataclass
class Result:
    best_candidate: str
    best_score: float
    metadata: dict = field(default_factory=dict)


@dataclass
class Task:
    name: str
    seed_candidate: str
    objective: str


@dataclass
class OptimizeAnythingConfig:
    engine: object = None
    run_dir: str | None = None
    engine_config: dict = field(default_factory=dict)


@dataclass
class BudgetTracker:
    max_evals: int
    used: int = field(default=0, init=False)

    @property
    def exhausted(self):
        return self.used >= self.max_evals

    def check(self):
        if self.exhausted:
            raise EvalBudgetExhausted(f"Eval budget exhausted: {self.used}/{self.max_evals} used")

    def status(self):
        return {
            "exhausted": self.exhausted,
            "max_evals": self.max_evals,
            "used": self.used,
            "remaining_evals": max(0, self.max_evals - self.used),
        }


class EvalServer:
    """Serialize stage evaluations; Agentagon's ledger owns durable evidence."""

    def __init__(self, task, evaluate, budget):
        self.task = task
        self.eval_fn = evaluate
        self.budget = budget
        self.best_candidate = task.seed_candidate
        self.best_score = float("-inf")
        self._lock = threading.Lock()

    def evaluate(self, candidate, example=None, **kwargs):
        with self._lock:
            self.budget.check()
            try:
                score, info = self.eval_fn(candidate, example, **kwargs)
            except Exception:
                self.budget.used += 1
                raise
            self.budget.used += 1
            if score > self.best_score:
                self.best_candidate, self.best_score = candidate, score
            return score, {**info, "_budget": self.budget.status()}


class GepaEngine:
    """Bridge the published GEPA algorithm to bounded native-host callbacks."""

    name = "gepa"

    def __init__(self, config):
        from gepa.optimize_anything import GEPAConfig

        self.config = GEPAConfig(**config.engine_config)
        self.config.engine.run_dir = config.run_dir

    def run(self, task, server):
        from gepa.optimize_anything import optimize_anything

        self.config.engine.max_metric_calls = server.budget.max_evals
        try:
            result = optimize_anything(
                seed_candidate=task.seed_candidate,
                evaluator=server.evaluate,
                objective=task.objective,
                config=self.config,
            )
        except EvalBudgetExhausted:
            # This directory belongs to the current advance, never a host reply
            # or a persisted optimizer resume. Retain upstream's completed best.
            result = self._load_result()
        if result is None:
            return Result(server.best_candidate, server.best_score)
        best = result.best_candidate
        if isinstance(best, dict):
            best = next(iter(best.values()), "")
        return Result(best, result.val_aggregate_scores[result.best_idx])

    def _load_result(self):
        from gepa.core.result import GEPAResult
        from gepa.core.state import GEPAState

        try:
            state = GEPAState.load(self.config.engine.run_dir)
            return GEPAResult.from_state(
                state,
                run_dir=self.config.engine.run_dir,
                seed=self.config.engine.seed,
                str_candidate_key="current_candidate",
            )
        except (OSError, ValueError, KeyError, AssertionError):
            return None


def optimize_parallel_with_server(servers, configs, *, max_workers):
    """Run caller-owned stages in input order with bounded concurrency."""
    if not configs or len(servers) != len(configs):
        raise ValueError("each optimizer stage needs one server and one engine")

    def run(entry):
        server, config = entry
        try:
            return config.engine.run(server.task, server)
        except EvalBudgetExhausted:
            return Result(server.best_candidate, server.best_score)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(run, zip(servers, configs, strict=True)))
