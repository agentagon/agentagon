"""Durable app-owned jobs with project isolation and explicit interruption recovery."""

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from agentagon.capabilities.traces import snapshots
from agentagon.core.records import AuditError, digest, now
from agentagon.domain import tasks as task_facts
from agentagon.storage.config import Config
from agentagon.storage.state import identifier, private_directory
from agentagon.workflows import procedures as workflows

ACTIVE = {"queued", "running", "needs_input"}
TERMINAL = {"completed", "completed_with_limits", "failed", "cancelled"}
BUDGET_DEFAULTS = {"max_trials": 24, "max_elapsed_seconds": 1800, "trial_timeout_seconds": 60}


def task_sandbox(job):
    if job["kind"] in {"design", "discover", "assess", "observe"}:
        return "read-only"
    if job["kind"] == "audit" and job.get("options", {}).get("agent_review_scope"):
        return "read-only"
    return "workspace-write"


def operation_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AuditError("operation_id must be a UUID") from exc


def public_task(job, *, host_active=None):
    host_active = bool(job.get("attempt_started_at")) if host_active is None else host_active
    result = {
        k: copy.deepcopy(v)
        for k, v in job.items()
        if k
        not in {
            "frozen_settings",
            "execution_profile",
            "request_binding",
            "receipts",
            "agent_settings",
            "attempt_started_at",
            "answers",
            "start_intent",
        }
    }
    result.update(
        accounting=task_facts.accounting(job),
        recovery=task_facts.recovery(job, host_active=host_active),
        available_actions=task_facts.available_actions(job, host_active=host_active),
    )
    return result


class TaskRuntime:
    def __init__(self, state, credentials, execute=None):
        from agentagon.brain.adapters import run_agent

        self.state = state
        self.credentials = credentials
        self.execute = execute or run_agent
        self.condition = threading.Condition(threading.RLock())
        self.active = {}
        self.slots = {}
        self.question_locks = {}
        self.answers = {}
        self.clocks = {}
        self.waiters = {}
        self.threads = set()
        self.stopping = False
        self.verify_result = lambda workspace, job, result: result
        self.record_outcome = lambda workspace, job: None
        self.prepare_task = lambda workspace, job, cancelled: None
        # Starting the service reconciles records; it never restarts agent work.
        for project in self.state.read()["projects"].values():
            try:
                for job in self.list(project["id"]):
                    unfinished = job["state"] in {"queued", "running"} or bool(job.get("question"))
                    charged = self._charge_attempt(job)
                    if unfinished:
                        job.update(
                            state="interrupted",
                            question=None,
                            next_action="Resume this task to reconcile its saved session.",
                        )
                    if unfinished or charged:
                        self._write(job)
            except (AuditError, OSError):
                continue
        self.accounting_thread = threading.Thread(
            target=self._account_loop, daemon=True, name="agentagon-task-accounting"
        )
        self.accounting_thread.start()

    def _phase(self, job):
        key = (job["project_id"], job["id"])
        if key in self.active:
            sessions = min(job.get("host_slots", 1), max(1, len(job.get("active_reflections", []))))
            return "waiting" if self.waiters.get(key, 0) >= sessions else "active"
        return {"queued": "queued", "paused": "paused", "needs_input": "waiting"}.get(job["state"])

    def _account_loop(self):
        with self.condition:
            while not self.stopping:
                delay = 5
                for project_id, task_id in list(self.active):
                    try:
                        job = self._read(project_id, task_id)
                        clock = self.clocks.get((project_id, task_id))
                        if not clock:
                            continue
                        elapsed = time.monotonic() - clock[0]
                        remaining = task_facts.accounting(job)["remaining_seconds"]
                        if elapsed >= 5 or (clock[1] == "active" and elapsed >= remaining):
                            self._write(job)
                            remaining = task_facts.accounting(job)["remaining_seconds"]
                            if remaining <= 0 and job["state"] in {"running", "needs_input"}:
                                job.update(
                                    state="failed",
                                    question=None,
                                    next_action="Task time budget exhausted. Start another attempt with explicit limits.",
                                )
                                job.pop("continue_after_guidance", None)
                                self._write(job)
                                self.active[(project_id, task_id)].set()
                        latest_clock = self.clocks[(project_id, task_id)]
                        if latest_clock[1] == "active" and job["state"] in {
                            "running",
                            "needs_input",
                        }:
                            pending = time.monotonic() - latest_clock[0]
                            delay = min(delay, max(0.01, remaining - pending))
                    except (AuditError, OSError):
                        continue
                self.condition.wait(timeout=delay)

    def _read(self, project_id, job_id):
        identifier(job_id, "task")
        job = self.state.db.get_record(project_id, "tasks", job_id)
        if job is None:
            raise AuditError("task not found in this project")
        return job

    def _write(self, job, transaction=None):
        key = (job["project_id"], job["id"])
        timestamp, tick = now(), time.monotonic()
        timing = dict(job.get("accounting") or {})
        previous = self.clocks.get(key)
        if previous:
            elapsed, phase = max(0, tick - previous[0]), previous[1]
        else:
            phase = timing.get("phase")
            elapsed = task_facts.seconds_between(timing.get("checkpoint_at"), timestamp)
            # Abandoned live attempts are reconciled by _charge_attempt on startup.
            if phase not in {"queued", "paused", "waiting"}:
                elapsed = 0
        if phase:
            field = f"{phase}_seconds"
            timing[field] = float(timing.get(field, 0)) + elapsed
            if phase == "active":
                job["elapsed_seconds"] += elapsed
        timing.update(
            active_seconds=job.get("elapsed_seconds", 0),
            checkpoint_at=timestamp,
            phase=self._phase(job),
        )
        job["accounting"] = timing
        saved = (transaction or self.state.db).put_record(
            job["project_id"], "tasks", job["id"], job
        )
        job.update(revision=saved["revision"], updated_at=saved["updated_at"])
        if key in self.active:
            self.clocks[key] = (tick, timing["phase"])
        with self.condition:
            self.condition.notify_all()

    def get(self, project_id, job_id):
        with self.condition:
            return public_task(
                self._read(project_id, job_id), host_active=(project_id, job_id) in self.active
            )

    def list(self, project_id):
        return self.state.db.list_records(project_id, "tasks")

    def existing_submission(self, project_id, operation, request_digest):
        operation = operation_id(operation)
        if not isinstance(request_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", request_digest):
            raise AuditError("invalid task request digest")
        job_id = "task_" + hashlib.sha256(f"{project_id}:{operation}".encode()).hexdigest()[:24]
        existing = self.state.db.get_record(project_id, "tasks", job_id)
        if existing is None:
            return None
        if existing["request_binding"] != request_digest:
            raise AuditError("operation_id was already used for different task settings")
        return public_task(existing)

    def submit(self, project_id, payload, *, request_binding=None, start_context=None):
        if not isinstance(payload, dict):
            raise AuditError("task must be an object")
        if set(payload) - {
            "operation_id",
            "kind",
            "goal",
            "agent",
            "model",
            "options",
            "application_agent_id",
            "goal_id",
            "workflow_version",
            "input",
            "memory_snapshot",
            "improvement_memory",
        }:
            raise AuditError("unsupported task fields")
        operation = operation_id(payload.get("operation_id"))
        kind = payload.get("kind")
        if kind not in workflows.REFERENCES:
            raise AuditError("choose design, audit, eval, fix, or baseline")
        job_id = "task_" + hashlib.sha256(f"{project_id}:{operation}".encode()).hexdigest()[:24]
        request_binding = digest(payload) if request_binding is None else request_binding
        with self.condition:
            existing = self.existing_submission(project_id, operation, request_binding)
            if existing is not None:
                return existing
        goal = payload.get("goal", "")
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
            raise AuditError("provide a goal of 1–4000 characters")
        options = copy.deepcopy(payload.get("options", {}))
        if not isinstance(options, dict):
            raise AuditError("task options must be an object")
        allowed = {
            "scope",
            "mode",
            "code_scopes",
            "trace_snapshot_id",
            "dataset_snapshot_id",
            "evaluation_id",
            "baseline_id",
            "issue_id",
            "audit_id",
            "profile",
            "max_trials",
            "max_elapsed_seconds",
            "trial_timeout_seconds",
            "permitted_paths",
            "engine",
            "investigation_plan",
            "suite_manifest",
            "measurement_design",
            "design_revision",
            "design_context",
            "optimization_background",
            "finalist_count",
            "host_concurrency",
            "assessment",
            "monitor_policy",
            "window",
            "scheduled",
            "agent_review_scope",
        }
        if set(options) - allowed:
            raise AuditError("unsupported task options")
        for key in ("application_agent_id", "goal_id"):
            value = payload.get(key)
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"[a-z_]+_[a-f0-9]{24}", value)
            ):
                raise AuditError(f"invalid {key}")
        for key in (
            "investigation_plan",
            "suite_manifest",
            "measurement_design",
            "agent_review_scope",
        ):
            value = options.get(key)
            if value is not None and (
                not isinstance(value, dict) or len(json.dumps(value).encode()) > 1_000_000
            ):
                raise AuditError(f"{key} must be a bounded object")
        for key, default in (("finalist_count", 3), ("host_concurrency", 1)):
            value = options.get(key, default)
            if type(value) is not int or not 1 <= value <= 10:
                raise AuditError(f"{key} must be an integer between 1 and 10")
            if kind == "optimize":
                options[key] = value
        for key, default in BUDGET_DEFAULTS.items():
            value = options.get(key, default)
            if type(value) is not int or not 1 <= value <= 86400:
                raise AuditError(f"{key} must be an integer between 1 and 86400")
            options[key] = value
        for key in ("code_scopes", "permitted_paths"):
            from agentagon.capabilities.experiments.spec import path

            values = options.get(key, [])
            if not isinstance(values, list) or len(values) > 100:
                raise AuditError(f"{key} must be a bounded list of relative paths")
            for value in values:
                path(value)
                if value.startswith(".agentagon"):
                    raise AuditError("private state is not an editable code scope")
        if options.get("scope", "application") not in {"application", "changes", "traces"}:
            raise AuditError("unsupported audit scope")
        if options.get("mode", "code") not in {"code", "combined", "traces"}:
            raise AuditError("unsupported audit mode")
        if options.get("engine", "omni") not in {"omni", "gepa", "autoresearch", "meta_harness"}:
            raise AuditError("unsupported optimizer engine")
        workspace = self.state.workspace(project_id)
        self._validate_references(workspace, options)
        frozen = Config().effective(workspace.root)
        execution_profile = None
        profile = options.get("profile")
        if profile is not None and not isinstance(profile, str):
            raise AuditError("execution profile must be a name")
        if kind not in {"audit", "design", "discover", "assess", "observe"}:
            if not profile and options.get("evaluation_id"):
                from agentagon.capabilities.experiments.preparation import load

                profile = load(workspace, options["evaluation_id"])["profile_name"]
            if not profile and options.get("baseline_id"):
                from agentagon.capabilities.experiments.baselines import status

                profile = status(workspace, options["baseline_id"])["profile_name"]
            if not profile and len(frozen["profiles"]) == 1:
                profile = next(iter(frozen["profiles"]))
            if not profile or profile not in frozen["profiles"]:
                raise AuditError(
                    "Choose a configured execution profile in Settings before starting this workflow."
                )
            options["profile"] = profile
            execution_profile = copy.deepcopy(frozen["profiles"][profile])
            for key in BUDGET_DEFAULTS:
                options[key] = min(options[key], execution_profile["limits"][key])
                execution_profile["limits"][key] = options[key]
            execution_profile["limits"]["parallel_trials"] = min(
                execution_profile["limits"]["parallel_trials"], options["max_trials"]
            )
        settings = self.state.read()["agents"]
        if not settings.get("claude_api_key_ref") and os.environ.get("ANTHROPIC_API_KEY"):
            settings["claude_api_key_ref"] = "env:ANTHROPIC_API_KEY"
        agent = payload.get("agent") or settings["default_agent"]
        model = payload.get("model", settings["models"].get(agent, ""))
        if agent not in {"codex", "claude"} or not isinstance(model, str) or len(model) > 200:
            raise AuditError("invalid coding agent or model")
        with self.condition:
            workspace.initialize()
            from agentagon.memory.store import MemoryGroups

            memory = MemoryGroups(self.state)
            if not payload.get("memory_snapshot"):
                payload = copy.deepcopy(payload)
                payload["memory_snapshot"] = memory.snapshot(
                    project_id, payload.get("application_agent_id")
                )
            if not payload.get("improvement_memory"):
                groups = memory.improvement_groups(project_id, payload.get("application_agent_id"))
                payload["improvement_memory"] = [
                    memory.recall(project_id, g["id"], goal, payload.get("application_agent_id"))
                    for g in groups
                ]
            job = {
                "version": 1,
                "id": job_id,
                "project_id": project_id,
                "application_agent_id": payload.get("application_agent_id"),
                "goal_id": payload.get("goal_id"),
                "kind": kind,
                "workflow_version": payload.get("workflow_version", 1),
                "goal": goal.strip(),
                "agent": agent,
                "model": model,
                "state": "queued",
                "options": options,
                "input": copy.deepcopy(payload.get("input")),
                "memory_snapshot": copy.deepcopy(payload.get("memory_snapshot")),
                "improvement_memory": copy.deepcopy(payload.get("improvement_memory", [])),
                "request_binding": request_binding,
                "created_at": now(),
                "events": [],
                "messages": [],
                "receipts": {},
                "answers": {},
                "session_id": None,
                "question": None,
                "workflow_ids": {},
                "review_tasks": {},
                "active_review_id": None,
                "active_reflections": [],
                "reflection_tasks": {},
                "frozen_settings": frozen,
                "execution_profile": execution_profile,
                "agent_settings": settings,
                "elapsed_seconds": 0,
                "next_action": "Waiting for coding-agent capacity.",
            }
            if start_context:
                job["start_intent"] = copy.deepcopy(start_context["intent"])
                if start_context.get("goal_run"):
                    context = start_context["goal_run"]
                    job["goal_run_id"] = context["id"]
                    job["goal"] += (
                        "\n\nContinue this goal automatically through its current stage. "
                        "Preserve existing required behaviors, scored metric definitions and regression guards. "
                        "Ask only for genuinely missing inputs or incompatible correctness decisions. "
                        "Do not publish, merge or deploy."
                    )
                    if context.get("details"):
                        job["goal"] += "\n\nAdditional goal details: " + context["details"]
                if start_context.get("continuation_of"):
                    job["continuation_of"] = start_context["continuation_of"]
                    if start_context.get("preparation"):
                        job["preparation"] = copy.deepcopy(start_context["preparation"])
                        if job["preparation"].get("snapshot_id"):
                            job["options"]["trace_snapshot_id"] = job["preparation"]["snapshot_id"]
            with self.state.db.transaction() as transaction:
                existing = transaction.get_record(project_id, "tasks", job_id)
                if existing is not None:
                    if existing["request_binding"] != request_binding:
                        raise AuditError(
                            "operation_id was already used for different task settings"
                        )
                    return public_task(existing)
                self._write(job, transaction)
            self._dispatch()
            return public_task(job)

    @staticmethod
    def _validate_references(workspace, options):
        from agentagon.capabilities.experiments import baselines, preparation
        from agentagon.domain.issues import list_issues

        for key, prefix in (
            ("audit_id", "audit"),
            ("evaluation_id", "eval"),
            ("baseline_id", "baseline"),
            ("issue_id", "issue"),
            ("trace_snapshot_id", "snapshot"),
            ("dataset_snapshot_id", "snapshot"),
        ):
            if options.get(key) is not None:
                identifier(options[key], prefix)

        for key in ("trace_snapshot_id", "dataset_snapshot_id"):
            if options.get(key):
                saved = snapshots.load(workspace, options[key])
                expected = "traces" if key.startswith("trace") else "dataset"
                if saved["kind"] != expected:
                    raise AuditError("import kind does not match the selected workflow input")
                if expected == "dataset":
                    from agentagon.capabilities.evaluation.datasets import assert_development

                    assert_development(workspace, saved)
                if saved.get("project_id") != workspace.project_id:
                    raise AuditError("import belongs to another project")
        if options.get("audit_id"):
            workspace.read_audit(options["audit_id"])
        if options.get("evaluation_id"):
            from agentagon.capabilities.evaluation.datasets import assert_development_evaluator

            assert_development_evaluator(workspace, options["evaluation_id"])
            preparation.load(workspace, options["evaluation_id"])
        if options.get("baseline_id"):
            from agentagon.capabilities.evaluation.datasets import assert_development_evaluator

            baseline = baselines.status(workspace, options["baseline_id"])
            assert_development_evaluator(workspace, baseline["evaluation_id"])
        if options.get("issue_id") and options["issue_id"] not in {
            i["issue_id"] for i in list_issues(workspace)
        }:
            raise AuditError("issue not found in this project")

    def _dispatch(self):
        if self.stopping:
            return
        settings = self.state.read()
        capacity = settings["agents"]["concurrency"]
        active_projects = {project for project, _ in self.active}
        for project_id in settings["projects"]:
            available = capacity - sum(self.slots.get(key, 1) for key in self.active)
            if available <= 0:
                return
            if project_id in active_projects:
                continue
            try:
                queued = [j for j in reversed(self.list(project_id)) if j["state"] == "queued"]
            except (OSError, AuditError):
                continue
            if not queued:
                continue
            job = queued[0]
            slots = job.get("host_slots") or min(
                job["options"].get("host_concurrency", 1), capacity
            )
            if slots > available:
                if slots > capacity:
                    message = (
                        f"Restore coding-agent capacity to at least {slots} to resume this task."
                    )
                    if job["next_action"] != message:
                        job["next_action"] = message
                        self._write(job)
                continue
            job["host_slots"] = slots
            self._write(job)
            key = (project_id, job["id"])
            cancel = threading.Event()
            self.active[key] = cancel
            self.slots[key] = slots
            thread = threading.Thread(target=self._run, args=(*key, cancel), daemon=True)
            self.threads.add(thread)
            thread.start()

    def _prepare(self, workspace, job):
        from agentagon.workflows.registry import handler

        prepare = getattr(handler(job["kind"]), "prepare", None)
        if prepare is not None:
            prepare(workspace, job, self._write)

    def _run(self, project_id, job_id, cancelled):
        request = {}
        try:
            with self.condition:
                job = self._read(project_id, job_id)
                if job["state"] != "queued":
                    return
                workspace = self.state.workspace(project_id)
                job.update(state="running", next_action=None, attempt_started_at=now())
                self._write(job)
                workflows.freeze_settings(workspace, job)
                remaining = task_facts.accounting(job)["remaining_seconds"]
                if remaining <= 0:
                    raise AuditError(
                        "task time budget exhausted; start a new task with explicit limits"
                    )
            automatic = self.prepare_task(workspace, job, cancelled)
            with self.condition:
                job = self._read(project_id, job_id)
                if cancelled.is_set() or job["state"] != "running":
                    return
                self._write(job)
                remaining = task_facts.accounting(job)["remaining_seconds"]
                if remaining <= 0:
                    raise AuditError(
                        "task time budget exhausted; start a new task with explicit limits"
                    )
                request = {
                    "agent": job["agent"],
                    "model": job.get("actual_model") or job["model"],
                    "cwd": str(workspace.root),
                    "session_id": job.get("session_id"),
                    "prompt": workflows.prompt(workspace, job),
                    "sandbox": task_sandbox(job),
                    "timeout_seconds": remaining,
                    "env": {
                        "AGENTAGON_APP_STATE": str(self.state.directory),
                        **(
                            {
                                "AGENTAGON_MEMORY_SNAPSHOT": str(
                                    workspace.root / job["memory_snapshot"]["path"]
                                )
                            }
                            if job.get("memory_snapshot")
                            else {}
                        ),
                        "AGENTAGON_CONFIG": str(
                            private_directory(workspace, "task-inputs") / f"{job['id']}-config.json"
                        ),
                    },
                }
                executable = job["agent_settings"].get(job["agent"] + "_executable")
                if executable:
                    request["executable"] = executable
                if job["agent"] == "claude" and automatic is None:
                    current_agents = self.state.read()["agents"]
                    ref = current_agents.get("claude_api_key_ref") or (
                        "env:ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else None
                    )
                    if not ref:
                        raise AuditError("Configure a Claude API key in Coding agents.")
                    request["api_key"] = self.credentials.resolve(ref)
                    job["agent_settings"]["claude_api_key_ref"] = ref
                    self._write(job)
            outcome = (
                automatic
                if automatic is not None
                else self._advance(project_id, job_id, workspace, request, cancelled)
            )
            with self.condition:
                job = self._read(project_id, job_id)
                if job["state"] in {"paused", "cancelled", "interrupted", "failed"}:
                    return
                if outcome.get("state") != "completed":
                    job.update(
                        state="interrupted", next_action="Resume the saved coding-agent session."
                    )
                else:
                    state, result, next_action = workflows.validate_result(workspace, job, outcome)
                    if state in {"completed", "completed_with_limits"}:
                        result = self.verify_result(workspace, job, result)
                    job.update(state=state, result=result, next_action=next_action)
                    key = {
                        "design": "design_id",
                        "audit": "audit_id",
                        "eval": "evaluation_id",
                        "optimize": "run_id",
                        "fix": "run_id",
                        "discover": "snapshot_id",
                        "baseline": "baseline_id",
                        "assess": "assessment_id",
                        "observe": "observation_id",
                    }[job["kind"]]
                    if result.get(key):
                        job["workflow_ids"][key] = result[key]
                job["session_id"] = outcome.get("session_id") or job.get("session_id")
                job["question"] = (
                    {
                        "id": uuid.uuid4().hex,
                        "kind": "input",
                        "text": job["next_action"],
                        "retained": True,
                    }
                    if job["state"] == "needs_input"
                    else None
                )
                self._write(job)
        except Exception as exc:
            with self.condition:
                job = self._read(project_id, job_id)
                if job["state"] not in {"paused", "cancelled", "interrupted", "failed"}:
                    message = (
                        str(exc) if isinstance(exc, AuditError) else f"{type(exc).__name__}: {exc}"
                    )
                    secrets = [
                        value
                        for name, value in os.environ.items()
                        if re.search(r"key|token|secret|password", name, re.I)
                    ]
                    secrets.append(request.get("api_key"))
                    for secret in secrets:
                        if secret:
                            message = message.replace(secret, "[redacted]")
                    job.update(state="failed", next_action=message, question=None)
                    self._write(job)
        finally:
            with self.condition:
                cancelled.set()  # Release outstanding approval waiters on timeout/failure too.
                job = self._read(project_id, job_id)
                self._write(job)
                job.pop("attempt_started_at", None)
                try:
                    self.record_outcome(workspace, job)
                except (AuditError, OSError, ValueError) as exc:
                    job["memory_note"] = f"Lesson recording failed: {exc}"
                    job["memory_retry_count"] = 1
                    job["memory_retry_at"] = now()

                self.active.pop((project_id, job_id), None)
                self.clocks.pop((project_id, job_id), None)
                self.waiters.pop((project_id, job_id), None)
                if job.pop("continue_after_guidance", False) and not self.stopping:
                    if task_facts.accounting(job)["remaining_seconds"] > 0:
                        job.update(
                            state="queued",
                            question=None,
                            next_action="Continuing with your guidance.",
                        )
                    else:
                        job.update(
                            state="failed",
                            question=None,
                            next_action="Task time budget exhausted. Start another attempt with explicit limits.",
                        )
                job["accounting"]["phase"] = None
                self._write(job)
                self.slots.pop((project_id, job_id), None)
                self.question_locks.pop((project_id, job_id), None)
                self.answers.pop((project_id, job_id), None)
                self.threads.discard(threading.current_thread())
                self._dispatch()

    def _advance(self, project_id, job_id, workspace, request, cancelled):
        """Own author/reviewer turns under one capacity slot and elapsed budget."""
        while True:
            with self.condition:
                job = self._read(project_id, job_id)
                if cancelled.is_set() or job["state"] != "running":
                    return {"state": "interrupted", "session_id": job.get("session_id")}
                self._write(job)
                remaining = task_facts.accounting(job)["remaining_seconds"]
                if remaining <= 0:
                    raise AuditError(
                        "task time budget exhausted; start a new task with explicit limits"
                    )
                review_id = job.get("active_review_id")
                task = job["review_tasks"][review_id] if review_id else None
                reflections = job["active_reflections"]
                current = {**request, "timeout_seconds": remaining}
                if task:
                    current.update(
                        session_id=task.get("session_id"),
                        sandbox="read-only",
                        model=task.get("model") or job.get("actual_model") or job["model"],
                        prompt=workflows.reviewer_prompt(workspace, job, task),
                    )
                    if task["kind"] == "native":
                        from agentagon.capabilities.experiments.host_bridge import HostBridge

                        pending = HostBridge(workspace, task["owner_id"]).start(
                            task["request_id"], timeout_seconds=remaining
                        )
                        if pending["state"] not in {"running", "completed"}:
                            raise AuditError("independent review request is no longer runnable")
                        if pending.get("deadline"):
                            current["timeout_seconds"] = min(
                                remaining, pending["deadline"] - time.time()
                            )
                            if current["timeout_seconds"] <= 0 and not task.get("response"):
                                raise AuditError(
                                    "independent review deadline expired; inspect retained evidence"
                                )
                else:
                    current.update(
                        session_id=job.get("session_id"),
                        model=job.get("actual_model") or job["model"],
                        prompt=workflows.prompt(workspace, job),
                        guidance_operations=[
                            item["operation_id"]
                            for item in job["messages"]
                            if item.get("delivery_state") == "pending"
                        ],
                    )
            if reflections:
                if not self._reflect_batch(project_id, job_id, workspace, job, request, cancelled):
                    return {"state": "interrupted", "session_id": job.get("session_id")}
                continue
            if task:
                if not task.get("response"):
                    outcome = self.execute(
                        current,
                        lambda event, review_id=review_id: self._emit(
                            project_id, job_id, event, review_id=review_id
                        ),
                        lambda question, review_id=review_id: (
                            {"decision": "decline"}
                            if question.get("kind") == "approval"
                            else self._ask(
                                project_id, job_id, {**question, "review_id": review_id}, cancelled
                            )
                        ),
                        cancelled,
                    )
                    if outcome.get("state") != "completed":
                        return {"state": "interrupted", "session_id": job.get("session_id")}
                    with self.condition:
                        job = self._read(project_id, job_id)
                        if cancelled.is_set() or job["state"] != "running":
                            return {"state": "interrupted", "session_id": job.get("session_id")}
                        task = job["review_tasks"][review_id]
                        response = workflows.result_object(outcome.get("text", ""))
                        if response.get("needs_input"):
                            task.update(
                                state="needs_input", next_action=str(response["needs_input"])
                            )
                            self._write(job)
                            return {
                                "state": "completed",
                                "session_id": job["session_id"],
                                "text": json.dumps({"needs_input": task["next_action"]}),
                            }
                        task.update(response=response, state="ready")
                        self._write(job)
                # A persisted response is submitted again after a crash; core replay
                # validation prevents double execution or replacement of a review.
                try:
                    review_state = workflows.apply_review(workspace, job, task)
                except AuditError as exc:
                    with self.condition:
                        job = self._read(project_id, job_id)
                        task = job["review_tasks"][review_id]
                        task.update(
                            state="needs_input",
                            rejected_response=task.pop("response"),
                            next_action=str(exc),
                        )
                        self._write(job)
                    raise
                with self.condition:
                    job = self._read(project_id, job_id)
                    job["review_tasks"][review_id]["state"] = review_state
                    job["active_review_id"] = None
                    job["messages"].append(
                        task_facts.message(
                            f"Application-managed independent review {review_id} is {review_state}. "
                            "Inspect its saved response. Continue the same workflow; a rejected evaluator "
                            "must be repaired and checked again before requesting a new review."
                        )
                    )
                    self._write(job)
                continue
            outcome = self.execute(
                current,
                lambda event: self._emit(project_id, job_id, event),
                lambda question: self._ask(project_id, job_id, question, cancelled),
                cancelled,
            )
            if outcome.get("state") != "completed":
                return outcome
            with self.condition:
                job = self._read(project_id, job_id)
                if cancelled.is_set() or job["state"] != "running":
                    return outcome
                reflections = workflows.reflection_handoffs(workspace, job, outcome.get("text", ""))
                if reflections:
                    for reflection in reflections:
                        reflection_key = reflection["owner_id"] + ":" + reflection["request_id"]
                        if reflection_key in job["reflection_tasks"]:
                            raise AuditError(
                                "this reflection is already finished; continue its workflow"
                            )
                    job["active_reflections"] = reflections
                    if reflections[0].get("run_id"):
                        job["workflow_ids"]["run_id"] = reflections[0]["run_id"]
                    self._write(job)
                    continue
                handoff = workflows.review_handoff(workspace, job, outcome.get("text", ""))
                if handoff is None:
                    return outcome
                if handoff.get("evaluation_id"):
                    job["workflow_ids"]["evaluation_id"] = handoff["evaluation_id"]
                if job["kind"] in {"optimize", "fix"} and handoff["kind"] in {
                    "native",
                    "candidate",
                }:
                    job["workflow_ids"]["run_id"] = handoff["workflow_run_id"]
                task = job["review_tasks"].setdefault(
                    handoff["id"],
                    {
                        **handoff,
                        "state": "pending",
                        "session_id": None,
                        "created_at": now(),
                    },
                )
                if task["state"] in {"rejected", "completed"}:
                    raise AuditError(
                        "this exact review is already finished; inspect its result and revalidate changed evidence"
                    )
                job["active_review_id"] = task["id"]
                self._write(job)

    def _reflect_batch(self, project_id, job_id, workspace, job, request, cancelled):
        from agentagon.brain.adapters import run_reflection

        def run(reflection):
            with self.condition:
                current = self._read(project_id, job_id)
                self._write(current)
                timeout = task_facts.accounting(current)["remaining_seconds"]
            if timeout <= 0 or cancelled.is_set():
                cancelled.set()
                return False
            reflected = run_reflection(
                workspace,
                reflection["owner_id"],
                reflection["request_id"],
                emit=lambda event: self._emit(project_id, job_id, event),
                ask=lambda question: self._ask(
                    project_id,
                    job_id,
                    {**question, "request_id": reflection["request_id"]},
                    cancelled,
                ),
                cancelled=cancelled,
                timeout_seconds=timeout,
                execute=self.execute,
                api_key=request.get("api_key"),
                executable=request.get("executable"),
                environment=request.get("env"),
                forbidden_session_id=job.get("session_id"),
            )
            if reflected["state"] != "completed":
                cancelled.set()
                return False
            with self.condition:
                saved = self._read(project_id, job_id)
                key = reflection["owner_id"] + ":" + reflection["request_id"]
                saved["reflection_tasks"][key] = {
                    **reflection,
                    "state": "completed",
                    "session_id": reflected.get("session_id"),
                }
                saved["active_reflections"] = [
                    item for item in saved["active_reflections"] if item != reflection
                ]
                saved["messages"].append(
                    task_facts.message(
                        f"Application-managed reflection {reflection['request_id']} is complete. Continue using its saved response."
                    )
                )
                self._write(saved)
            return True

        with ThreadPoolExecutor(max_workers=job["host_slots"]) as pool:
            futures = [pool.submit(run, item) for item in job["active_reflections"]]
            try:
                return all([future.result() for future in as_completed(futures)])
            except BaseException:
                cancelled.set()
                with self.condition:
                    self.condition.notify_all()
                raise

    def _emit(self, project_id, job_id, event, *, review_id=None):
        with self.condition:
            job = self._read(project_id, job_id)
            if event.get("type") == "guidance_delivered" and not review_id:
                supplied = set(event.get("operation_ids") or [])
                for item in job["messages"]:
                    if item.get("operation_id") in supplied:
                        item.update(
                            delivery_state="delivered",
                            delivered_at=now(),
                            delivered_session_id=event.get("session_id") or job.get("session_id"),
                        )
            if event.get("type") == "session" and review_id:
                task = job["review_tasks"][review_id]
                if event["session_id"] == job.get("session_id"):
                    raise AuditError(
                        "reviewer must run in a different native session from the author"
                    )
                if task.get("session_id") not in (None, event["session_id"]):
                    raise AuditError("reviewer resumed a different native session")
                task.update(
                    session_id=event["session_id"],
                    actual_model=event.get("model") or task.get("model"),
                    state="running",
                )
            elif event.get("type") == "session":
                if job.get("session_id") and job["session_id"] != event["session_id"]:
                    raise AuditError("coding host returned a different saved session")
                if (
                    job.get("actual_model")
                    and event.get("model")
                    and job["actual_model"] != event["model"]
                ):
                    raise AuditError("coding host changed the task's frozen model")
                job["session_id"] = event["session_id"]
                job["actual_model"] = (
                    event.get("model") or job.get("model") or job.get("actual_model")
                )
                self._write(job)
                workspace = self.state.workspace(project_id)
                self._prepare(workspace, job)
                workflows.write_context(workspace, job)
            event = copy.deepcopy(event)
            if review_id:
                event.update(role="reviewer", review_id=review_id)
            if "text" in event:
                event["text"] = str(event["text"])[-16000:]
            event["at"] = now()
            job["events"] = (job["events"] + [event])[-300:]
            self._write(job)
            if event.get("type") == "session" and review_id:
                workflows.write_context(self.state.workspace(project_id), job)

    def _ask(self, project_id, job_id, question, cancelled):
        with self.condition:
            lock = self.question_locks.setdefault((project_id, job_id), threading.Lock())
            key = (project_id, job_id)
            self._write(self._read(project_id, job_id))
            self.waiters[key] = self.waiters.get(key, 0) + 1
            self._write(self._read(project_id, job_id))
        # Concurrent native sessions may ask questions, but the browser answers one
        # exact question at a time. Do not hold the condition while waiting for this lock.
        try:
            with lock:
                return self._ask_one(project_id, job_id, question, cancelled)
        finally:
            with self.condition:
                self._write(self._read(project_id, job_id))
                self.waiters[key] = max(0, self.waiters.get(key, 1) - 1)
                self._write(self._read(project_id, job_id))

    def _ask_one(self, project_id, job_id, question, cancelled):
        with self.condition:
            job = self._read(project_id, job_id)
            if cancelled.is_set():
                return {"decision": "decline"}
            text = question.get("text") or question.get("reason") or question.get("prompt")
            if not text and question.get("questions"):
                text = "\n".join(str(q.get("question", "")) for q in question["questions"])
            if question.get("kind") == "approval":
                command = question.get("command")
                if command:
                    text = f"{text or 'Approve command?'}\n{command}"
                elif question.get("tool"):
                    text = f"{text or 'Approve tool?'} {question['tool']}"
            if not text:
                text = (
                    "Coding agent requests approval."
                    if question.get("kind") == "approval"
                    else "The coding agent needs your input."
                )
            job.update(
                state="needs_input",
                question={**question, "id": uuid.uuid4().hex, "text": text},
                next_action=text,
            )
            self._write(job)
            key = (project_id, job_id)
            while key not in self.answers and not cancelled.is_set():
                self.condition.wait(timeout=1)
            if cancelled.is_set():
                return {"decision": "decline"}
            answer = self.answers.pop(key)
            job = self._read(project_id, job_id)
            if job["state"] != "needs_input":
                return {"decision": "decline"}
            job["answers"][job["question"]["id"]]["consumed_at"] = now()
            for item in job["messages"]:
                if item.get("question_id") == job["question"]["id"]:
                    item.update(delivery_state="delivered", delivered_at=now())
            job.update(state="running", question=None, next_action=None)
            self._write(job)
            return answer

    def control(self, project_id, job_id, action, payload, *, goal_run_id=None):
        if action not in {"answer", "pause", "resume", "cancel", "message", "retry-memory"}:
            raise AuditError("unsupported task control")
        allowed = {"operation_id", "question_id", "answer", "message"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise AuditError("unsupported task control fields")
        op = operation_id(payload.get("operation_id"))
        binding = digest({"action": action, "payload": payload})
        if action == "retry-memory":
            return self._retry_memory(project_id, job_id, op, binding)
        with self.condition:
            key = (project_id, job_id)
            answer = None
            interrupt = False
            with self.state.db.transaction() as transaction:
                identifier(job_id, "task")
                job = transaction.get_record(project_id, "tasks", job_id)
                if job is None:
                    raise AuditError("task not found in this project")
                previous = job["receipts"].get(op)
                if previous:
                    if previous != binding:
                        raise AuditError("operation_id already belongs to a different task control")
                    return public_task(job)
                if action == "answer":
                    if (
                        job["state"] != "needs_input"
                        or not job.get("question")
                        or (key not in self.active and not job.get("question", {}).get("retained"))
                    ):
                        raise AuditError("this task has no live unanswered question")
                    question_id = job["question"]["id"]
                    if payload.get("question_id") != question_id:
                        raise AuditError(
                            "question changed; review the current request before replying"
                        )
                    answer = payload.get("answer")
                    if not isinstance(answer, dict) or len(json.dumps(answer).encode()) > 100_000:
                        raise AuditError("answer must be a bounded object")
                    if question_id in job["answers"]:
                        raise AuditError("this question already has an accepted response")
                    if (
                        job["question"].get("retained")
                        and task_facts.accounting(job)["remaining_seconds"] <= 0
                    ):
                        raise task_facts.TaskControlError(
                            "This task has no remaining time to act on an answer. Start another attempt with explicit limits.",
                            "budget_exhausted",
                            action=task_facts.recovery(job)["suggested_action"],
                        )
                    job["answers"][question_id] = {
                        "answer": copy.deepcopy(answer),
                        "accepted_at": now(),
                    }
                    job["messages"].append(
                        task_facts.message(
                            json.dumps({"question": job["question"]["text"], "answer": answer}),
                            role="user",
                            kind="answer",
                            operation_id=op,
                            question_id=question_id,
                        )
                    )
                    if len(job["messages"]) > 100:
                        raise AuditError("task guidance limit reached; start a new task")
                    if job["question"].get("retained"):
                        job.update(
                            state="queued",
                            question=None,
                            next_action="Continuing with your answer.",
                        )
                        answer = None
                    else:
                        job["question"]["answered"] = True
                elif action in {"pause", "cancel"}:
                    if job["state"] in TERMINAL:
                        raise AuditError("task has already finished")
                    job.update(
                        state="paused" if action == "pause" else "cancelled",
                        question=None,
                        next_action="Resume when ready." if action == "pause" else None,
                    )
                    interrupt = True
                    job.pop("continue_after_guidance", None)
                elif action == "resume":
                    if job.get("goal_run_id") and job["goal_run_id"] != goal_run_id:
                        raise task_facts.TaskControlError(
                            "Resume this work from its goal run so its shared scope and remaining allowance can be checked.",
                            "goal_run_owned",
                            action="resume_goal_run",
                        )
                    if key in self.active:
                        raise AuditError(
                            "wait for the previous agent process to stop before resuming"
                        )
                    status = task_facts.recovery(job)
                    if not status["resume_allowed"]:
                        raise task_facts.TaskControlError(
                            "This task cannot resume: its time allowance is exhausted or execution is unreconciled. Start another attempt with explicit limits."
                            if status["reason_code"]
                            in {"budget_exhausted", "execution_unreconciled"}
                            else "This task cannot resume; answer its current question or inspect its result.",
                            status["reason_code"] or "resume_unavailable",
                            action=status["suggested_action"],
                        )
                    job.update(
                        state="queued",
                        question=None,
                        next_action="Waiting for coding-agent capacity.",
                    )
                else:
                    message = payload.get("message")
                    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
                        raise AuditError("message must contain 1–4000 characters")
                    if job["state"] in TERMINAL:
                        raise AuditError("start a new task to follow up on a completed task")
                    job["messages"].append(
                        task_facts.message(
                            message.strip(), role="user", kind="guidance", operation_id=op
                        )
                    )
                    if len(job["messages"]) > 100:
                        raise AuditError("task guidance limit reached; start a new task")
                    if key in self.active:
                        interrupt = True
                        continue_running = job["state"] in {"running", "needs_input"} or bool(
                            job.get("continue_after_guidance")
                        )
                        job.update(
                            state="paused",
                            question=None,
                            next_action=(
                                "Guidance saved. Continuing after the current turn stops."
                                if continue_running
                                else "Guidance saved. Resume when ready."
                            ),
                            continue_after_guidance=continue_running,
                        )
                job["receipts"][op] = binding
                self._write(job, transaction)
            # Only release a waiter or interrupt a host after its receipt is durable.
            if answer is not None:
                self.answers[key] = copy.deepcopy(answer)
            if interrupt and key in self.active:
                self.active[key].set()
            self._dispatch()
            return public_task(job)

    def _retry_memory(self, project_id, job_id, operation, binding):
        """Retry only outcome-memory recording; retained workflow evidence is untouched."""

        with self.condition:
            job = self._read(project_id, job_id)
            previous = job["receipts"].get(operation)
            if previous:
                if previous != binding:
                    raise AuditError("operation_id already belongs to a different task control")
                return public_task(job)
            if job["state"] not in TERMINAL:
                raise AuditError("lesson recording can retry only after the task finishes")
            if not job.get("memory_note"):
                return public_task(job)
            retained = copy.deepcopy(job)
        try:
            self.record_outcome(self.state.workspace(project_id), retained)
        except (AuditError, OSError, ValueError) as exc:
            with self.condition:
                latest = self._read(project_id, job_id)
                latest["memory_retry_count"] = int(latest.get("memory_retry_count", 0)) + 1
                latest["memory_retry_at"] = now()
                latest["memory_note"] = f"Lesson recording failed: {exc}"
                self._write(latest)
            raise AuditError(f"lesson recording still needs attention: {exc}") from exc
        with self.condition:
            with self.state.db.transaction() as transaction:
                latest = transaction.get_record(project_id, "tasks", job_id)
                if latest is None:
                    raise AuditError("task not found in this project")
                latest.pop("memory_note", None)
                latest.pop("memory_retry_count", None)
                latest.pop("memory_retry_at", None)
                latest["receipts"][operation] = binding
                self._write(latest, transaction)
            return public_task(latest)

    @staticmethod
    def _charge_attempt(job):
        started = job.pop("attempt_started_at", None)
        if started is None:
            return False
        timing = dict(job.get("accounting") or {})
        elapsed = task_facts.seconds_between(timing.get("checkpoint_at") or started, now())
        timing["offline_seconds"] = float(timing.get("offline_seconds", 0)) + elapsed
        if timing.get("phase", "active") == "active":
            remaining = task_facts.accounting(job)["remaining_seconds"]
            timing["unknown_seconds"] = float(timing.get("unknown_seconds", 0)) + min(
                elapsed, remaining
            )
        timing.update(phase=None, checkpoint_at=now())
        job["accounting"] = timing
        job.pop("continue_after_guidance", None)
        return True

    def close(self):
        with self.condition:
            self.stopping = True
            for (project_id, job_id), cancel in list(self.active.items()):
                cancel.set()
                job = self._read(project_id, job_id)
                job.update(
                    state="interrupted",
                    question=None,
                    next_action="Restart Agentagon and resume this task.",
                )
                job.pop("continue_after_guidance", None)
                self._write(job)
            self.condition.notify_all()
            threads = list(self.threads)
        for thread in threads:
            thread.join(timeout=5)
        self.accounting_thread.join(timeout=5)
