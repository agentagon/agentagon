"""Application operations shared by the browser and local launcher."""

import copy
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from agentagon.brain.adapters import detect_backends
from agentagon.capabilities.traces import snapshots
from agentagon.capabilities.traces.normalize import redact
from agentagon.capabilities.traces.providers import (
    DEFAULT_ENDPOINTS,
    CredentialStore,
    ProviderClient,
)
from agentagon.core.records import AuditError, digest, load_json, now
from agentagon.dashboard.evidence import (
    _audits,
    _detail,
    _run_detail,
    _run_summary,
    _runs,
    _summary,
)
from agentagon.domain.catalog import Catalog
from agentagon.domain.issues import list_issues
from agentagon.storage.changes import git_bytes
from agentagon.storage.changes import revision as git_revision
from agentagon.storage.config import Config, credential
from agentagon.storage.state import AppState, identifier, private_directory
from agentagon.workflows.evaluate.designs import Designs
from agentagon.workflows.runtime import ACTIVE, TaskRuntime, operation_id, public_task

DISCOVERY_TTL_SECONDS = 600
MAX_CONNECTION_DISCOVERIES = 12
RESULT_TASK_KEYS = {
    "audit": "audit_id",
    "eval": "evaluation_id",
    "fix": "run_id",
    "optimize": "run_id",
    "baseline": "baseline_id",
    "patch": "patch_id",
}


def _result_task_context(application, project_id, kind, result_id):
    """Return the bounded task facts that explain a result projection."""
    key = RESULT_TASK_KEYS.get(kind)
    if key is None:
        return None
    matches = [
        job
        for job in application.runtime.list(project_id)
        if job.get("kind") == kind
        and result_id
        in {
            job.get("workflow_ids", {}).get(key),
            (job.get("result") or {}).get(key),
        }
    ]
    if not matches:
        return None
    job = max(matches, key=lambda item: (item.get("updated_at", ""), item.get("id", "")))
    options = job.get("options") or {}
    return {
        "id": job["id"],
        "workflow": job["kind"],
        "title": job.get("title") or job.get("goal") or f"{kind.title()} result",
        "state": job["state"],
        "result": copy.deepcopy(job.get("result")),
        "limits": {
            key: options[key]
            for key in ("max_trials", "max_elapsed_seconds", "trial_timeout_seconds")
            if type(options.get(key)) is int
        },
        "next_action": job.get("next_action"),
        "updated_at": job.get("updated_at"),
    }


def _static_delivery_projections(workspace, project_id, kind, source_id, deliveries):
    """Project durable app delivery receipts without exposing stored paths."""
    if not isinstance(deliveries, dict):
        return []
    projected = []
    for delivery_id, source_record in deliveries.items():
        if not isinstance(delivery_id, str) or not isinstance(source_record, dict):
            continue
        try:
            identifier(delivery_id, "delivery")
        except AuditError:
            continue
        receipt_path = private_directory(workspace, "deliveries") / f"{delivery_id}.json"
        if not receipt_path.exists():
            continue
        try:
            receipt = load_json(workspace.checked(receipt_path))
        except (AuditError, OSError, TypeError, ValueError):
            continue
        if (
            receipt.get("delivery_id") != delivery_id
            or receipt.get("app_kind") != kind
            or receipt.get("app_source_id") != source_id
        ):
            continue
        artifacts = {}
        for name, path in (receipt.get("artifacts") or {}).items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z_]+", name):
                continue
            if not isinstance(path, str):
                continue
            try:
                artifact = workspace.checked(Path(path))
            except (AuditError, OSError, ValueError):
                continue
            if (
                artifact.is_symlink()
                or not artifact.is_file()
                or artifact.stat().st_size > 20_000_000
            ):
                continue
            artifacts[name] = f"/api/projects/{project_id}/deliveries/{delivery_id}/{name}"
        item = {
            key: receipt[key]
            for key in (
                "delivery_id",
                "state",
                "branch",
                "base",
                "source_revision",
                "created_at",
                "updated_at",
            )
            if isinstance(receipt.get(key), str)
        }
        item["artifact_urls"] = artifacts
        projected.append(item)
    return sorted(
        projected,
        key=lambda item: (item.get("created_at", ""), item.get("delivery_id", "")),
        reverse=True,
    )


class Application:
    def __init__(self, directory=None, *, execute=None, credentials=None, provider_factory=None):
        self.started_at = now()
        self.state = AppState(directory)
        self.credentials = credentials or CredentialStore()
        self.provider_factory = provider_factory or ProviderClient
        self.runtime = TaskRuntime(self.state, self.credentials, execute)
        self.catalog = Catalog(self.state)
        from agentagon.memory.store import MemoryGroups

        self.memory = MemoryGroups(self.state)
        self.designs = Designs(self.state, self.catalog)
        self.runtime.verify_result = self.completed_job
        from agentagon.workflows.outcomes import record_outcome

        self.runtime.record_outcome = lambda workspace, job: record_outcome(self, workspace, job)
        self.previews = {}
        self.connection_discoveries = {}
        self._intelligence_refs = {}
        self.lock = self.runtime.condition
        self.selected_project_id = None
        from agentagon.domain.monitoring import Monitoring
        from agentagon.workflows.production_runtime import ProductionRuntime
        from agentagon.workflows.scheduler import Scheduler

        self.monitoring = Monitoring(self)
        self.production = ProductionRuntime(self)
        self.runtime.prepare_task = self.production.prepare
        self.scheduler = Scheduler(self)

    def diagnostics(self):
        """Return bounded identities for the code and state loaded by this process."""
        import hashlib
        import platform
        from importlib.resources import files

        from agentagon import __version__
        from agentagon.core.records import CONTRACT_VERSION
        from agentagon.storage.workspace import STATE_VERSION

        asset_hash = hashlib.sha256()
        assets = files("agentagon").joinpath("dashboard/assets")
        for name in ("webapp.html", "webapp.js", "webapp.css"):
            asset_hash.update(name.encode())
            asset_hash.update(assets.joinpath(name).read_bytes())
        configured_build = os.environ.get("AGENTAGON_BUILD_ID", "")
        build_id = (
            configured_build
            if configured_build.isascii() and 1 <= len(configured_build) <= 120
            else "Unknown build"
        )
        return {
            "application": "agentagon",
            "package_version": __version__,
            "build_id": build_id,
            "frontend_asset_version": asset_hash.hexdigest()[:16],
            "service_started_at": self.started_at,
            "python_version": platform.python_version(),
            "state_contracts": {
                "evidence": CONTRACT_VERSION,
                "workspace": STATE_VERSION,
                "metadata": self.state.read()["version"],
            },
        }

    def project_info(self, project):
        result = {
            **project,
            "active_tasks": 0,
            "branch": None,
            "available": True,
            "source": {"kind": "folder", "revision": None, "dirty": None},
        }
        try:
            workspace = self.state.workspace(project["id"])
            branch = git_bytes(workspace.root, "branch", "--show-current", optional=True)
            result["branch"] = branch.decode().strip() if branch else None
            inside = git_bytes(workspace.root, "rev-parse", "--is-inside-work-tree", optional=True)
            if inside and inside.strip() == b"true":
                status = git_bytes(
                    workspace.root,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=normal",
                    optional=True,
                )
                source_revision = git_revision(workspace.root)
                result["source"] = {
                    "kind": "git",
                    "revision": source_revision,
                    "dirty": bool(status),
                    "measured_work_ready": bool(source_revision and not status),
                }
            result["active_tasks"] = sum(
                j["state"] in ACTIVE for j in self.runtime.list(project["id"])
            )
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

    def workflows(self):
        from agentagon.domain.projections import workflow_definitions

        return {"workflows": workflow_definitions()}

    def connector_types(self):
        from agentagon.domain.projections import connector_types

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
        from agentagon.domain.projections import agent_projection

        agents = [agent_projection(item) for item in self.catalog.agents(project_id)]
        excluded = [agent_projection(item) for item in self.catalog.excluded_agents(project_id)]
        return {
            "agents": agents,
            "confirmed": [item for item in agents if item["status"] == "confirmed"],
            "suggestions": [item for item in agents if item["status"] != "confirmed"],
            "excluded": excluded,
        }

    def project_agent(self, project_id, agent_id):
        from agentagon.domain.projections import agent_projection

        return agent_projection(self.catalog.agent(project_id, agent_id))

    def issues(self, project_id):
        return {"issues": list_issues(self.state.workspace(project_id))}

    def issue(self, project_id, issue_id):
        from agentagon.domain.issues import issue_detail, lifecycle_projection

        detail = issue_detail(self.state.workspace(project_id), issue_id)
        lifecycle = lifecycle_projection(self, project_id, issue_id)
        return {
            **detail,
            "facets": lifecycle["facets"],
            "next_actions": lifecycle["next_actions"],
        }

    def update_issue(self, project_id, issue_id, payload):
        from agentagon.domain.issues import triage_issue

        if not isinstance(payload, dict):
            raise AuditError("issue update must be an object")
        if payload.get("action") == "assign":
            agent = self.catalog.agent(project_id, payload.get("agent_id"))
            if agent["status"] != "confirmed":
                raise AuditError("confirm the application agent before assigning this issue")
        return triage_issue(self.state.workspace(project_id), issue_id, payload)

    def trace_detail(self, project_id, snapshot_id, trace_id=None, max_spans=100):
        from agentagon.capabilities.traces.detail import trace_detail

        return trace_detail(
            self.state.workspace(project_id),
            snapshot_id,
            trace_id=trace_id,
            max_spans=max_spans,
        )

    def select_trace(self, project_id, snapshot_id, trace_id):
        selected = snapshots.select_trace(
            self.state.workspace(project_id), project_id, snapshot_id, trace_id
        )
        return snapshots.summary(selected)

    def goals(self, project_id, agent_id):
        from agentagon.domain.projections import goal_projection

        return {
            "goals": [goal_projection(item) for item in self.catalog.goals(project_id, agent_id)]
        }

    def goal(self, project_id, agent_id, goal_id):
        from agentagon.domain.projections import goal_projection

        goal_record = self.catalog.goal_record(project_id, agent_id, goal_id)
        result = goal_projection(goal_record)
        result["readiness"] = self.catalog.measurement_status(project_id, agent_id, goal_record)
        design = self.designs.get(project_id, agent_id, goal_id)
        result["measurement_plan"] = design
        return result

    def save_goal(self, project_id, agent_id, payload):
        return self.goal_projection(self.catalog.save_goal(project_id, agent_id, payload))

    @staticmethod
    def goal_projection(goal_record):
        from agentagon.domain.projections import goal_projection

        return goal_projection(goal_record)

    def tasks(self, project_id, filters=None):
        from agentagon.domain.projections import goal_projection, task_summary

        filters = filters or {}
        agents = {item["id"]: item for item in self.catalog.agents(project_id)}
        goals = {
            item["id"]: goal_projection(item)
            for agent in agents.values()
            for item in self.catalog.goals(project_id, agent["id"])
        }
        records = sorted(
            self.runtime.list(project_id),
            key=lambda item: item.get("updated_at") or item.get("created_at", ""),
            reverse=True,
        )
        for field, key in (
            ("agent_id", "application_agent_id"),
            ("goal_id", "goal_id"),
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
        from agentagon.domain.projections import goal_projection, task_detail

        agents = {item["id"]: item for item in self.catalog.agents(project_id)}
        goals = {
            item["id"]: goal_projection(item)
            for agent in agents.values()
            for item in self.catalog.goals(project_id, agent["id"])
        }
        return task_detail(self.runtime.get(project_id, task_id), agents, goals)

    def lessons(self, project_id, agent_id=None):
        from agentagon.domain.lessons import list_lessons

        return list_lessons(self, project_id, agent_id)

    def local_funnel(self, project_id):
        """Return bounded project-local journey metrics for inspection or export."""

        from agentagon.domain.funnel import projection

        return projection(self.state, project_id)

    def lesson(self, project_id, group_id, entry_id, agent_id=None):
        from agentagon.domain.lessons import lesson_detail

        return lesson_detail(self, project_id, group_id, entry_id, agent_id)

    def correct_lesson(self, project_id, group_id, entry_id, payload):
        from agentagon.domain.lessons import correct_lesson

        return correct_lesson(self, project_id, group_id, entry_id, payload)

    def prepare_workflow_start(self, project_id, payload):
        """Prepare the complete public start intent without executing work."""
        from agentagon.workflows.operations import prepare_workflow_start

        return prepare_workflow_start(self, project_id, payload)

    def submit_task(self, project_id, payload, *, scheduled=False):
        from agentagon.workflows.requests import submit

        return submit(self, project_id, payload, scheduled=scheduled)

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
        """Discovery is an ordinary managed assessment task."""
        if not isinstance(payload, dict) or set(payload) - {"operation_id", "options"}:
            raise AuditError("use an assessment operation_id and options")
        return self.submit_task(
            project_id,
            {
                "workflow": "assess",
                "operation_id": payload.get("operation_id"),
                "input": {"type": "project", "id": project_id},
                "options": payload.get("options", {}),
            },
        )

    def infer_application_agent_responsibility(self, project_id, agent_id, payload):
        """Review one retained suggestion with its exact bounded source context."""

        if not isinstance(payload, dict) or set(payload) != {"operation_id"}:
            raise AuditError("use an operation_id for responsibility inference")
        agent = self.catalog.agent(project_id, agent_id)
        if agent["status"] != "suggested":
            raise AuditError("responsibility inference is available for suggested identities")
        if len(agent.get("code_scopes", [])) != 1:
            raise AuditError("responsibility inference requires one retained agent definition")
        return self.submit_task(
            project_id,
            {
                "workflow": "assess",
                "operation_id": payload["operation_id"],
                "agent_id": agent_id,
                "input": {"type": "project", "id": project_id},
                "options": {"assessment": {}},
            },
        )

    def save_application_agent(self, project_id, payload, agent_id=None):
        selector = payload.get("trace_selector", {})
        if not isinstance(selector, dict):
            raise AuditError("agent trace selector must be an object")
        if selector.get("connection_id"):
            connection = self.connection(selector["connection_id"], project_id)
            self._selected_connection_project(connection, selector)
        with self.lock:
            return self.catalog.save_agent(project_id, payload, agent_id)

    def confirm_application_agent(self, project_id, agent_id, payload):
        if not isinstance(payload, dict) or set(payload) != {"expected_revision"}:
            raise AuditError("confirm the displayed identity revision")
        with self.lock:
            return self.catalog.confirm_suggestion(
                project_id, agent_id, payload["expected_revision"]
            )

    def exclude_application_agent(self, project_id, agent_id, payload):
        if not isinstance(payload, dict) or set(payload) != {"reason", "expected_revision"}:
            raise AuditError("exclude the displayed identity with a reason")
        with self.lock:
            return self.catalog.exclude_suggestion(
                project_id,
                agent_id,
                payload["reason"],
                payload["expected_revision"],
            )

    def restore_application_agent(self, project_id, agent_id, payload):
        if not isinstance(payload, dict) or set(payload) != {"expected_revision"}:
            raise AuditError("restore the displayed identity revision")
        with self.lock:
            return self.catalog.restore_suggestion(
                project_id, agent_id, payload["expected_revision"]
            )

    def measurement_design(self, project_id, agent_id, goal_id):
        from agentagon.capabilities.evaluation import native as evaluators
        from agentagon.capabilities.experiments import preparation

        goal_record = self.catalog.goal_record(project_id, agent_id, goal_id)
        workspace = self.state.workspace(project_id)
        inventory = evaluators.discover(workspace)
        overview = self.overview(project_id)
        frozen = []
        for item in overview["evaluations"]:
            if item["state"] == "frozen":
                record = preparation.load(workspace, item["evaluation_id"])
                frozen.append({**item, "scoring": record["package"]["spec"].get("scoring")})
        return {
            "goal_record": goal_record,
            "draft": self.designs.get(project_id, agent_id, goal_id),
            "evaluators": inventory["candidates"],
            "limitations": inventory["limitations"],
            "frozen_evaluators": frozen,
            "snapshots": overview["datasets"],
        }

    def agent_overview(self, project_id, agent_id, goal_id=None):
        agent = self.catalog.agent(project_id, agent_id)
        goals = self.catalog.goals(project_id, agent_id)
        if goal_id:
            goal_record = self.catalog.goal_record(project_id, agent_id, goal_id)
        else:
            goal_record = goals[0] if goals else None
        result = self.overview(project_id)
        jobs = [j for j in result["tasks"] if j.get("application_agent_id") == agent_id]
        identifiers = {
            value
            for j in jobs
            for key, value in {**j.get("workflow_ids", {}), **j.get("result", {})}.items()
            if key.endswith("_id") and isinstance(value, str)
        }
        result["tasks"] = jobs
        for name, key in (("audits", "audit_id"), ("runs", "run_id")):
            result[name] = [r for r in result[name] if r.get(key) in identifiers]
        result["issues"] = [
            issue for issue in result["issues"] if issue.get("agent_id") == agent_id
        ]
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
        if goal_record:
            readiness.update(self.catalog.measurement_status(project_id, agent_id, goal_record))
        result.update(
            agent=agent,
            goals=goals,
            active_goal_id=goal_record["id"] if goal_record else None,
            readiness=readiness,
        )
        return result

    def submit_job(
        self, project_id, payload, *, request_binding=None, suggested_read_only_scope=None
    ):
        request_binding = request_binding or digest(payload)
        existing = self.runtime.existing_submission(
            project_id, payload.get("operation_id"), request_binding
        )
        if existing:
            return existing
        payload = copy.deepcopy(payload)
        agent_id, goal_id = payload.get("application_agent_id"), payload.get("goal_id")
        if not agent_id:
            raise AuditError("select an application agent before starting this workflow")
        with self.lock:
            agent = self.catalog.agent(project_id, agent_id)
            if agent["status"] == "archived":
                raise AuditError("restore this excluded identity before starting work")
            if agent["status"] == "suggested":
                from agentagon.workflows.operations.preparation import agent_review_scope

                expected_scope = agent_review_scope(agent)
                if payload.get("kind") != "audit" or suggested_read_only_scope != expected_scope:
                    raise AuditError("confirm this application agent before starting work")
            elif agent["status"] != "confirmed":
                raise AuditError("confirm this application agent before starting work")
            elif suggested_read_only_scope:
                raise AuditError("read-only review scope requires a suggested identity")
            if goal_id:
                goal_record = self.catalog.goal_record(project_id, agent_id, goal_id)
            elif payload.get("kind") == "audit":
                goal_record = None
            else:
                raise AuditError("select a goal before starting this workflow")
            options = payload.setdefault("options", {})
            if not isinstance(options, dict):
                raise AuditError("workflow options must be an object")
            prepared_review_scope = options.pop("agent_review_scope", None)
            if prepared_review_scope is not None and (
                prepared_review_scope != suggested_read_only_scope
            ):
                raise AuditError("workflow evidence bindings are prepared by the application")
            if set(options) & {
                "investigation_plan",
                "suite_manifest",
                "measurement_design",
                "design_revision",
                "optimization_background",
                "design_context",
            }:
                raise AuditError("workflow evidence bindings are prepared by the application")
            if suggested_read_only_scope:
                options["agent_review_scope"] = copy.deepcopy(suggested_read_only_scope)
            payload["goal"] = payload.get("goal") or (
                goal_record["objective"] if goal_record else f"Audit {agent['name']}"
            )
            scopes = options.get("code_scopes")
            if scopes and scopes != agent["code_scopes"]:
                raise AuditError("update the agent binding before changing its audit code scope")
            options["code_scopes"] = agent["code_scopes"]
            for key in ("issue_id", "audit_id"):
                if goal_record and goal_record["source"].get(key):
                    options.setdefault(key, goal_record["source"][key])
            self.catalog.validate_evidence_reference(project_id, agent_id, options)
            if goal_record:
                plan = self.catalog.investigation(project_id, agent_id, goal_id, options)
            else:
                plan = {
                    "version": 1,
                    "agent_id": agent_id,
                    "binding_version": agent["binding_version"],
                    "binding_digest": agent["binding_digest"],
                    "goal_id": None,
                    "goal_version": None,
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
                self.designs.accepted(project_id, agent_id, goal_id)
                if goal_record and payload["kind"] in {"eval", "baseline", "optimize"}
                else None
            )
            if payload["kind"] == "design":
                draft = self.designs.get(project_id, agent_id, goal_id)
                options["design_revision"] = draft["revision"] if draft else 0
                options["design_context"] = self.measurement_design(project_id, agent_id, goal_id)
            elif accepted:
                if payload["kind"] == "eval" and accepted["native_plan"].get("id"):
                    from agentagon.capabilities.evaluation.native import validate_plan

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
                if payload["kind"] == "optimize":
                    options["optimization_background"] = self.designs.background(
                        accepted, goal_record["objective"]
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
            measurement = (goal_record.get("measurement") or {}) if goal_record else {}
            accepted_evaluator = accepted["evaluation"].get("evaluation_id") if accepted else None
            if (
                payload["kind"] in {"baseline", "optimize"}
                and measurement
                and not (payload["kind"] == "baseline" and accepted_evaluator)
            ):
                if (
                    options.get("evaluation_id")
                    and options["evaluation_id"] != measurement["evaluation_id"]
                ):
                    raise AuditError(
                        "Run a baseline for the accepted evaluator before starting a Fix."
                        if payload["kind"] == "optimize" and accepted_evaluator
                        else "selected evaluator differs from this goal_record's accepted measurement"
                    )
                options["evaluation_id"] = measurement["evaluation_id"]
            if payload["kind"] in {"baseline", "optimize"} and accepted:
                if not options.get("evaluation_id"):
                    raise AuditError("This measurement plan requires preparing a new evaluator.")
                self.designs.validate_evaluator(
                    self.state.workspace(project_id), accepted, options["evaluation_id"]
                )
            if payload["kind"] == "baseline" and options.get("baseline_id"):
                from agentagon.capabilities.experiments import baselines

                previous = baselines.status(
                    self.state.workspace(project_id), options["baseline_id"]
                )
                if (
                    options.get("evaluation_id")
                    and previous["evaluation_id"] != options["evaluation_id"]
                ):
                    raise AuditError("Selected baseline does not measure the accepted evaluator.")
            if payload["kind"] == "optimize":
                from agentagon.capabilities.experiments.checkouts import under

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
                    project_id, agent_id, goal_id, permitted_paths=options["permitted_paths"]
                )
                if suite["missing"]:
                    raise AuditError(
                        "Prepare evaluations and baselines for these active goals or narrow permitted changes: "
                        + ", ".join(m["name"] for m in suite["missing"])
                    )
                options["suite_manifest"] = suite
                if measurement.get("baseline_id"):
                    options["baseline_id"] = measurement["baseline_id"]
            return self.runtime.submit(project_id, payload, request_binding=request_binding)

    def completed_job(self, workspace, job, result):
        """Attach validated engine evidence to the goal_record captured by this task."""
        if job["kind"] == "fix":
            from agentagon.workflows.outcomes import resolve_repair

            result = resolve_repair(workspace, job, result)
        if job["kind"] in {"assess", "observe"}:
            return self.production.complete(workspace, job, result)
        from agentagon.domain.improvements import retain

        result = retain(self, workspace, job, result)
        agent_id, goal_id = job.get("application_agent_id"), job.get("goal_id")
        if not agent_id or not goal_id:
            return result
        goal_record = self.catalog.goal_record(job["project_id"], agent_id, goal_id)
        plan = job["options"].get("investigation_plan", {})
        agent = self.catalog.agent(job["project_id"], agent_id)
        if (
            plan.get("goal_version") != goal_record["version"]
            or plan.get("binding_digest") != agent["binding_digest"]
        ):
            if job["kind"] == "design":
                raise AuditError("Goal or agent scope changed. Request a new measurement proposal.")
            return {
                **result,
                "measurement_note": "Goal changed during execution. Retained evidence needs an explicit measurement binding.",
            }
        if job["kind"] == "design":
            saved = self.designs.save(
                job["project_id"],
                agent_id,
                goal_id,
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
                goal_id,
                {
                    "evaluation_id": result["evaluation_id"],
                    "expected_revision": goal_record["revision"],
                },
                expected_binding_digest=plan.get("binding_digest"),
            )
        elif job["kind"] == "baseline" and result.get("baseline_id"):
            from agentagon.capabilities.experiments import baselines

            baseline = baselines.status(workspace, result["baseline_id"])
            self.catalog.bind_measurement(
                job["project_id"],
                agent_id,
                goal_id,
                {
                    "evaluation_id": baseline["evaluation_id"],
                    "baseline_id": baseline["baseline_id"],
                    "expected_revision": goal_record["revision"],
                },
                expected_binding_digest=plan.get("binding_digest"),
            )
        return result

    def codex_models(self):
        from agentagon.brain.adapters import codex_models

        models = codex_models(self.state.read()["agents"].get("codex_executable"))
        return {
            "models": models,
            "default_model": next((m["id"] for m in models if m["default"]), None),
        }

    def remove_project(self, project_id):
        with self.lock, self.runtime.condition:
            self.state.workspace(project_id)
            if any(key[0] == project_id for key in self.runtime.active):
                raise AuditError("pause or cancel this project's tasks before removing it")
            if any(j["state"] in ACTIVE for j in self.runtime.list(project_id)):
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
        from agentagon.capabilities.experiments import baselines, inspection

        workspace = self.state.workspace(project_id)
        initialized = (workspace.state / "workspace.json").exists()
        imported = snapshots.list_snapshots(workspace)
        issues = list_issues(workspace)
        safe_issues = [
            {
                k: v
                for k, v in issue.items()
                if k
                in {
                    "issue_id",
                    "agent_id",
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
            "tasks": [public_task(j) for j in self.runtime.list(project_id)],
            "datasets": [s for s in imported if s["kind"] == "dataset"],
            "dataset_splits": self.state.db.list_records(project_id, "dataset_splits"),
            "traces": [s for s in imported if s["kind"] == "traces"],
            "discovery": self.catalog.discovery_preferences(project_id),
            "settings": self.settings(project_id),
        }

    def result(self, project_id, kind, result_id):
        from agentagon.capabilities.experiments import baselines, inspection, preparation

        workspace = self.state.workspace(project_id)
        task = _result_task_context(self, project_id, kind, result_id)
        if kind == "audit":
            return {**_detail(workspace, result_id), "result_kind": "audit", "task": task}
        if kind == "eval":
            evaluation = preparation.load(workspace, result_id)
            result = inspection.evaluation_summary(evaluation)
            return {
                **result,
                "result_kind": "eval",
                "task": task,
                "deliveries": _static_delivery_projections(
                    workspace,
                    project_id,
                    kind,
                    result_id,
                    evaluation.get("deliveries"),
                ),
                "allowed_actions": ["prepare_local_delivery"]
                if result["state"] == "frozen"
                else [],
            }
        if kind in {"fix", "optimize"}:
            from agentagon.capabilities.experiments import engine, suites
            from agentagon.capabilities.experiments.store import load_run
            from agentagon.domain.improvements import selection_projection

            result = _run_detail(workspace, result_id)
            data = load_run(workspace, result_id)
            if data.get("suite"):
                from agentagon.capabilities.experiments.budget import BudgetLedger

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
                        from agentagon.capabilities.experiments import optimize_run

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
            comparison = result.setdefault("comparisons", {})
            verified_alternatives = []
            rejected_alternatives = []
            for candidate in comparison.get("alternatives", []):
                try:
                    engine._verified_evidence(workspace, data, data["candidates"][candidate["id"]])
                except (AuditError, KeyError, OSError):
                    rejected_alternatives.append(candidate["id"])
                else:
                    verified_alternatives.append(candidate)
            comparison["alternatives"] = verified_alternatives
            if rejected_alternatives:
                comparison["result"] = "verification_incomplete"
                result["limitations"] = [
                    *result.get("limitations", []),
                    "Candidate identity or verification evidence changed; review or rerun verification before selecting it.",
                ]
            for delivery in result.get("deliveries", []):
                delivery_id = delivery.get("delivery_id")
                if not isinstance(delivery_id, str):
                    continue
                identifier(delivery_id, "delivery")
                receipt_path = private_directory(workspace, "deliveries") / f"{delivery_id}.json"
                if not receipt_path.exists():
                    continue
                receipt = load_json(workspace.checked(receipt_path))
                if receipt.get("app_kind") != kind or receipt.get("app_source_id") != result_id:
                    continue
                delivery["artifact_urls"] = {
                    name: f"/api/projects/{project_id}/deliveries/{delivery_id}/{name}"
                    for name in receipt.get("artifacts", {})
                    if re.fullmatch(r"[a-z_]+", name)
                }
                for field in ("user_decision_id", "user_decision_revision"):
                    if field in receipt:
                        delivery[field] = receipt[field]
            return {
                **result,
                "result_kind": kind,
                "task": task,
                "selection": selection_projection(
                    self,
                    project_id,
                    kind,
                    result_id,
                    data,
                    {
                        candidate["id"]
                        for candidate in result.get("comparisons", {}).get("alternatives", [])
                    },
                ),
            }
        if kind == "baseline":
            return {
                **baselines.public_projection(baselines.status(workspace, result_id)),
                "result_kind": "baseline",
                "task": task,
            }
        if kind == "patch":
            from agentagon.capabilities.experiments import patches

            patch = patches.load(workspace, result_id)
            reviewed = patch["state"] == "reviewed_unmeasured"
            if reviewed:
                patches.verified(workspace, patch)
            return {
                "result_kind": "patch",
                "task": task,
                "patch_id": patch["patch_id"],
                "goal": patch["goal"],
                "state": patch["state"],
                "created_at": patch["created_at"],
                "updated_at": patch.get("updated_at"),
                "source_revision": patch.get("source_revision"),
                "source_digest": patch.get("source_digest"),
                "editable_paths": list(patch["plan"]["editable_paths"]),
                "checks": [
                    {"id": item["check_id"], "state": item["state"]} for item in patch["checks"]
                ],
                "independent_review": "pass" if reviewed else None,
                "measurement": "unavailable",
                "limitations": [
                    patch["reason_no_comparison"],
                    "Baseline comparison is unavailable; this is not a measured improvement.",
                ],
                "deliveries": _static_delivery_projections(
                    workspace,
                    project_id,
                    kind,
                    result_id,
                    patch.get("deliveries"),
                ),
                "allowed_actions": ["prepare_local_delivery"] if reviewed else [],
            }
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

    def decide_result(self, project_id, kind, result_id, payload):
        from agentagon.domain.improvements import decide

        self.state.project(project_id)
        return decide(self, project_id, kind, result_id, payload)

    def derive_dataset(self, project_id, payload):
        from agentagon.capabilities.evaluation import datasets

        if set(payload) - {"trace_snapshot_id", "application_agent_id", "selection"}:
            raise AuditError("unsupported dataset derivation fields")
        agent = self.catalog.agent(project_id, payload.get("application_agent_id"))
        workspace = self.state.workspace(project_id)
        source = snapshots.select_traces(
            workspace, project_id, payload.get("trace_snapshot_id"), agent["trace_selector"]
        )
        return datasets.derive(workspace, project_id, source["id"], payload.get("selection", {}))

    def propose_evaluation_case(self, project_id, snapshot_id, payload):
        from agentagon.capabilities.evaluation import datasets

        self.state.project(project_id)
        return datasets.propose_case(
            self.state.workspace(project_id), project_id, snapshot_id, payload
        )

    def export_dataset(self, project_id, snapshot_id, payload):
        from agentagon.capabilities.evaluation.native import export_bundle

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
        from agentagon.capabilities.evaluation.datasets import assert_development

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
        from agentagon.capabilities.evaluation.native import preview_publication

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
        from agentagon.capabilities.evaluation.native import publish_dataset

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
        from agentagon.capabilities.experiments import controls
        from agentagon.capabilities.experiments.store import load_run

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
            from agentagon.capabilities.experiments import suites

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
            for j in self.runtime.list(project_id)
            if j["kind"] == "optimize"
            and j["options"].get("suite_manifest")
            and run_id
            in {j.get("workflow_ids", {}).get("run_id"), j.get("result", {}).get("run_id")}
        ]

    def deliver(self, project_id, payload):
        from agentagon.capabilities.experiments.delivery import deliver

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
        if kind not in {"fix", "optimize", "eval", "patch"} or not isinstance(source_id, str):
            raise AuditError("select a measured result, frozen evaluation, or reviewed patch")
        publish = payload.get("publish", False)
        if type(publish) is not bool:
            raise AuditError("publication must be explicitly true or false")
        if publish and not (payload.get("remote") and payload.get("base")):
            raise AuditError(
                "review the prepared package and specify remote and base before publishing"
            )
        workspace = self.state.workspace(project_id)
        decision = None
        if kind in {"fix", "optimize"}:
            from agentagon.capabilities.experiments import suites
            from agentagon.domain.improvements import require_user_selection

            for job in self._suite_jobs(project_id, source_id):
                if job["state"] not in {"completed", "completed_with_limits"}:
                    raise AuditError(
                        "finish this task's required verification before preparing delivery"
                    )
                suites.verify_completed(workspace, source_id, job["options"]["suite_manifest"])
            decision = require_user_selection(self, project_id, kind, source_id)
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
        source_argument = {
            "fix": "run_id",
            "optimize": "run_id",
            "eval": "evaluation_id",
            "patch": "patch_id",
        }[kind]
        result = deliver(
            workspace,
            **{source_argument: source_id},
            publish=publish,
            remote=payload.get("remote") or "origin",
            base=payload.get("base"),
            eval_parent_id=payload.get("eval_parent_id"),
            prepared_delivery_id=payload.get("delivery_id") if publish else None,
        )
        record = {
            **result,
            "app_kind": kind,
            "app_source_id": source_id,
            **(
                {
                    "user_decision_id": decision["id"],
                    "user_decision_revision": decision["revision"],
                    "user_decision_operation_id": decision["operation_id"],
                }
                if decision
                else {}
            ),
        }
        for name, path in record["artifacts"].items():
            workspace.checked(Path(path))
            if not re.fullmatch(r"[a-z_]+", name):
                raise AuditError("invalid delivery artifact name")
        delivery_id = record["delivery_id"]
        identifier(delivery_id, "delivery")
        workspace.write(private_directory(workspace, "deliveries") / f"{delivery_id}.json", record)
        return {
            **result,
            **(
                {
                    "user_decision_id": decision["id"],
                    "user_decision_revision": decision["revision"],
                }
                if decision
                else {}
            ),
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
        value.setdefault("status", "not_tested")
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

    def import_trace(self, project_id, payload):
        from agentagon.capabilities.traces.imports import import_trace

        return import_trace(self, project_id, payload)

    def preview_trace_import(self, project_id, payload):
        from agentagon.capabilities.traces.imports import preview_trace

        return preview_trace(self, project_id, payload)

    def import_preview(self, project_id, payload):
        if not isinstance(payload, dict) or set(payload) != {"preview_id", "operation_id"}:
            raise AuditError("confirm one preview with an operation ID")
        op = operation_id(payload.get("operation_id"))
        workspace = self.state.workspace(project_id)
        with self.lock:
            existing = None
            for record in snapshots.list_snapshots(workspace):
                provenance = record["provenance"]
                saved_binding = provenance.get("import_operation") or {}
                saved_operation = provenance.get("operation_id") or saved_binding.get(
                    "operation_id"
                )
                if saved_operation != op:
                    continue
                if provenance.get("operation_id") == op:
                    raise AuditError("operation_id already belongs to another import")
                if saved_binding.get("project_id") != project_id:
                    raise AuditError("operation_id already belongs to another import")
                existing = record
                if provenance.get("preview_id") == payload["preview_id"]:
                    return record
                break
            preview = self.previews.get(payload.get("preview_id"))
            if not preview or preview["project_id"] != project_id:
                raise AuditError("preview expired or belongs to another project; preview again")
            content_digest = digest(
                {
                    key: preview.get(key)
                    for key in (
                        "kind",
                        "connection_id",
                        "selection",
                        "items",
                        "provenance",
                        "completeness",
                    )
                }
            )
            if existing:
                if (
                    existing["provenance"].get("import_operation", {}).get("content_digest")
                    == content_digest
                ):
                    self.previews.pop(payload["preview_id"], None)
                    return existing
                raise AuditError("operation_id already belongs to another import")
            if preview["kind"] == "traces":
                from agentagon.capabilities.traces.detail import trace_usability

                usability = trace_usability(preview)
                if not usability["diagnosis_ready"]:
                    raise AuditError("trace preview has no diagnosis-ready spans; preview again")
            workspace.initialize()
            preview = copy.deepcopy(preview)
            binding = {
                "project_id": project_id,
                "operation_id": op,
                "content_digest": content_digest,
            }
            preview["provenance"].update(import_operation=binding, preview_id=payload["preview_id"])
            saved = snapshots.summary(snapshots.save(workspace, project_id, preview))
            self.previews.pop(payload["preview_id"], None)
            return saved

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
                for agent in detect_backends()
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
                from agentagon.brain.adapters import validate_codex_model

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
            with self.runtime.condition:
                try:
                    with self.state.locked() as data:
                        data["agents"] = settings
                except Exception:
                    if reference and reference != previous_reference:
                        self.credentials.delete(reference)
                    raise
                if previous_reference and previous_reference != reference:
                    self.credentials.delete(previous_reference)
                self.runtime._dispatch()
            return self.agents()

    def close(self):
        self.scheduler.close()
        self.runtime.close()
        with self.lock:
            for discovery_id in list(self.connection_discoveries):
                self._discard_discovery(discovery_id)
        for reference in self._intelligence_refs.values():
            self.credentials.delete(reference)
        self._intelligence_refs.clear()
