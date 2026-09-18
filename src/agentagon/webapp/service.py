"""Application operations shared by the browser and local launcher."""

import copy
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentagon.core.records import AuditError, digest, load_json, now
from agentagon.dashboard import _audits, _detail, _run_detail, _run_summary, _runs, _summary
from agentagon.storage.changes import git_bytes
from agentagon.storage.config import Config, credential
from agentagon.storage.issues import list_issues
from agentagon.telemetry.normalize import redact
from agentagon.webapp import snapshots
from agentagon.webapp.agents import detect_agents
from agentagon.webapp.catalog import Catalog
from agentagon.webapp.designs import Designs
from agentagon.webapp.jobs import ACTIVE, JobManager, operation_id, public_job
from agentagon.webapp.providers import DEFAULT_ENDPOINTS, CredentialStore, ProviderClient
from agentagon.webapp.state import AppState, identifier, private_directory

DISCOVERY_TTL_SECONDS = 600
MAX_CONNECTION_DISCOVERIES = 12


class Application:
    def __init__(self, directory=None, *, execute=None, credentials=None, provider_factory=None):
        self.state = AppState(directory)
        self.credentials = credentials or CredentialStore()
        self.provider_factory = provider_factory or ProviderClient
        self.jobs = JobManager(self.state, self.credentials, execute)
        self.catalog = Catalog(self.state)
        self.designs = Designs(self.state, self.catalog)
        self.jobs.verify_result = self.completed_job
        self.previews = {}
        self.connection_discoveries = {}
        self._intelligence_refs = {}
        self.lock = threading.RLock()
        self.selected_project_id = None

    def project_info(self, project):
        result = {**project, "active_jobs": 0, "branch": None, "available": True}
        try:
            workspace = self.state.workspace(project["id"])
            branch = git_bytes(workspace.root, "branch", "--show-current", optional=True)
            result["branch"] = branch.decode().strip() if branch else None
            result["active_jobs"] = sum(j["state"] in ACTIVE for j in self.jobs.list(project["id"]))
        except (AuditError, OSError):
            result.update(
                available=False,
                next_action="Project directory unavailable. Register its current location.",
            )
        return result

    def projects(self):
        return {
            "projects": [self.project_info(p) for p in self.state.read()["projects"].values()],
            "selected_project_id": self.selected_project_id,
        }

    def skills(self):
        from agentagon.webapp.resources import skill_definitions

        return {"skills": skill_definitions()}

    def connector_types(self):
        from agentagon.webapp.resources import connector_types

        return {"connector_types": connector_types()}

    def assistants(self):
        detected = self.agents()
        return {
            "assistants": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "available": item.get("available", False),
                    "authenticated": item.get("authenticated"),
                    "version": item.get("version"),
                    "message": item.get("message"),
                }
                for item in detected["agents"]
            ],
            "defaults": detected["settings"],
        }

    def project_agents(self, project_id):
        from agentagon.webapp.resources import agent_projection

        agents = [agent_projection(item) for item in self.catalog.agents(project_id)]
        return {
            "agents": agents,
            "confirmed": [item for item in agents if item["status"] == "confirmed"],
            "suggestions": [item for item in agents if item["status"] != "confirmed"],
        }

    def project_agent(self, project_id, agent_id):
        from agentagon.webapp.resources import agent_projection

        return agent_projection(self.catalog.agent(project_id, agent_id))

    def goals(self, project_id, agent_id):
        from agentagon.webapp.resources import goal_projection

        return {
            "goals": [goal_projection(item) for item in self.catalog.focuses(project_id, agent_id)]
        }

    def goal(self, project_id, agent_id, goal_id):
        from agentagon.webapp.resources import goal_projection

        focus = self.catalog.focus(project_id, agent_id, goal_id)
        result = goal_projection(focus)
        result["readiness"] = self.catalog.measurement_status(project_id, agent_id, focus)
        design = self.designs.get(project_id, agent_id, goal_id)
        result["measurement_plan"] = design
        return result

    def save_goal(self, project_id, agent_id, payload):
        objective = payload.get("objective")
        ideal_behavior = payload.get("ideal_behavior")
        translated = {
            "category": payload.get("category", "custom"),
            "goal": objective or ideal_behavior,
            "target": ideal_behavior if objective else None,
            "source": payload.get("source", {"kind": "goal"}),
        }
        if translated["category"] == "custom":
            translated["name"] = payload.get("name") or objective or ideal_behavior
        return self.goal_projection(self.catalog.save_focus(project_id, agent_id, translated))

    @staticmethod
    def goal_projection(focus):
        from agentagon.webapp.resources import goal_projection

        return goal_projection(focus)

    def tasks(self, project_id, filters=None):
        from agentagon.webapp.resources import goal_projection, task_summary

        filters = filters or {}
        agents = {item["id"]: item for item in self.catalog.agents(project_id)}
        goals = {
            item["id"]: goal_projection(item)
            for agent in agents.values()
            for item in self.catalog.focuses(project_id, agent["id"])
        }
        records = sorted(
            self.jobs.list(project_id),
            key=lambda item: item.get("updated_at") or item.get("created_at", ""),
            reverse=True,
        )
        for field, key in (
            ("agent_id", "application_agent_id"),
            ("goal_id", "focus_id"),
            ("workflow", "kind"),
            ("status", "state"),
        ):
            if filters.get(field):
                records = [item for item in records if item.get(key) == filters[field]]
        try:
            offset = max(0, int(filters.get("cursor", 0)))
            limit = min(100, max(1, int(filters.get("limit", 30))))
        except (TypeError, ValueError) as exc:
            raise AuditError("task pagination must use integer cursor and limit") from exc
        page = records[offset : offset + limit]
        next_cursor = offset + limit if offset + limit < len(records) else None
        return {
            "tasks": [task_summary(item, agents, goals) for item in page],
            "next_cursor": str(next_cursor) if next_cursor is not None else None,
        }

    def task(self, project_id, task_id):
        from agentagon.webapp.resources import goal_projection, task_detail

        agents = {item["id"]: item for item in self.catalog.agents(project_id)}
        goals = {
            item["id"]: goal_projection(item)
            for agent in agents.values()
            for item in self.catalog.focuses(project_id, agent["id"])
        }
        return task_detail(self.jobs.get(project_id, task_id), agents, goals)

    def workflow_readiness(self, project_id, workflow, agent_id=None, goal_id=None):
        from agentagon.webapp.workflows import REGISTRY

        definition = REGISTRY.get(workflow)
        if definition is None:
            raise AuditError("workflow not found")
        blockers = []
        agent = None
        if not agent_id:
            blockers.append({"code": "agent", "message": "Select an agent.", "action": "agent"})
        else:
            agent = self.catalog.agent(project_id, agent_id)
            if agent["status"] != "confirmed":
                blockers.append(
                    {"code": "agent", "message": "Confirm this agent.", "action": "agent"}
                )
        focus = None
        if definition["requires_goal"]:
            if not goal_id:
                blockers.append({"code": "goal", "message": "Select a goal.", "action": "goal"})
            elif agent:
                focus = self.catalog.focus(project_id, agent_id, goal_id)
        if agent and workflow == "audit" and not agent["code_scopes"]:
            blockers.append(
                {
                    "code": "evidence",
                    "message": "Bind code or import matching traces.",
                    "action": "evidence",
                }
            )
        if agent and focus and workflow in {"eval", "baseline", "fix"}:
            status = self.catalog.measurement_status(project_id, agent_id, focus)
            requirement = (
                "evaluation"
                if workflow == "baseline"
                else "baseline"
                if workflow == "fix"
                else None
            )
            if workflow == "eval" and not self.designs.accepted(project_id, agent_id, goal_id):
                blockers.append(
                    {
                        "code": "plan",
                        "message": "Accept the measurement plan.",
                        "action": "define",
                    }
                )
            elif requirement and not status[requirement]["ready"]:
                blockers.append(
                    {
                        "code": requirement,
                        "message": status[requirement]["reason"],
                        "action": "measure",
                    }
                )
        return {
            "workflow": workflow,
            "workflow_version": definition["version"],
            "ready": not blockers,
            "blockers": blockers,
            "next_action": blockers[0]["action"] if blockers else "start",
        }

    def submit_task(self, project_id, payload):
        from agentagon.webapp.workflows import REGISTRY

        allowed = {
            "operation_id",
            "workflow",
            "agent_id",
            "goal_id",
            "assistant",
            "model",
            "options",
        }
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise AuditError("unsupported task fields")
        workflow = payload.get("workflow")
        definition = REGISTRY.get(workflow)
        if definition is None:
            raise AuditError("choose a workflow")
        readiness = self.workflow_readiness(
            project_id, workflow, payload.get("agent_id"), payload.get("goal_id")
        )
        if not readiness["ready"]:
            raise AuditError(readiness["blockers"][0]["message"])
        body = {
            "operation_id": payload.get("operation_id"),
            "kind": workflow,
            "workflow_version": definition["version"],
            "application_agent_id": payload.get("agent_id"),
            "focus_id": payload.get("goal_id"),
            "agent": payload.get("assistant"),
            "model": payload.get("model", ""),
            "options": payload.get("options", {}),
        }
        return self.submit_job(project_id, body)

    def register(self, path):
        project = self.state.register(path)
        self.selected_project_id = project["id"]
        return self.project_info(project)

    def clone_project(self, payload):
        if set(payload) - {"repository", "path"}:
            raise AuditError("provide a GitHub repository URL and local destination")
        repository = payload.get("repository", "")
        if not isinstance(repository, str) or not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", repository
        ):
            raise AuditError(
                "use an HTTPS GitHub repository URL without credentials or query parameters"
            )
        value = payload.get("path")
        if not isinstance(value, str) or not value.strip():
            raise AuditError("choose a new local directory for this repository")
        path = Path(value).expanduser().absolute()
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise AuditError("choose a new directory inside an existing local folder")
        if any(p.is_symlink() for p in path.parents):
            raise AuditError("clone destination must not contain symbolic links")
        try:
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=never",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "clone",
                    "--no-local",
                    "--",
                    repository,
                    str(path),
                ],
                capture_output=True,
                timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            raise AuditError(
                "Clone timed out. Inspect the destination before retrying; existing files are preserved."
            ) from exc
        if result.returncode:
            raise AuditError(
                "Unable to clone this repository. Check the URL and local Git authentication."
            )
        return self.register(str(path))

    def application_agents(self, project_id):
        return {"agents": self.catalog.agents(project_id)}

    def discover_application_agents(self, project_id, payload):
        if not isinstance(payload, dict) or set(payload) - {"preferences"}:
            raise AuditError("unsupported discovery fields")
        preferences = self.catalog.discovery_preferences(project_id)
        if "preferences" in payload:
            preferences = self.catalog.save_discovery_preferences(
                project_id, payload["preferences"]
            )
        elif not preferences["seen"]:
            preferences = self.catalog.save_discovery_preferences(project_id, {})
        result = self.catalog.discover(project_id)
        enrichment = {}
        if preferences["coding_review"]:
            try:
                enrichment["coding_review"] = self._review_discovered_agents(project_id)
            except AuditError as exc:
                result["limitations"].append(f"Coding-agent review unavailable: {exc}")
        if preferences["trace_metadata"]:
            try:
                enrichment["trace_metadata"] = self._match_recent_trace_metadata(
                    project_id, preferences
                )
            except AuditError as exc:
                result["limitations"].append(f"Trace metadata matching unavailable: {exc}")
        result.update(
            agents=self.catalog.agents(project_id),
            preferences=self.catalog.discovery_preferences(project_id),
            enrichment=enrichment,
        )
        return result

    def _review_discovered_agents(self, project_id):
        candidates = [
            {
                "id": agent["id"],
                "file": agent["code_scopes"][0],
                "name": agent["name"],
                "confirmed": agent["status"] == "confirmed",
            }
            for agent in self.catalog.agents(project_id)
            if len(agent.get("code_scopes", [])) == 1
            and (
                agent["status"] == "suggested"
                or (
                    agent["status"] == "confirmed"
                    and agent.get("discovery_key")
                    and not agent.get("description")
                )
            )
        ]
        if not candidates:
            return {"reviewed": 0, "kept": 0}
        if len(candidates) > 100:
            raise AuditError("more than 100 suggestions require manual review")
        settings = self.state.read()["agents"]
        selected = settings.get("default_agent", "codex")
        capability = next(
            (item for item in self.agents()["agents"] if item["id"] == selected), None
        )
        if (
            not capability
            or not capability.get("available")
            or capability.get("authenticated") is not True
        ):
            raise AuditError(f"{selected.title()} is not authenticated")
        prompt = (
            "Review the following deterministic application-agent suggestions in this repository. "
            "Work read-only. Inspect only the listed files and their nearby imports. Return JSON only, "
            "with exactly one item per input candidate and no new candidates: "
            '{"candidates":[{"id":"agent_id","file":"path","name":"Agent name",'
            '"responsibility":"One concise sentence describing what the agent does for users",'
            '"keep":true}]}. '
            "Keep a candidate only when the file defines an application agent entrypoint rather than a "
            "library helper, test, example, documentation, or dependency. Preserve each id and file string "
            "exactly. Describe the agent's responsibility in plain user-facing language, based only on "
            "the inspected code, without implementation details or unsupported claims. When `confirmed` "
            "is true, preserve its name, set keep to true, and only supply its missing responsibility. "
            "Do not include the input-only `confirmed` field in the response. "
            "Use a concise user-facing name. Candidates: "
            + json.dumps(candidates, ensure_ascii=False)
        )
        request = {
            "agent": selected,
            "model": settings.get("models", {}).get(selected, ""),
            "cwd": str(self.state.workspace(project_id).root),
            "prompt": prompt,
            "sandbox": "read-only",
            "timeout_seconds": 120,
            "response_mode": "raw-final",
        }
        executable = settings.get(selected + "_executable")
        if executable:
            request["executable"] = executable
        if selected == "claude":
            reference = settings.get("claude_api_key_ref") or (
                "env:ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else None
            )
            if not reference:
                raise AuditError("Claude credentials are unavailable")
            request["api_key"] = self.credentials.resolve(reference)
        outcome = self.jobs.execute(
            request,
            lambda _event: None,
            lambda _question: {"decision": "decline"},
            threading.Event(),
        )
        if not isinstance(outcome, dict) or outcome.get("state") != "completed":
            raise AuditError("the coding-agent review did not complete")
        text = outcome.get("raw_final_text")
        if not isinstance(text, str):
            raise AuditError("the coding-agent review returned no structured result")
        try:
            response = json.loads(text)
        except json.JSONDecodeError:
            raise AuditError("the coding-agent review returned invalid JSON") from None
        if not isinstance(response, dict) or set(response) != {"candidates"}:
            raise AuditError("the coding-agent review returned an invalid result")
        return self.catalog.apply_coding_review(project_id, response["candidates"])

    def _match_recent_trace_metadata(self, project_id, preferences):
        connection_id = preferences.get("trace_connection_id")
        connection = self.connection(connection_id, project_id) if connection_id else None
        if connection is None:
            available = sorted(
                (
                    item
                    for item in self.state.read()["connections"].values()
                    if item.get("project_id") == project_id
                    and item.get("status") == "connected"
                    and item.get("project")
                ),
                key=lambda item: item["id"],
            )
            connection = available[0] if available else None
        if (
            connection is None
            or connection.get("status") != "connected"
            or not connection.get("project")
        ):
            raise AuditError("choose a tested trace connection with a provider project")
        end = datetime.now(UTC)
        selection = {
            "project": connection["project"],
            "start": (end - timedelta(days=7)).isoformat(),
            "end": end.isoformat(),
            "cap": preferences["trace_cap"],
            "filters": {},
        }
        sample = self.provider_factory(connection, self.credentials).trace_metadata(selection)
        return self.catalog.apply_trace_metadata(project_id, connection, sample.get("items", []))

    def save_application_agent(self, project_id, payload, agent_id=None):
        selector = payload.get("trace_selector", {})
        if not isinstance(selector, dict):
            raise AuditError("agent trace selector must be an object")
        if selector.get("connection_id"):
            connection = self.connection(selector["connection_id"], project_id)
            self._selected_connection_project(connection, selector)
        with self.lock:
            return self.catalog.save_agent(project_id, payload, agent_id)

    def measurement_design(self, project_id, agent_id, focus_id):
        from agentagon.experiments import preparation
        from agentagon.webapp import evaluators

        focus = self.catalog.focus(project_id, agent_id, focus_id)
        workspace = self.state.workspace(project_id)
        inventory = evaluators.discover(workspace)
        overview = self.overview(project_id)
        frozen = []
        for item in overview["evaluations"]:
            if item["state"] == "frozen":
                record = preparation.load(workspace, item["evaluation_id"])
                frozen.append({**item, "scoring": record["package"]["spec"].get("scoring")})
        return {
            "focus": focus,
            "draft": self.designs.get(project_id, agent_id, focus_id),
            "evaluators": inventory["candidates"],
            "limitations": inventory["limitations"],
            "frozen_evaluators": frozen,
            "snapshots": overview["datasets"],
        }

    def agent_overview(self, project_id, agent_id, focus_id=None):
        agent = self.catalog.agent(project_id, agent_id)
        focuses = self.catalog.focuses(project_id, agent_id)
        if focus_id:
            focus = self.catalog.focus(project_id, agent_id, focus_id)
        else:
            focus = focuses[0] if focuses else None
        result = self.overview(project_id)
        jobs = [j for j in result["jobs"] if j.get("application_agent_id") == agent_id]
        identifiers = {
            value
            for j in jobs
            for key, value in {**j.get("workflow_ids", {}), **j.get("result", {})}.items()
            if key.endswith("_id") and isinstance(value, str)
        }
        result["jobs"] = jobs
        for name, key in (("audits", "audit_id"), ("runs", "run_id")):
            result[name] = [r for r in result[name] if r.get(key) in identifiers]
        audit_ids = {r["audit_id"] for r in result["audits"]}
        scoped_issues = []
        for issue in result["issues"]:
            matching_audits = [key for key in issue["audit_ids"] if key in audit_ids]
            if matching_audits:
                scoped_issues.append(
                    {**issue, "audit_ids": matching_audits, "latest_audit_id": matching_audits[-1]}
                )
        result["issues"] = scoped_issues
        history = self.catalog.metrics(project_id, agent_id)
        baseline_ids = {
            m["baseline_id"]
            for s in history["metrics"]
            for m in s["measurements"]
            if "baseline_id" in m
        }
        result["baselines"] = [
            b
            for b in result["baselines"]
            if b["baseline_id"] in baseline_ids or b["baseline_id"] in identifiers
        ]
        readiness = self.catalog.readiness(project_id, agent_id)
        if focus:
            readiness.update(self.catalog.measurement_status(project_id, agent_id, focus))
        result.update(
            agent=agent,
            focuses=focuses,
            active_focus_id=focus["id"] if focus else None,
            readiness=readiness,
        )
        return result

    def submit_job(self, project_id, payload):
        request_binding = digest(payload)
        existing = self.jobs.existing_submission(
            project_id, payload.get("operation_id"), request_binding
        )
        if existing:
            return existing
        payload = copy.deepcopy(payload)
        agent_id, focus_id = payload.get("application_agent_id"), payload.get("focus_id")
        if not agent_id:
            raise AuditError("select an application agent before starting this workflow")
        with self.lock:
            agent = self.catalog.agent(project_id, agent_id)
            if agent["status"] != "confirmed":
                raise AuditError("confirm this application agent before starting work")
            if focus_id:
                focus = self.catalog.focus(project_id, agent_id, focus_id)
            elif payload.get("kind") == "audit":
                focus = None
            else:
                raise AuditError("select a goal before starting this workflow")
            options = payload.setdefault("options", {})
            if not isinstance(options, dict):
                raise AuditError("workflow options must be an object")
            if set(options) & {
                "investigation_plan",
                "suite_manifest",
                "measurement_design",
                "design_revision",
                "optimization_background",
                "design_context",
            }:
                raise AuditError("workflow evidence bindings are prepared by the application")
            payload["goal"] = payload.get("goal") or (
                focus["goal"] if focus else f"Audit {agent['name']}"
            )
            scopes = options.get("code_scopes")
            if scopes and scopes != agent["code_scopes"]:
                raise AuditError("update the agent binding before changing its audit code scope")
            options["code_scopes"] = agent["code_scopes"]
            for key in ("issue_id", "audit_id"):
                if focus and focus["source"].get(key):
                    options.setdefault(key, focus["source"][key])
            self.catalog.validate_evidence_reference(project_id, agent_id, options)
            if focus:
                plan = self.catalog.investigation(project_id, agent_id, focus_id, options)
            else:
                plan = {
                    "version": 1,
                    "agent_id": agent_id,
                    "binding_version": agent["binding_version"],
                    "binding_digest": agent["binding_digest"],
                    "focus_id": None,
                    "focus_version": None,
                    "goal": payload["goal"],
                    "source": {"kind": "agent"},
                    "code_scopes": agent["code_scopes"],
                    "trace_selector": agent["trace_selector"],
                    "trace_snapshot_id": options.get("trace_snapshot_id"),
                    "trace_digest": None,
                    "trace_cap": None,
                    "rubric": "audit-v1",
                    "limits": ["Audit covers the selected agent and bound evidence."],
                }
                plan["digest"] = digest(plan)
            options["investigation_plan"] = plan
            accepted = (
                self.designs.accepted(project_id, agent_id, focus_id)
                if focus and payload["kind"] in {"eval", "baseline", "fix"}
                else None
            )
            if payload["kind"] == "design":
                draft = self.designs.get(project_id, agent_id, focus_id)
                options["design_revision"] = draft["revision"] if draft else 0
                options["design_context"] = self.measurement_design(project_id, agent_id, focus_id)
            elif accepted:
                if payload["kind"] == "eval" and accepted["native_plan"].get("id"):
                    from agentagon.webapp.evaluators import validate_plan

                    validate_plan(self.state.workspace(project_id), accepted["native_plan"])
                options["measurement_design"] = accepted
                evaluation = accepted["evaluation"]
                if (
                    payload["kind"] == "eval"
                    and not evaluation.get("evaluation_id")
                    and options.get("evaluation_id")
                ):
                    raise AuditError("This measurement plan requires preparing a new evaluator.")
                for key in ("evaluation_id", "dataset_snapshot_id"):
                    if evaluation.get(key):
                        if options.get(key) and options[key] != evaluation[key]:
                            raise AuditError(
                                "Selected input differs from the accepted measurement design."
                            )
                        options[key] = evaluation[key]
                if payload["kind"] == "fix":
                    options["optimization_background"] = self.designs.background(
                        accepted, focus["goal"]
                    )
            if plan.get("trace_snapshot_id"):
                options["trace_snapshot_id"] = plan["trace_snapshot_id"]
            if payload["kind"] == "audit" and not agent["code_scopes"]:
                if not options.get("trace_snapshot_id"):
                    raise AuditError(
                        "Import matching traces or bind code before auditing this trace-only agent."
                    )
                if options.get("mode") in {"code", "combined"} or options.get("scope") == "changes":
                    raise AuditError(
                        "Bind code before including application source in this trace-only agent's audit."
                    )
                options.update(mode="traces", scope="traces")
            measurement = (focus.get("measurement") or {}) if focus else {}
            accepted_evaluator = accepted["evaluation"].get("evaluation_id") if accepted else None
            if (
                payload["kind"] in {"baseline", "fix"}
                and measurement
                and not (payload["kind"] == "baseline" and accepted_evaluator)
            ):
                if (
                    options.get("evaluation_id")
                    and options["evaluation_id"] != measurement["evaluation_id"]
                ):
                    raise AuditError(
                        "Run a baseline for the accepted evaluator before starting a Fix."
                        if payload["kind"] == "fix" and accepted_evaluator
                        else "selected evaluator differs from this focus's accepted measurement"
                    )
                options["evaluation_id"] = measurement["evaluation_id"]
            if payload["kind"] in {"baseline", "fix"} and accepted:
                if not options.get("evaluation_id"):
                    raise AuditError("This measurement plan requires preparing a new evaluator.")
                self.designs.validate_evaluator(
                    self.state.workspace(project_id), accepted, options["evaluation_id"]
                )
            if payload["kind"] == "baseline" and options.get("baseline_id"):
                from agentagon.experiments import baselines

                previous = baselines.status(
                    self.state.workspace(project_id), options["baseline_id"]
                )
                if (
                    options.get("evaluation_id")
                    and previous["evaluation_id"] != options["evaluation_id"]
                ):
                    raise AuditError("Selected baseline does not measure the accepted evaluator.")
            if payload["kind"] == "fix":
                from agentagon.experiments.checkouts import under

                if not agent["code_scopes"]:
                    raise AuditError("bind application code before starting a measured fix")
                options.setdefault("permitted_paths", agent["code_scopes"])
                if (
                    not isinstance(options["permitted_paths"], list)
                    or not options["permitted_paths"]
                    or not all(isinstance(p, str) for p in options["permitted_paths"])
                ):
                    raise AuditError("choose a nonempty list of permitted paths")
                if any(
                    not any(
                        under(name, scope)
                        for scope in agent["code_scopes"] + agent["shared_dependencies"]
                    )
                    for name in options["permitted_paths"]
                ):
                    raise AuditError(
                        "permitted changes must remain inside the confirmed agent scope and shared dependencies"
                    )
                suite = self.catalog.suite(
                    project_id, agent_id, focus_id, permitted_paths=options["permitted_paths"]
                )
                if suite["missing"]:
                    raise AuditError(
                        "Prepare evaluations and baselines for these active focuses or narrow permitted changes: "
                        + ", ".join(m["name"] for m in suite["missing"])
                    )
                options["suite_manifest"] = suite
                if measurement.get("baseline_id"):
                    options["baseline_id"] = measurement["baseline_id"]
            return self.jobs.submit(project_id, payload, request_binding=request_binding)

    def completed_job(self, workspace, job, result):
        """Attach validated engine evidence to the focus captured by this task."""
        agent_id, focus_id = job.get("application_agent_id"), job.get("focus_id")
        if not agent_id or not focus_id:
            return result
        focus = self.catalog.focus(job["project_id"], agent_id, focus_id)
        plan = job["options"].get("investigation_plan", {})
        agent = self.catalog.agent(job["project_id"], agent_id)
        if (
            plan.get("focus_version") != focus["version"]
            or plan.get("binding_digest") != agent["binding_digest"]
        ):
            if job["kind"] == "design":
                raise AuditError("Goal or agent scope changed. Request a new measurement proposal.")
            return {
                **result,
                "measurement_note": "Focus changed during execution. Retained evidence needs an explicit measurement binding.",
            }
        if job["kind"] == "design":
            saved = self.designs.save(
                job["project_id"],
                agent_id,
                focus_id,
                {
                    **result.pop("measurement_design"),
                    "expected_revision": job["options"]["design_revision"],
                },
            )
            return {
                **result,
                "design_id": saved["id"],
                "design_revision": saved["revision"],
                "next_action": "Review and accept the proposed measurements.",
            }
        if job["kind"] == "eval" and result.get("evaluation_id"):
            accepted = job["options"].get("measurement_design")
            if accepted:
                self.designs.validate_evaluator(workspace, accepted, result["evaluation_id"])
            self.catalog.bind_measurement(
                job["project_id"],
                agent_id,
                focus_id,
                {"evaluation_id": result["evaluation_id"], "expected_revision": focus["revision"]},
                expected_binding_digest=plan.get("binding_digest"),
            )
        elif job["kind"] == "baseline" and result.get("baseline_id"):
            from agentagon.experiments import baselines

            baseline = baselines.status(workspace, result["baseline_id"])
            self.catalog.bind_measurement(
                job["project_id"],
                agent_id,
                focus_id,
                {
                    "evaluation_id": baseline["evaluation_id"],
                    "baseline_id": baseline["baseline_id"],
                    "expected_revision": focus["revision"],
                },
                expected_binding_digest=plan.get("binding_digest"),
            )
        return result

    def codex_models(self):
        from agentagon.webapp.agents import codex_models

        models = codex_models(self.state.read()["agents"].get("codex_executable"))
        return {
            "models": models,
            "default_model": next((m["id"] for m in models if m["default"]), None),
        }

    def remove_project(self, project_id):
        with self.lock, self.jobs.condition:
            self.state.workspace(project_id)
            if any(key[0] == project_id for key in self.jobs.active):
                raise AuditError("pause or cancel this project's tasks before removing it")
            if any(j["state"] in ACTIVE for j in self.jobs.list(project_id)):
                raise AuditError("pause or cancel this project's tasks before removing it")
            connections = [
                c
                for c in self.state.read()["connections"].values()
                if c["project_id"] == project_id
            ]
            result = self.state.remove(project_id)
            for discovery_id, draft in list(self.connection_discoveries.items()):
                if draft["connection"]["project_id"] == project_id:
                    self._discard_discovery(discovery_id)
            for connection in connections:
                for reference in connection["credentials"].values():
                    self.credentials.delete(reference)
            reference = self._intelligence_refs.pop(project_id, None)
            if reference:
                self.credentials.delete(reference)
            return result

    def overview(self, project_id):
        from agentagon.experiments import baselines, inspection

        workspace = self.state.workspace(project_id)
        initialized = (workspace.state / "workspace.json").exists()
        imported = snapshots.list_snapshots(workspace)
        issues = list_issues(workspace) if initialized else []
        safe_issues = [
            {
                k: v
                for k, v in issue.items()
                if k
                in {
                    "issue_id",
                    "title",
                    "summary",
                    "severity",
                    "confidence",
                    "status",
                    "audit_ids",
                    "latest_audit_id",
                }
            }
            for issue in issues
        ]
        return {
            "project": self.project_info(self.state.project(project_id)),
            "audits": [_summary(a) for a in _audits(workspace)] if initialized else [],
            "evaluations": inspection.evaluations(workspace) if initialized else [],
            "runs": [_run_summary(r) for r in _runs(workspace)] if initialized else [],
            "baselines": [
                baselines.public_projection(b) for b in baselines.list_baselines(workspace)
            ]
            if initialized
            else [],
            "issues": safe_issues,
            "jobs": [public_job(j) for j in self.jobs.list(project_id)],
            "datasets": [s for s in imported if s["kind"] == "dataset"],
            "dataset_splits": self.state.db.list_records(project_id, "dataset_splits"),
            "traces": [s for s in imported if s["kind"] == "traces"],
            "discovery": self.catalog.discovery_preferences(project_id),
            "settings": self.settings(project_id),
        }

    def result(self, project_id, kind, result_id):
        from agentagon.experiments import baselines, inspection, preparation

        workspace = self.state.workspace(project_id)
        if kind == "audit":
            return _detail(workspace, result_id)
        if kind == "eval":
            return inspection.evaluation_summary(preparation.load(workspace, result_id))
        if kind == "fix":
            from agentagon.experiments import suites
            from agentagon.experiments.store import load_run

            result = _run_detail(workspace, result_id)
            data = load_run(workspace, result_id)
            if data.get("suite"):
                from agentagon.experiments.budget import BudgetLedger

                suite = suites.status(workspace, result_id)
                ledger = BudgetLedger(workspace, result_id)
                if ledger.path.exists():
                    budget = ledger.snapshot()
                    result["shared_budget"] = {
                        "used_trials": BudgetLedger.spent(budget),
                        "max_trials": budget["limits"]["max_trials"],
                    }
                result["measurement_suite"] = {
                    "state": suite["state"],
                    "manifest_digest": suite["manifest_digest"],
                    "result": suite.get("result"),
                    "next_action": suite.get("next_action"),
                    "members": suite["manifest"]["members"],
                    "finalists": suite["finalists"],
                }
                comparison = result.setdefault("comparisons", {})
                passed = {
                    candidate_id
                    for candidate_id, finalist in suite["finalists"].items()
                    if finalist["state"] == "completed" and finalist.get("result", {}).get("passed")
                }
                if not passed:
                    retained = False
                    if data.get("optimizer_configured"):
                        from agentagon.experiments import optimize_run

                        optimized = optimize_run.status(workspace, result_id)
                        retained = optimized["state"] == "completed" and bool(
                            (optimized.get("selection") or {}).get("retained_baseline")
                        )
                    comparison["result"] = (
                        "baseline_retained"
                        if suite["state"] == "failed" or retained
                        else "final_verification_pending"
                    )
                    comparison["alternatives"] = []
                else:
                    comparison["alternatives"] = [
                        c for c in comparison.get("alternatives", []) if c["id"] in passed
                    ]
            return result
        if kind == "baseline":
            return baselines.public_projection(baselines.status(workspace, result_id))
        if kind in {"dataset", "traces"}:
            record = snapshots.load(workspace, result_id)
            split = next(
                (
                    s
                    for s in self.state.db.list_records(project_id, "dataset_splits")
                    if s["parameters"]["source_snapshot_id"] == result_id
                ),
                None,
            )
            if split or record["provenance"].get("dataset_partition") == "final_holdout":
                return {
                    **snapshots.summary(record),
                    "split": split,
                    "limitations": [
                        "Reserved final inputs are unavailable to ordinary development tasks. Final use requires a separately reviewed evaluator and exact verification binding."
                    ],
                }
            return record
        raise AuditError("unknown result kind")

    def derive_dataset(self, project_id, payload):
        from agentagon.webapp import datasets

        if set(payload) - {"trace_snapshot_id", "application_agent_id", "selection"}:
            raise AuditError("unsupported dataset derivation fields")
        agent = self.catalog.agent(project_id, payload.get("application_agent_id"))
        workspace = self.state.workspace(project_id)
        source = snapshots.select_traces(
            workspace, project_id, payload.get("trace_snapshot_id"), agent["trace_selector"]
        )
        return datasets.derive(workspace, project_id, source["id"], payload.get("selection", {}))

    def export_dataset(self, project_id, snapshot_id, payload):
        from agentagon.webapp.evaluators import export_bundle

        if set(payload) != {"framework"}:
            raise AuditError("choose an export framework")
        record = export_bundle(self.state.workspace(project_id), snapshot_id, payload["framework"])
        return {
            **record,
            "artifact_urls": {
                item[
                    "name"
                ]: f"/api/projects/{project_id}/datasets/{snapshot_id}/exports/{record['id']}/{item['name']}"
                for item in record["files"]
            },
        }

    def export_artifact(self, project_id, snapshot_id, export_id, name):
        import hashlib

        identifier(export_id, "export")
        workspace = self.state.workspace(project_id)
        from agentagon.webapp.datasets import assert_development

        assert_development(workspace, snapshots.load(workspace, snapshot_id))
        record = load_json(
            workspace.checked(private_directory(workspace, "exports") / f"{export_id}.json")
        )
        if (
            record["snapshot_id"] != snapshot_id
            or digest({k: v for k, v in record.items() if k not in {"id", "digest"}})
            != record["digest"]
        ):
            raise AuditError("export does not match this snapshot")
        item = next((item for item in record["files"] if item["name"] == name), None)
        if not item:
            raise AuditError("export artifact not found")
        path = workspace.checked(workspace.root / item["artifact"])
        if path.stat().st_size > snapshots.MAX_IMPORT_BYTES:
            raise AuditError("export artifact exceeds download limit")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["digest"]:
            raise AuditError("export artifact integrity changed")
        return content

    def preview_dataset_publication(self, project_id, snapshot_id, payload):
        from agentagon.webapp.evaluators import preview_publication

        if set(payload) - {"connection_id", "project", "name"}:
            raise AuditError("unsupported dataset publication fields")
        connection = self.connection(payload.get("connection_id"), project_id)
        self._selected_connection_project(connection, payload)
        return preview_publication(
            self.state.workspace(project_id),
            connection,
            snapshot_id,
            {k: v for k, v in payload.items() if k != "connection_id"},
        )

    def publish_dataset(self, project_id, snapshot_id, payload):
        from agentagon.webapp.evaluators import publish_dataset

        if set(payload) != {"connection_id", "preview_id", "operation_id"}:
            raise AuditError("publish a reviewed dataset preview with an operation ID")
        workspace = self.state.workspace(project_id)
        connection = self.connection(payload["connection_id"], project_id)
        preview = self.state.db.get_record(
            project_id, "dataset_publications", payload["preview_id"]
        )
        if not preview or preview["snapshot_id"] != snapshot_id:
            raise AuditError("publication preview does not belong to this dataset")
        return publish_dataset(
            workspace,
            self.provider_factory(connection, self.credentials),
            payload["preview_id"],
            operation_id(payload["operation_id"]),
            authorized=True,
        )

    def settings(self, project_id):
        workspace = self.state.workspace(project_id)
        config = Config()
        effective = config.effective(workspace.root)
        intelligence_reference = self._intelligence_refs.get(project_id)
        return {
            "settings": effective,
            "profiles": effective["profiles"],
            "intelligence_key_configured": bool(
                intelligence_reference or credential(effective, "intelligence")
            ),
            "revision": digest([effective, intelligence_reference]),
            "scope": "project",
        }

    def control_run(self, project_id, run_id, payload):
        from agentagon.experiments import controls
        from agentagon.experiments.store import load_run

        workspace = self.state.workspace(project_id)
        if payload.get("action") not in {
            "policy",
            "directive",
            "expand",
            "stop",
            "continue",
            "select",
            "invalidate",
            "exhaust",
            "cancel",
        }:
            raise AuditError("unsupported browser run control")
        load_run(workspace, run_id)
        if payload.get("action") == "select":
            from agentagon.experiments import suites

            for job in self._suite_jobs(project_id, run_id):
                bound = suites.status(workspace, run_id)
                if bound["manifest_digest"] != job["options"]["suite_manifest"]["digest"]:
                    raise AuditError("run does not use the task's required measurement suite")
                suites.verify_selection(workspace, run_id, payload.get("candidate_id"))
        controls.submit(workspace, run_id, payload)
        return controls.projection(load_run(workspace, run_id))

    def _suite_jobs(self, project_id, run_id):
        return [
            j
            for j in self.jobs.list(project_id)
            if j["kind"] == "fix"
            and j["options"].get("suite_manifest")
            and run_id
            in {j.get("workflow_ids", {}).get("run_id"), j.get("result", {}).get("run_id")}
        ]

    def deliver(self, project_id, payload):
        from agentagon.experiments.delivery import deliver

        if set(payload) - {
            "kind",
            "source_id",
            "publish",
            "remote",
            "base",
            "eval_parent_id",
            "delivery_id",
        }:
            raise AuditError("unsupported delivery fields")
        kind, source_id = payload.get("kind"), payload.get("source_id")
        if kind not in {"fix", "eval"} or not isinstance(source_id, str):
            raise AuditError("select a verified fix or frozen evaluation")
        publish = payload.get("publish", False)
        if type(publish) is not bool:
            raise AuditError("publication must be explicitly true or false")
        if publish and not (payload.get("remote") and payload.get("base")):
            raise AuditError(
                "review the prepared package and specify remote and base before publishing"
            )
        workspace = self.state.workspace(project_id)
        if kind == "fix":
            from agentagon.experiments import suites

            for job in self._suite_jobs(project_id, source_id):
                if job["state"] not in {"completed", "completed_with_limits"}:
                    raise AuditError(
                        "finish this task's required verification before preparing delivery"
                    )
                suites.verify_completed(workspace, source_id, job["options"]["suite_manifest"])
        if publish:
            delivery_id = payload.get("delivery_id")
            if not delivery_id:
                raise AuditError("prepare the local package before publishing")
            identifier(delivery_id, "delivery")
            receipt = private_directory(workspace, "deliveries") / f"{delivery_id}.json"
            if not receipt.exists():
                raise AuditError("prepare the local package before publishing")
            prepared = load_json(workspace.checked(receipt))
            if prepared.get("app_kind") != kind or prepared.get("app_source_id") != source_id:
                raise AuditError("prepared delivery does not belong to this source")
        result = deliver(
            workspace,
            **{"run_id" if kind == "fix" else "evaluation_id": source_id},
            publish=publish,
            remote=payload.get("remote") or "origin",
            base=payload.get("base"),
            eval_parent_id=payload.get("eval_parent_id"),
            prepared_delivery_id=payload.get("delivery_id") if publish else None,
        )
        record = {**result, "app_kind": kind, "app_source_id": source_id}
        for name, path in record["artifacts"].items():
            workspace.checked(Path(path))
            if not re.fullmatch(r"[a-z_]+", name):
                raise AuditError("invalid delivery artifact name")
        delivery_id = record["delivery_id"]
        identifier(delivery_id, "delivery")
        workspace.write(private_directory(workspace, "deliveries") / f"{delivery_id}.json", record)
        return {
            **result,
            "artifact_urls": {
                name: f"/api/projects/{project_id}/deliveries/{delivery_id}/{name}"
                for name in record["artifacts"]
            },
        }

    def delivery_artifact(self, project_id, delivery_id, name):
        workspace = self.state.workspace(project_id)
        identifier(delivery_id, "delivery")
        receipt = private_directory(workspace, "deliveries") / f"{delivery_id}.json"
        if not receipt.exists():
            raise AuditError("delivery not found in this project")
        record = load_json(receipt)
        if name not in record["artifacts"]:
            raise AuditError("artifact not found in this delivery")
        path = workspace.checked(Path(record["artifacts"][name]))
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
            raise AuditError("delivery artifact is unavailable or exceeds the download limit")
        return path.read_bytes()

    def update_settings(self, project_id, payload):
        workspace = self.state.workspace(project_id)
        scope = payload.get("scope", "project")
        if scope not in {"project", "user"}:
            raise AuditError("settings scope must be user or project")
        if set(payload) - {
            "scope",
            "values",
            "unset",
            "profile_name",
            "profile",
            "expected_revision",
            "intelligence_api_key",
        }:
            raise AuditError("unsupported settings fields")
        intelligence_key = payload.get("intelligence_api_key")
        if intelligence_key is not None:
            if scope != "project":
                raise AuditError("Intelligence API keys must be configured for one project")
            if (
                not isinstance(intelligence_key, str)
                or not intelligence_key.strip()
                or len(intelligence_key) > 16384
            ):
                raise AuditError("Intelligence API key must be a nonempty string")
            intelligence_key = intelligence_key.strip()
        with self.lock:
            if (
                payload.get("expected_revision")
                and payload["expected_revision"] != self.settings(project_id)["revision"]
            ):
                raise AuditError("settings changed; refresh before saving")
            if payload.get("profile_name"):
                Config().update_profile(
                    scope, payload["profile_name"], payload.get("profile"), workspace.root
                )
            else:
                values, unset = copy.deepcopy(payload.get("values", {})), payload.get("unset", [])
                if (
                    not isinstance(values, dict)
                    or not isinstance(unset, list)
                    or not all(isinstance(k, str) for k in unset)
                ):
                    raise AuditError("settings require values and an unset list")
                reference = None
                if intelligence_key:
                    reference = self.credentials.set(intelligence_key, "session")
                try:
                    Config().update(scope, workspace.root, values, tuple(unset))
                except Exception:
                    if reference:
                        self.credentials.delete(reference)
                    raise
                if reference:
                    previous_reference = self._intelligence_refs.get(project_id)
                    self._intelligence_refs[project_id] = reference
                    if previous_reference:
                        self.credentials.delete(previous_reference)
        return self.settings(project_id)

    def connection(self, connection_id, project_id):
        self.state.project(project_id)
        identifier(connection_id, "connection")
        connection = self.state.read()["connections"].get(connection_id)
        if not connection or connection["project_id"] != project_id:
            raise AuditError("connection not found in this project")
        return connection

    def connection_projection(self, connection):
        value = {k: copy.deepcopy(v) for k, v in connection.items() if k != "credentials"}
        value["credential_fields"] = list(connection.get("credentials", {}))
        modes = {reference.split(":", 1)[0] for reference in connection["credentials"].values()}
        value["credential_mode"] = "session" if "session" in modes else next(iter(modes), "session")
        for reference in connection.get("credentials", {}).values():
            try:
                self.credentials.resolve(reference)
            except AuditError:
                value["status"] = "needs_credentials"
        return value

    def connections(self, project_id):
        self.state.project(project_id)
        return {
            "connections": [
                self.connection_projection(c)
                for c in self.state.read()["connections"].values()
                if c["project_id"] == project_id
            ]
        }

    def _discard_discovery(self, discovery_id):
        with self.lock:
            draft = self.connection_discoveries.pop(discovery_id, None)
            if draft:
                draft["timer"].cancel()
                for reference in draft["new_references"]:
                    self.credentials.delete(reference)

    @staticmethod
    def _connection_source(connection):
        return {k: v for k, v in connection.items() if k not in {"status", "last_checked_at"}}

    def discover_connection(self, project_id, payload):
        with self.lock:
            self.state.project(project_id)
            if not isinstance(payload, dict) or set(payload) - {
                "id",
                "provider",
                "endpoint",
                "credentials",
            }:
                raise AuditError("unsupported connection discovery fields")
            provider = payload.get("provider")
            if provider not in DEFAULT_ENDPOINTS:
                raise AuditError("choose Braintrust, LangSmith, or Langfuse")
            previous = self.connection(payload["id"], project_id) if "id" in payload else {}
            if previous and previous["provider"] != provider:
                raise AuditError("create a new connection to change providers")
            values = payload.get("credentials", {})
            required = {"public_key", "secret_key"} if provider == "langfuse" else {"api_key"}
            if (
                not isinstance(values, dict)
                or set(values) - required
                or any(
                    not isinstance(value, str) or len(value) > 16384 for value in values.values()
                )
            ):
                raise AuditError("unsupported credential fields for this provider")
            connection = {
                "id": previous.get("id") or "connection_" + uuid.uuid4().hex[:24],
                "project_id": project_id,
                "provider": provider,
                "endpoint": payload.get("endpoint")
                or previous.get("endpoint")
                or DEFAULT_ENDPOINTS[provider],
                "credentials": dict(previous.get("credentials", {})),
            }
            self.provider_factory(connection, self.credentials)  # Validate before storing secrets.
            for discovery_id, draft in list(self.connection_discoveries.items()):
                if draft["expires_at"] <= time.monotonic() or (
                    draft["connection"]["project_id"] == project_id
                    and draft["connection"]["provider"] == provider
                ):
                    self._discard_discovery(discovery_id)
            while len(self.connection_discoveries) >= MAX_CONNECTION_DISCOVERIES:
                self._discard_discovery(next(iter(self.connection_discoveries)))
            references = []
            try:
                for key, value in values.items():
                    if value.strip():
                        reference = self.credentials.set(value, "session")
                        references.append(reference)
                        connection["credentials"][key] = reference
                for key in required:
                    if key not in connection["credentials"]:
                        raise AuditError("provide the required provider credentials")
                    self.credentials.resolve(connection["credentials"][key])
            except Exception:
                for reference in references:
                    self.credentials.delete(reference)
                raise
            discovery_id = "discovery_" + uuid.uuid4().hex[:24]
            timer = threading.Timer(DISCOVERY_TTL_SECONDS, self._discard_discovery, (discovery_id,))
            timer.daemon = True
            draft = {
                "connection": connection,
                "previous": self._connection_source(previous),
                "new_references": references,
                "timer": timer,
                "expires_at": time.monotonic() + DISCOVERY_TTL_SECONDS,
            }
            self.connection_discoveries[discovery_id] = draft
            timer.start()
        try:
            result = self.provider_factory(connection, self.credentials).test()
            projects = result.get("projects")
            if (
                result.get("status") != "connected"
                or not isinstance(projects, list)
                or not projects
            ):
                raise AuditError("no accessible provider projects were found")
            if len(projects) > 1000:
                raise AuditError("too many provider projects; use more specific credentials")
            choices = {}
            for project in projects:
                if (
                    not isinstance(project, dict)
                    or any(
                        not isinstance(project.get(key), str)
                        or not project[key]
                        or len(project[key]) > 500
                        for key in ("id", "name")
                    )
                    or any(
                        not isinstance(project.get(key, ""), str) or len(project.get(key, "")) > 500
                        for key in ("workspace_id", "workspace_name")
                    )
                ):
                    raise AuditError("provider returned an invalid project")
                choice = {
                    key: project[key]
                    for key in ("id", "name", "workspace_id", "workspace_name")
                    if key in project
                }
                selection_id = (
                    "choice_" + digest([choice.get("workspace_id", ""), choice["id"]])[:24]
                )
                choices[selection_id] = {**choice, "selection_id": selection_id}
            with self.lock:
                if self.connection_discoveries.get(discovery_id) is not draft:
                    raise AuditError("connection discovery expired or was replaced; discover again")
                draft["projects"] = choices
                return {"discovery_id": discovery_id, "projects": list(choices.values())}
        except Exception:
            self._discard_discovery(discovery_id)
            raise

    def save_connection(self, project_id, payload):
        with self.lock:
            self.state.project(project_id)
            if not isinstance(payload, dict) or set(payload) != {"discovery_id", "project"}:
                raise AuditError("save a discovered provider project")
            discovery_id = identifier(payload["discovery_id"], "discovery")
            draft = self.connection_discoveries.get(discovery_id)
            if not draft or draft["connection"]["project_id"] != project_id:
                raise AuditError("connection discovery not found in this project; discover again")
            if draft["expires_at"] <= time.monotonic():
                self._discard_discovery(discovery_id)
                raise AuditError("connection discovery expired; discover again")
            selected = (
                draft.get("projects", {}).get(payload["project"])
                if isinstance(payload["project"], str)
                else None
            )
            if selected is None:
                raise AuditError("choose a project returned by this discovery")
            connection = copy.deepcopy(draft["connection"])
            previous = self.state.read()["connections"].get(connection["id"], {})
            if "saved_project" in draft:
                if (
                    payload["project"] != draft["saved_project"]
                    or self._connection_source(previous) != draft["saved_source"]
                ):
                    raise AuditError("connection changed after saving; discover again")
                return self.connection_projection(previous)
            if self._connection_source(previous) != draft["previous"]:
                self._discard_discovery(discovery_id)
                raise AuditError("connection changed during discovery; discover again")
            provider_name = {
                "braintrust": "Braintrust",
                "langsmith": "LangSmith",
                "langfuse": "Langfuse",
            }[connection["provider"]]
            connection.update(
                name=f"{provider_name} · {selected['name']}"[:100],
                project=selected["id"],
                project_name=selected["name"],
                workspace_id=selected.get("workspace_id", ""),
                status="connected",
                last_checked_at=now(),
                updated_at=now(),
            )
            references = []
            try:
                for key, reference in connection["credentials"].items():
                    if reference in draft["new_references"]:
                        replacement = self.credentials.set_auto(self.credentials.resolve(reference))
                        references.append(replacement)
                        connection["credentials"][key] = replacement
                with self.state.locked() as data:
                    data["connections"][connection["id"]] = connection
            except Exception:
                for reference in references:
                    self.credentials.delete(reference)
                self._discard_discovery(discovery_id)
                raise
            for reference in draft["new_references"]:
                self.credentials.delete(reference)
            draft.update(
                new_references=[],
                saved_project=payload["project"],
                saved_source=self._connection_source(connection),
            )
            for reference in previous.get("credentials", {}).values():
                if reference not in connection["credentials"].values():
                    self.credentials.delete(reference)
            return self.connection_projection(connection)

    def test_connection(self, project_id, connection_id):
        connection = self.connection(connection_id, project_id)
        try:
            result = self.provider_factory(connection, self.credentials).test()
            if result.get("status") != "connected" or not any(
                project.get("id") == connection["project"]
                and project.get("workspace_id", "") == connection.get("workspace_id", "")
                for project in result.get("projects", [])
            ):
                raise AuditError("the connected provider project is no longer accessible")
        except AuditError:
            self._save_connection_test(connection, {"status": "unavailable"})
            raise
        self._save_connection_test(connection, {"status": "connected", "last_checked_at": now()})
        return self.connection_projection(self.connection(connection_id, project_id))

    def _save_connection_test(self, connection, changes):
        with self.lock:
            current = self.connection(connection["id"], connection["project_id"])
            if self._connection_source(current) != self._connection_source(connection):
                raise AuditError(
                    "connection changed during its check; test the current configuration"
                )
            with self.state.locked() as data:
                data["connections"][connection["id"]] = {**current, **changes}

    def disconnect(self, project_id, connection_id):
        with self.lock:
            connection = self.connection(connection_id, project_id)
            with self.state.locked() as data:
                del data["connections"][connection_id]
            for discovery_id, draft in list(self.connection_discoveries.items()):
                if draft["connection"]["id"] == connection_id:
                    self._discard_discovery(discovery_id)
            for reference in connection["credentials"].values():
                self.credentials.delete(reference)
            return {"disconnected": connection_id}

    def connection_datasets(self, project_id, connection_id):
        return {
            "datasets": self.provider_factory(
                self.connection(connection_id, project_id), self.credentials
            ).datasets()
        }

    @staticmethod
    def _selected_connection_project(connection, selection):
        if "project" in selection and selection["project"] != connection["project"]:
            raise AuditError("use the connected provider project")

    def preview(self, project_id, payload):
        self.state.workspace(project_id)
        connection = self.connection(payload.get("connection_id"), project_id)
        if payload.get("kind") not in {"traces", "dataset"}:
            raise AuditError("choose traces or dataset")
        selection = copy.deepcopy(payload.get("selection", {}))
        if not isinstance(selection, dict):
            raise AuditError("selection must be an object")
        self._selected_connection_project(connection, selection)
        selection["project"] = connection["project"]
        result = self.provider_factory(connection, self.credentials).preview(
            payload["kind"], selection
        )
        selection = copy.deepcopy(result.get("provenance", {}).get("selection", redact(selection)))
        record = {
            **result,
            "kind": payload["kind"],
            "connection_id": connection["id"],
            "project_id": project_id,
            "selection": selection,
        }
        record["provenance"].setdefault("provider", connection["provider"])
        preview_id = "preview_" + uuid.uuid4().hex[:24]
        with self.lock:
            if len(self.previews) >= 12:
                self.previews.pop(next(iter(self.previews)))
            self.previews[preview_id] = record
        return {"preview_id": preview_id, **record}

    def import_preview(self, project_id, payload):
        op = operation_id(payload.get("operation_id"))
        workspace = self.state.workspace(project_id)
        with self.lock:
            binding = {"project_id": project_id, "operation_id": op}
            for record in snapshots.list_snapshots(workspace):
                if record["provenance"].get("import_operation") == binding:
                    if record["provenance"].get("preview_id") != payload["preview_id"]:
                        raise AuditError("operation_id already belongs to another import")
                    return record
            preview = self.previews.get(payload.get("preview_id"))
            if not preview or preview["project_id"] != project_id:
                raise AuditError("preview expired or belongs to another project; preview again")
            workspace.initialize()
            preview = copy.deepcopy(preview)
            preview["provenance"].update(import_operation=binding, preview_id=payload["preview_id"])
            return snapshots.summary(snapshots.save(workspace, project_id, preview))

    def agents(self):
        settings = self.state.read()["agents"]
        reference = settings.get("claude_api_key_ref") or (
            "env:ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else None
        )
        key_available = False
        if reference:
            try:
                key_available = bool(self.credentials.resolve(reference))
            except AuditError:
                pass
        return {
            "agents": [
                {
                    **agent,
                    **({"authenticated": key_available} if agent["agent"] == "claude" else {}),
                    "id": agent["agent"],
                    "name": "Codex" if agent["agent"] == "codex" else "Claude",
                    "message": agent.get("unavailable_reason"),
                }
                for agent in detect_agents()
            ],
            "settings": {k: v for k, v in settings.items() if not k.endswith("_ref")},
            "claude_key_configured": key_available,
        }

    def save_agents(self, payload):
        with self.lock:
            if set(payload) - {
                "default_agent",
                "model",
                "concurrency",
                "claude_api_key",
                "credential_mode",
            }:
                raise AuditError("unsupported coding-agent settings")
            settings = self.state.read()["agents"]
            agent = payload.get("default_agent", settings.get("default_agent"))
            if agent not in {"codex", "claude"}:
                raise AuditError("choose Codex or Claude")
            concurrency = payload.get("concurrency", settings.get("concurrency", 1))
            if type(concurrency) is not int or not 1 <= concurrency <= 8:
                raise AuditError("agent capacity must be between 1 and 8")
            model = payload.get("model", settings["models"][agent])
            if not isinstance(model, str) or len(model) > 200:
                raise AuditError("invalid model name")
            if agent == "codex" and model:
                from agentagon.webapp.agents import validate_codex_model

                model = validate_codex_model(model, settings.get("codex_executable"))
            settings.update(default_agent=agent, concurrency=concurrency)
            settings["models"][agent] = model
            previous_reference = settings.get("claude_api_key_ref")
            key = payload.get("claude_api_key")
            if key:
                mode = payload.get("credential_mode", "session")
                if mode == "env":
                    if not isinstance(key, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
                        raise AuditError("use an environment-variable name")
                    settings["claude_api_key_ref"] = "env:" + key
                else:
                    settings["claude_api_key_ref"] = self.credentials.set(key, mode)
            reference = settings.get("claude_api_key_ref")
            with self.jobs.condition:
                try:
                    with self.state.locked() as data:
                        data["agents"] = settings
                except Exception:
                    if reference and reference != previous_reference:
                        self.credentials.delete(reference)
                    raise
                if previous_reference and previous_reference != reference:
                    self.credentials.delete(previous_reference)
                self.jobs._dispatch()
            return self.agents()

    def close(self):
        self.jobs.close()
        with self.lock:
            for discovery_id in list(self.connection_discoveries):
                self._discard_discovery(discovery_id)
        for reference in self._intelligence_refs.values():
            self.credentials.delete(reference)
        self._intelligence_refs.clear()
