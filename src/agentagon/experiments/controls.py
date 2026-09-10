"""Durable run controls shared by the CLI, dashboard, and active host.

Only explicit host acknowledgment consumes queued authoring instructions. Control
requests never contain shell commands or mutate a frozen evaluation contract.
"""

import copy
import fcntl
import os
from contextlib import contextmanager

from agentagon.core.records import AuditError, encoded, now, validate_record
from agentagon.experiments import engine, evaluation, learning, search
from agentagon.experiments.store import load_run, locked, run_dir

QUEUED = {"directive", "expand", "continue"}
FINISHED = {"applied", "failed", "cancelled"}
MAX_CONTROL_REQUEST_BYTES = 64 * 1024


class ControlConflict(AuditError):
    """The client's run revision or operation identity is stale."""

    status_code = 409


@contextmanager
def _driver(workspace, run_id: str):
    directory = run_dir(workspace, run_id)
    if not (directory / "state.json").exists():
        raise AuditError("fix run not found in this checkout")
    fd = os.open(directory / "controls.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def _operation(data: dict, operation_id: str) -> dict:
    for operation in data.get("controls", []):
        if operation["operation_id"] == operation_id:
            return operation
    raise AuditError("control operation does not belong to this run")


def _project(operation: dict) -> dict:
    fields = {
        "operation_id",
        "action",
        "state",
        "created_at",
        "updated_at",
        "error",
        "host_id",
        "retryable",
    }
    result = {key: copy.deepcopy(value) for key, value in operation.items() if key in fields}
    request = operation["request"]
    for key in ("text", "hypothesis", "reason", "candidate_id", "parent_id", "target_operation_id"):
        if key in request:
            result[key] = request[key]
    return result


def projection(data: dict) -> dict:
    policy = data.get("search", {}).get("policy", {"strategy": "pareto", "seed": 0})
    return {
        "revision": data.get("revision", 0),
        "search_policy": copy.deepcopy(policy),
        "objectives": [
            {"name": name, "direction": value["direction"], "unit": value["unit"]}
            for name, value in data.get("spec", {}).get("metrics", {}).items()
        ],
        "controls": [_project(operation) for operation in data.get("controls", [])],
    }


def _response(data: dict, operation_id: str) -> dict:
    operation = _operation(data, operation_id)
    result = {"operation": _project(operation), "revision": data.get("revision", 0)}
    if "result" in operation:
        result["result"] = copy.deepcopy(operation["result"])
    return result


def _preflight(data: dict, request: dict) -> None:
    action = request["action"]
    if action == "policy":
        search.validate_policy(request["policy"], data["spec"])
    elif action == "expand":
        if data["state"] in {"stopped", "exhausted"}:
            raise AuditError("continue the stopped or exhausted run before requesting expansion")
        parent = engine._candidate(data, request["parent_id"])
        if (
            parent["state"] != "verified"
            or evaluation.invalidated(data, parent)
            or parent.get("expansion_exhausted")
        ):
            raise AuditError("requested parent is not eligible for expansion")
        if not parent.get("feasible") and parent["candidate_id"] != data["baseline_id"]:
            raise AuditError("requested parent must be feasible or the verified initial baseline")
    elif action == "continue":
        if data["state"] not in {"stopped", "exhausted"}:
            raise AuditError("run is already active")
        prospective = copy.deepcopy(data)
        engine._continue(prospective, True, request.get("limits"))
        engine._update(prospective)
        if prospective["state"] == "exhausted":
            raise AuditError("exhausted limits require an explicit extension before continuation")
    elif action == "select":
        if request["candidate_id"] not in evaluation.frontier(data):
            raise AuditError("select a verified feasible candidate on the current Pareto frontier")
    elif action in {"invalidate", "exhaust"}:
        candidate = engine._candidate(data, request["candidate_id"])
        if (
            action == "exhaust"
            and candidate["state"] not in engine.TERMINAL
            and data["state"] != "stopped"
        ):
            raise AuditError("finish or stop pending candidate work before marking its branch")
    elif action in {"ack", "cancel"}:
        target = _operation(data, request["target_operation_id"])
        if action == "cancel" and target["state"] != "queued":
            raise AuditError("only an unacknowledged queued control can be cancelled")
        if action == "ack":
            if target["action"] not in QUEUED:
                raise AuditError("this control does not require host acknowledgment")
            if target["state"] == "cancelled":
                raise AuditError("cancelled control cannot be acknowledged")
            if target.get("host_id") not in {None, request["host_id"]}:
                raise ControlConflict("another host already acknowledged this control")
            if target["state"] == "queued":
                _preflight(data, target["request"])


def _invalidate(data: dict, request: dict) -> list[str]:
    invalid = {request["candidate_id"]}
    while True:
        descendants = {
            cid
            for cid, candidate in data["candidates"].items()
            if candidate.get("parent_id") in invalid
        }
        if descendants <= invalid:
            break
        invalid |= descendants
    for cid in invalid:
        candidate = data["candidates"][cid]
        candidate["invalidated"] = True
        candidate.setdefault("invalidations", []).append(
            {"operation_id": request["operation_id"], "reason": request["reason"], "at": now()}
        )
    if data["baseline_id"] in invalid:
        data["state"] = "stopped"
    return sorted(invalid)


def _apply_locked(workspace, data: dict, operation: dict) -> str | None:
    """Apply atomic state changes; return the ID of an external engine action."""
    request = operation["request"]
    action = request["action"]
    if action in QUEUED:
        operation["state"] = "queued"
        return None
    if action == "policy":
        search.set_policy(data, request["policy"])
        operation["result"] = {"search_policy": copy.deepcopy(data["search"]["policy"])}
    elif action == "invalidate":
        operation["result"] = {"invalidated": _invalidate(data, request)}
        operation["state"] = "acknowledged"
        return operation["operation_id"]
    elif action == "exhaust":
        candidate = engine._candidate(data, request["candidate_id"])
        candidate["expansion_exhausted"] = True
        candidate["exhaustion"] = {
            "operation_id": operation["operation_id"],
            "reason": request["reason"],
            "at": now(),
        }
    elif action == "cancel":
        target = _operation(data, request["target_operation_id"])
        target.update(state="cancelled", updated_at=now())
    elif action == "ack":
        target = _operation(data, request["target_operation_id"])
        if target["state"] not in FINISHED or target.get("retryable"):
            target.update(state="acknowledged", host_id=request["host_id"], updated_at=now())
            if target["action"] == "expand":
                operation["state"] = "acknowledged"
                return target["operation_id"]
            if target["action"] == "continue":
                engine._continue(data, True, target["request"].get("limits"))
            target.update(state="applied", updated_at=now())
        operation["result"] = {
            "target_operation_id": target["operation_id"],
            "state": target["state"],
        }
        if target["action"] == "directive":
            operation["result"]["directive"] = target["request"]["text"]
    elif action == "scan":
        operation["result"] = learning.prepare(workspace, data)
    elif action == "insights":
        operation["result"] = learning.accept(workspace, data, request["response"])
    elif action == "scan_fail":
        operation["result"] = learning.fail(data, request["scan_id"], request["reason"])
    elif action in {"stop", "select"}:
        if action == "stop":
            # A crash after accepting stop must not permit another trial reservation.
            data["state"] = "stopped"
        operation["state"] = "acknowledged"
        return operation["operation_id"]
    operation.update(state="applied", updated_at=now())
    return None


def _execute(workspace, run_id: str, operation_id: str) -> None:
    data = load_run(workspace, run_id)
    operation = _operation(data, operation_id)
    if operation["state"] in FINISHED and not operation.get("retryable"):
        return
    request = operation["request"]
    try:
        pending = False
        if operation["action"] == "invalidate":
            cancelled = engine.cancel_invalidated(workspace, run_id)
            result = {
                **operation.get("result", {}),
                "invalidated_active_attempts": cancelled["invalidated_active_attempts"],
                "cleanup_pending": cancelled["cleanup_pending"],
            }
            pending = bool(result["invalidated_active_attempts"])
        elif operation["action"] == "stop":
            result = {"state": engine.stop(workspace, run_id)["state"]}
        elif operation["action"] == "select":
            selected = engine.select(workspace, run_id, request["candidate_id"])
            result = {
                "candidate_id": request["candidate_id"],
                "selected_branch": selected["selected_branch"],
            }
        elif operation["action"] == "expand":
            created = engine.new(
                workspace,
                run_id,
                parent_id=request["parent_id"],
                hypothesis=request["hypothesis"],
                author=operation["host_id"],
                operation_id=operation["operation_id"],
            )
            result = {
                "run_id": run_id,
                "candidate_id": created["candidate_id"],
                "state": created["candidate"]["state"],
                "worktree": str(workspace.root / created["candidate"]["worktree"]),
            }
        else:
            raise AuditError("unsupported pending engine control")
    except (AuditError, OSError) as exc:
        with locked(workspace, run_id):
            data = load_run(workspace, run_id)
            operation = _operation(data, operation_id)
            reserved = operation["action"] == "expand" and any(
                candidate.get("operation_id") == operation_id
                and not evaluation.invalidated(data, candidate)
                for candidate in data["candidates"].values()
            )
            operation.update(
                state="failed",
                updated_at=now(),
                error=str(exc) if isinstance(exc, AuditError) else "Local control operation failed",
                retryable=isinstance(exc, OSError) or reserved,
            )
            engine._save(workspace, data)
        return
    with locked(workspace, run_id):
        data = load_run(workspace, run_id)
        operation = _operation(data, operation_id)
        operation.update(
            state="acknowledged" if pending else "applied",
            updated_at=now(),
            result=result,
            retryable=False,
        )
        operation.pop("error", None)
        engine._save(workspace, data)


def submit(workspace, run_id: str, request: dict) -> dict:
    validate_record("fix-control", request)
    if len(encoded(request).encode()) > MAX_CONTROL_REQUEST_BYTES:
        raise AuditError(f"control request exceeds {MAX_CONTROL_REQUEST_BYTES} bytes")
    with _driver(workspace, run_id):
        external = None
        with locked(workspace, run_id):
            data = load_run(workspace, run_id)
            engine._update(data)
            operation = next(
                (
                    op
                    for op in data.get("controls", [])
                    if op["operation_id"] == request["operation_id"]
                ),
                None,
            )
            if operation:
                if operation["request"] != request:
                    raise ControlConflict("operation ID was already used for a different request")
                if operation["state"] == "acknowledged" or operation.get("retryable"):
                    external = (
                        request["target_operation_id"]
                        if request["action"] == "ack"
                        else operation["operation_id"]
                    )
            else:
                if request["expected_revision"] != data.get("revision", 0):
                    raise ControlConflict(
                        "run changed; refresh its revision before submitting a new control"
                    )
                _preflight(data, request)
                operation = {
                    "version": 1,
                    "operation_id": request["operation_id"],
                    "action": request["action"],
                    "state": "accepted",
                    "created_at": now(),
                    "updated_at": now(),
                    "request": copy.deepcopy(request),
                }
                data.setdefault("controls", []).append(operation)
                external = _apply_locked(workspace, data, operation)
                engine._save(workspace, data)
        if external:
            _execute(workspace, run_id, external)
            if request["action"] == "ack":
                with locked(workspace, run_id):
                    data = load_run(workspace, run_id)
                    operation = _operation(data, request["operation_id"])
                    target = _operation(data, external)
                    operation.update(
                        state="applied" if target["state"] == "applied" else "failed",
                        updated_at=now(),
                        result={
                            "target_operation_id": external,
                            "state": target["state"],
                            "result": target.get("result"),
                        },
                        retryable=bool(target.get("retryable")),
                    )
                    if target.get("error"):
                        operation["error"] = target["error"]
                    else:
                        operation.pop("error", None)
                    engine._save(workspace, data)
        return _response(load_run(workspace, run_id), request["operation_id"])
