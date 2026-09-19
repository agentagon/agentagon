"""Local service scheduler; persisted windows submit ordinary idempotent tasks."""

import copy
import threading
import uuid
from datetime import datetime

from agentagon.core.records import AuditError, identifier, now
from agentagon.workflows.production_runtime import after


class Scheduler:
    def __init__(self, application):
        self.app = application
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name="agentagon-observations")
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=5)

    def run(self):
        while not self.stopped.wait(5):
            try:
                self.tick()
                self.retry_memory()
            except (AuditError, OSError):
                continue

    def tick(self, at=None):
        at = at or now()
        for project in self.app.state.read()["projects"]:
            for item in self.app.state.db.list_records(project, "monitors"):
                try:
                    self.advance(project, item["id"], at)
                except (AuditError, OSError) as exc:
                    with self.app.lock:
                        monitor = self.app.monitoring.get(project, item["id"])
                        monitor.update(
                            state="needs_attention", error=str(exc), next_due=after(3600, at)
                        )
                        self.app.state.db.put_record(project, "monitors", item["id"], monitor)
                        self.attention(project, monitor)

    def attention(self, project, monitor):
        record_id = identifier("monitor_attention", monitor["id"])
        previous = self.app.state.db.get_record(project, "attention", record_id) or {}
        message = monitor.get("error") or "Production task requires input, resume or discard."
        if previous.get("active") and previous.get("message") == message:
            return
        self.app.state.db.put_record(
            project,
            "attention",
            record_id,
            {
                **previous,
                "agent_id": monitor["agent_id"],
                "monitor_id": monitor["id"],
                "message": message,
                "active": True,
                "at": now(),
            },
        )

    def advance(self, project, monitor_id, at):
        with self.app.lock:
            monitor = self.app.monitoring.get(project, monitor_id)
            if not monitor["enabled"]:
                return
            if monitor.get("task_id"):
                task = self.app.runtime.get(project, monitor["task_id"])
                if task["state"] not in {
                    "completed",
                    "completed_with_limits",
                    "failed",
                    "cancelled",
                }:
                    monitor["state"] = (
                        "needs_attention"
                        if task["state"] in {"interrupted", "needs_input", "paused"}
                        else "running"
                    )
                    self.app.state.db.put_record(project, "monitors", monitor_id, monitor)
                    if monitor["state"] == "needs_attention":
                        self.attention(project, monitor)
                    return
                if task["state"] == "failed":
                    monitor["failures"] = monitor.get("failures", 0) + 1
                    monitor.update(
                        state="needs_attention",
                        error=task.get("next_action"),
                        next_due=after(min(86400, 300 * 2 ** min(monitor["failures"], 8)), at),
                    )
                    message = (monitor.get("error") or "").lower()
                    if (
                        any(
                            word in message
                            for word in (
                                "storage budget",
                                "credential",
                                "unauthenticated",
                                "binding changed",
                            )
                        )
                        or monitor["failures"] >= 8
                    ):
                        monitor["enabled"] = False
                    self.attention(project, monitor)
                monitor.update(task_id=None, pending=None)
                self.app.state.db.put_record(project, "monitors", monitor_id, monitor)
                if not monitor["enabled"]:
                    return
            if monitor["next_due"] > at:
                return
            # Persist the exact submission before dispatch so a crash cannot mint another window.
            if not monitor.get("pending"):
                earliest = after(-monitor["catchup_days"] * 86400, at)
                checkpoint = monitor.get("checkpoint") or earliest
                start = max(earliest, after(-300, checkpoint))
                window = {"start": start, "end": at, "gap": checkpoint < earliest}
                if datetime.fromisoformat(start) >= datetime.fromisoformat(at):
                    return
                policy = {
                    k: copy.deepcopy(monitor[k])
                    for k in (
                        "id",
                        "agent_id",
                        "selector",
                        "binding_digest",
                        "measurements",
                        "series",
                        "interval_seconds",
                        "trace_cap",
                        "storage_budget_bytes",
                        "catchup_days",
                        "diagnosis",
                    )
                }
                operation = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL, f"{project}:{monitor_id}:{monitor['series']}:{at}"
                    )
                )
                monitor["pending"] = {
                    "operation_id": operation,
                    "workflow": "observe",
                    "agent_id": monitor["agent_id"],
                    "input": {"type": "monitor", "id": monitor_id},
                    "limits": {"max_elapsed_seconds": 300},
                    "options": {"monitor_policy": policy, "window": window, "scheduled": True},
                }
                self.app.state.db.put_record(project, "monitors", monitor_id, monitor)
            task = self.app.submit_task(project, monitor["pending"], scheduled=True)
            monitor.update(task_id=task["task_id"], state="running")
            self.app.state.db.put_record(project, "monitors", monitor_id, monitor)

    def retry_memory(self):
        from agentagon.workflows.outcomes import record_outcome

        for project in self.app.state.read()["projects"]:
            for job in self.app.runtime.list(project):
                if job.get("memory_note") and job["state"] in {
                    "completed",
                    "completed_with_limits",
                    "failed",
                    "cancelled",
                }:
                    try:
                        record_outcome(self.app, self.app.state.workspace(project), job)
                    except (AuditError, OSError):
                        continue
                    with self.app.runtime.condition:
                        latest = self.app.runtime._read(project, job["id"])
                        latest.pop("memory_note", None)
                        self.app.runtime._write(latest)
