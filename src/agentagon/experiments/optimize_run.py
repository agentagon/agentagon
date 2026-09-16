"""Connect native Omni proposals to sealed application trials and final review.

Candidates are JSON objects with a ``files`` map of repository-relative paths
to UTF-8 contents (or null for deletion). Each proposal is materialized in an
isolated engine checkout and checked against the frozen evaluator's scope.
Only engine observations supply optimization scores. Durable host reviews
remain a separate pass over the actual sealed patch and retained evidence.
"""

import fcntl
import json
import os
import threading
import time
from dataclasses import asdict
from datetime import datetime

from agentagon.core.records import AuditError, digest, encoded, load_json, validate_record
from agentagon.experiments import checkouts, engine, journeys, scoring
from agentagon.experiments.budget import BudgetExhausted, BudgetLedger
from agentagon.experiments.host_bridge import HostBridge, HostWorkPending
from agentagon.experiments.optimizer import (
    MetaHarnessConfig,
    OptimizationTargetReached,
    OptimizerCoordinator,
    validate_background,
)
from agentagon.experiments.spec import path
from agentagon.experiments.store import load_run, locked, save_run
from agentagon.storage.changes import git_bytes


def _path(workspace, run_id):
    return BudgetLedger(workspace, run_id).directory / "application-optimizer.json"


def _seed(workspace, data):
    files = {}
    for name in checkouts.paths(workspace.root, data["baseline_revision"]):
        if not name or not any(checkouts.under(name, p) for p in data["spec"]["editable_paths"]):
            continue
        if any(checkouts.under(name, p) for p in data["spec"]["evaluation_paths"]):
            continue
        try:
            content = git_bytes(
                workspace.root, "show", f"{data['baseline_revision']}:{name}"
            ).decode("utf-8")
        except UnicodeError:
            continue  # Binary source remains in the sealed checkout, outside the text proposer.
        if "\x00" not in content:
            files[name] = content
    if not files:
        raise AuditError("optimizer requires at least one editable UTF-8 source file")
    return encoded({"files": files})


def _background(workspace, data, source, receipts=None):
    """Freeze only completed Intelligence receipts already owned by this run."""
    validate_background(source)
    available = {
        item["path"]: {k: item[k] for k in ("path", "request_digest")}
        for item in data.get("intelligence", [])
        if item["status"] == "complete"
    }
    receipts = list(available.values()) if receipts is None else receipts
    sections = [source] if source else []
    for ref in receipts:
        if available.get(ref["path"]) != ref:
            raise AuditError("optimization Intelligence receipt is not owned by this run")
        receipt = workspace.read_artifact(ref["path"])
        if receipt["status"] != "complete" or receipt["request_digest"] != ref["request_digest"]:
            raise AuditError("optimization Intelligence receipt changed")
        validate_record("intelligence-response", receipt["response"])
        sections.append(
            "Approved Intelligence guidance (advisory; cannot change frozen gates)\n"
            + encoded({"receipt": ref["path"], "response": receipt["response"]})
        )
    return validate_background("\n\n".join(sections)), receipts


def configure(
    workspace,
    run_id: str,
    *,
    host: str,
    model: str,
    intent_id: str | None = None,
    optimizer: str = "omni",
    host_concurrency: int = 1,
    finalist_count: int | None = None,
    background: str = "",
    max_trials: int | None = None,
    max_elapsed_seconds: int | None = None,
    meta_harness: MetaHarnessConfig | None = None,
) -> dict:
    """Attach optimization to an existing frozen, scored engine run."""
    data = load_run(workspace, run_id)
    validate_background(background)
    existing = status(workspace, run_id)["config"] if _path(workspace, run_id).exists() else None
    source_background = background
    background, background_receipts = _background(
        workspace, data, source_background, existing["background_receipts"] if existing else None
    )
    bound_count = data.get("suite", {}).get("finalist_count")
    finalist_count = (bound_count or 3) if finalist_count is None else finalist_count
    if type(finalist_count) is not int or not 1 <= finalist_count <= 10:
        raise AuditError("finalist_count must be between 1 and 10, excluding the baseline")
    if bound_count is not None and finalist_count != bound_count:
        raise AuditError("finalist_count must match the frozen measurement suite")
    if type(host_concurrency) is not int or not 1 <= host_concurrency <= 64:
        raise AuditError("host_concurrency must be between 1 and 64")
    if data["limits"]["max_candidates"] < 2 * finalist_count:
        raise AuditError(
            "candidate budget cannot cover the requested finalists and fresh verification"
        )
    for entry in data["frozen"]:
        if entry.get("kind") != "inputs" or entry.get("deleted"):
            continue
        try:
            value = json.loads(workspace.read_blob(entry["artifact"]))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(value, dict) and value.get("dataset_partition") == "final_holdout":
            raise AuditError("final holdout inputs cannot be used for optimization feedback")
    if not data["spec"].get("scoring"):
        raise AuditError("agree on scoring before configuring automatic optimization")
    intent_id = intent_id or data.get("intent_id")
    intent = journeys.load(workspace, intent_id) if intent_id else None
    if intent:
        if intent["definition"]["scoring"] != data["spec"]["scoring"]:
            raise AuditError("saved intent scoring must match this run's frozen evaluator")
        saved_budget = intent["definition"]["budget"]
        if (max_trials is not None and max_trials > saved_budget["max_trials"]) or (
            max_elapsed_seconds is not None
            and max_elapsed_seconds > saved_budget["max_elapsed_seconds"]
        ):
            raise AuditError("optimization cannot expand the accepted intent budget")
        max_trials = saved_budget["max_trials"] if max_trials is None else max_trials
        max_elapsed_seconds = (
            saved_budget["max_elapsed_seconds"]
            if max_elapsed_seconds is None
            else max_elapsed_seconds
        )
    if data.get("budget_id") and data["budget_id"] != run_id:
        raise AuditError("optimizer must share the run's existing overall budget identity")
    if any(cid != data["baseline_id"] for cid in data["candidates"]):
        if not _path(workspace, run_id).exists():
            raise AuditError("configure optimization before proposing application candidates")
    repetitions = data["spec"]["repetitions"]
    suite_reserve = data.get("suite", {}).get("verification_trials", 0)
    ledger = BudgetLedger(workspace, run_id)
    if not ledger.path.exists():
        ledger.create(
            max_trials or data["limits"]["max_trials"],
            max_elapsed_seconds or data["limits"]["max_elapsed_seconds"],
            baseline_trials=repetitions,
            verification_trials=repetitions * finalist_count + suite_reserve,
            started_at=datetime.fromisoformat(data["created_at"]).timestamp(),
        )
    elif max_trials is not None or max_elapsed_seconds is not None:
        limits = ledger.snapshot()["limits"]
        if (max_trials is not None and max_trials != limits["max_trials"]) or (
            max_elapsed_seconds is not None and max_elapsed_seconds != limits["max_elapsed_seconds"]
        ):
            raise AuditError("overall budget is already frozen")
    budget = ledger.snapshot()
    if budget["limits"]["verification_trials"] < repetitions * finalist_count + suite_reserve:
        raise AuditError("budget does not protect all requested finalist verification")
    # Historical baseline trials are charged once when an existing measured run
    # gains the unified ledger. A fresh run charges these at engine admission.
    for trial in data["candidates"][data["baseline_id"]]["trials"]:
        if trial["trial_id"] in ledger.snapshot()["operations"]:
            continue
        ledger.admit(
            trial["trial_id"],
            "preparation",
            binding={
                "run_id": run_id,
                "candidate_id": data["baseline_id"],
                "source": data["candidates"][data["baseline_id"]]["source_digest"],
                "evaluator": data["evaluation_digest"],
            },
        )
        if trial["state"] not in {"running", "interrupted"}:
            ledger.finish(
                trial["trial_id"],
                status="completed" if trial["state"] == "completed" else "failed",
                result={"artifact": trial.get("artifact")},
            )
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        data["budget_id"] = run_id
        data["optimizer_configured"] = True
        if intent:
            data["intent_id"] = intent_id
            if intent.get("evaluation_id"):
                from agentagon.experiments import preparation

                if data.get("evaluator_id") not in {None, intent["evaluation_id"]}:
                    raise AuditError("saved intent refers to another frozen evaluator")
                data["evaluator_id"] = intent["evaluation_id"]
                data["evaluator_digest"] = preparation.evaluator_identity(
                    workspace, intent["evaluation_id"]
                )
            data["limits"]["trial_timeout_seconds"] = min(
                data["limits"]["trial_timeout_seconds"], saved_budget["trial_timeout_seconds"]
            )
        save_run(workspace, data)
    concurrency = min(
        host_concurrency,
        data["limits"]["parallel_candidates"],
        data["limits"]["parallel_trials"],
        data["profile"].get("orchestration", {}).get("host_capacity", 1),
        data["profile"].get("orchestration", {}).get("resource_slots", 1)
        // data["spec"].get("resources", {}).get("slots", 1),
    )
    if concurrency < 1:
        raise AuditError("actual host concurrency must be positive")
    config = {
        "host": host,
        "model": model,
        "intent_id": intent_id,
        "optimizer": optimizer,
        "host_concurrency": concurrency,
        "finalist_count": finalist_count,
        "background": background,
        "source_background": source_background,
        "background_receipts": background_receipts,
        "seed": _seed(workspace, data),
        "meta_harness": asdict(meta_harness or MetaHarnessConfig()),
    }
    filename = _path(workspace, run_id)
    with ledger.locked():
        if filename.exists():
            state = load_json(filename)
            if state["config"] != config:
                raise AuditError("application optimizer configuration is frozen")
        else:
            state = {
                "version": 1,
                "run_id": run_id,
                "config": config,
                "config_artifact": workspace.artifact(config),
                "state": "baseline",
                "evaluations": {},
                "verification": [],
                "selection": None,
            }
            workspace.write(filename, state)
    return status(workspace, run_id)


def status(workspace, run_id: str) -> dict:
    filename = _path(workspace, run_id)
    if not filename.exists():
        raise AuditError("configure this run's application optimizer first")
    state = load_json(workspace.checked(filename))
    if workspace.read_artifact(state["config_artifact"]) != state["config"]:
        raise AuditError("frozen optimizer configuration changed")
    config = state["config"]
    background, _ = _background(
        workspace,
        load_run(workspace, run_id),
        config["source_background"],
        config["background_receipts"],
    )
    if background != config["background"]:
        raise AuditError("frozen optimization background changed")
    return {
        **state,
        "pending": HostBridge(workspace, run_id).pending(),
        "budget": BudgetLedger(workspace, run_id).snapshot(),
    }


def _files(text, data):
    try:
        proposal = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise AuditError("proposal must be JSON with a files map") from exc
    if (
        not isinstance(proposal, dict)
        or set(proposal) != {"files"}
        or not isinstance(proposal["files"], dict)
    ):
        raise AuditError("proposal must contain exactly a files map")
    protected = data["spec"]["evaluation_paths"] + [
        item["path"] for kind in ("inputs", "overlays") for item in data["spec"][kind]
    ]
    for name, content in proposal["files"].items():
        path(name, "candidate source path")
        if not any(checkouts.under(name, p) for p in data["spec"]["editable_paths"]) or any(
            checkouts.under(name, p) for p in protected
        ):
            raise AuditError("proposal changed a protected or undeclared source path")
        if name == ".gitignore" or ".agentagon" in name.split("/") or name.split("/")[0] == ".git":
            raise AuditError("proposal cannot change private state or Git policy")
        if content is not None and not isinstance(content, str):
            raise AuditError("candidate source contents must be text or null for deletion")
    return proposal["files"]


def _materialize(workspace, candidate, files):
    destination = workspace.checked(workspace.root / candidate["worktree"])
    for name, content in files.items():
        target = destination / name
        if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
            raise AuditError("candidate source path escapes its isolated checkout")
        if target.exists() and not target.is_file():
            raise AuditError("candidate source must name a regular file")
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def advance(workspace, run_id: str, *, host_handler=None) -> dict:
    """Advance actual application trials, pending host reviews, and final selection."""
    directory = BudgetLedger(workspace, run_id).directory
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(directory / "application.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AuditError("application optimizer is already advancing") from exc
        adapter = _ApplicationOptimizer(workspace, run_id, host_handler)
        return adapter.advance()


class _ApplicationOptimizer:
    def __init__(self, workspace, run_id, host_handler):
        self.workspace, self.run_id, self.host_handler = workspace, run_id, host_handler
        self.ledger = BudgetLedger(workspace, run_id)
        self.state = status(workspace, run_id)
        self.state.pop("pending")
        self.state.pop("budget")
        self.config = self.state["config"]
        self.mutex = threading.RLock()
        self.candidate_locks = {}

    def save(self):
        self.workspace.write(_path(self.workspace, self.run_id), self.state)

    def review(self, candidate_id, stage):
        workspace, run_id = self.workspace, self.run_id
        config, host_handler = self.config, self.host_handler
        measured = engine.run(workspace, run_id, candidate_id)
        candidate = measured["candidate"]
        if candidate["state"] == "awaiting_grading":
            bridge = HostBridge(workspace, run_id)
            if host_handler:
                for request in bridge.pending():
                    if (
                        request["role"] != "judging"
                        or request["payload"].get("candidate_id") != candidate_id
                    ):
                        continue
                    bridge.fulfill(request, host_handler)
                measured = engine.run(workspace, run_id, candidate_id)
                candidate = measured["candidate"]
            if candidate["state"] == "awaiting_grading":
                raise HostWorkPending(candidate_id)
        if candidate["state"] in {"interrupted", "running", "sealed", "editing"}:
            raise HostWorkPending(candidate_id)
        if candidate["state"] != "awaiting_review":
            return candidate
        bridge = HostBridge(workspace, run_id)
        request = bridge.request(
            f"review:{candidate_id}",
            source=candidate["source_digest"],
            evaluator=measured["review_template"]["evaluation_digest"],
            role="review",
            scope=["sealed patch", "retained trial evidence"],
            host=config["host"],
            model=config["model"],
            stage=stage,
            payload={
                "candidate_id": candidate_id,
                "review_template": measured["review_template"],
                "source_revision": candidate["source_revision"],
                "instruction": "Independently inspect the exact sealed patch and retained trial artifacts. Fill review_template; never invent or edit measured metrics.",
            },
        )
        request = bridge.fulfill(request, host_handler)
        if request["state"] != "completed":
            raise HostWorkPending(request["request_id"])
        if request.get("deadline_exceeded"):
            raise BudgetExhausted(
                "native-host review exceeded its admitted deadline; evidence retained"
            )
        review = request["response"].get("review", request["response"])
        return engine.run(workspace, run_id, candidate_id, review=review)["candidate"]

    def observation(self, candidate):
        data = load_run(self.workspace, self.run_id)
        score = scoring.candidate_score(data, candidate)
        return {
            "score": score,
            "candidate_id": candidate["candidate_id"],
            "source_digest": candidate.get("source_digest"),
            "trial_ids": [t["trial_id"] for t in candidate["trials"]],
        }

    def evaluate(self, text, *, operation_id, timeout_seconds):
        with self.mutex:
            data = load_run(self.workspace, self.run_id)
            files = _files(text, data)
            key = digest(files)
            record = self.state["evaluations"].get(key)
            if record is None and text == self.config["seed"]:
                record = {"candidate_id": data["baseline_id"], "operations": []}
                self.state["evaluations"][key] = record
            if record is None or record["candidate_id"] is None:
                occupied = sum(c["state"] in engine.ACTIVE for c in data["candidates"].values())
                if occupied >= data["limits"]["parallel_candidates"]:
                    record = self.state["evaluations"].setdefault(
                        key, {"candidate_id": None, "operations": [], "proposal": text}
                    )
                    if operation_id not in record["operations"]:
                        record["operations"].append(operation_id)
                    self.save()
                    raise HostWorkPending("Complete pending candidate review to free capacity")
                reserve = min(
                    self.config["finalist_count"],
                    self.ledger.snapshot()["allocations"]["verification"]
                    // data["spec"]["repetitions"],
                )
                if data["usage"]["candidates"] >= data["limits"]["max_candidates"] - reserve:
                    raise BudgetExhausted("candidate capacity reserved for final verification")
                created = engine.new(
                    self.workspace,
                    self.run_id,
                    parent_id=data["baseline_id"],
                    hypothesis="Measure native optimizer proposal under the frozen evaluator",
                    author=f"optimizer:{self.config['host']}:{self.config['model']}",
                    operation_id=f"optimizer-candidate:{key}",
                )
                record = {
                    "candidate_id": created["candidate_id"],
                    "operations": record["operations"] if record else [],
                }
                self.state["evaluations"][key] = record
                self.save()
            if operation_id not in record["operations"]:
                record["operations"].append(operation_id)
            self.save()
            candidate_lock = self.candidate_locks.setdefault(key, threading.Lock())
        # Different source maps execute in independent engine worktrees. A duplicate
        # proposal waits for its own candidate instead of racing materialization.
        with candidate_lock:
            candidate = load_run(self.workspace, self.run_id)["candidates"][record["candidate_id"]]
            if candidate["state"] == "editing":
                _materialize(self.workspace, candidate, files)
            candidate = self.review(record["candidate_id"], "optimization")
            if candidate["state"] == "duplicate":
                candidate = load_run(self.workspace, self.run_id)["candidates"][
                    candidate["duplicate_of"]
                ]
            observation = self.observation(candidate)
        with self.mutex:
            if (
                candidate["candidate_id"] != data["baseline_id"]
                and observation["score"]["target_reached"]
                and candidate["candidate_id"] not in self.state.get("failed_targets", [])
            ):
                self.ledger.finish(operation_id, result=observation)
                self.state["target_candidate"] = candidate["candidate_id"]
                self.save()
                raise OptimizationTargetReached()
            return observation

    def reconcile(self):
        target_reached = False
        for record in sorted(
            list(self.state["evaluations"].values()), key=lambda r: r["candidate_id"] is None
        ):
            pending = [
                op
                for op in record["operations"]
                if self.ledger.snapshot()["operations"][op]["status"] == "running"
            ]
            if not pending:
                continue
            if record["candidate_id"] is None:
                try:
                    observation = self.evaluate(
                        record["proposal"],
                        operation_id=pending[0],
                        timeout_seconds=self.ledger.snapshot()["operations"][pending[0]][
                            "timeout_seconds"
                        ],
                    )
                except OptimizationTargetReached:
                    target_reached = True
                    observation = self.ledger.snapshot()["operations"][pending[0]]["result"]
                except BudgetExhausted as exc:
                    for operation_id in pending:
                        self.ledger.finish(
                            operation_id, status="failed", result={"error": str(exc)}
                        )
                    record["failure"] = str(exc)
                    self.save()
                    continue
                for operation_id in pending:
                    self.ledger.finish(operation_id, result=observation)
                continue
            candidate = self.review(record["candidate_id"], "optimization")
            if candidate["state"] == "duplicate":
                candidate = load_run(self.workspace, self.run_id)["candidates"][
                    candidate["duplicate_of"]
                ]
            for operation_id in pending:
                self.ledger.finish(operation_id, result=self.observation(candidate))
            if self.observation(candidate)["score"]["target_reached"] and candidate[
                "candidate_id"
            ] not in self.state.get("failed_targets", []):
                self.state["target_candidate"] = candidate["candidate_id"]
                target_reached = True
        self.save()
        return target_reached

    def advance(self):
        if self.state["state"] == "completed":
            return status(self.workspace, self.run_id)
        try:
            data = load_run(self.workspace, self.run_id)
            baseline = self.review(data["baseline_id"], "preparation")
            if (
                baseline["state"] != "verified"
                or self.observation(baseline)["score"]["value"] is None
            ):
                raise AuditError(
                    "optimization requires a runnable, independently reviewed scored baseline"
                )
            self.ledger.release_preparation()
            coordinator = OptimizerCoordinator(self.workspace, self.run_id)
            coordinator.create(
                source=data["origin_revision"],
                evaluator=data["evaluation_digest"],
                seed=self.config["seed"],
                objective=(data["spec"]["goal"] or "Improve the saved issues")
                + " The replacement component must be a JSON object containing exactly a files map; preserve the frozen evaluator and allowed scope.",
                background=self.config["background"],
                scope=data["spec"]["editable_paths"],
                host=self.config["host"],
                model=self.config["model"],
                engine=self.config["optimizer"],
                host_concurrency=self.config["host_concurrency"],
                meta_harness=MetaHarnessConfig(**self.config["meta_harness"]),
            )
            if self.state["state"] != "verification":
                if self.reconcile():
                    optimized = {"state": "target_reached"}
                else:
                    optimized = coordinator.advance(
                        self.evaluate,
                        host_handler=self.host_handler,
                        count_callback_as_trial=False,
                        trials_per_evaluation=data["spec"]["repetitions"],
                    )
                self.state["optimizer_state"] = optimized["state"]
                if optimized["state"] == "host_pending":
                    self.state["state"] = "host_pending"
                    self.save()
                    return status(self.workspace, self.run_id)
                self.state["state"] = "verification"
                self.save()
            if not self.verify_and_select():
                return self.advance()
        except HostWorkPending:
            if self.state["state"] != "verification":
                self.state["state"] = "host_pending"
        except BudgetExhausted as exc:
            self.state.update(state="budget_exhausted", reason=str(exc))
        self.save()
        return status(self.workspace, self.run_id)

    def verify_and_select(self):
        data = load_run(self.workspace, self.run_id)
        suite_reserve = data.get("suite", {}).get("verification_trials", 0)
        if "verification_queue" not in self.state:
            self.state["verification_queue"] = [
                entry["candidate_id"]
                for entry in scoring.qualifying(data)
                if entry["candidate_id"] not in self.state.get("failed_targets", [])
                and not data["candidates"][entry["candidate_id"]].get("verification_of")
            ][: self.config["finalist_count"]]
            self.save()
        for original_id in self.state["verification_queue"]:
            entry = next(
                (e for e in self.state["verification"] if e["original_id"] == original_id), None
            )
            if entry is None:
                remaining = (
                    self.ledger.snapshot()["allocations"]["verification"]
                    - self.ledger.spent(self.ledger.snapshot(), "verification")
                    - suite_reserve
                )
                if remaining < data["spec"]["repetitions"]:
                    break
                created = engine.new(
                    self.workspace,
                    self.run_id,
                    parent_id=original_id,
                    hypothesis="Independently remeasure the frozen candidate for final verification",
                    author="optimizer-final-verification",
                    operation_id=f"final-verification:{original_id}",
                )
                entry = {"original_id": original_id, "candidate_id": created["candidate_id"]}
                self.state["verification"].append(entry)
                self.save()
            # Repair a crash after the durable mapping but before marking the
            # already-created checkout for fresh final verification.
            with locked(self.workspace, self.run_id):
                current = load_run(self.workspace, self.run_id)
                candidate = current["candidates"][entry["candidate_id"]]
                if candidate["state"] == "editing":
                    candidate.update(budget_stage="verification", verification_of=original_id)
                    save_run(self.workspace, current)
            candidate = self.review(entry["candidate_id"], "verification")
            entry["state"] = candidate["state"]
            self.save()
        data = load_run(self.workspace, self.run_id)
        verified_ids = {
            entry["candidate_id"]
            for entry in self.state["verification"]
            if entry.get("state") == "verified"
        }
        qualifying = [
            entry for entry in scoring.qualifying(data) if entry["candidate_id"] in verified_ids
        ][: self.config["finalist_count"]]
        budget = self.ledger.snapshot()
        remaining_verification = (
            budget["allocations"]["verification"]
            - self.ledger.spent(budget, "verification")
            - suite_reserve
        )
        remaining_optimization = budget["allocations"]["optimization"] - self.ledger.spent(
            budget, "optimization"
        )
        if (
            self.state.get("target_candidate")
            and not any(entry["score"]["target_reached"] for entry in qualifying)
            and remaining_verification >= data["spec"]["repetitions"]
            and remaining_optimization >= data["spec"]["repetitions"]
            and time.time() < budget["started_at"] + budget["limits"]["max_elapsed_seconds"] * 0.8
        ):
            self.state.setdefault("failed_targets", []).append(self.state.pop("target_candidate"))
            self.state.pop("verification_queue", None)
            self.state["state"] = "optimization"
            self.save()
            return False
        if qualifying and data.get("suite"):
            from agentagon.experiments import suites

            passing = []
            for entry in qualifying:
                result = suites.advance(
                    self.workspace,
                    self.run_id,
                    candidate_id=entry["candidate_id"],
                    host_handler=self.host_handler,
                )
                self.state["suite"] = {"state": result["state"], "result": result.get("result")}
                self.save()
                if result["state"] in {"host_pending", "running", "blocked"}:
                    raise HostWorkPending(result.get("next_action", "Complete the required suite"))
                if result["state"] == "completed":
                    passing.append(entry)
            qualifying = passing
        if qualifying:
            engine.select(self.workspace, self.run_id, qualifying[0]["candidate_id"])
        self.state["selection"] = {
            "winner": qualifying[0] if qualifying else None,
            "alternatives": qualifying[1 : self.config["finalist_count"]],
            "retained_baseline": not qualifying,
        }
        self.state["state"] = "completed"
        return True
