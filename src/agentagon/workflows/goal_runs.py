"""Durable goal-to-improvement coordination; only explicit Go/resume dispatches work."""

import copy
import math
import re
import threading
import uuid

from agentagon.core.records import AuditError, digest, identifier, now
from agentagon.domain.tasks import accounting
from agentagon.storage.config import Config
from agentagon.storage.state import identifier as validate_identifier
from agentagon.workflows.runtime import operation_id

KIND = "goal_runs"
FINISHED = {"completed", "cancelled", "failed"}
LABELS = {
    "design": "Defining useful measurements for your goal.",
    "eval": "Preparing and independently reviewing the evaluation.",
    "baseline": "Measuring the current agent.",
    "optimize": "Finding and verifying improvements.",
}


class ActiveGoalRun(AuditError):
    status_code = 409

    def __init__(self, run_id):
        super().__init__(
            "A run is already active for this goal. Stop it before changing details, runner or limits."
        )
        self.details = {"code": "goal_run_active", "run_id": run_id, "retryable": False}


class InputNeeded(AuditError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _request(payload):
    if not isinstance(payload, dict) or set(payload) - {
        "operation_id",
        "details",
        "profile",
        "max_elapsed_seconds",
        "max_trials",
    }:
        raise AuditError("unsupported goal run fields")
    result = copy.deepcopy(payload)
    result["operation_id"] = operation_id(result.get("operation_id"))
    details = result.get("details", "")
    if not isinstance(details, str) or len(details) > 4000:
        raise AuditError("details must be text up to 4000 characters")
    result["details"] = details.strip()
    for field in ("max_elapsed_seconds", "max_trials"):
        if field in result and (type(result[field]) is not int or not 1 <= result[field] <= 86400):
            raise AuditError(f"{field} must be an integer between 1 and 86400")
    profile = result.get("profile")
    if profile is not None and (
        not isinstance(profile, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", profile)
    ):
        raise AuditError("profile must name a configured execution profile")
    return result


class GoalRuns:
    def __init__(self, application):
        self.app = application
        self.live = set()
        self.stopping = False
        # A new process never infers permission to restart an uncertain child.
        for project_id in application.state.read()["projects"]:
            for run in application.state.db.list_records(project_id, KIND):
                if run["state"] in {"running", "needs_input"}:
                    run.update(
                        state="interrupted",
                        summary="Agentagon restarted. Resume to reconcile the saved work.",
                    )
                    self._save(run)
        self.thread = threading.Thread(target=self._loop, daemon=True, name="agentagon-goal-runs")
        self.thread.start()

    def _read(self, project_id, run_id):
        validate_identifier(run_id, "goalrun")
        record = self.app.state.db.get_record(project_id, KIND, run_id)
        if not record:
            raise AuditError("goal run not found in this project")
        return record

    def _save(self, run):
        saved = self.app.state.db.put_record(run["project_id"], KIND, run["id"], run)
        run.update(revision=saved["revision"], updated_at=saved["updated_at"])

    def _accounting(self, run):
        values = {"active_seconds": 0.0, "unknown_seconds": 0.0}
        for task_id in run["task_ids"]:
            task = self.app.runtime.get(run["project_id"], task_id)
            timing = accounting(task)
            for key in values:
                values[key] += timing[key]
        reserved = sum(stage.get("reserved_trials", 0) for stage in run["stages"])
        return {
            **values,
            "remaining_seconds": max(
                0, run["limits"]["max_elapsed_seconds"] - sum(values.values())
            ),
            "trials_reserved": reserved,
            "trials_remaining": max(0, run["limits"]["max_trials"] - reserved),
        }

    def _pending_task(self, run):
        intent = run.get("pending_intent")
        if not intent:
            return None
        return self.app.runtime.existing_submission(
            run["project_id"], intent["operation_id"], digest(intent)
        )

    def _attach_pending(self, run, child):
        intent = run["pending_intent"]
        name = intent["workflow"]
        if child.get("kind") != name:
            raise AuditError("Saved child workflow differs from the pending goal stage.")
        target = (intent["agent_id"], intent["input"]["id"])
        if target != self._target(run):
            raise AuditError("Saved child belongs to a different goal preparation target.")
        task_id = child.get("task_id") or child["id"]
        self._stage(run, name).update(
            state="running",
            task_id=task_id,
            reserved_trials=0 if name == "design" else intent["limits"]["max_trials"],
        )
        if task_id not in run["task_ids"]:
            run["task_ids"].append(task_id)
        run.update(stage=name, active_task_id=task_id)
        run.pop("pending_intent", None)

    def _public(self, run):
        run = copy.deepcopy(run)
        child = self._pending_task(run)
        if child:
            self._attach_pending(run, child)
        result = {
            key: copy.deepcopy(value)
            for key, value in run.items()
            if key
            not in {"request_binding", "receipts", "pending_intent", "intent_binding", "request"}
        }
        result["accounting"] = self._accounting(run)
        actions = []
        if run["state"] not in {"completed", "cancelled"}:
            actions.append("cancel")
            if run["state"] in {"running", "needs_input"}:
                actions.append("pause")
            if run["state"] in {"paused", "interrupted", "failed", "needs_input"}:
                possible = result["accounting"]["remaining_seconds"] >= 1
                possible = possible and not any(
                    item["code"] in {"goal_changed", "budget_exhausted"}
                    for item in run["requirements"]
                )
                if run.get("active_task_id"):
                    child = self.app.runtime.get(run["project_id"], run["active_task_id"])
                    possible = possible and (
                        child["state"]
                        in {"queued", "running", "completed", "completed_with_limits"}
                        or child["recovery"]["resume_allowed"]
                    )
                if possible:
                    actions.append("resume")
        result["available_actions"] = actions
        return result

    def get(self, project_id, run_id):
        with self.app.lock:
            return self._public(self._read(project_id, run_id))

    def list(self, project_id, agent_id, goal_id):
        self.app.catalog.goal_record(project_id, agent_id, goal_id)
        with self.app.lock:
            return {
                "runs": [
                    self._public(run)
                    for run in self.app.state.db.list_records(project_id, KIND)
                    if run["agent_id"] == agent_id and run["goal_id"] == goal_id
                ]
            }

    def _binding(self, project_id, agent_id, goal_id):
        agent = self.app.catalog.agent(project_id, agent_id)
        goal = self.app.catalog.goal_record(project_id, agent_id, goal_id)
        return digest(
            {
                "agent": agent["binding_digest"],
                "objective": goal["objective"],
                "ideal_behavior": goal.get("ideal_behavior"),
                "source": goal["source"],
            }
        )

    def start(self, project_id, agent_id, goal_id, payload):
        request = _request(payload)
        binding = digest({"agent_id": agent_id, "goal_id": goal_id, "request": request})
        receipt_id = identifier("goalrunop", request["operation_id"])
        with self.app.lock:
            receipt = self.app.state.db.get_record(project_id, "goal_run_operations", receipt_id)
            if receipt:
                if receipt["binding"] != binding:
                    raise AuditError("operation_id already belongs to different goal run settings")
                return self.get(project_id, receipt["run_id"])
            intent_binding = self._binding(project_id, agent_id, goal_id)
            active = next(
                (
                    run
                    for run in self.app.state.db.list_records(project_id, KIND)
                    if run["agent_id"] == agent_id
                    and run["goal_id"] == goal_id
                    and run["state"] not in FINISHED
                ),
                None,
            )
            if active:
                previous = active["request"]
                same = request["details"] == previous["details"]
                for field in ("profile", "max_elapsed_seconds", "max_trials"):
                    requested = request.get(field, previous.get(field))
                    effective = (
                        active["resolved"].get("profile")
                        if field == "profile"
                        else active["limits"][field]
                    )
                    same = same and requested in {previous.get(field), effective}
                if not same or intent_binding != active["intent_binding"]:
                    raise ActiveGoalRun(active["id"])
            run = active or {
                "id": identifier("goalrun", project_id, request["operation_id"]),
                "project_id": project_id,
                "agent_id": agent_id,
                "goal_id": goal_id,
                "state": "running",
                "stage": "design",
                "active_task_id": None,
                "task_ids": [],
                "stages": [],
                "requirements": [],
                "outcome": None,
                "request": request,
                "request_binding": binding,
                "intent_binding": intent_binding,
                "details": request["details"],
                "limits": {
                    "max_elapsed_seconds": request.get("max_elapsed_seconds", 1800),
                    "max_trials": request.get("max_trials", 30),
                },
                "receipts": {},
                "resolved": {},
                "acceptances": [],
                "authorization": {
                    "operation_id": request["operation_id"],
                    "at": now(),
                    "automatic_measurement_acceptance": True,
                    "scope": "design_evaluate_baseline_optimize",
                    "delivery_authorized": False,
                },
                "summary": "Starting work toward your goal.",
                "revision": 0,
            }
            with self.app.state.db.transaction() as tx:
                if not active:
                    run = tx.put_record(project_id, KIND, run["id"], run, expected_revision=0)
                tx.put_record(
                    project_id,
                    "goal_run_operations",
                    receipt_id,
                    {"binding": binding, "run_id": run["id"]},
                    expected_revision=0,
                )
            if not active:
                self.live.add((project_id, run["id"]))
                self.app.lock.notify_all()
            return self._public(run)

    def _defaults(self, run):
        if run["resolved"].get("profile"):
            return
        root = self.app.state.workspace(run["project_id"]).root
        config = Config()
        profiles = config.effective(root)["profiles"]
        selected = run["request"].get("profile")
        if selected and selected not in profiles:
            raise InputNeeded(
                "The selected runner is unavailable. Configure it before continuing.",
                "execution_profile",
            )
        if not selected:
            # Prefer the saved evaluator's runner when it remains available.
            goal = self.app.catalog.goal_record(run["project_id"], run["agent_id"], run["goal_id"])
            evaluation_id = (goal.get("measurement") or {}).get("evaluation_id")
            if evaluation_id:
                from agentagon.capabilities.experiments import preparation

                try:
                    selected = preparation.load(
                        self.app.state.workspace(run["project_id"]), evaluation_id
                    )["profile_name"]
                except (AuditError, OSError, KeyError):
                    selected = None
            if selected not in profiles:
                selected = next(
                    (
                        name
                        for name in sorted(profiles)
                        if profiles[name]["runner"]["kind"] == "local"
                    ),
                    None,
                )
                selected = selected or next(iter(sorted(profiles)), None)
        if not selected:
            selected = "goal-local"
            config.update_profile(
                "project",
                selected,
                {
                    "runner": {"kind": "local"},
                    "limits": {
                        "max_candidates": 6,
                        "max_trials": 30,
                        "max_elapsed_seconds": 1800,
                        "parallel_candidates": 1,
                        "parallel_trials": 1,
                        "trial_timeout_seconds": 60,
                    },
                },
                root,
            )
            profiles = config.effective(root)["profiles"]
            run["resolved"]["created_local_profile"] = True
        for field in ("max_elapsed_seconds", "max_trials"):
            if field not in run["request"]:
                run["limits"][field] = min(
                    run["limits"][field], profiles[selected]["limits"][field]
                )
        run["resolved"]["profile"] = selected
        self._save(run)

    def _assistant(self):
        available = self.app.assistants()
        preferred = available["defaults"].get("default_agent")
        usable = [
            item["id"]
            for item in available["assistants"]
            if item.get("available") and item.get("authenticated") is True
        ]
        return preferred if preferred in usable else next(iter(usable), preferred)

    def _blocked(
        self, run, message, *, code="goal_run_input", requirements=None, state="needs_input"
    ):
        if requirements:
            from agentagon.workflows.operations.preparation import _blocking_message

            requirements = [
                {**item, "message": item.get("message") or _blocking_message(item)}
                for item in requirements
            ]
            message = requirements[0]["message"]
        context = (
            {"requested": run["request"].get("profile") or run["resolved"].get("profile")}
            if code == "execution_profile"
            else {}
        )
        run.update(
            state=state,
            summary=message,
            requirements=requirements
            or [
                {
                    "code": code,
                    "blocking": True,
                    "message": message,
                    "resolution": {
                        "action": "configure_execution"
                        if code == "execution_profile"
                        else "resolve_and_resume",
                        "context": context,
                    },
                }
            ],
        )
        self._save(run)
        self.live.discard((run["project_id"], run["id"]))

    def _stage(self, run, name):
        agent_id, goal_id = self._target(run)
        stage = next(
            (
                item
                for item in run["stages"]
                if item["stage"] == name
                and item["agent_id"] == agent_id
                and item["goal_id"] == goal_id
            ),
            None,
        )
        if stage is None:
            stage = {
                "stage": name,
                "state": "pending",
                "reserved_trials": 0,
                "agent_id": agent_id,
                "goal_id": goal_id,
            }
            run["stages"].append(stage)
        return stage

    @staticmethod
    def _target(run):
        focus = run.get("focus") or run
        return focus["agent_id"], focus["goal_id"]

    def _owns_target(self, run):
        target = self._target(run)
        contenders = [
            item
            for item in self.app.state.db.list_records(run["project_id"], KIND)
            if item["state"] not in FINISHED
            and ((item["agent_id"], item["goal_id"]) == target or self._target(item) == target)
        ]

        def priority(item):
            dispatched = any(
                stage.get("task_id") and (stage["agent_id"], stage["goal_id"]) == target
                for stage in item["stages"]
            )
            pending = item.get("pending_intent")
            dispatched = dispatched or bool(
                pending and (pending["agent_id"], pending["input"]["id"]) == target
            )
            return not dispatched, item.get("created_at", ""), item["id"]

        owner = min(contenders, key=priority) if contenders else run
        if owner["id"] == run["id"]:
            if run.pop("waiting_for_goal_run", None):
                run.update(state="running", requirements=[], summary="Continuing the saved goal.")
                self._save(run)
            return True
        state = (
            "needs_input"
            if owner["state"] in {"paused", "interrupted", "needs_input"}
            else "running"
        )
        message = (
            "Waiting for the existing run of this regression goal. It will be reused when ready."
        )
        if state == "needs_input":
            message = "The existing run of this regression goal needs attention before this goal can continue."
        requirement = {
            "code": "dependency_run_active",
            "blocking": True,
            "message": message,
            "resolution": {
                "action": "inspect_goal_run",
                "context": {
                    "run_id": owner["id"],
                    "agent_id": owner["agent_id"],
                    "goal_id": owner["goal_id"],
                    "route": f"/projects/{run['project_id']}/agents/{owner['agent_id']}/goals/{owner['goal_id']}",
                },
            },
        }
        if (run.get("waiting_for_goal_run"), run["state"], run["summary"]) != (
            owner["id"],
            state,
            message,
        ):
            run.update(
                waiting_for_goal_run=owner["id"],
                state=state,
                summary=message,
                requirements=[requirement],
            )
            self._save(run)
        return False

    def _settle_trials(self, run, task, stage):
        if stage["stage"] == "design":
            return
        from agentagon.capabilities.experiments import baselines, preparation
        from agentagon.capabilities.experiments.budget import BudgetLedger

        workspace = self.app.state.workspace(run["project_id"])
        identities = {**task.get("workflow_ids", {}), **(task.get("result") or {})}
        if stage["stage"] == "eval":
            record = preparation.load(workspace, identities["evaluation_id"])
            owner = record.get("budget_id") or record["evaluation_id"]
        elif stage["stage"] == "baseline":
            record = baselines.status(workspace, identities["baseline_id"])
            owner = record.get("budget_id") or record["execution_run_id"]
        else:
            owner = identities["run_id"]
        ledger = BudgetLedger(workspace, owner)
        if not ledger.path.exists():
            raise AuditError(
                "The completed task's admission ledger is missing. Inspect its retained evidence before continuing."
            )
        spent = ledger.spent(ledger.snapshot())
        previous = max(
            run.setdefault("budget_usage", {}).get(owner, 0),
            stage.get("initial_budget_usage", {}).get(owner, 0),
        )
        used = max(0, spent - previous)
        if used > stage["reserved_trials"]:
            raise AuditError("Retained execution exceeded its goal-run trial reservation.")
        stage.update(reserved_trials=used, actual_trials=used, trial_accounting="measured")
        run["budget_usage"][owner] = spent

    def _accept(self, run, draft):
        from agentagon.workflows.evaluate.designs import METRIC_FIELDS, score_definition

        agent_id, goal_id = self._target(run)
        goal = self.app.catalog.goal_record(run["project_id"], agent_id, goal_id)
        previous = (
            self.app.state.db.get_record(
                run["project_id"], "accepted_designs", goal["accepted_design_id"]
            )
            if goal.get("accepted_design_id")
            else None
        )
        old = score_definition(previous) if previous else None
        if old is None and (goal.get("measurement") or {}).get("evaluation_id"):
            from agentagon.capabilities.experiments import preparation

            evaluator = preparation.load(
                self.app.state.workspace(run["project_id"]), goal["measurement"]["evaluation_id"]
            )
            old = evaluator["package"]["spec"]["scoring"]
        if old:
            new = score_definition(draft)
            for name, metric in old["metrics"].items():
                expected = {key: value for key, value in metric.items() if key in METRIC_FIELDS}
                if new["metrics"].get(name) != expected:
                    raise InputNeeded(
                        "The new plan changes an existing scored metric. Preserve its definition or explicitly resolve that measurement change before continuing.",
                        "measurement_contract",
                    )
            behaviors = {item["id"]: item for item in new["behaviors"]}
            if any(
                item.get("required") and behaviors.get(item["id"]) != item
                for item in old["behaviors"]
            ):
                raise InputNeeded(
                    "The new plan changes a required behavior. Resolve this correctness decision before continuing.",
                    "measurement_contract",
                )
            if any(
                new.get(key) != value
                for key, value in old.items()
                if key not in {"metrics", "behaviors", "version", "source_paths"}
            ):
                raise InputNeeded(
                    "The new plan changes accepted scoring. Resolve this measurement decision before continuing.",
                    "measurement_contract",
                )
        for guard in (goal.get("measurement") or {}).get("guardrails", []):
            if guard["metric"] not in draft["metrics"]:
                raise InputNeeded(
                    "The new plan omits a saved regression guard. Preserve it before continuing.",
                    "measurement_contract",
                )
        intent = {
            "design_id": draft["id"],
            "revision": draft["revision"],
            "authorized_by": run["authorization"]["operation_id"],
            "at": now(),
        }
        run["acceptance_intent"] = intent
        self._save(run)
        accepted = self.app.designs.accept(
            run["project_id"],
            agent_id,
            goal_id,
            {"expected_revision": draft["revision"]},
        )
        run["acceptances"].append(
            {**intent, "accepted_id": accepted["id"], "digest": accepted["digest"]}
        )
        run.pop("acceptance_intent", None)
        self._save(run)
        return accepted

    def _launch(self, run, name):
        agent_id, goal_id = self._target(run)
        if not self._owns_target(run):
            return
        timing = self._accounting(run)
        remaining = math.floor(timing["remaining_seconds"])
        if remaining < 1:
            self._blocked(
                run,
                "The overall time allowance is exhausted. Start a new bounded run.",
                code="budget_exhausted",
                state="failed",
            )
            return
        stage = self._stage(run, name)
        evaluator = None
        trials = 1 if name == "design" else timing["trials_remaining"]
        if name == "eval":
            trials = min(6, trials - 2)
        elif name == "baseline":
            from agentagon.capabilities.experiments import preparation

            target = self.app.catalog.goal_record(run["project_id"], agent_id, goal_id)
            accepted = self.app.designs.accepted(run["project_id"], agent_id, goal_id)
            evaluation_id = (accepted or {}).get("evaluation", {}).get("evaluation_id") or (
                target.get("measurement") or {}
            ).get("evaluation_id")
            evaluator = preparation.load(self.app.state.workspace(run["project_id"]), evaluation_id)
            repetitions = evaluator["package"]["spec"]["repetitions"]
            profile = Config().profile(
                self.app.state.workspace(run["project_id"]).root, run["resolved"]["profile"]
            )
            if profile["limits"]["max_trials"] < repetitions:
                raise InputNeeded(
                    f"The selected runner allows fewer than the {repetitions} trials required by this frozen baseline. Increase its trial limit or start a new run with another runner.",
                    "execution_profile",
                )
            trials = min(max(6, repetitions), trials - 1)
            if trials < repetitions:
                self._blocked(
                    run,
                    f"This frozen evaluator needs {repetitions} baseline trials, exceeding the remaining overall allowance. Start a new bounded run; the prepared evaluator will be reused.",
                    code="budget_exhausted",
                    state="failed",
                )
                return
        if trials < 1:
            self._blocked(
                run,
                "The overall trial allowance cannot cover the remaining work. Start a new bounded run.",
                code="budget_exhausted",
                state="failed",
            )
            return
        intent = run.get("pending_intent") or {
            "operation_id": str(
                uuid.uuid5(
                    uuid.UUID(run["authorization"]["operation_id"]), f"{agent_id}:{goal_id}:{name}"
                )
            ),
            "workflow": name,
            "agent_id": agent_id,
            "input": {"type": "goal", "id": goal_id},
            "assistant": self._assistant(),
            "limits": {"max_elapsed_seconds": remaining, "max_trials": trials},
            "options": {}
            if name == "design"
            else {
                "profile": run["resolved"]["profile"],
                **(
                    {"finalist_count": run["resolved"]["finalist_count"]}
                    if name == "optimize"
                    else {}
                ),
            },
        }
        if intent["workflow"] != name:
            raise AuditError("Reconcile the pending goal stage before starting another stage.")
        prepared = self.app.prepare_workflow_start(run["project_id"], intent)
        blockers = [item for item in prepared["prerequisites"] if item["blocking"]]
        if blockers:
            self._blocked(
                run,
                "A required input needs attention before work can continue.",
                requirements=blockers,
            )
            return
        if evaluator and "initial_budget_usage" not in stage:
            from agentagon.capabilities.experiments.budget import BudgetLedger

            # A first baseline may reuse the evaluator's admission ledger. Freeze
            # the imported usage before dispatch so only this run's new admissions
            # consume its allowance. A replay retains this original checkpoint.
            stage["initial_budget_usage"] = {}
            owner = evaluator.get("budget_id")
            if owner:
                ledger = BudgetLedger(self.app.state.workspace(run["project_id"]), owner)
                if ledger.path.exists():
                    stage["initial_budget_usage"][owner] = ledger.spent(ledger.snapshot())
        # Persist the exact child request before dispatch. A lost response is replayed,
        # never reconstructed with a new operation or a different remaining budget.
        run.update(pending_intent=intent, stage=name, summary=LABELS[name], requirements=[])
        self._save(run)
        child = self.app.submit_task(
            run["project_id"],
            intent,
            goal_run={
                "id": run["id"],
                "details": run["details"] if not run.get("focus") else "",
            },
        )
        self._attach_pending(run, child)
        run["state"] = "running"
        self._save(run)

    def _advance(self, run):
        project_id = run["project_id"]
        agent_id, goal_id = self._target(run)
        if run.get("pending_intent"):
            child = self._pending_task(run)
            if child:
                self._attach_pending(run, child)
                self._save(run)
            else:
                self._launch(run, run["pending_intent"]["workflow"])
                return
        if run.get("active_task_id"):
            task = self.app.runtime.get(project_id, run["active_task_id"])
            state = task["state"]
            if (project_id, task["id"]) in self.app.runtime.active or state in {
                "queued",
                "running",
                "needs_input",
            }:
                projected = "needs_input" if state == "needs_input" else "running"
                summary = (
                    task.get("next_action") if state == "needs_input" else LABELS[run["stage"]]
                )
                if (run["state"], run["summary"]) != (projected, summary):
                    run.update(state=projected, summary=summary, requirements=[])
                    self._save(run)
                return
            if state not in {"completed", "completed_with_limits"}:
                self._blocked(
                    run,
                    task.get("next_action")
                    or "The saved task stopped. Inspect it before resuming.",
                    code="child_task_stopped",
                    state="paused" if state == "paused" else "failed",
                )
                return
            stage = self._stage(run, run["stage"])
            self._settle_trials(run, task, stage)
            stage.update(state="completed", completed_at=now())
            run["active_task_id"] = None
            if run["stage"] == "optimize":
                result_id = (task.get("result") or {}).get("run_id") or task.get(
                    "workflow_ids", {}
                ).get("run_id")
                if not result_id:
                    self._blocked(
                        run,
                        "The task finished without a verified improvement reference. Inspect its retained evidence.",
                        state="failed",
                    )
                    return
                run.update(
                    state="completed",
                    summary="Your improvement results are ready to review.",
                    outcome={"kind": "optimize", "id": result_id},
                    requirements=[],
                )
                self._save(run)
                self.live.discard((project_id, run["id"]))
                return
            self._save(run)
        if self._binding(project_id, run["agent_id"], run["goal_id"]) != run["intent_binding"] or (
            run.get("focus")
            and self._binding(project_id, agent_id, goal_id) != run["focus"]["binding"]
        ):
            self._blocked(
                run,
                "The agent scope or goal changed. Start a new run for the changed goal.",
                code="goal_changed",
                state="failed",
            )
            return
        if not self._owns_target(run):
            return
        self._defaults(run)
        if not run.get("preflight_complete"):
            preflight = self.app.prepare_workflow_start(
                project_id,
                {
                    "workflow": "design",
                    "agent_id": agent_id,
                    "input": {"type": "goal", "id": goal_id},
                    "assistant": self._assistant(),
                    "options": {},
                    "limits": run["limits"],
                },
            )
            blockers = [item for item in preflight["prerequisites"] if item["blocking"]]
            from agentagon.capabilities.experiments import checkouts

            try:
                workspace = self.app.state.workspace(project_id)
                if not workspace.is_git:
                    raise AuditError(
                        "Measured work needs a clean committed Git revision. Open a committed Git checkout before continuing. Detection and assessment remain available for this folder."
                    )
                checkouts.clean_revision(workspace.root)
            except AuditError as exc:
                blockers.append(
                    {
                        "code": "clean_source",
                        "blocking": True,
                        "message": str(exc),
                        "resolution": {"action": "commit_source", "context": {"reason": str(exc)}},
                    }
                )
            if blockers:
                self._blocked(
                    run,
                    "A required input needs attention before work can start.",
                    requirements=blockers,
                )
                return
            run["preflight_complete"] = True
            self._save(run)
        goal = self.app.catalog.goal_record(project_id, agent_id, goal_id)
        design_stage = self._stage(run, "design")
        # New constraints/examples may change what correctness means. Give the host
        # one design turn to reconcile them with existing measurements before reuse.
        if run["details"] and not run.get("focus") and not design_stage.get("task_id"):
            self._launch(run, "design")
            return
        try:
            accepted = self.app.designs.accepted(project_id, agent_id, goal_id)
        except AuditError:
            accepted = None
        draft = self.app.designs.get(project_id, agent_id, goal_id)
        agent = self.app.catalog.agent(project_id, agent_id)
        if (
            draft
            and draft["state"] == "draft"
            and draft["binding_digest"] == agent["binding_digest"]
        ):
            accepted = self._accept(run, draft)
        if not accepted:
            if draft and draft["binding_digest"] == agent["binding_digest"]:
                accepted = self._accept(run, draft)
            else:
                if self._stage(run, "design")["state"] == "completed":
                    self._blocked(
                        run,
                        "The design task did not retain usable measurements. Inspect its result.",
                    )
                    return
                self._launch(run, "design")
                return
        if run.get("acceptance_intent"):
            run["acceptances"].append(
                {
                    **run.pop("acceptance_intent"),
                    "accepted_id": accepted["id"],
                    "digest": accepted["digest"],
                }
            )
        design_stage = self._stage(run, "design")
        design_stage.update(
            state="completed", reused=not bool(design_stage.get("task_id")), evidence=accepted["id"]
        )
        goal = self.app.catalog.goal_record(project_id, agent_id, goal_id)
        readiness = self.app.catalog.measurement_status(project_id, agent_id, goal)
        if readiness["baseline"]["ready"]:
            from agentagon.capabilities.experiments import baselines, checkouts

            workspace = self.app.state.workspace(project_id)
            baseline = baselines.status(workspace, goal["measurement"]["baseline_id"])
            if baseline["source_revision"] != checkouts.clean_revision(workspace.root):
                readiness["baseline"] = {
                    "ready": False,
                    "reason": "Measure the current committed agent before optimizing.",
                }
        for name, key in (("eval", "evaluation"), ("baseline", "baseline")):
            stage = self._stage(run, name)
            if not readiness[key]["ready"]:
                if stage["state"] == "completed":
                    self._blocked(run, readiness[key]["reason"], code=key)
                    return
                self._launch(run, name)
                return
            stage.update(
                state="completed",
                reused=not bool(stage.get("task_id")),
                evidence=(goal.get("measurement") or {}).get(f"{key}_id"),
            )
        if run.get("focus"):
            run.pop("focus")
            run.update(
                stage="optimize", summary="Regression measurements are ready. Continuing your goal."
            )
            self._save(run)
            return
        suite = self.app.catalog.suite(project_id, agent_id, goal_id)
        missing = suite["missing"]
        unknown = [item for item in missing if not item.get("goal_id")]
        if unknown:
            item = unknown[0]
            sibling = self.app.catalog.agent(project_id, item["application_agent_id"])
            existing = [
                goal
                for goal in self.app.catalog.goals(project_id, sibling["id"])
                if goal["state"] == "active"
            ]
            if not existing:
                protected = self.app.catalog.save_goal(
                    project_id,
                    sibling["id"],
                    {
                        "operation_id": str(
                            uuid.uuid5(
                                uuid.UUID(run["authorization"]["operation_id"]),
                                "protect:" + sibling["id"],
                            )
                        ),
                        "name": ("Preserve " + sibling["name"])[:160],
                        "category": "correctness",
                        "objective": f"Preserve {sibling['name']}'s externally observable contracts and accepted behaviors while shared code is improved. Infer representative regression checks from code and retained evidence. Ask if a material expectation cannot be inferred; never treat current or observed outputs as ground truth.",
                        "source": {"kind": "goal"},
                    },
                )
                run.setdefault("protective_goals", []).append(
                    {
                        "agent_id": sibling["id"],
                        "goal_id": protected["id"],
                        "authorized_by": run["authorization"]["operation_id"],
                        "at": now(),
                    }
                )
                self._save(run)
            return
        if missing:
            item = missing[0]
            target_agent, target_goal = item["application_agent_id"], item["goal_id"]
            if (target_agent, target_goal) == (agent_id, goal_id):
                self._blocked(run, item["reason"], code="regression_baselines")
                return
            run["focus"] = {
                "agent_id": target_agent,
                "goal_id": target_goal,
                "name": item["name"],
                "binding": self._binding(project_id, target_agent, target_goal),
            }
            run.update(
                stage="design", summary=f"Preparing regression measurements for {item['name']}."
            )
            self._save(run)
            return
        from agentagon.capabilities.experiments import preparation

        workspace = self.app.state.workspace(project_id)
        repetitions = {
            member["goal_id"]: preparation.load(workspace, member["evaluation_id"])["package"][
                "spec"
            ]["repetitions"]
            for member in suite["members"]
        }
        primary = repetitions[goal_id]
        profile = Config().profile(workspace.root, run["resolved"]["profile"])
        available = min(self._accounting(run)["trials_remaining"], profile["limits"]["max_trials"])
        if profile["limits"]["max_candidates"] < 2:
            raise InputNeeded(
                "The selected runner must allow at least two candidates to compare an improvement with the baseline.",
                "execution_profile",
            )

        def fits(count, finalists):
            preparation_units = max(primary, count // 5)
            verification = max(
                primary * finalists + 2 * finalists * sum(repetitions.values()),
                math.ceil(count / 5),
            )
            return count - preparation_units - verification >= primary

        finalists = next(
            (
                count
                for count in (3, 2, 1)
                if 2 * count <= profile["limits"]["max_candidates"] and fits(available, count)
            ),
            None,
        )
        if finalists is None:
            minimum = next((count for count in range(available + 1, 86401) if fits(count, 1)), None)
            self._blocked(
                run,
                f"The remaining {available} trials cannot cover search and the protected regression checks. "
                + (f"At least {minimum} trials are needed for this stage. " if minimum else "")
                + "Start a new bounded run with enough allowance; saved measurements will be reused.",
                code="budget_exhausted",
                state="failed",
            )
            return
        run["resolved"]["finalist_count"] = finalists
        self._launch(run, "optimize")

    def _loop(self):
        with self.app.lock:
            while not self.stopping:
                for project_id, run_id in list(self.live):
                    run = None
                    try:
                        run = self._read(project_id, run_id)
                        self._advance(run)
                    except (AuditError, OSError, KeyError, ValueError) as exc:
                        if run is None:
                            self.live.discard((project_id, run_id))
                        else:
                            try:
                                self._blocked(
                                    run, str(exc), code=getattr(exc, "code", "goal_run_blocked")
                                )
                            except (AuditError, OSError):
                                # A removed project can no longer receive metadata;
                                # it must not stop progress for the other projects.
                                self.live.discard((project_id, run_id))
                self.app.lock.wait(timeout=1)

    def control(self, project_id, run_id, action, payload):
        if (
            action not in {"pause", "resume", "cancel"}
            or not isinstance(payload, dict)
            or set(payload) != {"operation_id"}
        ):
            raise AuditError("choose pause, resume or cancel with an operation_id")
        operation = operation_id(payload["operation_id"])
        with self.app.lock:
            run = self._read(project_id, run_id)
            prior = run["receipts"].get(operation)
            if prior:
                if prior != action:
                    raise AuditError("operation_id already belongs to another goal run control")
                return self._public(run)
            if action not in self._public(run)["available_actions"]:
                raise AuditError(
                    "This goal run cannot perform that action. Inspect its current task and allowance."
                )
            pending = self._pending_task(run)
            if pending:
                self._attach_pending(run, pending)
            child = (
                self.app.runtime.get(project_id, run["active_task_id"])
                if run.get("active_task_id")
                else None
            )
            if action == "resume":
                if child and child["recovery"]["resume_allowed"]:
                    if run["state"] == "failed":
                        target = self._target(run)
                        replacement = next(
                            (
                                item
                                for item in self.app.state.db.list_records(project_id, KIND)
                                if item["id"] != run_id
                                and item["state"] not in FINISHED
                                and (
                                    (item["agent_id"], item["goal_id"]) == target
                                    or self._target(item) == target
                                )
                            ),
                            None,
                        )
                        if replacement:
                            raise ActiveGoalRun(replacement["id"])
                    if not self._owns_target(run):
                        run["receipts"][operation] = action
                        self._save(run)
                        self.live.add((project_id, run_id))
                        self.app.lock.notify_all()
                        return self._public(run)
                    self.app.runtime.control(
                        project_id,
                        child["id"],
                        "resume",
                        {"operation_id": operation},
                        goal_run_id=run_id,
                    )
                run.update(state="running", summary="Continuing the saved work.", requirements=[])
                self.live.add((project_id, run_id))
            else:
                run.update(
                    state="paused" if action == "pause" else "cancelled",
                    summary="Paused. Resume when ready."
                    if action == "pause"
                    else "Stopped. Saved evidence is retained.",
                )
                self.live.discard((project_id, run_id))
                if child and action in child["available_actions"]:
                    self.app.runtime.control(
                        project_id, child["id"], action, {"operation_id": operation}
                    )
            run["receipts"][operation] = action
            self._save(run)
            self.app.lock.notify_all()
            return self._public(run)

    def close(self):
        with self.app.lock:
            self.stopping = True
            self.app.lock.notify_all()
        self.thread.join(timeout=5)
