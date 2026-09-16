"""Application operations shared by the browser and local launcher."""

import copy
import os
import re
import subprocess
import threading
import uuid
from pathlib import Path

from agentagon.core.records import AuditError, digest, load_json, now
from agentagon.dashboard import _audits, _detail, _run_detail, _run_summary, _runs, _summary
from agentagon.storage.changes import git_bytes
from agentagon.storage.config import Config
from agentagon.storage.issues import list_issues
from agentagon.telemetry.normalize import redact
from agentagon.webapp import snapshots
from agentagon.webapp.agents import detect_agents
from agentagon.webapp.catalog import Catalog
from agentagon.webapp.jobs import ACTIVE, JobManager, operation_id, public_job
from agentagon.webapp.providers import DEFAULT_ENDPOINTS, CredentialStore, ProviderClient
from agentagon.webapp.state import AppState, identifier, private_directory


class Application:
    def __init__(self, directory=None, *, execute=None, credentials=None, provider_factory=None):
        self.state = AppState(directory)
        self.credentials = credentials or CredentialStore()
        self.provider_factory = provider_factory or ProviderClient
        self.jobs = JobManager(self.state, self.credentials, execute)
        self.catalog = Catalog(self.state)
        self.jobs.verify_result = self.completed_job
        self.previews = {}
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

    def save_application_agent(self, project_id, payload, agent_id=None):
        selector = payload.get("trace_selector", {})
        if not isinstance(selector, dict):
            raise AuditError("agent trace selector must be an object")
        if selector.get("connection_id"):
            self.connection(selector["connection_id"], project_id)
        with self.lock:
            return self.catalog.save_agent(project_id, payload, agent_id)

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
        result["issues"] = [
            i
            for i in result["issues"]
            if set(i.get("audit_ids", [])) & audit_ids or i.get("latest_audit_id") in audit_ids
        ]
        history = self.catalog.metrics(project_id, agent_id)
        baseline_ids = {m["baseline_id"] for s in history["metrics"] for m in s["measurements"]}
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
            if payload.get("kind") != "audit":
                raise AuditError(
                    "select an application agent and focus before starting this workflow"
                )
            return self.jobs.submit(project_id, payload, request_binding=request_binding)
        with self.lock:
            agent = self.catalog.agent(project_id, agent_id)
            if agent["status"] != "confirmed":
                raise AuditError("confirm this application agent before starting work")
            focus = self.catalog.focus(project_id, agent_id, focus_id)
            options = payload.setdefault("options", {})
            if not isinstance(options, dict):
                raise AuditError("workflow options must be an object")
            if "investigation_plan" in options or "suite_manifest" in options:
                raise AuditError("workflow evidence bindings are prepared by the application")
            payload["goal"] = payload.get("goal") or focus["goal"]
            scopes = options.get("code_scopes")
            if scopes and scopes != agent["code_scopes"]:
                raise AuditError("update the agent binding before changing its audit code scope")
            options["code_scopes"] = agent["code_scopes"]
            for key in ("issue_id", "audit_id"):
                if focus["source"].get(key):
                    options.setdefault(key, focus["source"][key])
            self.catalog.validate_evidence_reference(project_id, agent_id, options)
            plan = self.catalog.investigation(project_id, agent_id, focus_id, options)
            options["investigation_plan"] = plan
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
            measurement = focus.get("measurement") or {}
            if payload["kind"] in {"baseline", "fix"} and measurement:
                if (
                    options.get("evaluation_id")
                    and options["evaluation_id"] != measurement["evaluation_id"]
                ):
                    raise AuditError(
                        "selected evaluator differs from this focus's accepted measurement"
                    )
                options["evaluation_id"] = measurement["evaluation_id"]
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
            return {
                **result,
                "measurement_note": "Focus changed during execution. Retained evidence needs an explicit measurement binding.",
            }
        if job["kind"] == "eval" and result.get("evaluation_id"):
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
            "models": [
                {**m, "name": m["display_name"], "default": m["is_default"]} for m in models
            ],
            "default_model": next((m["id"] for m in models if m["is_default"]), None),
        }

    def remove_project(self, project_id):
        with self.lock, self.jobs.condition:
            return self._remove_project(project_id)

    def _remove_project(self, project_id):
        self.state.project(project_id)
        if any(key[0] == project_id for key in self.jobs.active):
            raise AuditError("pause or cancel this project's tasks before removing it")
        try:
            jobs = self.jobs.list(project_id)
        except (AuditError, OSError):
            jobs = []
        if any(j["state"] in ACTIVE or (project_id, j["id"]) in self.jobs.active for j in jobs):
            raise AuditError("pause or cancel this project's tasks before removing it")
        return self.state.remove(project_id)

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
            "settings": self.settings(project_id),
        }

    def result(self, project_id, kind, result_id):
        from agentagon.experiments import baselines, inspection, preparation

        workspace = self.state.workspace(project_id)
        if kind in {"audit", "audits"}:
            return _detail(workspace, result_id)
        if kind in {"eval", "evaluations"}:
            return inspection.evaluation_summary(preparation.load(workspace, result_id))
        if kind in {"fix", "runs"}:
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
                }
                comparison = result.setdefault("comparisons", {})
                if suite["state"] != "completed":
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
                    finalist = (suite.get("binding") or {}).get("candidate_id")
                    comparison["alternatives"] = [
                        c for c in comparison.get("alternatives", []) if c["id"] == finalist
                    ]
            return result
        if kind in {"baseline", "baselines"}:
            return baselines.public_projection(baselines.status(workspace, result_id))
        if kind in {"dataset", "traces", "imports"}:
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

    def settings(self, project_id):
        workspace = self.state.workspace(project_id)
        config = Config()
        effective = config.effective(workspace.root)
        return {
            "settings": effective,
            "profiles": effective["profiles"],
            "revision": digest(effective),
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
        }:
            raise AuditError("unsupported settings fields")
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
                values, unset = payload.get("values", {}), payload.get("unset", [])
                if (
                    not isinstance(values, dict)
                    or not isinstance(unset, list)
                    or not all(isinstance(k, str) for k in unset)
                ):
                    raise AuditError("settings require values and an unset list")
                Config().update(scope, workspace.root, values, tuple(unset))
        return self.settings(project_id)

    def _connections(self):
        return self.state.read()["connections"]

    def connection(self, connection_id, project_id=None):
        identifier(connection_id, "connection")
        connection = self._connections().get(connection_id)
        if not connection or connection.get("disconnected"):
            raise AuditError("connection not found")
        if project_id and project_id not in connection.get("project_ids", []):
            raise AuditError("assign this connection to the selected project first")
        return connection

    def connection_projection(self, connection):
        value = {k: copy.deepcopy(v) for k, v in connection.items() if k != "credentials"}
        value["credential_fields"] = list(connection.get("credentials", {}))
        value["credential_mode"] = next(
            iter(connection.get("credentials", {}).values()), "session:"
        ).split(":", 1)[0]
        for reference in connection.get("credentials", {}).values():
            if reference.startswith(("session:", "env:")):
                try:
                    self.credentials.resolve(reference)
                except AuditError:
                    value["status"] = "needs_credentials"
        return value

    def connections(self):
        return {
            "connections": [
                self.connection_projection(c)
                for c in self._connections().values()
                if not c.get("disconnected")
            ]
        }

    def save_connection(self, payload):
        with self.lock:
            return self._save_connection(payload)

    def _save_connection(self, payload):
        allowed = {
            "id",
            "name",
            "provider",
            "endpoint",
            "project",
            "workspace_id",
            "project_ids",
            "credentials",
            "credential_mode",
        }
        if set(payload) - allowed:
            raise AuditError("unsupported connection fields")
        provider = payload.get("provider")
        if provider not in DEFAULT_ENDPOINTS:
            raise AuditError("choose Braintrust, LangSmith, or Langfuse")
        name = payload.get("name", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise AuditError("connection name must contain 1–100 characters")
        connection_id = payload.get("id") or "connection_" + uuid.uuid4().hex[:24]
        identifier(connection_id, "connection")
        previous = self._connections().get(connection_id, {})
        if previous and previous.get("provider") != provider:
            raise AuditError("create a new connection to change providers")
        project_ids = payload.get("project_ids", previous.get("project_ids", []))
        if not isinstance(project_ids, list) or len(project_ids) > 100:
            raise AuditError("project assignments must be a bounded list")
        for project_id in project_ids:
            self.state.project(project_id)
        mode = payload.get("credential_mode", "session")
        if mode not in {"env", "session", "keyring"}:
            raise AuditError("choose environment, session, or OS credential storage")
        values = payload.get("credentials", {})
        allowed_credentials = (
            {"public_key", "secret_key"} if provider == "langfuse" else {"api_key"}
        )
        if not isinstance(values, dict) or set(values) - allowed_credentials:
            raise AuditError("unsupported credential fields for this provider")
        connection = {
            "id": connection_id,
            "name": name.strip(),
            "provider": provider,
            "endpoint": payload.get("endpoint") or DEFAULT_ENDPOINTS[provider],
            "project": payload.get("project", ""),
            "workspace_id": payload.get("workspace_id", ""),
            "project_ids": list(dict.fromkeys(project_ids)),
            "credentials": dict(previous.get("credentials", {})),
            "status": "not_tested",
            "updated_at": now(),
        }
        for field in ("project", "workspace_id"):
            if not isinstance(connection[field], str) or len(connection[field]) > 500:
                raise AuditError(f"invalid {field}")
        self.provider_factory(
            connection, self.credentials
        )  # Validate endpoint before saving secrets.
        new_references = []
        try:
            for key, value in values.items():
                if not value:
                    continue
                if mode == "env":
                    if not isinstance(value, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
                        raise AuditError("credential references must be environment-variable names")
                    reference = "env:" + value
                else:
                    reference = self.credentials.set(connection_id, key, value, mode)
                    new_references.append(reference)
                connection["credentials"][key] = reference
            with self.state.locked() as data:
                data["connections"][connection_id] = connection
        except Exception:
            for reference in new_references:
                self.credentials.delete(reference)
            raise
        for reference in previous.get("credentials", {}).values():
            if reference not in connection["credentials"].values():
                self.credentials.delete(reference)
        return self.connection_projection(connection)

    def test_connection(self, connection_id):
        connection = self.connection(connection_id)
        try:
            result = self.provider_factory(connection, self.credentials).test()
        except AuditError:
            self._save_connection_test(connection, {"status": "unavailable"})
            raise
        self._save_connection_test(
            connection,
            {
                "status": result["status"],
                "last_checked_at": now(),
                "projects": result.get("projects", []),
            },
        )
        return {**self.connection_projection(self.connection(connection_id)), **result}

    def _save_connection_test(self, connection, changes):
        with self.lock:
            current = self.connection(connection["id"])
            ignored = {"status", "last_checked_at", "projects"}
            if {k: v for k, v in current.items() if k not in ignored} != {
                k: v for k, v in connection.items() if k not in ignored
            }:
                raise AuditError(
                    "connection changed during its check; test the current configuration"
                )
            with self.state.locked() as data:
                data["connections"][connection["id"]] = {**current, **changes}

    def disconnect(self, connection_id):
        with self.lock:
            return self._disconnect(connection_id)

    def _disconnect(self, connection_id):
        connection = self.connection(connection_id)
        for ref in connection.get("credentials", {}).values():
            self.credentials.delete(ref)
        with self.state.locked() as data:
            del data["connections"][connection_id]
        return {"disconnected": connection_id}

    def preview(self, project_id, payload):
        self.state.workspace(project_id)
        connection = self.connection(payload.get("connection_id"), project_id)
        if payload.get("kind") not in {"traces", "dataset"}:
            raise AuditError("choose traces or dataset")
        selection = copy.deepcopy(payload.get("selection", {}))
        if not isinstance(selection, dict):
            raise AuditError("selection must be an object")
        selection.setdefault("project", connection.get("project"))
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
            return self._save_agents(payload)

    def _save_agents(self, payload):
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
        model = payload.get("model", settings.get("models", {}).get(agent, ""))
        if not isinstance(model, str) or len(model) > 200:
            raise AuditError("invalid model name")
        if agent == "codex" and model:
            from agentagon.webapp.agents import validate_codex_model

            model = validate_codex_model(model, settings.get("codex_executable"))
        settings.update(default_agent=agent, model=model, concurrency=concurrency)
        settings.setdefault("models", {})[agent] = model
        key = payload.get("claude_api_key")
        if key:
            mode = payload.get("credential_mode", "session")
            if mode == "env":
                if not isinstance(key, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
                    raise AuditError("use an environment-variable name")
                settings["claude_api_key_ref"] = "env:" + key
            else:
                settings["claude_api_key_ref"] = self.credentials.set(
                    "claude", "api_key", key, mode
                )
        with self.state.locked() as data:
            data["agents"] = settings
        with self.jobs.condition:
            self.jobs._dispatch()
        return self.agents()

    def close(self):
        self.jobs.close()
