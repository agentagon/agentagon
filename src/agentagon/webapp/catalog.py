"""Confirmed application agents, versioned focuses and retained measurement suites."""

import ast
import copy
import math
import os
import re
import uuid
from pathlib import Path

from agentagon.core.records import AuditError, digest, now
from agentagon.experiments.spec import path as relative_path
from agentagon.webapp import snapshots
from agentagon.webapp.state import identifier

FOCUSES = {
    "correctness": "Task success and correctness",
    "reliability": "Reliability and tool use",
    "grounding": "Grounding and factuality",
    "safety": "Safety and policy",
    "security": "Security and permissions",
    "latency": "Latency",
    "cost": "Cost and efficiency",
    "interaction": "Interaction and instruction following",
    "custom": "Custom objective",
}
EXCLUDED = {".git", ".agentagon", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
AGENT_CALLS = {
    "Agent",
    "create_agent",
    "create_react_agent",
    "StateGraph",
    "AgentExecutor",
    "Workflow",
}


def _id(prefix):
    return prefix + "_" + uuid.uuid4().hex[:24]


def _text(value, label, limit=4000, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise AuditError(f"{label} must be {'0' if empty else '1'}–{limit} characters")
    return value.strip()


def _paths(values, root):
    if not isinstance(values, list) or len(values) > 100:
        raise AuditError("code scopes must be a bounded list of relative paths")
    result = []
    for value in values:
        value = relative_path(value)
        if set(Path(value).parts) & EXCLUDED:
            raise AuditError("private state and dependencies cannot be application-agent scopes")
        target = root / value
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise AuditError("agent scope escapes this project")
        if not target.exists():
            raise AuditError(f"agent scope does not exist: {value}")
        result.append(value)
    return list(dict.fromkeys(result))


def _selector(value):
    if not isinstance(value, dict) or set(value) - {
        "connection_id",
        "project",
        "filters",
        "name",
        "environment",
    }:
        raise AuditError("invalid agent trace selector")
    if len(str(value)) > 8000:
        raise AuditError("agent trace selector is too large")
    for key in ("connection_id", "project", "name", "environment"):
        if key in value:
            _text(value[key], key, 500)
    filters = value.get("filters", {})
    if not isinstance(filters, dict) or len(filters) > 20:
        raise AuditError("trace filters must be a bounded object")
    if any(
        not isinstance(k, str) or not isinstance(v, (str, bool, int, float))
        for k, v in filters.items()
    ):
        raise AuditError("trace filters must contain scalar values")
    return copy.deepcopy(value)


class Catalog:
    def __init__(self, state):
        self.state = state

    def agents(self, project_id):
        self.state.project(project_id)
        return sorted(
            self.state.db.list_records(project_id, "application_agents"),
            key=lambda a: (a["status"] != "confirmed", a["name"].lower()),
        )

    def agent(self, project_id, agent_id):
        self.state.project(project_id)
        identifier(agent_id, "agent")
        result = self.state.db.get_record(project_id, "application_agents", agent_id)
        if result is None:
            raise AuditError("application agent not found in this project")
        return result

    def save_agent(self, project_id, payload, agent_id=None, *, suggestion=None):
        root = self.state.workspace(project_id).root
        if set(payload) - {
            "name",
            "description",
            "code_scopes",
            "shared_dependencies",
            "trace_selector",
            "status",
            "expected_revision",
        }:
            raise AuditError("unsupported application-agent fields")
        previous = self.agent(project_id, agent_id) if agent_id else {}
        record = {**previous, "id": agent_id or _id("agent"), "project_id": project_id}
        record["name"] = _text(payload.get("name", previous.get("name")), "agent name", 160)
        record["description"] = _text(
            payload.get("description", previous.get("description", "")), "description", empty=True
        )
        record["code_scopes"] = _paths(
            payload.get("code_scopes", previous.get("code_scopes", [])), root
        )
        record["shared_dependencies"] = _paths(
            payload.get("shared_dependencies", previous.get("shared_dependencies", [])), root
        )
        record["trace_selector"] = _selector(
            payload.get("trace_selector", previous.get("trace_selector", {}))
        )
        if not record["code_scopes"] and not record["trace_selector"]:
            raise AuditError("bind the agent to code or a trace selector")
        record["status"] = payload.get("status", previous.get("status", "confirmed"))
        if record["status"] not in {"suggested", "confirmed", "archived"}:
            raise AuditError("unsupported application-agent status")
        if record["status"] == "confirmed" and not record["code_scopes"]:
            record["limitations"] = ["Trace-only agent: bind code before starting a measured fix."]
        else:
            record["limitations"] = []
        if suggestion:
            record.update(suggestion)
        binding = {k: record[k] for k in ("code_scopes", "shared_dependencies", "trace_selector")}
        changed = digest(binding) != previous.get("binding_digest")
        record.update(
            binding_digest=digest(binding),
            binding_version=previous.get("binding_version", 0) + int(changed),
            created_at=previous.get("created_at", now()),
            updated_at=now(),
        )
        with self.state.db.transaction() as tx:
            saved = tx.put_record(
                project_id,
                "application_agents",
                record["id"],
                record,
                expected_revision=payload.get("expected_revision", previous.get("revision", 0)),
            )
        return saved

    def discover(self, project_id):
        workspace = self.state.workspace(project_id)
        existing = {a.get("discovery_key"): a for a in self.agents(project_id)}
        found = []
        scanned, limited = 0, False
        for directory, dirs, files in os.walk(workspace.root, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in EXCLUDED
                and not d.startswith(".")
                and not (Path(directory) / d).is_symlink()
            )
            for filename in sorted(files):
                file = Path(directory) / filename
                if file.suffix not in {".py", ".js", ".ts", ".tsx", ".jsx"} or file.is_symlink():
                    continue
                scanned += 1
                if scanned > 1500:
                    limited = True
                    break
                if file.stat().st_size > 500_000:
                    continue
                source = file.read_text(errors="replace")
                relative = file.relative_to(workspace.root).as_posix()
                candidates = []
                if file.suffix == ".py":
                    try:
                        tree = ast.parse(source)
                    except (SyntaxError, ValueError):
                        continue
                    for node in ast.walk(tree):
                        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(
                            node.value, ast.Call
                        ):
                            continue
                        func = node.value.func
                        call = (
                            func.id
                            if isinstance(func, ast.Name)
                            else func.attr
                            if isinstance(func, ast.Attribute)
                            else ""
                        )
                        if call not in AGENT_CALLS:
                            continue
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        symbol = next((v.id for v in targets if isinstance(v, ast.Name)), "agent")
                        name = next(
                            (
                                k.value.value
                                for k in node.value.keywords
                                if k.arg == "name"
                                and isinstance(k.value, ast.Constant)
                                and isinstance(k.value.value, str)
                            ),
                            symbol,
                        )
                        candidates.append((name[:160], symbol, node.lineno, call))
                else:
                    pattern = r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:new\s+)?(?:\w+\.)?(Agent|createAgent|createReactAgent|StateGraph|Workflow)\s*\("
                    for match in re.finditer(pattern, source):
                        candidates.append(
                            (match[1], match[1], source[: match.start()].count("\n") + 1, match[2])
                        )
                for name, symbol, line, call in candidates:
                    key = digest({"path": relative, "symbol": symbol})
                    if key in existing:
                        found.append(existing[key])
                        continue
                    candidate = self.save_agent(
                        project_id,
                        {
                            "name": name,
                            "description": f"Discovered {call} entrypoint in {relative}. Confirm its code and trace boundaries.",
                            "code_scopes": [relative],
                            "status": "suggested",
                        },
                        suggestion={
                            "discovery_key": key,
                            "confidence": "suggested",
                            "evidence": [
                                {"kind": "code", "path": relative, "line": line, "symbol": symbol}
                            ],
                        },
                    )
                    existing[key] = candidate
                    found.append(candidate)
            if limited:
                break
        for summary in snapshots.list_snapshots(workspace):
            if summary["kind"] != "traces":
                continue
            record = snapshots.load(workspace, summary["id"])
            for item in record["items"]:
                metadata = item.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                name = metadata.get("agent_name") or item.get("agent_name")
                if not isinstance(name, str) or not name.strip():
                    continue
                selector = {
                    "connection_id": record["connection_id"],
                    "name": name[:160],
                    "filters": {"agent_name": name[:160]},
                }
                provider_project = record["selection"].get("project")
                if provider_project:
                    selector["project"] = provider_project
                key = digest(selector)
                if key not in existing:
                    existing[key] = self.save_agent(
                        project_id,
                        {
                            "name": name[:160],
                            "description": "Discovered in imported traces. Confirm its code binding and trace selector.",
                            "trace_selector": selector,
                            "status": "suggested",
                        },
                        suggestion={
                            "discovery_key": key,
                            "confidence": "suggested",
                            "evidence": [{"kind": "traces", "snapshot_id": record["id"]}],
                        },
                    )
                    found.append(existing[key])
        return {
            "agents": self.agents(project_id),
            "discovered": len({a["id"] for a in found}),
            "scanned_files": min(scanned, 1500),
            "limitations": [
                "Discovery is a bounded code and imported-trace scan; confirm suggestions or add an agent manually.",
                *(
                    [
                        "File scan cap reached; narrow the repository or add remaining agents manually."
                    ]
                    if limited
                    else []
                ),
            ],
        }

    def focuses(self, project_id, agent_id):
        self.agent(project_id, agent_id)
        return sorted(
            (
                f
                for f in self.state.db.list_records(project_id, "focuses")
                if f["agent_id"] == agent_id
            ),
            key=lambda f: f["created_at"],
            reverse=True,
        )

    def focus(self, project_id, agent_id, focus_id):
        self.agent(project_id, agent_id)
        identifier(focus_id, "focus")
        result = self.state.db.get_record(project_id, "focuses", focus_id)
        if not result or result["agent_id"] != agent_id:
            raise AuditError("focus not found for this application agent")
        return result

    def save_focus(self, project_id, agent_id, payload):
        agent = self.agent(project_id, agent_id)
        if agent["status"] != "confirmed":
            raise AuditError("confirm the application agent before setting a focus")
        if set(payload) - {"name", "category", "goal", "target", "source"}:
            raise AuditError("unsupported focus fields")
        category = payload.get("category", "custom")
        if category not in FOCUSES:
            raise AuditError("choose a supported focus category")
        goal = _text(payload.get("goal"), "focus goal")
        source = copy.deepcopy(payload.get("source", {"kind": "goal"}))
        if (
            not isinstance(source, dict)
            or set(source) - {"kind", "count", "trace_snapshot_id", "issue_id", "audit_id"}
            or source.get("kind") not in {"goal", "issue", "recent_traces"}
        ):
            raise AuditError("unsupported focus source")
        if source["kind"] == "recent_traces":
            count = source.get("count", 100)
            if type(count) is not int or not 1 <= count <= 10000:
                raise AuditError("trace count must be between 1 and 10000")
            source["count"] = count
            if source.get("trace_snapshot_id"):
                snapshot = snapshots.load(
                    self.state.workspace(project_id), source["trace_snapshot_id"]
                )
                if snapshot["kind"] != "traces":
                    raise AuditError("select a trace snapshot")
        if source["kind"] == "issue":
            if not source.get("issue_id"):
                raise AuditError("select the saved issue for this focus")
            self.validate_evidence_reference(project_id, agent_id, source)
        target = payload.get("target")
        if target is not None:
            target = _text(target, "focus target", 1000, empty=True)
        record = {
            "id": _id("focus"),
            "project_id": project_id,
            "agent_id": agent_id,
            "name": _text(payload.get("name") or FOCUSES[category], "focus name", 160),
            "category": category,
            "goal": goal,
            "target": target,
            "source": source,
            "state": "active",
            "version": 1,
            "measurement": None,
            "guardrails": [],
            "created_at": now(),
        }
        with self.state.db.transaction() as tx:
            saved = tx.put_record(project_id, "focuses", record["id"], record)
            version = {
                "id": _id("focusversion"),
                "focus_id": record["id"],
                "definition": copy.deepcopy(saved),
                "created_at": now(),
            }
            tx.put_record(project_id, "focus_versions", version["id"], version)
        return saved

    def validate_evidence_reference(self, project_id, agent_id, source):
        from agentagon.storage.issues import list_issues

        workspace = self.state.workspace(project_id)
        audit_ids = set()
        if source.get("audit_id"):
            audit_ids.add(workspace.read_audit(source["audit_id"])["audit_id"])
        if source.get("issue_id"):
            identifier(source["issue_id"], "issue")
            issue = next(
                (i for i in list_issues(workspace) if i["issue_id"] == source["issue_id"]), None
            )
            if issue is None:
                raise AuditError("issue not found in this project")
            issue_audits = set(issue["audit_ids"])
            if source.get("audit_id") and source["audit_id"] not in issue_audits:
                raise AuditError("selected issue does not belong to that audit")
            if not source.get("audit_id"):
                audit_ids.update(issue_audits)
        owners = {
            j["application_agent_id"]
            for j in self.state.db.list_records(project_id, "jobs")
            if j.get("application_agent_id")
            and (
                j.get("workflow_ids", {}).get("audit_id") in audit_ids
                or j.get("result", {}).get("audit_id") in audit_ids
            )
        }
        if owners and agent_id not in owners:
            raise AuditError("selected evidence belongs to a different application agent")

    def bind_measurement(
        self, project_id, agent_id, focus_id, payload, *, expected_binding_digest=None
    ):
        from agentagon.experiments import preparation
        from agentagon.webapp.comparisons import _completed

        if set(payload) - {
            "evaluation_id",
            "baseline_id",
            "primary_metric",
            "guardrails",
            "expected_revision",
        }:
            raise AuditError("unsupported measurement fields")
        focus = self.focus(project_id, agent_id, focus_id)
        agent = self.agent(project_id, agent_id)
        if (
            expected_binding_digest is not None
            and agent["binding_digest"] != expected_binding_digest
        ):
            raise AuditError("agent binding changed; review the measurement before attaching it")
        workspace = self.state.workspace(project_id)
        evaluation_id = payload.get("evaluation_id") or (focus.get("measurement") or {}).get(
            "evaluation_id"
        )
        identifier(evaluation_id, "eval")
        from agentagon.webapp.datasets import assert_development_evaluator

        assert_development_evaluator(workspace, evaluation_id)
        evaluation = preparation.load(workspace, evaluation_id)
        evaluator_digest = preparation.evaluator_identity(workspace, evaluation_id)
        spec = evaluation["package"]["spec"]
        if not spec.get("scoring"):
            raise AuditError("evaluation requires accepted scoring before binding a measurement")
        previous = focus.get("measurement") or {}
        primary = (
            payload.get("primary_metric")
            or previous.get("primary_metric")
            or spec["scoring"].get("primary")
            or spec["scoring"].get("custom_metric")
            or next(iter(spec["metrics"]))
        )
        if primary not in spec["metrics"]:
            raise AuditError("primary metric must be an output of the frozen evaluator")
        scoring_mode = spec["scoring"]["mode"]
        ranked_metric = spec["scoring"].get(
            "primary" if scoring_mode == "primary" else "custom_metric"
        )
        if scoring_mode != "weighted" and primary != ranked_metric:
            raise AuditError(
                "focus metric must match the evaluator's accepted scoring objective; prepare a new evaluator to change it"
            )
        guards = payload.get("guardrails")
        if guards is None:
            guards = (
                previous.get("guardrails")
                if previous.get("evaluator_digest") == evaluator_digest
                else None
            )
        if guards is None:
            guards = [
                {
                    "metric": primary,
                    "op": "gte" if spec["metrics"][primary]["direction"] == "max" else "lte",
                    "bound": 0,
                    "reference": "baseline_delta",
                }
            ]
        if not isinstance(guards, list) or not guards or len(guards) > 100:
            raise AuditError("measurement needs at least one bounded regression guardrail")
        for guard in guards:
            if (
                not isinstance(guard, dict)
                or set(guard) != {"metric", "op", "bound", "reference"}
                or guard["metric"] not in spec["metrics"]
                or guard["op"] not in {"gte", "lte"}
                or guard["reference"] not in {"absolute", "baseline_delta", "baseline_ratio"}
            ):
                raise AuditError("invalid measurement guardrail")
            if type(guard["bound"]) not in (int, float) or not math.isfinite(guard["bound"]):
                raise AuditError("guardrail bounds must be finite numbers")
        baseline_id = payload.get("baseline_id")
        if "baseline_id" not in payload and previous.get("evaluator_digest") == evaluator_digest:
            baseline_id = previous.get("baseline_id")
        if baseline_id:
            baseline, _ = _completed(workspace, baseline_id)
            if (
                baseline["evaluation_id"] != evaluation_id
                or baseline["evaluator_digest"] != evaluator_digest
            ):
                raise AuditError("baseline must measure this exact frozen evaluation")
        focus["measurement"] = {
            "agent_binding_digest": agent["binding_digest"],
            "evaluation_id": evaluation_id,
            "evaluator_digest": evaluator_digest,
            "baseline_id": baseline_id,
            "primary_metric": primary,
            "scoring_mode": scoring_mode,
            "metrics": spec["metrics"],
            "guardrails": copy.deepcopy(guards),
            "profile_name": evaluation["profile_name"],
        }
        focus["guardrails"] = copy.deepcopy(guards)
        focus["version"] += 1
        focus["updated_at"] = now()
        with self.state.db.transaction() as tx:
            current_agent = tx.get_record(project_id, "application_agents", agent_id)
            if current_agent["binding_digest"] != agent["binding_digest"]:
                raise AuditError(
                    "agent binding changed; review the measurement before attaching it"
                )
            saved = tx.put_record(
                project_id,
                "focuses",
                focus_id,
                focus,
                expected_revision=payload.get("expected_revision", focus["revision"]),
            )
            version = {
                "id": _id("focusversion"),
                "focus_id": focus_id,
                "definition": copy.deepcopy(saved),
                "created_at": now(),
            }
            tx.put_record(project_id, "focus_versions", version["id"], version)
        return saved

    def measurement_status(self, project_id, agent_id, focus):
        """Revalidate evidence; a saved identifier alone is not measurement readiness."""
        from agentagon.experiments import preparation
        from agentagon.webapp.comparisons import _completed

        measurement = focus.get("measurement")
        evaluation = {
            "ready": False,
            "reason": "Create or select a reviewed evaluation for this focus.",
        }
        baseline = {"ready": False, "reason": "Run a baseline after evaluation preparation."}
        if not measurement:
            return {"evaluation": evaluation, "baseline": baseline}
        workspace = self.state.workspace(project_id)
        try:
            if (
                measurement.get("agent_binding_digest")
                != self.agent(project_id, agent_id)["binding_digest"]
            ):
                raise AuditError(
                    "Agent scope changed. Review and bind this measurement to the current agent."
                )
            identity = preparation.evaluator_identity(workspace, measurement["evaluation_id"])
            if identity != measurement["evaluator_digest"]:
                raise AuditError("Frozen evaluator identity changed. Prepare a new evaluation.")
            evaluation = {"ready": True, "reason": "Frozen evaluation available."}
            if measurement.get("baseline_id"):
                record, _ = _completed(workspace, measurement["baseline_id"])
                if (
                    record["evaluation_id"] != measurement["evaluation_id"]
                    or record["evaluator_digest"] != identity
                ):
                    raise AuditError("Baseline does not measure this frozen evaluator.")
                baseline = {"ready": True, "reason": "Verified baseline available."}
        except (AuditError, OSError, KeyError, ValueError) as exc:
            reason = f"Measurement evidence unavailable: {exc}"
            if not evaluation["ready"]:
                evaluation["reason"] = reason
            baseline["reason"] = reason
        return {"evaluation": evaluation, "baseline": baseline}

    def suite(self, project_id, agent_id, focus_id, *, permitted_paths=None):
        from agentagon.experiments.checkouts import under

        active = self.focus(project_id, agent_id, focus_id)
        agent = self.agent(project_id, agent_id)
        permitted_paths = permitted_paths or agent["code_scopes"]
        affected = [
            a
            for a in self.agents(project_id)
            if a["status"] == "confirmed"
            and (
                a["id"] == agent_id
                or any(
                    under(path, scope) or under(scope, path)
                    for path in permitted_paths
                    for scope in a["code_scopes"] + a["shared_dependencies"]
                )
            )
        ]
        members, missing = [], []
        for affected_agent in affected:
            focuses = [
                f for f in self.focuses(project_id, affected_agent["id"]) if f["state"] == "active"
            ]
            if not focuses:
                missing.append(
                    {
                        "application_agent_id": affected_agent["id"],
                        "name": affected_agent["name"],
                        "reason": "Changes affect this agent. Add a focus and establish its baseline, or narrow permitted changes.",
                    }
                )
            for focus in focuses:
                readiness = self.measurement_status(project_id, affected_agent["id"], focus)
                if not readiness["baseline"]["ready"]:
                    missing.append(
                        {
                            "application_agent_id": affected_agent["id"],
                            "focus_id": focus["id"],
                            "name": focus["name"],
                            "reason": readiness["baseline"]["reason"],
                        }
                    )
                    continue
                members.append(
                    {
                        "application_agent_id": affected_agent["id"],
                        "focus_id": focus["id"],
                        "focus_version": focus["version"],
                        "name": focus["name"],
                        **copy.deepcopy(focus["measurement"]),
                    }
                )
        manifest = {
            "version": 1,
            "agent_id": agent_id,
            "focus_id": active["id"],
            "affected_agents": [
                {"agent_id": a["id"], "binding_digest": a["binding_digest"]} for a in affected
            ],
            "members": members,
        }
        return {**manifest, "digest": digest(manifest), "missing": missing}

    def readiness(self, project_id, agent_id):
        agent = self.agent(project_id, agent_id)
        focuses = [f for f in self.focuses(project_id, agent_id) if f["state"] == "active"]
        statuses = [self.measurement_status(project_id, agent_id, f) for f in focuses]
        accepted = [s for s in statuses if s["evaluation"]["ready"]]
        baselined = [s for s in statuses if s["baseline"]["ready"]]
        code = agent["status"] == "confirmed" and bool(agent["code_scopes"])
        ready = (
            code
            and bool(focuses)
            and len(baselined) == len(focuses)
            and not self.suite(project_id, agent_id, focuses[0]["id"])["missing"]
        )
        return {
            "evaluation": {
                "ready": bool(accepted),
                "reason": "Frozen evaluation available."
                if accepted
                else "Create or select a reviewed evaluation for this focus.",
            },
            "baseline": {
                "ready": bool(baselined),
                "reason": "Verified baseline available."
                if baselined
                else "Run a baseline after evaluation preparation.",
            },
            "fix": {
                "ready": ready,
                "reason": "All active focuses have evaluations and baselines."
                if ready
                else "Confirm code scope and establish a baseline for every active focus.",
            },
        }

    def investigation(self, project_id, agent_id, focus_id, options):
        agent = self.agent(project_id, agent_id)
        focus = self.focus(project_id, agent_id, focus_id)
        snapshot_id = options.get("trace_snapshot_id") or focus["source"].get("trace_snapshot_id")
        trace = None
        if snapshot_id:
            trace = snapshots.load(self.state.workspace(project_id), snapshot_id)
            if trace["kind"] != "traces":
                raise AuditError("audit evidence must be a trace snapshot")
            if agent["trace_selector"] or focus["source"].get("count"):
                trace = snapshots.select_traces(
                    self.state.workspace(project_id),
                    project_id,
                    snapshot_id,
                    agent["trace_selector"],
                    focus["source"].get("count"),
                )
                snapshot_id = trace["id"]
        if focus["source"]["kind"] == "recent_traces" and trace is None:
            raise AuditError("Import and select traces before reviewing recent failures.")
        plan = {
            "version": 1,
            "agent_id": agent_id,
            "binding_version": agent["binding_version"],
            "binding_digest": agent["binding_digest"],
            "focus_id": focus_id,
            "focus_version": focus["version"],
            "goal": focus["goal"],
            "source": copy.deepcopy(focus["source"]),
            "code_scopes": agent["code_scopes"],
            "trace_selector": agent["trace_selector"],
            "trace_snapshot_id": snapshot_id,
            "trace_digest": trace["digest"] if trace else None,
            "trace_cap": focus["source"].get("count"),
            "rubric": "audit-v1",
            "limits": [
                "Investigation covers the selected agent and evidence; unrelated issues are not resolved by omission."
            ],
        }
        return {**plan, "digest": digest(plan)}

    def metrics(self, project_id, agent_id):
        from agentagon.webapp.comparisons import _completed

        focuses = self.focuses(project_id, agent_id)
        focus_ids = {f["id"] for f in focuses}
        versions = [
            r["definition"]
            for r in self.state.db.list_records(project_id, "focus_versions")
            if r["focus_id"] in focus_ids
        ]
        workspace = self.state.workspace(project_id)
        series, seen, limits = {}, set(), []

        def rows(focus_id, focus_name, measurement, metrics, run):
            execution = (
                digest(
                    {
                        "profile": run["profile"],
                        "limits": run["limits"],
                        "scoring": run["spec"].get("scoring"),
                    }
                )
                if run
                else None
            )
            for name, definition in metrics.items():
                key = f"{focus_id}:{measurement['evaluator_digest']}:{execution or 'unmeasured'}:{name}"
                yield (
                    name,
                    series.setdefault(
                        key,
                        {
                            "id": key,
                            "name": name,
                            "unit": definition["unit"],
                            "direction": definition["direction"],
                            "focus_id": focus_id,
                            "focus_name": focus_name,
                            "evaluator_id": measurement["evaluation_id"],
                            "evaluator_digest": measurement["evaluator_digest"],
                            "execution_digest": execution,
                            "profile_name": run["profile_name"] if run else None,
                            "measurements": [],
                        },
                    ),
                )

        for focus in versions:
            measurement = focus.get("measurement")
            if not measurement:
                continue
            baseline_id = measurement.get("baseline_id")
            if baseline_id and (focus["id"], baseline_id) in seen:
                continue
            baseline, run = None, None
            if baseline_id:
                seen.add((focus["id"], baseline_id))
                try:
                    baseline, run = _completed(workspace, baseline_id)
                except (AuditError, OSError) as exc:
                    limits.append(f"Baseline {baseline_id} is unavailable: {exc}")
            for name, row in rows(
                focus["id"], focus["name"], measurement, measurement["metrics"], run
            ):
                if baseline is None:
                    continue
                value = baseline["measurement"]["metrics"].get(name)
                row["measurements"].append(
                    {
                        "value": value,
                        "created_at": baseline["created_at"],
                        "source_revision": baseline["source_revision"],
                        "baseline_id": baseline_id,
                        "state": "measured" if type(value) in (int, float) else "missing",
                    }
                )
        # Include retained optimization outcomes without promoting them to reference baselines.
        from agentagon.experiments import suites
        from agentagon.experiments.store import load_run

        run_ids = {
            j.get("workflow_ids", {}).get("run_id") or j.get("result", {}).get("run_id")
            for j in self.state.db.list_records(project_id, "jobs")
            if j["kind"] == "fix"
            and any(
                member["focus_id"] in focus_ids
                for member in j["options"].get("suite_manifest", {}).get("members", [])
            )
        } - {None}
        for run_id in sorted(run_ids):
            try:
                state = suites.status(workspace, run_id)
                if not state.get("result"):
                    continue
                outcome = suites.verify_outcome(
                    workspace,
                    run_id,
                    {
                        **state["manifest"],
                        "digest": state["manifest_digest"],
                        "missing": [],
                    },
                )
                for member in outcome.get("members", []):
                    if member["focus_id"] not in focus_ids:
                        continue
                    child = load_run(workspace, member["executions"]["finalist"])
                    for name, row in rows(
                        member["focus_id"], member["name"], member, child["spec"]["metrics"], child
                    ):
                        row["measurements"].append(
                            {
                                "value": member["finalist"].get(name),
                                "created_at": state.get("completed_at", state["created_at"]),
                                "source_revision": state["binding"]["source_revision"],
                                "run_id": run_id,
                                "state": "verified" if outcome["passed"] else "guardrail_failed",
                            }
                        )
            except (AuditError, OSError, KeyError) as exc:
                limits.append(f"Fix measurement {run_id} is unavailable: {exc}")
        guards = []
        for focus in focuses:
            measurement = focus.get("measurement")
            if not measurement:
                continue
            for index, guard in enumerate(measurement["guardrails"]):
                guards.append(
                    {
                        "id": f"{focus['id']}:{index}",
                        "name": f"{focus['name']}: {guard['metric']}",
                        "focus_id": focus["id"],
                        "state": "active"
                        if self.measurement_status(project_id, agent_id, focus)["baseline"]["ready"]
                        else "needs_baseline",
                        "threshold": guard["bound"],
                        **guard,
                    }
                )
        return {
            "metrics": list(series.values()),
            "guardrails": guards,
            "limitations": list(dict.fromkeys(limits)),
        }
