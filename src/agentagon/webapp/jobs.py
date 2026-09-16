"""Durable app-owned jobs with project isolation and explicit interruption recovery."""

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime

from agentagon.core.records import AuditError, digest, now
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
            "binding",
            "request_binding",
            "receipts",
            "agent_settings",
            "attempt_started_at",
            "answers",
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
        self.answers = {}
        self.threads = set()
        self.stopping = False
        self.revision = 0
        self.verify_result = lambda workspace, job, result: result
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

    def _read(self, project_id, job_id):
        identifier(job_id, "job")
        job = self.state.db.get_record(project_id, "jobs", job_id)
        if job is None:
            raise AuditError("task not found in this project")
        return job

    def _write(self, job, transaction=None):
        saved = (transaction or self.state.db).put_record(job["project_id"], "jobs", job["id"], job)
        job.update(revision=saved["revision"], updated_at=saved["updated_at"])
        self.revision += 1
        with self.condition:
            self.condition.notify_all()

    def get(self, project_id, job_id):
        with self.condition:
            return public_job(self._read(project_id, job_id))

    def list(self, project_id):
        return self.state.db.list_records(project_id, "jobs")

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
        }:
            raise AuditError("unsupported task fields")
        operation = operation_id(payload.get("operation_id"))
        kind = payload.get("kind")
        if kind not in workflows.REFERENCES:
            raise AuditError("choose audit, eval, fix, or baseline")
        job_id = "job_" + hashlib.sha256(f"{project_id}:{operation}".encode()).hexdigest()[:24]
        binding = digest(payload)
        request_binding = binding if request_binding is None else request_binding
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
        }
        if set(options) - allowed:
            raise AuditError("unsupported task options")
        for key in ("application_agent_id", "focus_id"):
            value = payload.get(key)
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"[a-z_]+_[a-f0-9]{24}", value)
            ):
                raise AuditError(f"invalid {key}")
        for key in ("investigation_plan", "suite_manifest"):
            value = options.get(key)
            if value is not None and (
                not isinstance(value, dict) or len(json.dumps(value).encode()) > 1_000_000
            ):
                raise AuditError(f"{key} must be a bounded object")
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
        if kind != "audit":
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
        agent = payload.get("agent") or settings.get("default_agent", "codex")
        model = (
            payload.get("model")
            or settings.get("models", {}).get(agent)
            or (
                settings.get("model", "") if agent == settings.get("default_agent", "codex") else ""
            )
        )
        if agent not in {"codex", "claude"} or not isinstance(model, str) or len(model) > 200:
            raise AuditError("invalid coding agent or model")
        job_id = "job_" + hashlib.sha256(f"{project_id}:{operation}".encode()).hexdigest()[:24]
        binding = digest(payload)
        with self.condition:
            workspace.initialize()
            job = {
                "version": 1,
                "id": job_id,
                "project_id": project_id,
                "application_agent_id": payload.get("application_agent_id"),
                "focus_id": payload.get("focus_id"),
                "kind": kind,
                "goal": goal.strip(),
                "agent": agent,
                "model": model,
                "state": "queued",
                "options": copy.deepcopy(options),
                "binding": binding,
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
                "active_reflection": None,
                "reflection_tasks": {},
                "frozen_settings": frozen,
                "execution_profile": execution_profile,
                "agent_settings": copy.deepcopy(settings),
                "elapsed_seconds": 0,
                "next_action": "Waiting for coding-agent capacity.",
            }
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
        capacity = self.state.read()["agents"].get("concurrency", 1)
        active_projects = {project for project, _ in self.active}
        for project_id in self.state.read()["projects"]:
            if len(self.active) >= capacity:
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
            key = (project_id, job["id"])
            cancel = threading.Event()
            self.active[key] = cancel
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
                job.update(state="running", next_action=None, attempt_started_at=now())
                self._write(job)
                workflows.freeze_settings(workspace, job)
                remaining = job["options"].get("max_elapsed_seconds", 1800) - job.get(
                    "elapsed_seconds", 0
                )
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
                    "sandbox": "workspace-write",
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
                        "audit": "audit_id",
                        "eval": "evaluation_id",
                        "fix": "run_id",
                        "baseline": "baseline_id",
                    }[job["kind"]]
                    if result.get(key):
                        job["workflow_ids"][key] = result[key]
                job["session_id"] = outcome.get("session_id") or job.get("session_id")
                job["question"] = None
                self._write(job)
        except Exception as exc:
            with self.condition:
                job = self._read(project_id, job_id)
                if job["state"] not in {"paused", "cancelled", "interrupted"}:
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
                job["elapsed_seconds"] = job.get("elapsed_seconds", 0) + time.monotonic() - started
                job.pop("attempt_started_at", None)
                self._write(job)
                self.active.pop((project_id, job_id), None)
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
                    - job.get("elapsed_seconds", 0)
                    - (time.monotonic() - started)
                )
                if remaining <= 0:
                    raise AuditError(
                        "task time budget exhausted; start a new task with explicit limits"
                    )
                review_id = job.get("active_review_id")
                task = job.get("review_tasks", {}).get(review_id) if review_id else None
                reflection = job.get("active_reflection")
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
            if reflection:
                from agentagon.webapp.agents import run_reflection

                reflected = run_reflection(
                    workspace,
                    reflection["owner_id"],
                    reflection["request_id"],
                    emit=lambda event: self._emit(project_id, job_id, event),
                    ask=lambda question: self._ask(project_id, job_id, question, cancelled),
                    cancelled=cancelled,
                    timeout_seconds=remaining,
                    execute=self.execute,
                    api_key=request.get("api_key"),
                    executable=request.get("executable"),
                    environment=request.get("env"),
                    forbidden_session_id=job.get("session_id"),
                )
                if reflected["state"] != "completed":
                    return {"state": "interrupted", "session_id": job.get("session_id")}
                with self.condition:
                    job = self._read(project_id, job_id)
                    reflection_key = reflection["owner_id"] + ":" + reflection["request_id"]
                    job["reflection_tasks"][reflection_key] = {
                        **reflection,
                        "state": "completed",
                        "session_id": reflected.get("session_id"),
                    }
                    job["active_reflection"] = None
                    job["messages"].append(
                        "Application-managed reflection "
                        + reflection["request_id"]
                        + " is complete. Continue the same workflow using its saved response."
                    )
                    self._write(job)
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
                        f"Application-managed independent review {review_id} is {review_state}. "
                        "Inspect its saved response. Continue the same workflow; a rejected evaluator "
                        "must be repaired and checked again before requesting a new review."
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
                reflection = workflows.reflection_handoff(workspace, job, outcome.get("text", ""))
                if reflection is not None:
                    reflection_key = reflection["owner_id"] + ":" + reflection["request_id"]
                    if reflection_key in job["reflection_tasks"]:
                        raise AuditError(
                            "this reflection is already finished; continue its workflow"
                        )
                    job["active_reflection"] = reflection
                    if reflection.get("run_id"):
                        job["workflow_ids"]["run_id"] = reflection["run_id"]
                    self._write(job)
                    continue
                handoff = workflows.review_handoff(workspace, job, outcome.get("text", ""))
                if handoff is None:
                    return outcome
                if handoff.get("evaluation_id"):
                    job["workflow_ids"]["evaluation_id"] = handoff["evaluation_id"]
                if job["kind"] == "fix" and handoff.get("run_id"):
                    job["workflow_ids"]["run_id"] = handoff.get(
                        "workflow_run_id", handoff["run_id"]
                    )
                task = job.setdefault("review_tasks", {}).setdefault(
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
                self._prepare(self.state.workspace(project_id), job)
                workflows.write_context(self.state.workspace(project_id), job)
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
            budget_left = job["options"]["max_elapsed_seconds"] - job.get("elapsed_seconds", 0)
            deadline = datetime.fromisoformat(job["attempt_started_at"]).timestamp() + budget_left
            while key not in self.answers and not cancelled.is_set():
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise AuditError("task time budget exhausted while waiting for input")
                self.condition.wait(timeout=min(1, remaining))
            if cancelled.is_set():
                return {"decision": "decline"}
            answer = self.answers.pop(key)
            job = self._read(project_id, job_id)
            if job["state"] != "needs_input":
                return {"decision": "decline"}
            job["answers"][job["question"]["id"]]["consumed_at"] = now()
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
            answer = None
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
                    if (
                        job["state"] != "needs_input"
                        or not job.get("question")
                        or key not in self.active
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
                    job["answers"][question_id] = {
                        "answer": copy.deepcopy(answer),
                        "accepted_at": now(),
                    }
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
                elif action == "resume":
                    if key in self.active:
                        raise AuditError(
                            "wait for the previous agent process to stop before resuming"
                        )
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
                    job["messages"].append(message.strip())
                    if len(job["messages"]) > 100:
                        raise AuditError("task guidance limit reached; start a new task")
                    if key in self.active:
                        interrupt = True
                        job.update(
                            state="paused",
                            question=None,
                            next_action="Guidance saved. Resume after the current turn stops.",
                        )
                job["receipts"][op] = binding
                self._write(job, transaction)
            # Only release a waiter or interrupt a host after its receipt is durable.
            if answer is not None:
                self.answers[key] = copy.deepcopy(answer)
            if interrupt and key in self.active:
                self.active[key].set()
            self._dispatch()
            return public_job(job)

    @staticmethod
    def _charge_attempt(job):
        started = job.pop("attempt_started_at", None)
        if started is None:
            return False
        elapsed = max(0, time.time() - datetime.fromisoformat(started).timestamp())
        job["elapsed_seconds"] = job.get("elapsed_seconds", 0) + elapsed
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
                self._write(job)
            self.condition.notify_all()
            threads = list(self.threads)
        for thread in threads:
            thread.join(timeout=5)
