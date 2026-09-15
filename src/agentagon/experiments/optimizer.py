"""Real GEPA composition with Agentagon native-host adapters and durable replay.

Upstream selects search parents; Agentagon remains authoritative for actual
trial admission, evidence, gates, independent verification and final selection.
An advance is finite: when host work is unavailable the synchronous engine
unwinds and exposes its durable request. Resume deterministically replays
recorded calls in a fresh upstream engine, never deserializing host pickle data.
"""

import fcntl
import math
import os
import tempfile
import threading
from dataclasses import asdict, dataclass

from agentagon.core.records import AuditError, load_json
from agentagon.experiments.budget import BudgetExhausted, BudgetLedger
from agentagon.experiments.host_bridge import HostBridge, HostWorkPending

GEPA_REVISION = "0632cdb5dcc052e690eab439e1b4a7e3e9cfe407"
ENGINES = {"omni", "gepa", "autoresearch", "meta_harness"}


@dataclass(frozen=True)
class MetaHarnessConfig:
    """Agentagon native-host overrides; judging configuration stays separate."""

    host: str | None = None
    model: str | None = None
    max_candidates_per_iter: int = 2


class MeasurementUnavailable(BaseException):
    """Preserve missing/failed observations instead of making up numeric scores."""


class OptimizationTargetReached(BaseException):
    """Pause search for bounded final verification of a measured target."""


class _QuietLogger:
    def log(self, message):
        pass


class _AttemptPreservingEngine:
    """A failed attempt ends its stage while other engines may still improve."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.name = delegate.name

    def run(self, task, server):
        from gepa.oa.engine import Result

        try:
            return self.delegate.run(task, server)
        except MeasurementUnavailable as exc:
            return Result(
                best_candidate=server.best_candidate,
                best_score=server.best_score,
                metadata={"measurement_failure": str(exc)},
            )

    def process_result(self, result, output_dir):
        self.delegate.process_result(result, output_dir)


class NativeAutoResearchEngine:
    """Agentagon host adapter: sequential hypotheses, measured keep-or-revert."""

    name = "agentagon-autoresearch"

    def __init__(self, config):
        self.propose = config.engine_config["propose"]

    def run(self, task, server):
        from gepa.oa.engine import Result

        best = task.seed_candidate
        best_score, feedback = server.evaluate(best)
        history = [{"candidate": best, "score": best_score, "feedback": feedback}]
        while not server.budget.exhausted:
            candidate = self.propose(
                best, {"history": history, "method": "hypothesis-measure-keep-or-revert"}
            )
            score, feedback = server.evaluate(candidate)
            history.append({"candidate": candidate, "score": score, "feedback": feedback})
            if score > best_score:
                best, best_score = candidate, score
        return Result(best_candidate=best, best_score=best_score)

    def process_result(self, result, output_dir):
        pass


class NativeMetaHarnessEngine(NativeAutoResearchEngine):
    """Agentagon host adapter: harness analysis and diverse candidate rounds."""

    name = "agentagon-meta-harness"

    def __init__(self, config):
        super().__init__(config)
        self.width = config.engine_config["max_candidates_per_iter"]

    def run(self, task, server):
        from gepa.oa.engine import Result

        best = task.seed_candidate
        best_score, feedback = server.evaluate(best)
        history = [{"candidate": best, "score": best_score, "feedback": feedback}]
        round_number = 0
        while not server.budget.exhausted:
            parent = best
            round_number += 1
            for branch in range(self.width):
                if server.budget.exhausted:
                    break
                candidate = self.propose(
                    parent,
                    {
                        "method": "analyze-harness-and-diversify",
                        "round": round_number,
                        "branch": branch,
                        "history": history,
                    },
                )
                score, feedback = server.evaluate(candidate)
                history.append({"candidate": candidate, "score": score, "feedback": feedback})
                if score > best_score:
                    best, best_score = candidate, score
        return Result(best_candidate=best, best_score=best_score)


class OptimizerCoordinator:
    def __init__(self, workspace, run_id: str):
        self.workspace = workspace
        self.run_id = run_id
        self.ledger = BudgetLedger(workspace, run_id)
        self.bridge = HostBridge(workspace, run_id)
        self.path = self.ledger.directory / "optimizer.json"

    def create(
        self,
        *,
        source: str,
        evaluator: str,
        seed: str,
        objective: str,
        scope: list[str],
        host: str,
        model: str,
        engine: str = "omni",
        host_concurrency: int = 1,
        meta_harness: MetaHarnessConfig | None = None,
    ) -> dict:
        if engine not in ENGINES:
            raise AuditError("optimizer must be omni, gepa, autoresearch or meta_harness")
        if type(host_concurrency) is not int or host_concurrency < 1:
            raise AuditError("actual host concurrency must be a positive integer")
        if not all(
            isinstance(v, str) and v.strip()
            for v in (source, evaluator, seed, objective, host, model)
        ):
            raise AuditError(
                "optimizer requires frozen source, evaluator, seed, goal and host/model"
            )
        meta_harness = meta_harness or MetaHarnessConfig()
        if (
            type(meta_harness.max_candidates_per_iter) is not int
            or meta_harness.max_candidates_per_iter < 1
        ):
            raise AuditError("Meta-Harness candidate width must be a positive integer")
        budget = self.ledger.snapshot()
        allocation = budget["allocations"]["optimization"]
        if allocation < (4 if engine == "omni" else 1):
            raise BudgetExhausted(
                "remaining optimization budget cannot cover the requested engines"
            )
        # Three quarters explore evenly; the final quarter is a fresh GEPA.
        if engine == "omni":
            refine = max(1, allocation // 4)
            share, remainder = divmod(allocation - refine, 3)
            stages = [
                {"id": name, "engine": name, "max_evals": share + (index < remainder)}
                for index, name in enumerate(("gepa", "autoresearch", "meta_harness"))
            ] + [{"id": "refinement", "engine": "gepa", "max_evals": refine}]
        else:
            stages = [{"id": engine, "engine": engine, "max_evals": allocation}]
        config = {
            "source": source,
            "evaluator": evaluator,
            "seed": seed,
            "objective": objective,
            "scope": scope,
            "host": host,
            "model": model,
            "engine": engine,
            "host_concurrency": host_concurrency,
            "meta_harness": asdict(meta_harness),
            "upstream_revision": GEPA_REVISION,
        }
        with self.ledger.locked():
            if self.path.exists():
                state = self.snapshot()
                if state["config"] != config:
                    raise AuditError("optimizer configuration is frozen for this run")
                return state
            state = {
                "version": 1,
                "run_id": self.run_id,
                "config": config,
                "stages": stages,
                "state": "ready",
            }
            self.workspace.write(self.path, state)
            return state

    def snapshot(self) -> dict:
        if not self.path.exists():
            raise AuditError("optimizer has not been configured")
        state = load_json(self.workspace.checked(self.path))
        if state.get("version") != 1 or state.get("run_id") != self.run_id:
            raise AuditError("incompatible optimizer state")
        return state

    def advance(self, evaluate_trial, *, host_handler=None, count_callback_as_trial=True) -> dict:
        """Run until complete, budget exhausted, or native work needs collection.

        ``evaluate_trial(candidate, operation_id=..., timeout_seconds=...)`` must
        return Agentagon's score observation (or an object containing ``score``).
        By default each callback is one actual trial. A bounded multi-trial
        pipeline sets ``count_callback_as_trial=False`` and admits each actual
        trial into this ledger itself. It must reuse existing executions on
        replay and use the supplied deadline for its runner.
        """
        try:
            from gepa.oa.ensemble import optimize_parallel_with_server
        except ImportError as exc:
            raise AuditError(
                "install the pinned GEPA optimizer dependency before optimizing"
            ) from exc
        self.ledger.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(
            self.ledger.directory / "optimizer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AuditError("optimizer coordinator is already advancing") from exc
            state = self.snapshot()
            if state["state"] == "completed":
                return self._view(state)
            semaphore = threading.BoundedSemaphore(state["config"]["host_concurrency"])
            stop_requested = threading.Event()
            config = state["config"]
            try:
                batches = (
                    [state["stages"][:-1], state["stages"][-1:]]
                    if config["engine"] == "omni"
                    else [state["stages"]]
                )
                for batch in batches:
                    unfinished = [
                        stage for stage in batch if "result" not in stage and "failure" not in stage
                    ]
                    if not unfinished:
                        continue
                    completed = [stage["result"] for stage in state["stages"] if "result" in stage]
                    seed = (
                        max(completed, key=lambda r: r["score"])["candidate"]
                        if completed and batch[0]["id"] == "refinement"
                        else config["seed"]
                    )
                    with tempfile.TemporaryDirectory(
                        prefix="gepa-", dir=self.ledger.directory
                    ) as run_dir:
                        entries = [
                            self._engine(
                                stage,
                                config,
                                seed,
                                run_dir,
                                semaphore,
                                evaluate_trial,
                                host_handler,
                                count_callback_as_trial,
                                stop_requested,
                            )
                            for stage in unfinished
                        ]
                        try:
                            # Public upstream composition, with actual host concurrency.
                            results = optimize_parallel_with_server(
                                [entry[0] for entry in entries],
                                [entry[1] for entry in entries],
                                max_workers=config["host_concurrency"],
                            )
                        finally:
                            for server, _ in entries:
                                server.stop()
                    for stage, result in zip(unfinished, results, strict=True):
                        if not math.isfinite(result.best_score):
                            stage["failure"] = result.metadata.get(
                                "measurement_failure", "optimizer stage has no measured score"
                            )
                        else:
                            stage["result"] = {
                                "candidate": result.best_candidate,
                                "score": result.best_score,
                                "engine": stage["engine"],
                            }
                            if result.metadata.get("measurement_failure"):
                                stage["failure"] = result.metadata["measurement_failure"]
                    self.workspace.write(self.path, state)
                state["state"] = (
                    "completed"
                    if any("result" in stage for stage in state["stages"])
                    else "measurement_unavailable"
                )
            except HostWorkPending:
                state["state"] = "host_pending"
            except OptimizationTargetReached:
                state["state"] = "target_reached"
            except BudgetExhausted as exc:
                state.update(state="budget_exhausted", reason=str(exc))
            except MeasurementUnavailable as exc:
                state.update(state="measurement_unavailable", reason=str(exc))
            self.workspace.write(self.path, state)
            return self._view(state)

    def _view(self, state):
        return {
            **state,
            "pending": self.bridge.pending(),
            "budget": self.ledger.snapshot(),
            "selection": "requires_agentagon_independent_verification",
        }

    def _engine(
        self,
        stage,
        config,
        seed,
        run_dir,
        semaphore,
        evaluator,
        host_handler,
        count_trial,
        stop_requested,
    ):
        from gepa.oa.budget import BudgetTracker
        from gepa.oa.config import OptimizeAnythingConfig
        from gepa.oa.eval_server import EvalServer
        from gepa.oa.task import Task

        counters = {"proposal": 0, "evaluation": 0}
        route = config["meta_harness"] if stage["engine"] == "meta_harness" else {}
        host, model = route.get("host") or config["host"], route.get("model") or config["model"]

        def propose(candidate, feedback):
            if stop_requested.is_set():
                raise OptimizationTargetReached()
            index = counters["proposal"]
            counters["proposal"] += 1
            request = self.bridge.request(
                f"{stage['id']}:proposal:{index}",
                source=config["source"],
                evaluator=config["evaluator"],
                role="proposal",
                scope=config["scope"],
                host=host,
                model=model,
                payload={
                    "engine": stage["engine"],
                    "stage": stage["id"],
                    "candidate": candidate,
                    "feedback": feedback,
                    "objective": config["objective"],
                },
            )
            if request["state"] == "pending" and host_handler is not None:
                with semaphore:
                    claimed = self.bridge.start(request["request_id"])
                    if not claimed["replay"]:
                        response = host_handler(claimed)
                        request = self.bridge.reply(
                            request["request_id"],
                            response,
                            host=host,
                            model=model,
                            binding_digest=request["binding_digest"],
                        )
            if request["state"] == "cancelled":
                raise MeasurementUnavailable("native-host proposal was cancelled")
            if request["state"] != "completed":
                raise HostWorkPending(request["request_id"])
            if request.get("deadline_exceeded"):
                raise BudgetExhausted("native-host proposal exceeded its admitted deadline")
            proposed = request["response"].get("candidate")
            if not isinstance(proposed, str) or not proposed.strip():
                raise AuditError(
                    "native-host proposal must return candidate text or a source identity"
                )
            return proposed

        def evaluate(candidate, example=None, **kwargs):
            if stop_requested.is_set():
                raise OptimizationTargetReached()
            index = counters["evaluation"]
            counters["evaluation"] += 1
            operation_id = f"optimizer:{stage['id']}:evaluation:{index}"
            binding = {
                "candidate": candidate,
                "evaluator": config["evaluator"],
                "source": config["source"],
            }
            with semaphore:
                operation = self.ledger.admit(
                    operation_id, "optimization", units=int(count_trial), binding=binding
                )
                if operation["replay"]:
                    if operation["status"] == "running":
                        raise HostWorkPending(operation_id)
                    if operation["status"] != "completed":
                        raise MeasurementUnavailable(
                            f"recorded {operation['status']} evaluation: {operation_id}"
                        )
                    observation = operation["result"]
                else:
                    try:
                        observation = evaluator(
                            candidate,
                            operation_id=operation_id,
                            timeout_seconds=operation["timeout_seconds"],
                        )
                        if not isinstance(observation, dict) or not isinstance(
                            observation.get("score", observation), dict
                        ):
                            raise AuditError(
                                "evaluation must return a structured score observation"
                            )
                    except OptimizationTargetReached:
                        stop_requested.set()
                        raise
                    except BudgetExhausted:
                        raise
                    except Exception as exc:
                        self.ledger.finish(
                            operation_id, status="failed", result={"error": str(exc)}
                        )
                        raise MeasurementUnavailable(
                            f"application evaluation failed: {operation_id}"
                        ) from exc
                    self.ledger.finish(operation_id, result=observation)
                score = observation.get("score", observation)
                value = score.get("value")
                if (
                    score.get("state") != "measured"
                    or isinstance(value, bool)
                    or not isinstance(value, (float, int))
                    or not math.isfinite(value)
                ):
                    raise MeasurementUnavailable(
                        f"unmeasured application evaluation: {operation_id}"
                    )
                # Gates remain in the durable observation and final selection.
                # Feasibility is not a made-up numeric penalty.
                return float(value), {"observation": observation, "operation_id": operation_id}

        server = EvalServer(
            Task(
                name=f"{self.run_id}-{stage['id']}",
                seed_candidate=seed,
                objective=config["objective"],
            ),
            evaluate,
            BudgetTracker(max_evals=stage["max_evals"]),
            max_concurrency=1,
        )
        if stage["engine"] == "gepa":
            from gepa.oa.engines.gepa import GepaEngine

            def gepa_propose(candidate, reflective_dataset, components_to_update, **kwargs):
                updated = propose(next(iter(candidate.values())), dict(reflective_dataset))
                return {key: updated for key in components_to_update}

            upstream_config = OptimizeAnythingConfig(
                engine="gepa",
                run_dir=f"{run_dir}/{stage['id']}",
                engine_config={
                    "engine": {"parallel": False, "use_cloudpickle": False, "seed": 0},
                    "reflection": {
                        "reflection_lm": None,
                        "custom_candidate_proposer": gepa_propose,
                    },
                    "tracking": {"logger": _QuietLogger()},
                },
            )
            upstream_config = OptimizeAnythingConfig(
                engine=_AttemptPreservingEngine(GepaEngine(upstream_config))
            )
        else:
            cls = (
                NativeAutoResearchEngine
                if stage["engine"] == "autoresearch"
                else NativeMetaHarnessEngine
            )
            native = cls(
                OptimizeAnythingConfig(
                    engine_config={
                        "propose": propose,
                        "max_candidates_per_iter": config["meta_harness"][
                            "max_candidates_per_iter"
                        ],
                    }
                )
            )
            upstream_config = OptimizeAnythingConfig(engine=_AttemptPreservingEngine(native))
        return server, upstream_config
