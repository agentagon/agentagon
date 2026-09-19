"""One durable admission ledger for actual trials and native-host work.

An evaluation unit is one actual application trial, including a failed trial or
retry. Replaying a recorded operation consumes no new units. Callers must pass
the returned timeout to their bounded runner; interrupted operations stay
running until their existing execution is collected or explicitly cancelled.
"""

import fcntl
import math
import os
import re
import time
from contextlib import contextmanager

from agentagon.core.records import AuditError, digest, load_json

STAGES = {"preparation", "optimization", "verification"}


class BudgetExhausted(AuditError):
    """No admission fits the unchanged budget and protected reserve."""


class BudgetLedger:
    def __init__(self, workspace, run_id: str):
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-z]+_[a-f0-9]{24}", run_id):
            raise AuditError("invalid budget run identity")
        self.workspace = workspace
        self.run_id = run_id
        self.directory = workspace.checked(workspace.state / "coordinators" / run_id)
        self.path = self.directory / "budget.json"

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.directory / "budget.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def create(
        self,
        max_trials: int,
        max_elapsed_seconds: int,
        *,
        journey: str = "fix",
        baseline_trials: int = 1,
        verification_trials: int = 1,
        max_cost: float | None = None,
        started_at: float | None = None,
    ) -> dict:
        for value in (max_trials, max_elapsed_seconds, baseline_trials, verification_trials):
            if type(value) is not int or value < 1:
                raise AuditError("budget limits and minimum trials must be positive integers")
        if journey not in {"fix", "init", "baseline"}:
            raise AuditError("unknown budget journey")
        if max_cost is not None and (not math.isfinite(max_cost) or max_cost <= 0):
            raise AuditError("cost limit must be positive and finite")
        if started_at is not None and (not math.isfinite(started_at) or started_at > time.time()):
            raise AuditError("budget start must be a finite time no later than now")
        preparation = max(baseline_trials, max_trials // 5) if journey == "fix" else max_trials
        verification = (
            max(verification_trials, math.ceil(max_trials / 5)) if journey == "fix" else 0
        )
        if preparation + verification > max_trials:
            raise BudgetExhausted(
                "total budget cannot cover baseline and minimum final verification"
            )
        limits = {
            "max_trials": max_trials,
            "max_elapsed_seconds": max_elapsed_seconds,
            "max_cost": max_cost,
            "journey": journey,
            "baseline_trials": baseline_trials,
            "verification_trials": verification_trials,
        }
        with self.locked():
            if self.path.exists():
                state = self.snapshot()
                if state["limits"] != limits:
                    raise AuditError("an existing overall budget cannot be replaced or expanded")
                return state
            state = {
                "version": 1,
                "run_id": self.run_id,
                "origin": str(self.workspace.root),
                "limits": limits,
                "started_at": time.time() if started_at is None else started_at,
                "preparation_released": journey != "fix",
                "allocations": {
                    "preparation": preparation,
                    "optimization": max_trials - preparation - verification,
                    "verification": verification,
                },
                "operations": {},
            }
            self.workspace.write(self.path, state)
            return state

    def snapshot(self) -> dict:
        if not self.path.exists():
            raise AuditError("overall budget has not been configured")
        state = load_json(self.workspace.checked(self.path))
        if (
            state.get("version") != 1
            or state.get("run_id") != self.run_id
            or state.get("origin") != str(self.workspace.root)
        ):
            raise AuditError("incompatible budget or originating checkout")
        return state

    @staticmethod
    def spent(state: dict, stage: str | None = None) -> int:
        return sum(
            op["units"]
            for op in state["operations"].values()
            if stage is None or op["stage"] == stage
        )

    def release_preparation(self) -> dict:
        with self.locked():
            state = self.snapshot()
            if not state["preparation_released"]:
                if any(
                    op["stage"] == "preparation" and op["status"] == "running"
                    for op in state["operations"].values()
                ):
                    raise AuditError(
                        "collect pending preparation work before releasing its capacity"
                    )
                unused = state["allocations"]["preparation"] - self.spent(state, "preparation")
                state["allocations"]["preparation"] -= unused
                state["allocations"]["optimization"] += unused
                state["preparation_released"] = True
                self.workspace.write(self.path, state)
            return state

    def admit(
        self,
        operation_id: str,
        stage: str,
        *,
        kind: str = "evaluation",
        units: int = 1,
        timeout_seconds: float | None = None,
        binding: dict | None = None,
        reserved_cost: float | None = None,
    ) -> dict:
        if stage not in STAGES or kind not in {
            "evaluation",
            "proposal",
            "judging",
            "review",
            "acquisition",
        }:
            raise AuditError("unknown budget stage or operation kind")
        if type(units) is not int or units < 0 or (kind != "evaluation" and units != 0):
            raise AuditError("evaluation units must count actual trials; host work uses zero units")
        if not operation_id or not isinstance(operation_id, str):
            raise AuditError("a stable operation identity is required")
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise AuditError("operation timeout must be positive and finite")
        if reserved_cost is not None and (not math.isfinite(reserved_cost) or reserved_cost < 0):
            raise AuditError("reserved cost must be nonnegative and finite")
        identity = {"stage": stage, "kind": kind, "units": units, "binding": binding or {}}
        with self.locked():
            state = self.snapshot()
            existing = state["operations"].get(operation_id)
            if existing:
                if existing["identity"] != digest(identity):
                    raise AuditError("operation identity was reused with different work")
                return {**existing, "replay": True}
            if (
                stage == "preparation"
                and state["preparation_released"]
                and state["limits"]["journey"] == "fix"
            ):
                raise BudgetExhausted("preparation capacity was already released")
            if self.spent(state, stage) + units > state["allocations"][stage]:
                raise BudgetExhausted(
                    f"{stage} trial budget exhausted; verification reserve is protected"
                )
            if self.spent(state) + units > state["limits"]["max_trials"]:
                raise BudgetExhausted("overall trial budget exhausted")
            duration = state["limits"]["max_elapsed_seconds"]
            fraction = 1.0
            if state["limits"]["journey"] == "fix":
                fraction = {"preparation": 0.2, "optimization": 0.8, "verification": 1.0}[stage]
            deadline = state["started_at"] + duration * fraction
            remaining = deadline - time.time()
            if remaining <= 0:
                raise BudgetExhausted(
                    f"{stage} time budget exhausted; the overall budget cannot expand"
                )
            if state["limits"]["max_cost"] is not None:
                if reserved_cost is None:
                    raise BudgetExhausted("cost-capped work requires a bounded cost reservation")
                committed_cost = sum(
                    op.get("measured_cost", op.get("reserved_cost") or 0)
                    for op in state["operations"].values()
                )
                if committed_cost + reserved_cost > state["limits"]["max_cost"]:
                    raise BudgetExhausted("overall measured/reserved cost budget exhausted")
            operation = {
                **identity,
                "operation_id": operation_id,
                "identity": digest(identity),
                "status": "running",
                "started_at": time.time(),
                "timeout_seconds": min(timeout_seconds or remaining, remaining),
                "deadline": min(time.time() + (timeout_seconds or remaining), deadline),
                "reserved_cost": reserved_cost,
            }
            state["operations"][operation_id] = operation
            self.workspace.write(self.path, state)
            return {**operation, "replay": False}

    def finish(
        self,
        operation_id: str,
        *,
        status: str = "completed",
        result: dict | None = None,
        measured_cost: float | None = None,
    ) -> dict:
        if status not in {"completed", "failed", "cancelled"}:
            raise AuditError("invalid terminal operation status")
        if measured_cost is not None and (not math.isfinite(measured_cost) or measured_cost < 0):
            raise AuditError("measured cost must be nonnegative and finite")
        with self.locked():
            state = self.snapshot()
            operation = state["operations"].get(operation_id)
            if operation is None:
                raise AuditError("operation was not admitted")
            if operation["status"] != "running":
                if operation["status"] != status or operation.get("result") != (result or {}):
                    raise AuditError("a completed operation cannot be rewritten")
                return operation
            operation.update(status=status, result=result or {}, finished_at=time.time())
            operation["deadline_exceeded"] = operation["finished_at"] > operation["deadline"]
            operation["elapsed_seconds"] = max(
                0, operation["finished_at"] - operation["started_at"]
            )
            if measured_cost is not None:
                operation["measured_cost"] = measured_cost
            self.workspace.write(self.path, state)
            return operation
