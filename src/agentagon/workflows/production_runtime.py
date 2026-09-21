"""Code-owned assessment/acquisition stages for shared managed workflow tasks."""

import copy
import json
from datetime import UTC, datetime, timedelta

from agentagon.capabilities import discovery as agent_discovery
from agentagon.capabilities import production
from agentagon.capabilities.traces import snapshots
from agentagon.core.records import AuditError, digest, identifier, now, timestamp_ns
from agentagon.domain.issues import list_issues


def after(seconds, start=None):
    return (
        (datetime.fromisoformat(start or now()) + timedelta(seconds=seconds))
        .astimezone(UTC)
        .isoformat()
    )


def automatic_result(summary):
    return {
        "state": "completed",
        "text": json.dumps({"summary": summary, "issues": [], "lessons": []}),
    }


def _issue_evidence_material(workspace, issue):
    """Describe issue evidence without task/snapshot-specific artifact names."""
    semantic = {}
    opaque = []
    for reference in issue.get("evidence", []):
        value = None
        if isinstance(reference, str) and reference.startswith(".agentagon/evidence/"):
            try:
                value = workspace.read_artifact(reference)
            except (AuditError, OSError, TypeError, ValueError):
                value = None
        if isinstance(value, dict) and isinstance(value.get("diagnosis"), dict):
            value = value["diagnosis"]
        elif isinstance(value, dict):
            value = {
                key: item
                for key, item in value.items()
                if key not in {"task_id", "snapshot_id", "assessment_id", "observation_id"}
            }
        if value is None:
            if not (
                isinstance(reference, str)
                and reference.startswith(("task_", "finding_", "audit_", "snapshot_"))
            ):
                opaque.append(reference)
        else:
            semantic[digest(value)] = value
    occurrences = [
        {
            key: occurrence.get(key)
            for key in (
                "id",
                "trace_ids",
                "observed_ns",
                "finding_id",
                "basis",
                "environment",
                "deployment_id",
                "release",
                "revision",
            )
            if occurrence.get(key) is not None
        }
        for occurrence in issue.get("occurrences", [])
    ]
    return {
        "key": issue["key"],
        "agent_id": issue.get("agent_id"),
        "title": issue["title"],
        "summary": issue["summary"],
        "severity": issue["severity"],
        "confidence": issue["confidence"],
        "occurrences": sorted(occurrences, key=lambda item: item.get("id", "")),
        "semantic_evidence": [semantic[key] for key in sorted(semantic)],
        "opaque_evidence": sorted(set(opaque)),
    }


class ProductionRuntime:
    def __init__(self, application):
        self.app = application
        self.db = application.state.db

    def onboarding(self, project):
        self.app.state.project(project)
        return self.db.get_record(project, "onboarding", "setup") or {
            "id": "setup",
            "state": "configure",
            "scope": {},
            "task_id": None,
        }

    def save_onboarding(self, project, payload):
        if not isinstance(payload, dict) or set(payload) - {"scope", "state"}:
            raise AuditError("unsupported onboarding fields")
        scope = self.scope(project, payload.get("scope", {}))
        state = payload.get("state", "configure")
        if state not in {"configure", "ready"}:
            raise AuditError("choose configure or ready")
        return self.db.put_record(
            project,
            "onboarding",
            "setup",
            {
                **self.onboarding(project),
                "scope": scope,
                "state": state,
            },
        )

    def scope(self, project, value):
        if not isinstance(value, dict) or set(value) - {
            "connection_id",
            "snapshot_id",
            "environment",
            "trace_cap",
            "lookback_days",
        }:
            raise AuditError("unsupported assessment scope")
        scope = {"trace_cap": 100, "lookback_days": 7, **value}
        if type(scope["trace_cap"]) is not int or not 1 <= scope["trace_cap"] <= 100:
            raise AuditError("assessment trace cap must be 1–100")
        if type(scope["lookback_days"]) is not int or not 1 <= scope["lookback_days"] <= 7:
            raise AuditError("assessment lookback must be 1–7 days")
        if scope.get("connection_id") and scope.get("snapshot_id"):
            raise AuditError("choose a provider or an imported snapshot")
        if scope.get("connection_id"):
            self.app.connection(scope["connection_id"], project)
        if scope.get("snapshot_id"):
            snapshots.load(self.app.state.workspace(project), scope["snapshot_id"])
        if scope.get("environment") is not None and (
            not isinstance(scope["environment"], str) or len(scope["environment"]) > 200
        ):
            raise AuditError("invalid environment")
        return scope

    def acquire(self, project, workspace, connection_id, selection):
        connection = self.app.connection(connection_id, project)
        client = self.app.provider_factory(connection, self.app.credentials)
        result = client.preview("traces", selection)
        return snapshots.save(
            workspace,
            project,
            {"kind": "traces", "connection_id": connection_id, "selection": selection, **result},
        )

    def prepare(self, workspace, job, cancelled):
        if job["kind"] not in {"assess", "observe"}:
            return None
        project = job["project_id"]
        if job.get("preparation"):
            prepared = job["preparation"]
        else:
            prepared = {"limitations": [], "issue_ids": []}
            if job["kind"] == "assess":
                self._progress(
                    job, cancelled, "Discovering application agents from repository code."
                )
                scope = job["options"]["assessment"]
                discovery = self.app.catalog.discover(project, snapshot_ids=[])
                prepared["discovery"] = discovery
                prepared["limitations"].extend(discovery.get("limitations", []))
                snapshot_id = scope.get("snapshot_id")
                if scope.get("connection_id"):
                    self._progress(job, cancelled, "Acquiring the selected bounded trace window.")
                    try:
                        end = now()
                        snapshot = self.acquire(
                            project,
                            workspace,
                            scope["connection_id"],
                            {
                                "start": after(-scope["lookback_days"] * 86400, end),
                                "end": end,
                                "cap": scope["trace_cap"],
                            },
                        )
                        snapshot_id = snapshot["id"]
                    except (AuditError, OSError) as exc:
                        prepared["limitations"].append(str(exc))
                if snapshot_id:
                    try:
                        snapshot_id = snapshots.select_traces(
                            workspace,
                            project,
                            snapshot_id,
                            {"environment": scope["environment"]}
                            if scope.get("environment")
                            else {},
                            scope["trace_cap"],
                        )["id"]
                    except AuditError as exc:
                        prepared["limitations"].append(str(exc))
                        snapshot_id = None
                    if snapshot_id:
                        prepared["snapshot_id"] = snapshot_id
                        prepared["discovery"] = self.app.catalog.discover(
                            project, snapshot_ids=[snapshot_id]
                        )
                prepared["agent_measurements"] = {}
                trace_agents = {}
                if prepared.get("snapshot_id"):
                    for agent in self.app.catalog.agents(project):
                        if not agent["trace_selector"]:
                            continue
                        samples, coverage = production.rows(
                            workspace, prepared["snapshot_id"], agent["trace_selector"]
                        )
                        for sample in samples:
                            trace_agents.setdefault(sample["trace_id"], []).append(agent["id"])
                        metrics = []
                        for metric in ("latency_ms", "cost_usd", "failure_rate"):
                            scored = [row[metric] for row in samples if row.get(metric) is not None]
                            if scored:
                                metrics.append(
                                    {
                                        "metric": metric,
                                        "value": production.estimate(scored, "mean"),
                                        "count": len(scored),
                                        "coverage": len(scored) / len(samples),
                                        "complete": coverage["complete"],
                                        "source": prepared["snapshot_id"],
                                    }
                                )
                        prepared["agent_measurements"][agent["id"]] = metrics
                prepared["trace_agents"] = {
                    t: agents[0] for t, agents in trace_agents.items() if len(agents) == 1
                }
                review_scope = job["options"].get("agent_review_scope")
                if review_scope:
                    from agentagon.workflows.operations.preparation import agent_review_scope

                    target = self.app.catalog.agent(project, review_scope["agent_id"])
                    if (
                        target["status"] != "suggested"
                        or agent_review_scope(target) != review_scope
                    ):
                        raise AuditError(
                            "Suggested identity changed; review it before retrying responsibility "
                            "inference."
                        )
                    if len(target.get("code_scopes", [])) != 1:
                        raise AuditError(
                            "Responsibility inference requires one retained agent definition."
                        )
                    suggestions = [target]
                else:
                    suggestions = [
                        agent
                        for agent in self.app.catalog.agents(project)
                        if agent["status"] == "suggested" and len(agent.get("code_scopes", [])) == 1
                    ]
                # Subsequent bounded assessments progress to candidates that still lack a
                # responsibility before revisiting an already enriched suggestion.
                suggestions.sort(
                    key=lambda agent: (
                        bool(agent.get("description", "").strip()),
                        agent["name"].casefold(),
                        agent["id"],
                    )
                )
                selected = suggestions[:100]
                candidates = []
                missing_context = 0
                for agent in selected:
                    path = agent["code_scopes"][0]
                    code_evidence = next(
                        (
                            item
                            for item in agent.get("evidence", [])
                            if isinstance(item, dict)
                            and item.get("kind") == "code"
                            and item.get("path") == path
                        ),
                        {},
                    )
                    context = agent_discovery.source_context(
                        workspace.root, path, code_evidence.get("line")
                    )
                    candidate = {
                        "id": agent["id"],
                        "file": path,
                        "name": agent["name"],
                        "line": code_evidence.get("line"),
                        "symbol": code_evidence.get("symbol"),
                        "constructor": code_evidence.get("call"),
                    }
                    if context:
                        candidate["context"] = context
                    else:
                        missing_context += 1
                    candidates.append(candidate)
                prepared["candidates"] = candidates
                if len(suggestions) > len(selected):
                    prepared["limitations"].append(
                        f"Coding review is bounded to 100 candidates; "
                        f"{len(suggestions) - len(selected)} remain for a later assessment."
                    )
                if missing_context:
                    prepared["limitations"].append(
                        f"Source context was unavailable for {missing_context} coding-review "
                        "candidate(s); responsibility inference remains limited for them."
                    )
            else:
                self._progress(
                    job, cancelled, "Collecting production evidence within the saved monitor scope."
                )
                policy = job["options"]["monitor_policy"]
                agent = self.app.catalog.agent(project, policy["agent_id"])
                if agent["binding_digest"] != policy["binding_digest"]:
                    raise AuditError("Agent binding changed; update and re-enable this monitor.")
                size = sum(
                    p.stat().st_size
                    for base in (
                        workspace.state / "evidence",
                        workspace.state / "runtime" / "imports",
                    )
                    for p in base.rglob("*")
                    if p.is_file() and not p.is_symlink()
                )
                if size + 20_000_000 > policy["storage_budget_bytes"]:
                    raise AuditError(
                        "Background evidence storage budget exhausted; clean up or raise the budget."
                    )
                window = job["options"]["window"]
                snapshot = self.acquire(
                    project,
                    workspace,
                    policy["selector"]["connection_id"],
                    {
                        "project": policy["selector"]["project"],
                        "start": window["start"],
                        "end": window["end"],
                        "cap": policy["trace_cap"],
                    },
                )
                prepared.update(snapshot_id=snapshot["id"], window=window)
                samples, coverage = production.rows(workspace, snapshot["id"], policy["selector"])
                prepared.update(samples=samples, coverage=coverage)
                if samples:
                    selected = snapshots.select_traces(
                        workspace, project, snapshot["id"], policy["selector"], policy["trace_cap"]
                    )
                    prepared["snapshot_id"] = selected["id"]
            if cancelled.is_set():
                raise AuditError(
                    "Observation cancelled after acquisition; retained evidence is unchanged."
                )
            if prepared.get("snapshot_id"):
                job["options"]["trace_snapshot_id"] = prepared["snapshot_id"]
            job["preparation"] = prepared
            self._save_preparation(job, cancelled)
        if prepared.get("brain_requested"):
            return None  # Explicit resume continues the same native session and pinned evidence.
        if job["kind"] == "observe":
            policy = job["options"]["monitor_policy"]
            current = self.app.monitoring.get(project, policy["id"])
            evidence_digest = digest(
                [{k: v for k, v in s.items() if k != "snapshot_id"} for s in prepared["samples"]]
            )
            prepared["evidence_digest"] = evidence_digest
            scheduled = job["options"].get("scheduled", False)
            allowance = self.db.get_record(project, "diagnosis_allowances", policy["agent_id"])
            due = not allowance or allowance["day"] != now()[:10]
            diagnose = (
                policy["diagnosis"]
                and bool(prepared["samples"])
                and evidence_digest != current.get("diagnosed_digest")
                and (not scheduled or due)
            )
            if not diagnose:
                return automatic_result(
                    "Production observations retained; no diagnosis required in this window."
                )
        # Local scanning and acquisition remain useful without an authenticated brain.
        available = next((a for a in self.app.agents()["agents"] if a["id"] == job["agent"]), {})
        if not available.get("available") or available.get("authenticated") is not True:
            limitation = (
                "Coding backend unavailable or unauthenticated; deterministic code candidates "
                "were retained, but responsibility inference"
                + (" and trace diagnosis" if prepared.get("snapshot_id") else "")
                + " did not run."
            )
            if limitation not in prepared["limitations"]:
                prepared["limitations"].append(limitation)
            job["preparation"] = prepared
            self._save_preparation(job, cancelled)
            return automatic_result(
                f"Retained {len(prepared.get('candidates', []))} code candidate(s). "
                "Configure and authenticate the coding backend to infer responsibilities"
                + (" and diagnose the selected traces." if prepared.get("snapshot_id") else ".")
            )
        prepared["brain_requested"] = True
        if job["kind"] == "observe" and job["options"].get("scheduled"):
            with self.app.lock:
                allowance = self.db.get_record(project, "diagnosis_allowances", policy["agent_id"])
                if (
                    allowance
                    and allowance["day"] == now()[:10]
                    and allowance["task_id"] != job["id"]
                ):
                    prepared.pop("brain_requested", None)
                    self._save_preparation(job, cancelled)
                    return automatic_result(
                        "Daily diagnosis already reserved by another observation."
                    )
                monitor = self.app.monitoring.get(project, job["options"]["monitor_policy"]["id"])
                monitor["last_diagnosis_at"] = now()
                self.db.put_record(
                    project,
                    "diagnosis_allowances",
                    policy["agent_id"],
                    {
                        **(allowance or {}),
                        "id": policy["agent_id"],
                        "day": now()[:10],
                        "task_id": job["id"],
                    },
                )
                self.db.put_record(project, "monitors", monitor["id"], monitor)
        job["preparation"] = prepared
        self._progress(
            job, cancelled, "Diagnosing retained evidence with the managed coding backend."
        )
        return None

    def _progress(self, job, cancelled, message):
        job["next_action"] = message
        job["events"] = (job["events"] + [{"type": "progress", "text": message, "at": now()}])[
            -300:
        ]
        self._save_preparation(job, cancelled)

    def _save_preparation(self, job, cancelled):
        with self.app.runtime.condition:
            current = self.app.runtime._read(job["project_id"], job["id"])
            if cancelled.is_set() or current["state"] != "running":
                raise AuditError("Task interrupted during preparation; resume explicitly.")
            self.app.runtime._write(job)

    def complete(self, workspace, job, result):
        project, prepared = job["project_id"], job["preparation"]
        if job["kind"] == "assess":
            if prepared.get("brain_requested") and result.get("candidates"):
                self.app.catalog.apply_coding_review(
                    project, result["candidates"], task_id=job["id"]
                )
            if not job["options"].get("agent_review_scope"):
                self.db.put_record(
                    project,
                    "onboarding",
                    "setup",
                    {
                        **self.onboarding(project),
                        "state": "complete",
                        "task_id": job["id"],
                        "scope": job["options"]["assessment"],
                        "limitations": prepared["limitations"],
                        "agent_measurements": prepared.get("agent_measurements", {}),
                    },
                )
            return {
                **result,
                "assessment_id": job["id"],
                "recommendations": self.recommendations(project),
            }
        policy = job["options"]["monitor_policy"]
        observation_id = identifier("observation", job["id"])
        existing = self.db.get_record(project, "observations", observation_id)
        if existing:
            # A crash/retry must project the already-retained observation rather
            # than accepting a later model response as if it were authoritative.
            return {
                **result,
                "observation_id": observation_id,
                "metrics": copy.deepcopy(existing.get("metrics", [])),
                "issue_ids": copy.deepcopy(existing.get("issue_ids", [])),
            }
        from agentagon.domain.improvements import retain_reported_deployments

        retain_reported_deployments(self.app, project, policy, prepared["samples"])
        records = self.db.list_records(project, "observations")
        latest = {}
        reviewed = []
        complete = prepared["coverage"]["complete"] and not prepared["window"].get("gap")
        windows = [{**prepared["window"], "complete": prepared["coverage"]["complete"]}]
        earliest = after(-7 * 86400, prepared["window"]["end"])
        deployments = [
            d
            for d in self.db.list_records(project, "deployments")
            if d["agent_id"] == policy["agent_id"]
            and d["environment"] == policy["selector"]["environment"]
            and d.get("deployed_at")
            and timestamp_ns(d["deployed_at"]) <= timestamp_ns(prepared["window"]["end"])
        ]
        deployment = (
            max(deployments, key=lambda d: timestamp_ns(d["deployed_at"])) if deployments else None
        )
        current_monitor = self.app.monitoring.get(project, policy["id"])
        reference_key = f"{policy['series']}:{deployment['id']}" if deployment else None
        reference_path = current_monitor.get("references", {}).get(reference_key)
        reference = workspace.read_artifact(reference_path) if reference_path else None
        if deployment and not reference:
            hours = max((m["reference_hours"] for m in policy["measurements"]), default=168)
            earliest = min(earliest, after(-hours * 3600, deployment["deployed_at"]))
        for record in reversed(records):
            if (
                record["monitor_id"] != policy["id"]
                or record["series"] != policy["series"]
                or record["window"]["end"] < earliest
            ):
                continue
            saved = workspace.read_artifact(record["evidence"])
            reviewed.extend(saved.get("issue_checks", []))
            windows.append({**saved["window"], "complete": saved["coverage"]["complete"]})
            complete = complete and saved["coverage"]["complete"] and not saved["window"].get("gap")
            for sample in saved["samples"]:
                latest[sample["identity"]] = sample
        for sample in prepared["samples"]:
            latest[sample["identity"]] = sample
        if reference:
            windows.extend(reference["windows"])
            reviewed.extend(reference.get("issue_checks", []))
            for sample in reference["samples"]:
                latest[sample["identity"]] = sample
            complete = complete and reference["complete"]
        elif deployment:
            before = [
                s
                for s in latest.values()
                if s["started_ns"] < timestamp_ns(deployment["deployed_at"])
            ]
            if before:
                reference_path = workspace.artifact(
                    {
                        "samples": before,
                        "windows": windows,
                        "issue_checks": reviewed + result.get("issue_checks", []),
                        "complete": complete,
                        "deployment_id": deployment["id"],
                        "series": policy["series"],
                    }
                )
        issue_traces = {}
        latest_traces = {s["trace_id"]: s for s in latest.values()}
        for check in reviewed + result.get("issue_checks", []):
            if (
                latest_traces.get(check["trace_id"], {}).get("evidence_digest")
                == check["evidence_digest"]
            ):
                issue_traces.setdefault(check["issue_id"], {})[check["trace_id"]] = int(
                    check["present"]
                )
        metrics = production.compare(
            list(latest.values()),
            {**policy, "coverage_complete": complete, "acquisition_windows": windows},
            self.db.list_records(project, "deployments"),
            prepared["window"]["end"],
            issue_traces,
        )
        evidence = workspace.artifact(
            {
                **prepared,
                "metrics": metrics,
                "policy": policy,
                "task_id": job["id"],
                "reference_evidence": reference_path,
                "issue_checks": result.get("issue_checks", []),
            }
        )
        record = {
            "id": observation_id,
            "monitor_id": policy["id"],
            "agent_id": policy["agent_id"],
            "series": policy["series"],
            "window": prepared["window"],
            "task_id": job["id"],
            "evidence": evidence,
            "metrics": metrics,
            "coverage": prepared["coverage"],
            "limitations": prepared["limitations"],
            "issue_ids": result.get("issue_ids", []),
        }
        with self.app.lock, self.db.transaction() as tx:
            monitor = self.app.monitoring.get(project, policy["id"])
            if monitor["series"] == policy["series"]:
                if reference_path:
                    monitor.setdefault("references", {})[reference_key] = reference_path
                previous = monitor.get("outcome_digest")
                known = sorted(
                    set(monitor.get("known_issue_ids", [])) | set(result.get("issue_ids", []))
                )
                states = {i["issue_id"]: i["status"] for i in list_issues(workspace)}
                outcome = digest(
                    [[m["name"], m["status"]] for m in metrics]
                    + [[i, states.get(i)] for i in known]
                )
                monitor["known_issue_ids"] = known
                record["lesson_due"] = previous != outcome and bool(metrics or known)
                if record["lesson_due"]:
                    attention_id = identifier("attention", policy["id"])
                    tx.put_record(
                        project,
                        "attention",
                        attention_id,
                        {
                            **(tx.get_record(project, "attention", attention_id) or {}),
                            "id": attention_id,
                            "agent_id": policy["agent_id"],
                            "monitor_id": policy["id"],
                            "observation_id": observation_id,
                            "message": "Production assessment changed",
                            "at": now(),
                        },
                    )
                monitor.update(
                    checkpoint=prepared["window"]["end"],
                    last_checked=now(),
                    outcome_digest=outcome,
                    failures=0,
                    state="ready",
                    next_due=after(policy["interval_seconds"]),
                    last_observation_id=observation_id,
                )
                monitor.pop("error", None)
                attention_id = identifier("monitor_attention", policy["id"])
                attention = tx.get_record(project, "attention", attention_id)
                if attention:
                    tx.put_record(
                        project, "attention", attention_id, {**attention, "active": False}
                    )
                if prepared.get("brain_requested"):
                    monitor["diagnosed_digest"] = prepared["evidence_digest"]
                tx.put_record(project, "monitors", monitor["id"], monitor)
            tx.put_record(project, "observations", observation_id, record)
        return {**result, "observation_id": observation_id, "metrics": metrics}

    def _recommendations(self, project):
        from agentagon.domain.issues import lifecycle_projection

        result = []
        workspace = self.app.state.workspace(project)
        issues = list_issues(workspace)
        for issue in issues:
            lifecycle = lifecycle_projection(self.app, project, issue["issue_id"])
            actions = lifecycle["next_actions"]
            if not actions:
                continue
            action = actions[0]
            if action == "reopen_issue":
                continue
            labels = {
                "assign_agent": f"Assign an agent to {issue['title']}",
                "inspect_task": f"Continue work on {issue['title']}",
                "start_fix": issue["title"],
                "investigate_recurrence": f"Investigate recurrence: {issue['title']}",
                "review_verified_change": f"Review verified change: {issue['title']}",
                "prepare_local_delivery": f"Prepare delivery: {issue['title']}",
                "record_deployment": f"Record deployment: {issue['title']}",
                "observe_production": f"Observe production: {issue['title']}",
            }
            prerequisites = {
                "assign_agent": "Confirm which code-owned agent is responsible",
                "inspect_task": "Resolve the task's pending work or requested input",
                "start_fix": "Confirm ownership and expected behavior",
                "investigate_recurrence": "Review the post-deployment production evidence",
                "review_verified_change": "Choose the verified result or keep the current version",
                "prepare_local_delivery": "Prepare the selected change for review",
                "record_deployment": "Declare the release, environment, revision, and deployment time",
                "observe_production": "Enable or run a matching production monitor",
            }
            latest_change = next(
                iter(lifecycle["facets"]["test_verification"].get("changes", [])), None
            )
            target = {"type": "issue", "id": issue["issue_id"]}
            if action == "inspect_task" and lifecycle["facets"]["work"].get("active_task_id"):
                target["task_id"] = lifecycle["facets"]["work"]["active_task_id"]
            if latest_change and latest_change.get("run_id"):
                target.update(
                    {
                        "run_id": latest_change["run_id"],
                        "workflow": next(
                            (
                                item.get("workflow")
                                for item in self.db.list_records(project, "improvements")
                                if item.get("run_id") == latest_change["run_id"]
                            ),
                            "fix",
                        ),
                    }
                )
            result.append(
                {
                    "id": issue["issue_id"],
                    "agent_id": issue.get("agent_id"),
                    "title": labels.get(action, issue["title"]),
                    "workflow": "fix",
                    "input": {"type": "issue", "id": issue["issue_id"]},
                    "action": action,
                    "target": target,
                    "basis": (
                        "production_recurrence"
                        if action == "investigate_recurrence"
                        else "verified_change"
                        if lifecycle["facets"]["test_verification"]["state"] == "verified"
                        else "trace_evidence"
                        if issue["occurrences"]
                        else "reported_issue"
                    ),
                    "evidence": issue["evidence"],
                    "confidence": issue["confidence"],
                    "prerequisite": prerequisites.get(action, "Review the retained evidence"),
                    "_evidence_material": {
                        "issue": _issue_evidence_material(workspace, issue),
                        "action": action,
                        "target": target,
                    },
                }
            )
        latest_metrics = {}
        for observation in reversed(self.db.list_records(project, "observations")):
            for metric in observation["metrics"]:
                if metric["current"] is not None:
                    latest_metrics[(observation["agent_id"], metric["definition"]["metric"])] = (
                        metric,
                        observation,
                    )
        assessment = self.onboarding(project).get("agent_measurements", {})
        for agent in self.app.catalog.agents(project):
            for metric in assessment.get(agent["id"], []):
                latest_metrics.setdefault(
                    (agent["id"], metric["metric"]),
                    (
                        {
                            "current": metric["value"],
                            "count": metric["count"],
                            "coverage": metric["coverage"],
                        },
                        {"evidence": metric["source"], "window": "assessment"},
                    ),
                )
            for name, label in [
                ("latency", "Make this agent faster"),
                ("reliability", "Improve task reliability"),
                ("cost", "Reduce cost"),
            ]:
                measured = latest_metrics.get(
                    (
                        agent["id"],
                        {
                            "latency": "latency_ms",
                            "cost": "cost_usd",
                            "reliability": "failure_rate",
                        }[name],
                    )
                )
                result.append(
                    {
                        "id": identifier("recommendation", agent["id"], name),
                        "agent_id": agent["id"],
                        "title": label,
                        "workflow": "design",
                        "category": name,
                        "basis": "measured" if measured else "not_measured",
                        "evidence": [measured[1]["evidence"]] if measured else [],
                        "measurement": {
                            "value": measured[0]["current"],
                            "count": measured[0]["count"],
                            "coverage": measured[0]["coverage"],
                            "window": measured[1]["window"],
                        }
                        if measured
                        else None,
                        "prerequisite": "Create a goal and accept a measurement plan",
                    }
                )
        return result

    def recommendations(self, project):
        """Return recommendations with evidence-scoped user dispositions.

        A dismissal applies only to the exact evidence revision the user saw.  If
        an issue, measurement, or prerequisite changes, the recommendation is
        active again and the previous disposition remains available as history.
        """
        result = []
        for recommendation in self._recommendations(project):
            revision_material = recommendation.pop("_evidence_material", None)
            evidence_revision = digest(
                revision_material
                or {
                    key: recommendation.get(key)
                    for key in (
                        "agent_id",
                        "title",
                        "workflow",
                        "input",
                        "basis",
                        "evidence",
                        "measurement",
                        "prerequisite",
                    )
                }
            )
            saved = self.db.get_record(project, "recommendation_dispositions", recommendation["id"])
            current = bool(saved and saved.get("evidence_revision") == evidence_revision)
            result.append(
                {
                    **recommendation,
                    "evidence_revision": evidence_revision,
                    "active": not current,
                    "disposition": (
                        {
                            key: saved.get(key)
                            for key in ("value", "reason", "decided_at", "revision")
                        }
                        if current
                        else None
                    ),
                }
            )
        return result

    def disposition_recommendation(self, project, recommendation_id, payload):
        """Record Not now / Not relevant for one exact recommendation revision."""
        if not isinstance(payload, dict) or set(payload) - {
            "value",
            "reason",
            "evidence_revision",
            "expected_revision",
        }:
            raise AuditError("unsupported recommendation decision fields")
        value = payload.get("value")
        if value not in {"not_now", "not_relevant"}:
            raise AuditError("choose not now or not relevant")
        reason = payload.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise AuditError("recommendation reason must be at most 1000 characters")
        current = next(
            (item for item in self.recommendations(project) if item["id"] == recommendation_id),
            None,
        )
        if current is None:
            raise AuditError("recommendation is unavailable")
        if payload.get("evidence_revision") != current["evidence_revision"]:
            raise AuditError("recommendation evidence changed; review the updated recommendation")
        existing = self.db.get_record(project, "recommendation_dispositions", recommendation_id)
        expected_revision = payload.get("expected_revision")
        if expected_revision is None:
            expected_revision = existing.get("revision", 0) if existing else 0
        if type(expected_revision) is not int or expected_revision < 0:
            raise AuditError("expected recommendation revision must be a non-negative integer")
        return self.db.put_record(
            project,
            "recommendation_dispositions",
            recommendation_id,
            {
                "id": recommendation_id,
                "evidence_revision": current["evidence_revision"],
                "value": value,
                "reason": reason.strip(),
                "decided_at": now(),
            },
            expected_revision=expected_revision,
        )

    def submit(self, project, payload, binding, scheduled=False):
        from agentagon.workflows.requests import public_start

        source = payload.get("input", {})
        kind = payload["workflow"]
        if (
            not isinstance(source, dict)
            or set(source) - {"type", "id"}
            or source.get("type") != ("project" if kind == "assess" else "monitor")
        ):
            raise AuditError(
                "assessment requires project input; observation requires monitor input"
            )
        limits = payload.get("limits", {})
        if not isinstance(limits, dict) or set(limits) - {
            "max_trials",
            "max_elapsed_seconds",
            "trial_timeout_seconds",
        }:
            raise AuditError("unsupported task limits")
        options = copy.deepcopy(payload.get("options", {}))
        if not isinstance(options, dict):
            raise AuditError("task options must be an object")
        agent_id = payload.get("agent_id")
        if agent_id:
            selected_agent = self.app.catalog.agent(project, agent_id)
        else:
            selected_agent = None
        if payload.get("scope"):
            raise AuditError("assessment and observation use their saved evidence scope")
        with self.app.lock:
            if kind == "assess":
                if source.get("id", project) != project or set(options) - {
                    "assessment",
                    "agent_review_scope",
                }:
                    raise AuditError("invalid project assessment input")
                review_scope = options.get("agent_review_scope")
                if selected_agent and selected_agent["status"] == "archived":
                    raise AuditError("restore this excluded identity before starting work")
                if selected_agent and selected_agent["status"] == "suggested":
                    from agentagon.workflows.operations.preparation import agent_review_scope

                    if review_scope != agent_review_scope(selected_agent):
                        raise AuditError("suggested identity changed; review it again")
                elif review_scope:
                    raise AuditError("read-only review scope requires a suggested identity")
                options = {
                    "assessment": self.scope(
                        project, options.get("assessment", self.onboarding(project)["scope"])
                    ),
                    **({"agent_review_scope": review_scope} if review_scope else {}),
                }
                objective = (
                    f"Infer responsibility for {selected_agent['name']}"
                    if review_scope
                    else "Assess application agents, issues and improvement opportunities"
                )
            else:
                monitor = self.app.monitoring.get(project, source.get("id"))
                if agent_id not in (None, monitor["agent_id"]):
                    raise AuditError("monitor belongs to another agent")
                agent_id = monitor["agent_id"]
                if monitor.get("task_id"):
                    current = self.app.runtime.get(project, monitor["task_id"])
                    if current["state"] not in {
                        "completed",
                        "completed_with_limits",
                        "failed",
                        "cancelled",
                    }:
                        raise AuditError("finish, resume or discard the pending observation first")
                if scheduled:
                    if options != (monitor.get("pending") or {}).get("options"):
                        raise AuditError("scheduled observation differs from its saved submission")
                else:
                    if options:
                        raise AuditError("observation uses its saved monitor policy")
                    end = now()
                    earliest = after(-monitor["catchup_days"] * 86400, end)
                    checkpoint = monitor.get("checkpoint") or earliest
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
                    options = {
                        "monitor_policy": policy,
                        "scheduled": False,
                        "window": {
                            "start": max(earliest, after(-300, checkpoint)),
                            "end": end,
                            "gap": checkpoint < earliest,
                        },
                    }
                objective = "Observe production outcomes and discover issues"
            options.update(limits)
            task = self.app.runtime.submit(
                project,
                {
                    "operation_id": payload["operation_id"],
                    "kind": kind,
                    "goal": objective,
                    "application_agent_id": agent_id,
                    "input": source,
                    "options": options,
                    "agent": payload.get("assistant"),
                    **({"model": payload["model"]} if "model" in payload else {}),
                },
                request_binding=binding,
            )
            if kind == "assess":
                if not options.get("agent_review_scope"):
                    self.db.put_record(
                        project,
                        "onboarding",
                        "setup",
                        {
                            **self.onboarding(project),
                            "state": "analyzing",
                            "scope": options["assessment"],
                            "task_id": task["id"],
                        },
                    )
            else:
                monitor.update(task_id=task["id"], state="running")
                self.db.put_record(project, "monitors", monitor["id"], monitor)
            return public_start(project, task)
