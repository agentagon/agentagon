"""Versioned local observation policies and read-only lifecycle projections."""

import copy
import math
import uuid

from agentagon.core.records import AuditError, digest, now, timestamp_ns

DEFAULTS = {
    "interval_seconds": 3600,
    "trace_cap": 100,
    "storage_budget_bytes": 1024**3,
    "catchup_days": 7,
    "diagnosis": True,
}
METRICS = {"latency_ms", "cost_usd", "failure_rate", "issue_recurrence", "quality"}

PUBLIC_STATES = {
    "paused": (
        "Paused",
        "Automatic production collection is paused.",
        "enable_monitoring",
    ),
    "scheduled": (
        "Scheduled",
        "The next bounded production check is scheduled.",
        "analyze_now",
    ),
    "collecting": (
        "Collecting",
        "A production evidence check is running.",
        "inspect_check",
    ),
    "waiting_for_traffic": (
        "Waiting for traffic",
        "The latest complete window contained no matching production traffic.",
        "review_trace_scope",
    ),
    "credentials_needed": (
        "Credentials needed",
        "The provider credentials must be repaired before collection can continue.",
        "repair_credentials",
    ),
    "storage_limit_reached": (
        "Storage limit reached",
        "The evidence storage budget blocked production collection.",
        "adjust_storage_budget",
    ),
    "interrupted_check": (
        "Interrupted check",
        "A prior production check requires an explicit resume or discard decision.",
        "resolve_interrupted_check",
    ),
    "partial_coverage": (
        "Partial coverage",
        "The latest retained window contains incomplete coverage or a collection gap.",
        "inspect_coverage",
    ),
    "backing_off": (
        "Backing off",
        "Production collection failed and its next attempt is delayed.",
        "analyze_now",
    ),
    "stale": (
        "Stale",
        "A scheduled check is overdue, so coverage after the last checkpoint is unknown.",
        "analyze_now",
    ),
}


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


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        return timestamp_ns(value)
    except (TypeError, ValueError):
        return None


def _latest_observation(observations, monitor, *, current_series=False):
    selected = [item for item in observations if item.get("monitor_id") == monitor["id"]]
    if current_series:
        selected = [item for item in selected if item.get("series") == monitor.get("series")]
    return max(
        selected,
        key=lambda item: (
            _time((item.get("window") or {}).get("end")) or -1,
            item.get("id", ""),
        ),
        default=None,
    )


def _task_evidence(application, project, monitor):
    task_id = monitor.get("task_id")
    if not task_id:
        return None
    try:
        task = application.runtime.get(project, task_id)
    except (AuditError, KeyError, OSError):
        return {"id": task_id, "state": "unavailable", "next_action": None}
    return {
        "id": task_id,
        "state": task.get("state"),
        "next_action": task.get("next_action"),
        "updated_at": task.get("updated_at"),
    }


def _coverage_summary(observation, monitor):
    if observation is None:
        return {
            "state": "unknown",
            "observation_id": None,
            "series": monitor.get("series"),
            "complete": None,
            "gap": None,
            "matching_traces": None,
            "measurements": [],
            "limitations": [],
        }
    coverage = observation.get("coverage") or {}
    window = observation.get("window") or {}
    metrics = observation.get("metrics") or []
    metric_coverage = [
        {
            key: copy.deepcopy(metric.get(key))
            for key in (
                "name",
                "count",
                "population",
                "coverage",
                "time_coverage_complete",
            )
        }
        for metric in metrics
    ]
    complete = coverage.get("complete")
    if type(complete) is not bool:
        complete = None
    gap = window.get("gap") is True
    incomplete_measurement = any(
        metric.get("time_coverage_complete") is False for metric in metrics
    )
    if complete is False or gap or incomplete_measurement:
        state = "partial"
        complete = False
    elif complete is True:
        state = "complete"
    else:
        state = "unknown"
    populations = [
        metric["population"] for metric in metrics if type(metric.get("population")) in (int, float)
    ]
    waiting = (
        state == "complete"
        and bool(metrics)
        and len(populations) == len(metrics)
        and all(value == 0 for value in populations)
    )
    return {
        "state": state,
        "observation_id": observation.get("id"),
        "series": observation.get("series"),
        "complete": complete,
        "gap": gap,
        "matching_traces": max(populations) if populations else None,
        "waiting_for_traffic": waiting,
        "acquisition": copy.deepcopy(coverage),
        "measurements": metric_coverage,
        "limitations": copy.deepcopy(observation.get("limitations", [])),
    }


def _has_words(message, phrases):
    text = message.casefold() if isinstance(message, str) else ""
    return any(phrase in text for phrase in phrases)


def _is_stale(monitor, at):
    due = _time(monitor.get("next_due"))
    current = _time(at)
    if due is None or current is None:
        return bool(monitor.get("enabled"))
    # The scheduler checks every five seconds.  A short grace period keeps a
    # newly enabled monitor from appearing stale before its first scheduler tick.
    return bool(monitor.get("enabled")) and current > due + 60 * 1_000_000_000


def _public_state(monitor, task, coverage, at):
    task_state = task.get("state") if task else None
    message = " ".join(
        value
        for value in (monitor.get("error"), task.get("next_action") if task else None)
        if isinstance(value, str)
    )
    if monitor.get("state") == "paused":
        code = "paused"
    elif task_state in {"interrupted", "needs_input", "paused", "unavailable"}:
        code = "interrupted_check"
    elif task_state == "running":
        code = "collecting"
    elif task_state == "queued":
        code = "scheduled"
    elif _has_words(message, ("credential", "unauthenticated", "authentication", "api key")):
        code = "credentials_needed"
    elif _has_words(
        message,
        ("storage budget", "storage limit", "storage exhausted", "disk full", "no space left"),
    ):
        code = "storage_limit_reached"
    elif (
        task_state == "failed"
        or monitor.get("failures", 0)
        or monitor.get("state") == "needs_attention"
    ):
        code = "backing_off"
    elif not monitor.get("enabled"):
        code = "paused"
    elif _is_stale(monitor, at):
        code = "stale"
    elif coverage["state"] == "partial":
        code = "partial_coverage"
    elif coverage.get("waiting_for_traffic"):
        code = "waiting_for_traffic"
    else:
        code = "scheduled"
    label, reason, action = PUBLIC_STATES[code]
    return {
        "code": code,
        "label": label,
        "reason": reason,
        "resolution_action": action,
        "task_state": task_state,
        "next_due": monitor.get("next_due"),
        "last_checked": monitor.get("last_checked"),
    }


def _project_monitor(application, project, monitor, observations, at):
    current = _latest_observation(observations, monitor, current_series=True)
    latest = _latest_observation(observations, monitor)
    coverage = _coverage_summary(current, monitor)
    latest_coverage = _coverage_summary(latest, monitor)
    task = _task_evidence(application, project, monitor)
    projected = copy.deepcopy(monitor)
    projected.update(
        public_state=_public_state(monitor, task, coverage, at),
        current_check=task,
        last_successful_window=(
            {
                "observation_id": latest.get("id"),
                "series": latest.get("series"),
                "coverage_complete": latest_coverage["complete"] is True,
                **copy.deepcopy(latest.get("window", {})),
            }
            if latest
            else None
        ),
        last_successful_checkpoint=copy.deepcopy(monitor.get("checkpoint")),
        coverage_summary=coverage,
        revision_summary={
            "comparison_series": monitor.get("series"),
            "operational_revision": monitor.get("operational_revision"),
            "record_revision": monitor.get("revision"),
        },
    )
    return projected


def monitor_projection(application, project, monitor_id, *, observations=None, at=None):
    """Return one read-only monitor projection without changing scheduler authority."""

    application.state.project(project)
    monitor = application.state.db.get_record(project, "monitors", monitor_id)
    if monitor is None:
        raise AuditError("monitor not found in this project")
    records = (
        application.state.db.list_records(project, "observations")
        if observations is None
        else observations
    )
    return _project_monitor(application, project, monitor, records, at or now())


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
            comparison = {
                "agent_id": agent["id"],
                "selector": selector,
                "binding_digest": agent["binding_digest"],
                "measurements": measurements,
                # This is currently both a provider acquisition bound and the
                # maximum sampled population passed to comparison. Changing it
                # can change the measured population, so it belongs here.
                "trace_cap": config["trace_cap"],
            }
            operations = {
                "enabled": payload.get("enabled", False),
                "interval_seconds": config["interval_seconds"],
                "storage_budget_bytes": config["storage_budget_bytes"],
                "catchup_days": config["catchup_days"],
                "diagnosis": config["diagnosis"],
            }
            policy = {**comparison, **operations}
            policy_digest = digest(policy)
            comparison_digest = digest(comparison)
            operations_digest = digest(operations)
            comparison_changed = (
                not previous or previous.get("comparison_digest") != comparison_digest
            )
            operations_changed = (
                not previous or previous.get("operations_digest") != operations_digest
            )
            changed = comparison_changed or operations_changed
            record = {
                **(previous or {}),
                **policy,
                "id": monitor_id or "monitor_" + uuid.uuid4().hex[:24],
                "policy_digest": policy_digest,
                "comparison_digest": comparison_digest,
                "operations_digest": operations_digest,
                "operational_revision": (
                    (previous.get("operational_revision", 1) if previous else 0)
                    + int(operations_changed)
                ),
                "enabled": payload.get("enabled", False),
                "series": (previous.get("series", 0) if previous else 0) + int(comparison_changed),
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
            if comparison_changed:
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
                enabled = action == "enable"
                changed = monitor.get("enabled") is not enabled
                monitor.update(
                    enabled=enabled,
                    next_due=now(),
                    state="ready" if enabled else "paused",
                )
                if changed:
                    operations = {
                        key: monitor[key]
                        for key in (
                            "enabled",
                            "interval_seconds",
                            "storage_budget_bytes",
                            "catchup_days",
                            "diagnosis",
                        )
                    }
                    comparison = {
                        key: monitor[key]
                        for key in (
                            "agent_id",
                            "selector",
                            "binding_digest",
                            "measurements",
                            "trace_cap",
                        )
                    }
                    monitor.update(
                        operations_digest=digest(operations),
                        policy_digest=digest({**comparison, **operations}),
                        operational_revision=monitor.get("operational_revision", 1) + 1,
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
        at = now()
        monitors = [
            _project_monitor(self.app, project, monitor, observations, at)
            for monitor in self.db.list_records(project, "monitors")
        ]
        return {
            "monitors": monitors,
            "observations": observations,
            "deployments": self.db.list_records(project, "deployments"),
            "improvements": list_improvements(self.app, project),
            "attention": [
                r for r in self.db.list_records(project, "attention") if r.get("active", True)
            ],
        }
