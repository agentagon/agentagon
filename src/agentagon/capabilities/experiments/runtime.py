"""Agentagon's in-process optimizer runtime.

Run bounded evaluation stages with native-host proposal callbacks and parallel
engine execution. The GEPA engine uses the published dependency for search;
Agentagon's durable ledger owns trial admission and evidence.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


class EvalBudgetExhausted(Exception):
    """The current optimizer stage has used its evaluation allowance."""


class MeasurementUnavailable(BaseException):
    """Preserve missing/failed observations instead of making up numeric scores."""


@dataclass
class Result:
    best_candidate: str
    best_score: float
    failure: str | None = None


@dataclass
class Task:
    seed_candidate: str
    objective: str
    background: str = ""


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
    """Admit bounded concurrent evaluations; Agentagon's ledger owns durable evidence."""

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
            self.budget.used += 1
        score, info = self.eval_fn(candidate, example, **kwargs)
        with self._lock:
            if score > self.best_score:
                self.best_candidate, self.best_score = candidate, score
        # Scheduling-dependent counters must not enter upstream reflection feedback:
        # replay must assemble exactly the same prompt regardless of completion order.
        return score, info


class GepaEngine:
    """Run GEPA search with bounded native-host callbacks."""

    def __init__(self, run_dir, **settings):
        from gepa.optimize_anything import GEPAConfig

        self.config = GEPAConfig(**settings)
        self.config.engine.run_dir = run_dir

    def run(self, task, server):
        from gepa.optimize_anything import optimize_anything

        self.config.engine.max_metric_calls = server.budget.max_evals
        try:
            result = optimize_anything(
                seed_candidate=task.seed_candidate,
                evaluator=server.evaluate,
                objective=task.objective,
                background=task.background,
                config=self.config,
            )
        except EvalBudgetExhausted:
            # This directory belongs to the current advance, never a host reply
            # or a persisted optimizer resume. Retain upstream's completed best.
            result = self._load_result()
        if result is None:
            return Result(server.best_candidate, server.best_score)
        return Result(result.best_candidate, result.val_aggregate_scores[result.best_idx])

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


def run_stages(stages, *, max_workers):
    """Run caller-owned stages in input order with bounded concurrency."""

    def run(entry):
        server, engine = entry
        try:
            return engine.run(server.task, server)
        except EvalBudgetExhausted:
            return Result(server.best_candidate, server.best_score)
        except MeasurementUnavailable as exc:
            return Result(server.best_candidate, server.best_score, failure=str(exc))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(run, stages))
