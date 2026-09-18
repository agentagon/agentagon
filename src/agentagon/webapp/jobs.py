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
from datetime import datetime

from agentagon.core.records import AuditError, digest, load_json, now
from agentagon.storage.config import Config
from agentagon.webapp import snapshots, workflows
from agentagon.webapp.state import identifier, private_directory

ACTIVE = {"queued", "running", "needs_input"}
TERMINAL = {"completed", "completed_with_limits", "failed", "cancelled"}
BUDGET_DEFAULTS = {"max_trials": 24, "max_elapsed_seconds": 1800, "trial_timeout_seconds": 60}


def operation_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AuditError("operation_id must be a UUID") from exc


def public_job(job):
    return {
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
            "attempt_wait_seconds",
            "answers",
            "input_history",
            "pending_guidance",
        }
    }


class JobManager:
    def __init__(self, state, credentials, execute=None):
        from agentagon.webapp.agents import run_agent

        self.state = state
        self.credentials = credentials
        self.execute = execute or run_agent
        self.condition = threading.Condition(threading.RLock())
        self.active = {}
        self.slots = {}
        self.question_locks = {}
        self.answers = {}
        self.threads = set()
        self.stopping = False
        self.verify_result = lambda workspace, job, result: result
        # Starting the service reconciles records; it never restarts agent work.
        for project in self.state.read()["projects"].values():
            try:
                for job in self.list(project["id"]):
                    question = job.get("question")
                    durable = question and question.get("source") == "agentagon"
                    migrated = False
                    unfinished = job["state"] in {"queued", "running"} or bool(
                        question and not durable
                    )
                    charged = self._charge_attempt(job)
                    if unfinished:
                        if question:
                            self._expire_question(
                                job,
                                "The coding-agent process ended before this request was answered.",
                            )
                        job.update(
                            state="interrupted",
                            next_action="Resume this task to reconcile its saved session.",
                        )
                    elif job["state"] == "needs_input" and not question:
                        self._set_durable_question(
                            job,
                            {
                                "kind": "blocker",
                                "text": job.get("next_action")
                                or "The task needs a decision before it can continue.",
                            },
                        )
                        migrated = True
                    if unfinished or charged or migrated:
                        self._write(job)
            except (AuditError, OSError):
                continue

    def _read(self, project_id, job_id):
        identifier(job_id, "job")
        job = self.state.db.get_record(project_id, "jobs", job_id)
        if job is None:
            raise AuditError("task not found in this project")
        return job

    def _write(self, job, transaction=None):
        saved = (transaction or self.state.db).put_record(job["project_id"], "jobs", job["id"], job)
        job.update(revision=saved["revision"], updated_at=saved["updated_at"])
        with self.condition:
            self.condition.notify_all()

    def get(self, project_id, job_id):
        with self.condition:
            return public_job(self._read(project_id, job_id))

    def list(self, project_id):
        return self.state.db.list_records(project_id, "jobs")

    @staticmethod
    def _wait_seconds(question):
        asked_at = question.get("asked_at") if isinstance(question, dict) else None
        if not asked_at:
            return 0
        try:
            return max(0, time.time() - datetime.fromisoformat(asked_at).timestamp())
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _record_wait(cls, job, question):
        if not question or question.get("wait_charged"):
            return
        job["attempt_wait_seconds"] = job.get("attempt_wait_seconds", 0) + cls._wait_seconds(
            question
        )
        question["wait_charged"] = True

    @classmethod
    def _expire_question(cls, job, reason):
        question = job.get("question")
        if not question:
            return
        cls._record_wait(job, question)
        job.setdefault("input_history", []).append(
            {
                **copy.deepcopy(question),
                "status": "expired",
                "expired_at": now(),
                "reason": reason,
            }
        )
        job["input_history"] = job["input_history"][-100:]
        job["question"] = None

    @staticmethod
    def _set_durable_question(job, request):
        text = request.get("text") or request.get("reason") or request.get("purpose")
        if not isinstance(text, str) or not text.strip():
            text = "The task needs a decision before it can continue."
        job.update(
            state="needs_input",
            question={
                **copy.deepcopy(request),
                "id": "input_" + uuid.uuid4().hex,
                "source": "agentagon",
                "durable": True,
                "asked_at": now(),
                "status": "pending",
                "text": text.strip(),
            },
            next_action=text.strip(),
        )

    @staticmethod
    def _input_text(value, fallback):
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ("text", "reason", "purpose", "next_action"):
                text = value.get(key)
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return fallback

    def _result_question(self, workspace, job, result, next_action):
        request = result.get("needs_input")
        if isinstance(request, dict) and request.get("kind") == "intelligence":
            from agentagon.lookup import owners

            workflow = request.get("workflow")
            owner_key = {
                "audit": "audit_id",
                "eval": "evaluation_id",
                "fix": "run_id",
            }.get(workflow)
            if owner_key is None or workflow != job["kind"]:
                raise AuditError("Intelligence approval belongs to a different task workflow")
            owner_id = request.get("owner_id") or job["workflow_ids"].get(owner_key)
            if owner_id != job["workflow_ids"].get(owner_key):
                raise AuditError("Intelligence approval belongs to a different workflow record")
            path = owners.approval_path(workspace, workflow, owner_id)
            prepared = load_json(workspace.checked(path)) if path.exists() else None
            approval_id = request.get("approval_id")
            if (
                not isinstance(prepared, dict)
                or prepared.get("status") != "pending"
                or prepared.get("approval_id") != approval_id
                or not isinstance(prepared.get("preview"), dict)
            ):
                raise AuditError("Intelligence approval is stale or does not match this task")
            preview = copy.deepcopy(prepared["preview"])
            return {
                "kind": "intelligence",
                "approval_id": approval_id,
                "owner_id": owner_id,
                **preview,
                "text": preview.get("purpose") or "Allow this AG Intelligence request?",
            }
        details = copy.deepcopy(request) if isinstance(request, dict) else {}
        kind = details.get("kind")
        if kind not in {"blocker", "question"}:
            kind = "blocker"
        return {
            **details,
            "kind": kind,
            "text": self._input_text(
                request,
                next_action or "The task needs a decision before it can continue.",
            ),
        }

    def existing_submission(self, project_id, operation, request_digest):
        operation = operation_id(operation)
        if not isinstance(request_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", request_digest):
            raise AuditError("invalid task request digest")
        job_id = "job_" + hashlib.sha256(f"{project_id}:{operation}".encode()).hexdigest()[:24]
        existing = self.state.db.get_record(project_id, "jobs", job_id)
        if existing is None:
            return None
        if existing["request_binding"] != request_digest:
            raise AuditError("operation_id was already used for different task settings")
        return public_job(existing)

    def submit(self, project_id, payload, *, request_binding=None):
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
            "focus_id",
            "workflow_version",
        }:
            raise AuditError("unsupported task fields")
        operation = operation_id(payload.get("operation_id"))
        kind = payload.get("kind")
        if kind not in workflows.REFERENCES:
            raise AuditError("choose design, audit, eval, fix, or baseline")
        job_id = "job_" + hashlib.sha256(f"{project_id}:{operation}".encode()).hexdigest()[:24]
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
        }
        if set(options) - allowed:
            raise AuditError("unsupported task options")
        for key in ("application_agent_id", "focus_id"):
            value = payload.get(key)
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"[a-z_]+_[a-f0-9]{24}", value)
            ):
                raise AuditError(f"invalid {key}")
        for key in ("investigation_plan", "suite_manifest", "measurement_design"):
            value = options.get(key)
            if value is not None and (
                not isinstance(value, dict) or len(json.dumps(value).encode()) > 1_000_000
            ):
                raise AuditError(f"{key} must be a bounded object")
        for key, default in (("finalist_count", 3), ("host_concurrency", 1)):
            value = options.get(key, default)
            if type(value) is not int or not 1 <= value <= 10:
                raise AuditError(f"{key} must be an integer between 1 and 10")
            if kind == "fix":
                options[key] = value
        for key, default in BUDGET_DEFAULTS.items():
            value = options.get(key, default)
            if type(value) is not int or not 1 <= value <= 86400:
                raise AuditError(f"{key} must be an integer between 1 and 86400")
            options[key] = value
        for key in ("code_scopes", "permitted_paths"):
            from agentagon.experiments.spec import path

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
        if kind not in {"audit", "design"}:
            if not profile and options.get("evaluation_id"):
                from agentagon.experiments.preparation import load

                profile = load(workspace, options["evaluation_id"])["profile_name"]
            if not profile and options.get("baseline_id"):
                from agentagon.experiments.baselines import status

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
        settings = self.state.read()["agents"]
        if not settings.get("claude_api_key_ref") and os.environ.get("ANTHROPIC_API_KEY"):
            settings["claude_api_key_ref"] = "env:ANTHROPIC_API_KEY"
        agent = payload.get("agent") or settings["default_agent"]
        model = payload.get("model", settings["models"].get(agent, ""))
        if agent not in {"codex", "claude"} or not isinstance(model, str) or len(model) > 200:
            raise AuditError("invalid coding agent or model")
        with self.condition:
            workspace.initialize()
            job = {
                "version": 1,
                "id": job_id,
                "project_id": project_id,
                "application_agent_id": payload.get("application_agent_id"),
                "focus_id": payload.get("focus_id"),
                "kind": kind,
                "workflow_version": payload.get("workflow_version", 1),
                "goal": goal.strip(),
                "agent": agent,
                "model": model,
                "state": "queued",
                "options": options,
                "request_binding": request_binding,
                "created_at": now(),
                "events": [],
                "messages": [],
                "receipts": {},
                "answers": {},
                "input_history": [],
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
                "attempt_wait_seconds": 0,
                "pending_guidance": False,
                "next_action": "Waiting for coding-agent capacity.",
            }
            trace_id = options.get("trace_snapshot_id")
            trace_setting = frozen.get("traces", {}).get("state", "unset")
            if trace_id and trace_setting == "disabled":
                raise AuditError("Runtime traces are disabled for this project")
            if trace_id and trace_setting == "unset":
                trace = snapshots.load(workspace, trace_id)
                selection = trace.get("selection", {})
                provenance = trace.get("provenance", {})
                self._set_durable_question(
                    job,
                    {
                        "kind": "trace_access",
                        "text": "Allow this task to use the selected runtime traces?",
                        "request": {
                            "snapshot_id": trace_id,
                            "provider": provenance.get("provider"),
                            "project": selection.get("project") or provenance.get("project"),
                            "start": selection.get("start"),
                            "end": selection.get("end"),
                            "limit": selection.get("cap") or len(trace.get("items", [])),
                        },
                    },
                )
            with self.state.db.transaction() as transaction:
                existing = transaction.get_record(project_id, "jobs", job_id)
                if existing is not None:
                    if existing["request_binding"] != request_binding:
                        raise AuditError(
                            "operation_id was already used for different task settings"
                        )
                    return public_job(existing)
                self._write(job, transaction)
            self._dispatch()
            return public_job(job)

    @staticmethod
    def _validate_references(workspace, options):
        from agentagon.experiments import baselines, preparation
        from agentagon.storage.issues import list_issues

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
                    from agentagon.webapp.datasets import assert_development

                    assert_development(workspace, saved)
                if saved.get("project_id") != workspace.project_id:
                    raise AuditError("import belongs to another project")
        if options.get("audit_id"):
            workspace.read_audit(options["audit_id"])
        if options.get("evaluation_id"):
            from agentagon.webapp.datasets import assert_development_evaluator

            assert_development_evaluator(workspace, options["evaluation_id"])
            preparation.load(workspace, options["evaluation_id"])
        if options.get("baseline_id"):
            from agentagon.webapp.datasets import assert_development_evaluator

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
        from agentagon import operations
        from agentagon.experiments import baselines

        options = job["options"]
        if job["kind"] == "audit":
            trace = (
                snapshots.load(workspace, options["trace_snapshot_id"])
                if options.get("trace_snapshot_id")
                else None
            )
            mode = options.get("mode", "combined" if trace else "code")
            if options.get("scope") == "traces":
                mode = "traces"
            if mode != "code" and not trace:
                raise AuditError("import and select traces before starting a trace audit")
            selection = trace["selection"] if trace else {}
            provenance = trace["provenance"] if trace else {}
            if not job.get("actual_model"):
                raise AuditError(
                    "Coding host did not report its actual model; update the host or select a model explicitly."
                )
            if not job["workflow_ids"].get("audit_id"):
                audit = operations.start(
                    workspace,
                    mode=mode,
                    source=provenance.get("provider") if mode != "code" else None,
                    project=selection.get("project") or provenance.get("project")
                    if mode != "code"
                    else None,
                    start_time=selection.get("start") if mode != "code" else None,
                    end_time=selection.get("end") if mode != "code" else None,
                    limit=selection.get("cap", 100) if mode != "code" else None,
                    scopes=options.get("code_scopes", []),
                    host=job["agent"],
                    model=job["actual_model"],
                    goal=job["goal"],
                    request_id=job["id"],
                    code_scope="changes" if options.get("scope") == "changes" else "full",
                )
                job["workflow_ids"]["audit_id"] = audit["audit_id"]
                self._write(job)
            if trace and mode != "code" and not job.get("trace_imported"):
                saved = workspace.read_audit(job["workflow_ids"]["audit_id"])
                if not saved.get("acquisition"):
                    exported = workspace.artifact(trace["items"])
                    operations.import_traces(
                        workspace, saved["audit_id"], workspace.root / exported
                    )
                job["trace_imported"] = True
                self._write(job)
        elif job["kind"] == "baseline":
            from agentagon.experiments import journeys, preparation

            if job["workflow_ids"].get("baseline_id"):
                return
            if options.get("baseline_id"):
                previous = baselines.status(workspace, options["baseline_id"])
                evaluation_id = previous["evaluation_id"]
            elif options.get("evaluation_id"):
                evaluation_id = options["evaluation_id"]
            else:
                raise AuditError("select a frozen evaluation or a saved baseline")
            evaluator = preparation.load(workspace, evaluation_id)
            specification = evaluator.get("package", {}).get("spec", {})
            if not specification.get("scoring"):
                raise AuditError("selected evaluator needs an accepted scoring definition")
            definition = {
                "version": 1,
                "goal": job["goal"],
                "accepted_by": f"webapp:{job['id']}",
                "scoring": specification["scoring"],
                "discovery": {
                    "status": "usable",
                    "paths": specification["evaluation_paths"],
                    "evidence": [f"User selected frozen evaluator {evaluation_id}"],
                    "creation_authorized": False,
                },
                "budget": {key: options[key] for key in BUDGET_DEFAULTS},
                "host": {"name": job["agent"], "model": job["actual_model"]},
            }
            if options.get("baseline_id") and previous.get("intent_id"):
                prior = journeys.load(workspace, previous["intent_id"])["definition"]
                if prior.get("acquisition"):
                    definition["acquisition"] = copy.deepcopy(prior["acquisition"])
            intent = journeys.save(workspace, definition, evaluation_id)
            result = baselines.start(
                workspace,
                evaluation_id,
                intent["intent_id"],
                profile_name=options["profile"],
                request_id=job["id"],
                execution_profile=job["execution_profile"],
            )
            job["workflow_ids"]["baseline_id"] = result["baseline_id"]
            self._write(job)

    def _run(self, project_id, job_id, cancelled):
        started = time.monotonic()
        request = {}
        try:
            with self.condition:
                job = self._read(project_id, job_id)
                if job["state"] != "queued":
                    return
                workspace = self.state.workspace(project_id)
                job.update(
                    state="running",
                    next_action=None,
                    attempt_started_at=now(),
                    attempt_wait_seconds=0,
                )
                self._write(job)
                workflows.freeze_settings(workspace, job)
                remaining = job["options"]["max_elapsed_seconds"] - job["elapsed_seconds"]
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
                    "sandbox": "read-only" if job["kind"] == "design" else "workspace-write",
                    "timeout_seconds": remaining,
                    "env": {
                        "AGENTAGON_APP_STATE": str(self.state.directory),
                        "AGENTAGON_CONFIG": str(
                            private_directory(workspace, "job-inputs") / f"{job['id']}-config.json"
                        ),
                    },
                }
                executable = job["agent_settings"].get(job["agent"] + "_executable")
                if executable:
                    request["executable"] = executable
                if job["agent"] == "claude":
                    current_agents = self.state.read()["agents"]
                    ref = current_agents.get("claude_api_key_ref") or (
                        "env:ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else None
                    )
                    if not ref:
                        raise AuditError("Configure a Claude API key in Coding agents.")
                    request["api_key"] = self.credentials.resolve(ref)
                    job["agent_settings"]["claude_api_key_ref"] = ref
                    self._write(job)
            outcome = self._advance(project_id, job_id, workspace, request, cancelled, started)
            with self.condition:
                job = self._read(project_id, job_id)
                if job["state"] in {"paused", "cancelled", "interrupted"}:
                    return
                job["session_id"] = outcome.get("session_id") or job.get("session_id")
                if job.pop("pending_guidance", False):
                    job.update(
                        state="queued",
                        question=None,
                        next_action="Waiting for coding-agent capacity.",
                    )
                elif outcome.get("state") != "completed":
                    self._expire_question(
                        job,
                        "The coding-agent process ended before this request was answered.",
                    )
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
                        "fix": "run_id",
                        "baseline": "baseline_id",
                    }[job["kind"]]
                    if result.get(key):
                        job["workflow_ids"][key] = result[key]
                    if state == "needs_input":
                        self._set_durable_question(
                            job, self._result_question(workspace, job, result, next_action)
                        )
                    else:
                        job["question"] = None
                self._write(job)
        except Exception as exc:
            with self.condition:
                job = self._read(project_id, job_id)
                if job.pop("pending_guidance", False):
                    job.update(
                        state="queued",
                        question=None,
                        next_action="Waiting for coding-agent capacity.",
                    )
                    self._write(job)
                elif job["state"] not in {"paused", "cancelled", "interrupted"}:
                    self._expire_question(
                        job,
                        "The coding-agent process failed before this request was answered.",
                    )
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
                    job.update(state="failed", next_action=message)
                    self._write(job)
        finally:
            with self.condition:
                cancelled.set()  # Release outstanding approval waiters on timeout/failure too.
                job = self._read(project_id, job_id)
                active_seconds = max(
                    0,
                    time.monotonic() - started - job.pop("attempt_wait_seconds", 0),
                )
                job["elapsed_seconds"] += active_seconds
                job.pop("attempt_started_at", None)
                self._write(job)
                self.active.pop((project_id, job_id), None)
                self.slots.pop((project_id, job_id), None)
                self.question_locks.pop((project_id, job_id), None)
                self.answers.pop((project_id, job_id), None)
                self.threads.discard(threading.current_thread())
                self._dispatch()

    def _advance(self, project_id, job_id, workspace, request, cancelled, started):
        """Own author/reviewer turns under one capacity slot and elapsed budget."""
        while True:
            with self.condition:
                job = self._read(project_id, job_id)
                if cancelled.is_set() or job["state"] != "running":
                    return {"state": "interrupted", "session_id": job.get("session_id")}
                remaining = (
                    job["options"]["max_elapsed_seconds"]
                    - job["elapsed_seconds"]
                    - max(
                        0,
                        time.monotonic() - started - job.get("attempt_wait_seconds", 0),
                    )
                )
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
                        from agentagon.experiments.host_bridge import HostBridge

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
                    )
            if reflections:
                if not self._reflect_batch(
                    project_id, job_id, workspace, job, request, cancelled, remaining
                ):
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
                            else {
                                "text": (
                                    "Use the available evidence and your best judgment. "
                                    "If required evidence is missing, return needs_input as a blocker."
                                )
                            }
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
                        f"Application-managed independent review {review_id} is {review_state}. "
                        "Inspect its saved response. Continue the same workflow; a rejected evaluator "
                        "must be repaired and checked again before requesting a new review."
                    )
                    self._write(job)
                continue
            outcome = self.execute(
                current,
                lambda event: self._emit(project_id, job_id, event),
                lambda question: self._author_input(project_id, job_id, question, cancelled),
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
                if job["kind"] == "fix" and handoff["kind"] == "native":
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

    def _reflect_batch(self, project_id, job_id, workspace, job, request, cancelled, remaining):
        from agentagon.webapp.agents import run_reflection

        deadline = time.monotonic() + remaining

        def run(reflection):
            timeout = deadline - time.monotonic()
            if timeout <= 0 or cancelled.is_set():
                cancelled.set()
                return False
            reflected = run_reflection(
                workspace,
                reflection["owner_id"],
                reflection["request_id"],
                emit=lambda event: self._emit(project_id, job_id, event),
                ask=lambda question: (
                    {"decision": "decline"}
                    if question.get("kind") == "approval"
                    else {
                        "text": (
                            "Use the available evidence and your best judgment. "
                            "Return a blocker if essential evidence is missing."
                        )
                    }
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
                    f"Application-managed reflection {reflection['request_id']} is complete. Continue using its saved response."
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
        # Concurrent native sessions may ask questions, but the browser answers one
        # exact question at a time. Do not hold the condition while waiting for this lock.
        with lock:
            return self._ask_one(project_id, job_id, question, cancelled)

    def _author_input(self, project_id, job_id, question, cancelled):
        """Keep author turns autonomous while preserving explicit security approvals."""
        if question.get("kind") != "approval":
            return {
                "text": (
                    "Use your best judgment, continue without asking the user, and state any "
                    "material assumptions in the result."
                )
            }
        return self._ask(project_id, job_id, question, cancelled)

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
                question={
                    **question,
                    "id": "input_" + uuid.uuid4().hex,
                    "source": "coding_agent",
                    "durable": False,
                    "asked_at": now(),
                    "status": "pending",
                    "text": text,
                },
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
            current = job["question"]
            self._record_wait(job, current)
            job["answers"][current["id"]]["consumed_at"] = now()
            job.setdefault("input_history", []).append(
                {
                    **copy.deepcopy(current),
                    "status": "answered",
                    "answered_at": job["answers"][current["id"]]["accepted_at"],
                }
            )
            job["input_history"] = job["input_history"][-100:]
            job.update(state="running", question=None, next_action=None)
            self._write(job)
            return answer

    def control(self, project_id, job_id, action, payload):
        if action not in {"reply", "pause", "resume", "cancel", "message"}:
            raise AuditError("unsupported task control")
        allowed = {"operation_id", "question_id", "answer", "message"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise AuditError("unsupported task control fields")
        op = operation_id(payload.get("operation_id"))
        binding = digest({"action": action, "payload": payload})
        with self.condition:
            key = (project_id, job_id)
            live_answer = None
            interrupt = False
            with self.state.db.transaction() as transaction:
                identifier(job_id, "job")
                job = transaction.get_record(project_id, "jobs", job_id)
                if job is None:
                    raise AuditError("task not found in this project")
                previous = job["receipts"].get(op)
                if previous:
                    if previous != binding:
                        raise AuditError("operation_id already belongs to a different task control")
                    return public_job(job)
                if action == "reply":
                    if job["state"] != "needs_input" or not job.get("question"):
                        raise AuditError("this task has no unanswered input request")
                    durable = job["question"].get("source") == "agentagon"
                    if not durable and key not in self.active:
                        raise AuditError(
                            "this native request expired with its coding-agent process; resume the task to request it again"
                        )
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
                    job["answers"][question_id] = {
                        "answer": copy.deepcopy(answer),
                        "accepted_at": now(),
                    }
                    job["question"]["answered"] = True
                    self._record_wait(job, job["question"])
                    if durable:
                        question = copy.deepcopy(job["question"])
                        decision = answer.get("decision")
                        if question.get("kind") in {
                            "approval",
                            "intelligence",
                            "trace_access",
                        } and decision not in {"accept", "decline"}:
                            raise AuditError("choose approve or decline for this exact request")
                        if question.get("kind") == "intelligence":
                            option = "--approve" if decision == "accept" else "--decline"
                            verb = "approved" if decision == "accept" else "declined"
                            instruction = (
                                f"The user {verb} AG Intelligence request "
                                f"{question['approval_id']}. Re-run the exact same "
                                f"{question['workflow']} lookup with `{option} "
                                f"{question['approval_id']}`. Do not change its prepared payload."
                            )
                        elif question.get("kind") == "trace_access":
                            instruction = (
                                "The user approved the exact trace-access request. Continue with "
                                "only the described trace scope."
                                if decision == "accept"
                                else "The user declined trace access, so this trace-bound task was not started."
                            )
                        else:
                            response = answer.get("text")
                            if not isinstance(response, str) or not response.strip():
                                response = decision or json.dumps(answer, sort_keys=True)
                            instruction = (
                                "Agentagon resolved the blocking input: " + response.strip()
                            )
                        job["messages"].append({"role": "system", "text": instruction, "at": now()})
                        job.setdefault("input_history", []).append(
                            {
                                **question,
                                "status": "answered",
                                "answered_at": job["answers"][question_id]["accepted_at"],
                                "decision": decision,
                            }
                        )
                        job["input_history"] = job["input_history"][-100:]
                        job["answers"][question_id]["consumed_at"] = now()
                        if question.get("kind") == "trace_access" and decision == "decline":
                            job.update(state="cancelled", question=None, next_action=None)
                        else:
                            job.update(
                                state="queued",
                                question=None,
                                next_action="Waiting for coding-agent capacity.",
                            )
                    else:
                        live_answer = copy.deepcopy(answer)
                elif action in {"pause", "cancel"}:
                    if job["state"] in TERMINAL:
                        raise AuditError("task has already finished")
                    verb = "paused" if action == "pause" else "cancelled"
                    self._expire_question(
                        job, f"The task was {verb} before this request was answered."
                    )
                    job.update(
                        state="paused" if action == "pause" else "cancelled",
                        next_action="Resume when ready." if action == "pause" else None,
                    )
                    interrupt = True
                elif action == "resume":
                    if key in self.active:
                        raise AuditError(
                            "wait for the previous agent process to stop before resuming"
                        )
                    if job["state"] == "needs_input" and job.get("question"):
                        raise AuditError("respond to the current input request before resuming")
                    if job["state"] not in {"paused", "interrupted", "failed", "needs_input"}:
                        raise AuditError("only paused, interrupted or blocked tasks can resume")
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
                    job["messages"].append({"role": "user", "text": message.strip(), "at": now()})
                    if len(job["messages"]) > 100:
                        raise AuditError("task guidance limit reached; start a new task")
                    if key in self.active:
                        interrupt = True
                        job["pending_guidance"] = True
                        self._expire_question(
                            job,
                            "The coding-agent turn was interrupted by new user guidance.",
                        )
                        job.update(
                            next_action=None,
                        )
                    elif job["state"] == "needs_input" and job.get("question"):
                        # Chat is steering, not an approval channel. Keep an exact
                        # durable request pending until its guided card is answered.
                        pass
                    elif job["state"] != "queued":
                        job.update(
                            state="queued",
                            question=None,
                            next_action="Waiting for coding-agent capacity.",
                        )
                job["receipts"][op] = binding
                self._write(job, transaction)
            # Only release a waiter or interrupt a host after its receipt is durable.
            if live_answer is not None:
                self.answers[key] = live_answer
            if interrupt and key in self.active:
                self.active[key].set()
            self._dispatch()
            return public_job(job)

    @staticmethod
    def _charge_attempt(job):
        started = job.pop("attempt_started_at", None)
        if started is None:
            return False
        waiting = job.pop("attempt_wait_seconds", 0)
        question = job.get("question")
        if question and not question.get("wait_charged"):
            waiting += JobManager._wait_seconds(question)
            question["wait_charged"] = True
        elapsed = max(
            0,
            time.time() - datetime.fromisoformat(started).timestamp() - waiting,
        )
        job["elapsed_seconds"] += elapsed
        return True

    def close(self):
        with self.condition:
            self.stopping = True
            for (project_id, job_id), cancel in list(self.active.items()):
                cancel.set()
                job = self._read(project_id, job_id)
                self._expire_question(
                    job, "Agentagon stopped before the native request was answered."
                )
                job.update(
                    state="interrupted",
                    next_action="Restart Agentagon and resume this task.",
                )
                self._write(job)
            self.condition.notify_all()
            threads = list(self.threads)
        for thread in threads:
            thread.join(timeout=5)
