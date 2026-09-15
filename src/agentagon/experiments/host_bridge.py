"""Durable native-host requests shared by proposals, judging, and reviews.

The bridge never launches a different host. Hosts claim a pending request,
perform only its allowed scope, and reply with the exact host/model provenance.
An interrupted claim stays running for collection instead of being dispatched
again. A reply is evidence, not authority to change runner-validated scores.
"""

import fcntl
import os
import time
from contextlib import contextmanager

from agentagon.core.records import AuditError, digest, identifier, load_json
from agentagon.experiments.budget import STAGES, BudgetLedger


class HostWorkPending(BaseException):
    """Stop synchronous upstream engines without converting pending work to failure."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        super().__init__(request_id)


class HostBridge:
    def __init__(self, workspace, run_id: str):
        self.workspace = workspace
        self.run_id = run_id
        self.ledger = BudgetLedger(workspace, run_id)
        self.directory = self.ledger.directory
        self.path = self.directory / "host-requests.json"

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.directory / "host.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def snapshot(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "run_id": self.run_id, "requests": {}}
        state = load_json(self.workspace.checked(self.path))
        if state.get("version") != 1 or state.get("run_id") != self.run_id:
            raise AuditError("incompatible host bridge")
        return state

    def request(
        self,
        key: str,
        *,
        source: str,
        evaluator: str,
        role: str,
        scope: list[str],
        host: str,
        model: str,
        payload: dict,
        stage: str = "optimization",
    ) -> dict:
        if role not in {"proposal", "judging", "review", "acquisition"}:
            raise AuditError("unsupported native-host role")
        if stage not in STAGES:
            raise AuditError("unsupported native-host budget stage")
        if not all(isinstance(v, str) and v.strip() for v in (key, source, evaluator, host, model)):
            raise AuditError("host requests require source, evaluator, host and model identities")
        if (
            not isinstance(scope, list)
            or not scope
            or any(not isinstance(p, str) or not p for p in scope)
        ):
            raise AuditError("host requests require an explicit allowed scope")
        if role == "judging" and not all(
            payload.get(k) for k in ("trial_id", "evidence_digest", "rubric_version")
        ):
            raise AuditError("judging must bind actual trial evidence and the frozen rubric")
        request_id = identifier("host", self.run_id, key)
        binding = {
            "key": key,
            "run_id": self.run_id,
            "source": source,
            "evaluator": evaluator,
            "role": role,
            "scope": scope,
            "host": host,
            "model": model,
            "payload": payload,
            "stage": stage,
        }
        with self.locked():
            state = self.snapshot()
            existing = state["requests"].get(request_id)
            if existing:
                if existing["binding_digest"] != digest(binding):
                    raise AuditError("request replay differs from its frozen binding")
                # Recover the narrow crash window between the ledger's durable
                # reply and the bridge projection write, without redispatch.
                if existing["state"] == "running":
                    operation = self.ledger.snapshot()["operations"].get(request_id)
                    if operation and operation["status"] == "completed":
                        existing.update(
                            state="completed",
                            response=operation["result"],
                            replied_at=operation["finished_at"],
                            deadline_exceeded=operation.get("deadline_exceeded", False),
                        )
                        self.workspace.write(self.path, state)
                    elif operation and operation["status"] == "cancelled":
                        existing.update(
                            state="cancelled",
                            cancellation_reason=operation["result"].get("reason", "cancelled"),
                        )
                        self.workspace.write(self.path, state)
                return existing
            request = {
                **binding,
                "request_id": request_id,
                "binding_digest": digest(binding),
                "state": "pending",
                "created_at": time.time(),
            }
            state["requests"][request_id] = request
            self.workspace.write(self.path, state)
            return request

    def start(
        self,
        request_id: str,
        *,
        timeout_seconds: float | None = None,
        reserved_cost: float | None = None,
    ) -> dict:
        with self.locked():
            state = self.snapshot()
            request = self._get(state, request_id)
            if request["state"] != "pending":
                return {**request, "replay": True}
            admission = self.ledger.admit(
                request_id,
                request["stage"],
                kind=request["role"],
                units=0,
                timeout_seconds=timeout_seconds,
                reserved_cost=reserved_cost,
                binding={"request_digest": request["binding_digest"]},
            )
            request.update(
                state="running", deadline=admission["deadline"], started_at=admission["started_at"]
            )
            self.workspace.write(self.path, state)
            return {**request, "replay": admission["replay"]}

    def reply(
        self,
        request_id: str,
        response: dict,
        *,
        host: str,
        model: str,
        binding_digest: str,
        measured_cost: float | None = None,
    ) -> dict:
        with self.locked():
            state = self.snapshot()
            request = self._get(state, request_id)
            if (host, model, binding_digest) != (
                request["host"],
                request["model"],
                request["binding_digest"],
            ):
                raise AuditError(
                    "host reply provenance or frozen binding does not match the request"
                )
            if not isinstance(response, dict):
                raise AuditError("host reply must be an object")
            if request["state"] == "completed":
                if request["response"] != response:
                    raise AuditError("a recorded host reply cannot be replaced")
                return request
            if request["state"] != "running":
                raise AuditError("claim the pending host request before replying")
            operation = self.ledger.finish(request_id, result=response, measured_cost=measured_cost)
            request.update(
                state="completed",
                response=response,
                replied_at=time.time(),
                deadline_exceeded=operation["deadline_exceeded"],
            )
            self.workspace.write(self.path, state)
            return request

    def cancel(self, request_id: str, *, reason: str) -> dict:
        if not reason.strip():
            raise AuditError("cancellation requires a reason")
        with self.locked():
            state = self.snapshot()
            request = self._get(state, request_id)
            if request["state"] == "completed":
                raise AuditError("a completed host request cannot be cancelled")
            if request["state"] == "running":
                self.ledger.finish(request_id, status="cancelled", result={"reason": reason})
            request.update(state="cancelled", cancellation_reason=reason, cancelled_at=time.time())
            self.workspace.write(self.path, state)
            return request

    def pending(self) -> list[dict]:
        return [
            r for r in self.snapshot()["requests"].values() if r["state"] in {"pending", "running"}
        ]

    def fulfill(self, request: dict, handler) -> dict:
        """Run an available host callback only for a newly claimed request."""
        if request["state"] == "pending" and handler is not None:
            claimed = self.start(request["request_id"])
            if not claimed["replay"]:
                return self.reply(
                    request["request_id"],
                    handler(claimed),
                    host=request["host"],
                    model=request["model"],
                    binding_digest=request["binding_digest"],
                )
        return request

    @staticmethod
    def _get(state: dict, request_id: str) -> dict:
        try:
            return state["requests"][request_id]
        except KeyError as exc:
            raise AuditError("host request not found in this run") from exc
