"""Coordinate optimizer engines, native-host callbacks and durable replay.

Upstream selects search parents; Agentagon remains authoritative for actual
trial admission, evidence, gates, independent verification and final selection.
An advance is finite: when host work is unavailable the synchronous engine
unwinds and exposes its durable request. Resume deterministically replays
recorded calls in a fresh upstream engine, never deserializing host pickle data.
"""

import fcntl
import json
import math
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from agentagon.capabilities.experiments.budget import BudgetExhausted, BudgetLedger
from agentagon.capabilities.experiments.host_bridge import HostBridge, HostWorkPending
from agentagon.capabilities.experiments.runtime import MeasurementUnavailable
from agentagon.core.records import AuditError, digest, encoded, load_json, validate_record

GEPA_REVISION = "0632cdb5dcc052e690eab439e1b4a7e3e9cfe407"
GEPA_VERSION = "0.1.4"
# Persisted identity stays stable across module renames.
RUNTIME_VERSION = "agentagon-gepa-reflection-v3"
ENGINES = {"omni", "gepa", "autoresearch", "meta_harness"}
MAX_BACKGROUND_BYTES = 128 * 1024


def validate_background(background):
    if not isinstance(background, str) or len(background.encode("utf-8")) > MAX_BACKGROUND_BYTES:
        raise AuditError("optimization background must be text of at most 128 KiB")
    return background


@dataclass(frozen=True)
class MetaHarnessConfig:
    """Agentagon native-host overrides; judging configuration stays separate."""

    host: str | None = None
    model: str | None = None
    max_candidates_per_iter: int = 2


class OptimizationTargetReached(BaseException):
    """Pause search for bounded final verification of a measured target."""


class _QuietLogger:
    def log(self, message):
        pass


class NativeAutoResearchEngine:
    """Agentagon host adapter: sequential hypotheses, measured keep-or-revert."""

    def __init__(self, propose, width=1):
        self.propose = propose
        self.width = width

    def run(self, task, server):
        from agentagon.capabilities.experiments.runtime import Result

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
                candidate = self.propose(parent, self._feedback(history, round_number, branch))
                score, feedback = server.evaluate(candidate)
                history.append({"candidate": candidate, "score": score, "feedback": feedback})
                if score > best_score:
                    best, best_score = candidate, score
        return Result(best_candidate=best, best_score=best_score)

    def _feedback(self, history, round_number, branch):
        return {"history": history, "method": "hypothesis-measure-keep-or-revert"}


class NativeMetaHarnessEngine(NativeAutoResearchEngine):
    """Agentagon host adapter: harness analysis and diverse candidate rounds."""

    def _feedback(self, history, round_number, branch):
        return {
            "method": "analyze-harness-and-diversify",
            "round": round_number,
            "branch": branch,
            "history": history,
        }


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
        background: str = "",
        engine: str = "omni",
        host_concurrency: int = 1,
        meta_harness: MetaHarnessConfig | None = None,
    ) -> dict:
        if engine not in ENGINES:
            raise AuditError("optimizer must be omni, gepa, autoresearch or meta_harness")
        validate_background(background)
        if type(host_concurrency) is not int or not 1 <= host_concurrency <= 64:
            raise AuditError("actual host concurrency must be between 1 and 64")
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
            "background": background,
            "scope": scope,
            "host": host,
            "model": model,
            "engine": engine,
            "host_concurrency": host_concurrency,
            "meta_harness": asdict(meta_harness),
            "upstream_revision": GEPA_REVISION,
            "gepa_version": GEPA_VERSION,
            "runtime": RUNTIME_VERSION,
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

    def advance(
        self,
        evaluate_trial,
        *,
        host_handler=None,
        count_callback_as_trial=True,
        trials_per_evaluation=1,
    ) -> dict:
        """Run until complete, budget exhausted, or native work needs collection.

        ``evaluate_trial(candidate, operation_id=..., timeout_seconds=...)`` must
        return Agentagon's score observation (or an object containing ``score``).
        ``trials_per_evaluation`` bounds each callback's actual trial cost.
        A pipeline with an already measured seed sets ``count_callback_as_trial=False``
        and admits its actual trials into this ledger itself. It must reuse
        existing executions on replay and use the supplied deadline for its runner.
        """
        if type(trials_per_evaluation) is not int or trials_per_evaluation < 1:
            raise AuditError("trials per evaluation must be a positive integer")
        from agentagon.capabilities.experiments.runtime import run_stages

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
            if (
                state["config"].get("gepa_version") != GEPA_VERSION
                or state["config"].get("runtime") != RUNTIME_VERSION
            ):
                raise AuditError(
                    "optimizer runtime changed; use the original installation to resume this run"
                )
            semaphore = threading.BoundedSemaphore(state["config"]["host_concurrency"])
            stop_requested = threading.Event()
            config = state["config"]
            try:
                if any(stage["max_evals"] < trials_per_evaluation for stage in state["stages"]):
                    raise BudgetExhausted(
                        "optimization stages cannot cover the required trial repetitions"
                    )
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
                                trials_per_evaluation,
                                stop_requested,
                            )
                            for stage in unfinished
                        ]
                        results = run_stages(entries, max_workers=config["host_concurrency"])
                    for stage, result in zip(unfinished, results, strict=True):
                        if not math.isfinite(result.best_score):
                            stage["failure"] = (
                                result.failure or "optimizer stage has no measured score"
                            )
                        else:
                            stage["result"] = {
                                "candidate": result.best_candidate,
                                "score": result.best_score,
                                "engine": stage["engine"],
                            }
                            if result.failure:
                                stage["failure"] = result.failure
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
        trials_per_evaluation,
        stop_requested,
    ):
        from agentagon.capabilities.experiments.runtime import BudgetTracker, EvalServer, Task

        counters = {"proposal": 0}
        evaluation_locks = {}
        evaluation_lock_guard = threading.Lock()
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
                    "background": config["background"],
                },
            )
            if request["state"] == "pending" and host_handler is not None:
                with semaphore:
                    request = self.bridge.fulfill(request, host_handler)
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

        def reflect(prompt, *, index=None):
            if stop_requested.is_set():
                raise OptimizationTargetReached()
            if not isinstance(prompt, str) or not prompt.strip():
                raise MeasurementUnavailable(
                    "native GEPA reflection requires a text prompt; multimodal prompts are not supported"
                )
            if len(prompt.encode("utf-8")) > 8 * 1024 * 1024:
                raise MeasurementUnavailable(
                    "GEPA reflection prompt exceeds the native transport limit"
                )
            if index is None:
                index = counters["proposal"]
                counters["proposal"] += 1
            try:
                request = self.bridge.request(
                    f"{stage['id']}:reflection:{index}",
                    source=config["source"],
                    evaluator=config["evaluator"],
                    role="proposal",
                    scope=config["scope"],
                    host=host,
                    model=model,
                    payload={
                        "protocol": "gepa-reflection-v1",
                        "engine": "gepa",
                        "stage": stage["id"],
                        "prompt": prompt,
                    },
                )
            except AuditError as exc:
                # Upstream retries ordinary reflection exceptions. A changed
                # durable binding must instead stop this stage, without redispatch.
                raise MeasurementUnavailable(str(exc)) from exc
            if request["state"] == "pending" and host_handler is not None:
                with semaphore:
                    try:
                        request = self.bridge.fulfill(request, host_handler)
                    except Exception as exc:
                        # A claimed callback may already have started a native
                        # turn. Stop upstream retries and collect that exact call.
                        raise HostWorkPending(request["request_id"]) from exc
            if request["state"] == "cancelled":
                raise MeasurementUnavailable("native GEPA reflection was cancelled")
            if request["state"] != "completed":
                raise HostWorkPending(request["request_id"])
            if request.get("deadline_exceeded"):
                raise BudgetExhausted("native GEPA reflection exceeded its admitted deadline")
            try:
                validate_record("host-reflection", request["response"], definition="response")
            except AuditError as exc:
                raise MeasurementUnavailable(
                    "native GEPA reflection returned an invalid response contract"
                ) from exc
            text = request["response"].get("text")
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text.encode("utf-8")) > 8 * 1024 * 1024
            ):
                raise MeasurementUnavailable(
                    "native GEPA reflection must return bounded raw final text"
                )
            return text

        def batch_complete(messages_list):
            prompts = []
            for messages in messages_list:
                if (
                    not isinstance(messages, list)
                    or len(messages) != 1
                    or messages[0].get("role") != "user"
                    or not isinstance(messages[0].get("content"), str)
                ):
                    raise MeasurementUnavailable(
                        "native GEPA batch requires text-only rendered prompts"
                    )
                prompts.append(messages[0]["content"])
            # Reserve deterministic identities in upstream input order before any
            # transport threads run. Return raw responses in that same order.
            start = counters["proposal"]
            counters["proposal"] += len(prompts)
            with ThreadPoolExecutor(max_workers=config["host_concurrency"]) as pool:
                futures = [
                    pool.submit(reflect, prompt, index=start + i)
                    for i, prompt in enumerate(prompts)
                ]
                return [future.result() for future in futures]

        reflect.batch_complete = batch_complete

        def evaluate(candidate, example=None, **kwargs):
            with evaluation_lock_guard:
                lock = evaluation_locks.setdefault(digest(candidate), threading.Lock())
            with lock:
                return evaluate_one(candidate, example, **kwargs)

        def evaluate_one(candidate, example=None, **kwargs):
            if stop_requested.is_set():
                raise OptimizationTargetReached()
            operation_id = f"optimizer:{stage['id']}:evaluation:{digest(candidate)}"
            binding = {
                "candidate": candidate,
                "evaluator": config["evaluator"],
                "source": config["source"],
            }
            with semaphore:
                operation = self.ledger.admit(
                    operation_id,
                    "optimization",
                    units=trials_per_evaluation if count_trial else 0,
                    binding=binding,
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
                    except BudgetExhausted as exc:
                        self.ledger.finish(
                            operation_id, status="failed", result={"error": str(exc)}
                        )
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
                # Upstream renders mapping insertion order into its exact prompt.
                # Match freshly returned observations to their serialized replay.
                return float(value), {
                    "observation": json.loads(encoded(observation)),
                    "operation_id": operation_id,
                }

        server = EvalServer(
            Task(
                seed_candidate=seed,
                objective=config["objective"],
                background=config["background"],
            ),
            evaluate,
            # Replayed seeds are already measured by the enclosing application pipeline.
            BudgetTracker(
                max_evals=stage["max_evals"] // trials_per_evaluation + int(not count_trial)
            ),
        )
        if stage["engine"] == "gepa":
            from gepa.strategies.proposal_sampling import PxNSampling

            from agentagon.capabilities.experiments.runtime import GepaEngine

            engine = GepaEngine(
                f"{run_dir}/{stage['id']}",
                engine={
                    "parallel": config["host_concurrency"] > 1,
                    "max_workers": config["host_concurrency"],
                    "sampling_strategy": PxNSampling(p=1, n=config["host_concurrency"]),
                    "use_cloudpickle": False,
                    "seed": 0,
                    "max_candidate_proposals": stage["max_evals"],
                    "cache_evaluation": not count_trial,
                    "cache_evaluation_storage": "memory" if not count_trial else "auto",
                },
                reflection={"reflection_lm": reflect},
                tracking={"logger": _QuietLogger()},
            )
        elif stage["engine"] == "autoresearch":
            engine = NativeAutoResearchEngine(propose)
        else:
            engine = NativeMetaHarnessEngine(
                propose, config["meta_harness"]["max_candidates_per_iter"]
            )
        return server, engine
