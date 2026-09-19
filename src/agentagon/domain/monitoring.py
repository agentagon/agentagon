"""Versioned local observation policies and read-only lifecycle projections."""

import copy
import math
import uuid

from agentagon.core.records import AuditError, digest, now

DEFAULTS = {
    "interval_seconds": 3600,
    "trace_cap": 100,
    "storage_budget_bytes": 1024**3,
    "catchup_days": 7,
    "diagnosis": True,
}
METRICS = {"latency_ms", "cost_usd", "failure_rate", "issue_recurrence", "quality"}


def measurement(value):
    if not isinstance(value, dict) or set(value) - {
        "name",
        "metric",
        "aggregation",
        "direction",
        "target",
        "material_change",
        "minimum_samples",
        "minimum_coverage",
        "window_hours",
        "reference_hours",
        "issue_id",
        "quality_key",
        "accepted",
        "population",
    }:
        raise AuditError("unsupported production measurement")
    result = {
        "aggregation": "mean",
        "direction": "lower",
        "minimum_samples": 100,
        "minimum_coverage": 0.95,
        "window_hours": 24,
        "reference_hours": 168,
        "material_change": 0,
        "accepted": False,
        "population": {},
        **copy.deepcopy(value),
    }
    if result.get("metric") not in METRICS or result["aggregation"] not in {"mean", "p95"}:
        raise AuditError("choose a supported metric and aggregation")
    if result["direction"] not in {"lower", "higher"} or type(result["accepted"]) is not bool:
        raise AuditError("invalid measurement direction or acceptance")
    result.setdefault("name", result["metric"])
    if not isinstance(result["name"], str) or not 1 <= len(result["name"]) <= 200:
        raise AuditError("measurement requires a bounded name")
    if not isinstance(result["population"], dict) or len(result["population"]) > 20:
        raise AuditError("measurement population must be bounded equality filters")
    if any(
        not isinstance(k, str) or not isinstance(v, (str, int, bool))
        for k, v in result["population"].items()
    ):
        raise AuditError("invalid population filter")
    for field, minimum, maximum in [
        ("minimum_samples", 2, 10000),
        ("window_hours", 1, 168),
        ("reference_hours", 1, 168),
    ]:
        if type(result[field]) is not int or not minimum <= result[field] <= maximum:
            raise AuditError(f"invalid measurement {field}")
    for field in ("minimum_coverage", "material_change", "target"):
        v = result.get(field)
        if v is not None and (type(v) not in (int, float) or not math.isfinite(v)):
            raise AuditError(f"invalid measurement {field}")
    if not 0 < result["minimum_coverage"] <= 1 or result["material_change"] < 0:
        raise AuditError("invalid measurement coverage or effect threshold")
    if result["metric"] == "issue_recurrence" and not result.get("issue_id"):
        raise AuditError("recurrence measurement requires an issue")
    if result["metric"] == "quality" and not result.get("quality_key"):
        raise AuditError("quality measurement requires an accepted trace score key")
    return result


class Monitoring:
    def __init__(self, application):
        self.app = application
        self.db = application.state.db

    def get(self, project, monitor_id):
        self.app.state.project(project)
        record = self.db.get_record(project, "monitors", monitor_id)
        if record is None:
            raise AuditError("monitor not found in this project")
        return record

    def save(self, project, payload, monitor_id=None):
        if not isinstance(payload, dict) or set(payload) - {
            "agent_id",
            "environment",
            "connection_id",
            "enabled",
            "measurements",
            "expected_revision",
            *DEFAULTS,
        }:
            raise AuditError("unsupported monitor fields")
        with self.app.lock:
            previous = self.get(project, monitor_id) if monitor_id else None
            if previous and payload.get("expected_revision") != previous["revision"]:
                raise AuditError("monitor changed; reload before editing")
            agent = self.app.catalog.agent(project, payload.get("agent_id"))
            if agent["status"] != "confirmed":
                raise AuditError("confirm the agent before enabling monitoring")
            selector = copy.deepcopy(agent["trace_selector"])
            connection_id = payload.get("connection_id") or selector.get("connection_id")
            connection = self.app.connection(connection_id, project)
            if selector.get("connection_id") != connection_id:
                raise AuditError("confirm this connection in the agent trace binding first")
            environment = payload.get("environment") or selector.get("environment")
            if not isinstance(environment, str) or not 1 <= len(environment) <= 200:
                raise AuditError("monitor requires an explicit environment")
            selector["environment"] = environment
            if selector.get("project") not in (None, connection.get("project")):
                raise AuditError("monitor provider project differs from agent binding")
            selector["project"] = connection.get("project")
            config = {**DEFAULTS, **{k: payload[k] for k in DEFAULTS if k in payload}}
            for k, low, high in [
                ("interval_seconds", 60, 86400),
                ("trace_cap", 1, 100),
                ("storage_budget_bytes", 20_000_000, 1024**4),
                ("catchup_days", 1, 7),
            ]:
                if type(config[k]) is not int or not low <= config[k] <= high:
                    raise AuditError(f"invalid monitor {k}")
            if (
                type(config["diagnosis"]) is not bool
                or type(payload.get("enabled", False)) is not bool
            ):
                raise AuditError("monitor enabled and diagnosis must be booleans")
            measurements = payload.get("measurements", [])
            if not isinstance(measurements, list) or len(measurements) > 20:
                raise AuditError("monitor accepts at most 20 measurements")
            measurements = [measurement(m) for m in measurements]
            if len({m["name"] for m in measurements}) != len(measurements):
                raise AuditError("measurement names must be unique")
            for m in measurements:
                if m.get("issue_id"):
                    from agentagon.domain.issues import get_issue

                    issue = get_issue(self.app.state.workspace(project), m["issue_id"])
                    if issue.get("agent_id") != agent["id"]:
                        raise AuditError("measurement issue belongs to another agent")
            policy = {
                "agent_id": agent["id"],
                "selector": selector,
                "binding_digest": agent["binding_digest"],
                "measurements": measurements,
                **config,
            }
            policy_digest = digest(policy)
            changed = not previous or previous["policy_digest"] != policy_digest
            record = {
                **(previous or {}),
                **policy,
                "id": monitor_id or "monitor_" + uuid.uuid4().hex[:24],
                "policy_digest": policy_digest,
                "enabled": payload.get("enabled", False),
                "series": (previous.get("series", 0) if previous else 0) + int(changed),
                "next_due": now(),
                "state": "ready" if payload.get("enabled") else "paused",
            }
            if previous and previous.get("task_id"):
                task = self.app.runtime.get(project, previous["task_id"])
                if task["state"] not in {
                    "completed",
                    "completed_with_limits",
                    "failed",
                    "cancelled",
                }:
                    if changed:
                        raise AuditError(
                            "finish or cancel the active observation before changing its policy"
                        )
                    if not record["enabled"] and task["state"] in {"queued", "running"}:
                        self.app.runtime.control(
                            project, task["id"], "cancel", {"operation_id": str(uuid.uuid4())}
                        )
            if changed:
                record.update(
                    checkpoint=None,
                    last_diagnosis_at=None,
                    diagnosed_digest=None,
                    known_issue_ids=[],
                    outcome_digest=None,
                    references={},
                    last_checked=None,
                    last_observation_id=None,
                    task_id=None,
                    pending=None,
                )
            return self.db.put_record(project, "monitors", record["id"], record)

    def control(self, project, monitor_id, action):
        if action not in {"pause", "enable", "discard"}:
            raise AuditError("choose pause, enable or discard")
        with self.app.lock:
            monitor = self.get(project, monitor_id)
            if action == "discard":
                if monitor.get("task_id"):
                    task = self.app.runtime.get(project, monitor["task_id"])
                    if task["state"] not in {
                        "completed",
                        "completed_with_limits",
                        "failed",
                        "cancelled",
                    }:
                        self.app.runtime.control(
                            project, task["id"], "cancel", {"operation_id": str(uuid.uuid4())}
                        )
                monitor.update(task_id=None, pending=None, next_due=now(), state="ready")
            else:
                monitor.update(
                    enabled=action == "enable",
                    next_due=now(),
                    state="ready" if action == "enable" else "paused",
                )
                if action == "pause" and monitor.get("task_id"):
                    task = self.app.runtime.get(project, monitor["task_id"])
                    if task["state"] in {"queued", "running"}:
                        self.app.runtime.control(
                            project, task["id"], "cancel", {"operation_id": str(uuid.uuid4())}
                        )
            return self.db.put_record(project, "monitors", monitor_id, monitor)

    def overview(self, project):
        from agentagon.domain.improvements import list_improvements

        self.app.state.project(project)
        observations = self.db.list_records(project, "observations")
        monitors = self.db.list_records(project, "monitors")
        for monitor in monitors:
            monitor["stale"] = (
                not monitor.get("last_checked") or monitor.get("next_due", "") < now()
            )
        return {
            "monitors": monitors,
            "observations": observations,
            "deployments": self.db.list_records(project, "deployments"),
            "improvements": list_improvements(self.app, project),
            "attention": [
                r for r in self.db.list_records(project, "attention") if r.get("active", True)
            ],
        }
