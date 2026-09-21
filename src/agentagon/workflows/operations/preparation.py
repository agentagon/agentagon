"""Side-effect-free workflow intent preparation and start validation.

Preparation is deliberately stricter than a display-only readiness hint.  It
normalizes the same public request accepted by submission, resolves the records
that request names, and reports every prerequisite that can be established
without running a provider, model, evaluator, or workflow.  Submission consumes
the same :class:`PreparedStart`, then re-runs preparation immediately before it
creates a task.
"""

from __future__ import annotations

import copy
import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentagon.core.records import AuditError, digest
from agentagon.domain.issues import get_issue
from agentagon.storage.changes import EXCLUDED, git_bytes, names, revision
from agentagon.workflows.registry import REGISTRY
from agentagon.workflows.runtime import BUDGET_DEFAULTS, TERMINAL, operation_id

PUBLIC_FIELDS = {
    "operation_id",
    "workflow",
    "agent_id",
    "input",
    "scope",
    "limits",
    "assistant",
    "model",
    "options",
    "expected_revisions",
}
INPUT_FIELDS = {"type", "id", "text"}
INPUT_TYPES = {"goal", "issue", "trace", "description", "agent", "project", "monitor"}
LIMIT_FIELDS = {"max_trials", "max_elapsed_seconds", "trial_timeout_seconds"}
OPTION_FIELDS = {
    "profile",
    "engine",
    "finalist_count",
    "host_concurrency",
    "evaluation_id",
    "baseline_id",
    "dataset_snapshot_id",
    "assessment",
    # Service-owned scheduled observation inputs. Manual observation rejects
    # these below unless they exactly match the monitor's durable pending request.
    "monitor_policy",
    "window",
    "scheduled",
}
REVISION_FIELDS = {
    "brain",
    "code_source",
    "project",
    "settings",
    "agent",
    "goal",
    "source",
    "measurement_design",
    "suite",
    "monitor",
}

CODE_SENSITIVE_WORKFLOWS = {
    "assess",
    "audit",
    "baseline",
    "design",
    "eval",
    "fix",
    "optimize",
}
BRAIN_REQUIRED_WORKFLOWS = {"audit", "baseline", "design", "discover", "eval", "fix", "optimize"}
COMMITTED_SOURCE_WORKFLOWS = {"baseline", "eval", "fix", "optimize"}
SUGGESTED_READ_ONLY_WORKFLOWS = {"assess", "audit", "discover"}
SOURCE_MAX_FILES = 10_000
SOURCE_MAX_BYTES = 64 * 1024 * 1024

EXPECTED_OUTPUTS = {
    "assess": ["agent_suggestions", "issues", "recommendations", "coverage"],
    "discover": ["grouped_issues", "occurrences", "diagnosis_limits"],
    "fix": ["repair_attempts", "verified_candidate", "independent_review"],
    "design": ["measurement_plan"],
    "eval": ["frozen_evaluator", "evaluation_review"],
    "baseline": ["baseline_measurement", "evaluation_identity"],
    "optimize": ["verified_candidates", "comparison", "independent_review"],
    "audit": ["audit_findings", "evidence_report"],
    "observe": ["production_observation", "coverage", "recommendations"],
}


def _evidence(kind: str, identity: str, **values: Any) -> dict:
    return {"kind": kind, "id": identity, **values}


def _prerequisite(
    code: str,
    *,
    state: str = "satisfied",
    field_name: str | None = None,
    evidence_refs: list[dict] | None = None,
    action: str | None = None,
    context: dict | None = None,
) -> dict:
    value = {
        "code": code,
        "state": state,
        "blocking": state in {"missing", "stale"},
        "evidence_refs": evidence_refs or [],
    }
    if field_name:
        value["field"] = field_name
    if action:
        value["resolution"] = {"action": action, "context": context or {}}
    elif context:
        value["context"] = context
    return value


def _limitation(code: str, *, field_name: str | None = None, context: dict | None = None):
    value = {"code": code, "evidence_refs": []}
    if field_name:
        value["field"] = field_name
    if context:
        value["context"] = context
    return value


def agent_review_scope(agent: dict) -> dict:
    """Freeze the exact suggested identity permitted for a read-only task."""

    return {
        "version": 1,
        "agent_id": agent["id"],
        "revision": agent["revision"],
        "binding_version": agent["binding_version"],
        "binding_digest": agent["binding_digest"],
        "code_scopes": copy.deepcopy(agent["code_scopes"]),
        "shared_dependencies": copy.deepcopy(agent["shared_dependencies"]),
        "trace_selector": copy.deepcopy(agent["trace_selector"]),
    }


def _path_fingerprint(root: Path, relative: str, remaining: int) -> tuple[dict, int, bool]:
    """Return a bounded content identity for one source path."""

    lexical = Path(relative)
    if lexical.is_absolute() or ".." in lexical.parts:
        return {"path": relative, "kind": "invalid"}, 0, False
    path = root / lexical
    try:
        metadata = path.lstat()
    except OSError:
        return {"path": relative, "kind": "unavailable"}, 0, False
    record = {
        "path": relative,
        "mode": stat.S_IMODE(metadata.st_mode),
        "size": metadata.st_size,
    }
    if stat.S_ISLNK(metadata.st_mode):
        try:
            record.update(kind="symlink", target=os.readlink(path))
        except OSError:
            record.update(kind="symlink_unavailable")
            return record, 0, False
        return record, 0, True
    if not stat.S_ISREG(metadata.st_mode):
        record["kind"] = "directory" if stat.S_ISDIR(metadata.st_mode) else "special"
        record["modified_ns"] = metadata.st_mtime_ns
        return record, 0, False
    record["kind"] = "file"
    if metadata.st_size > remaining:
        # Metadata still makes the bounded identity useful, but callers expose
        # that the snapshot is incomplete instead of claiming an exact pin.
        record["modified_ns"] = metadata.st_mtime_ns
        return record, 0, False
    content = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                content.update(chunk)
    except OSError:
        record["kind"] = "unavailable"
        return record, 0, False
    record["digest"] = content.hexdigest()
    return record, metadata.st_size, True


def _fingerprint_paths(root: Path, paths: list[str]) -> tuple[str, int, int, bool]:
    records = []
    captured_bytes = 0
    complete = True
    selected = sorted(set(paths))
    if len(selected) > SOURCE_MAX_FILES:
        selected = selected[:SOURCE_MAX_FILES]
        complete = False
    for relative in selected:
        record, used, exact = _path_fingerprint(
            root, relative, max(0, SOURCE_MAX_BYTES - captured_bytes)
        )
        records.append(record)
        captured_bytes += used
        complete = complete and exact
    return digest(records), len(records), captured_bytes, complete


def _git_source_identity(root: Path) -> dict:
    head = revision(root)
    status = git_bytes(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    changed: list[str] = []
    comparisons = (
        (("diff", "--name-only", "-z", "HEAD", "--"),)
        if head
        else (
            ("diff", "--name-only", "-z", "--cached", "--"),
            ("diff", "--name-only", "-z", "--"),
        )
    )
    for arguments in comparisons:
        value = git_bytes(root, *arguments, optional=True)
        if value:
            changed.extend(names(value))
    untracked = git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked:
        changed.extend(names(untracked))
    index_state = (
        git_bytes(root, "diff", "--cached", "--raw", "--no-abbrev", "-z", "HEAD", "--")
        if head
        else git_bytes(root, "ls-files", "--stage", "-z")
    )
    content, path_count, captured_bytes, complete = _fingerprint_paths(root, changed)
    source = {
        "kind": "git",
        "revision": head,
        "dirty": bool(status),
        "changed_paths": path_count,
        "captured_bytes": captured_bytes,
        "complete": complete,
        "status_digest": hashlib.sha256(status).hexdigest(),
        "index_digest": hashlib.sha256(index_state).hexdigest(),
        "content_digest": content,
    }
    source["fingerprint"] = digest(source)
    return source


def _folder_source_paths(root: Path) -> tuple[list[str], list[str], bool]:
    pending = [root]
    paths: list[str] = []
    unavailable: list[str] = []
    complete = True
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError:
            unavailable.append(directory.relative_to(root).as_posix())
            complete = False
            continue
        child_directories = []
        for entry in entries:
            relative = Path(entry.path).relative_to(root).as_posix()
            if entry.name in EXCLUDED and entry.is_dir(follow_symlinks=False):
                continue
            if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                child_directories.append(Path(entry.path))
                continue
            paths.append(relative)
            if len(paths) >= SOURCE_MAX_FILES:
                complete = False
                pending.clear()
                child_directories.clear()
                break
        pending.extend(reversed(child_directories))
    return paths, unavailable, complete


def _folder_source_identity(root: Path) -> dict:
    paths, unavailable, inventory_complete = _folder_source_paths(root)
    content, path_count, captured_bytes, content_complete = _fingerprint_paths(root, paths)
    source = {
        "kind": "folder",
        "revision": None,
        "dirty": None,
        "file_count": path_count,
        "captured_bytes": captured_bytes,
        "complete": inventory_complete and content_complete and not unavailable,
        "unavailable_paths": unavailable[:100],
        "content_digest": content,
    }
    source["fingerprint"] = digest(source)
    return source


def _capture_code_source(prepared: PreparedStart) -> None:
    if prepared.workflow not in CODE_SENSITIVE_WORKFLOWS:
        return
    source = (
        _git_source_identity(prepared.workspace.root)
        if prepared.workspace.is_git
        else _folder_source_identity(prepared.workspace.root)
    )
    prepared.code_source = source
    prepared.revisions["code_source"] = source["fingerprint"]
    prepared.prerequisites.append(
        _prerequisite(
            "source_snapshot",
            evidence_refs=[
                _evidence(
                    "code_source",
                    source["fingerprint"],
                    source_kind=source["kind"],
                    revision=source["revision"],
                    dirty=source["dirty"],
                    complete=source["complete"],
                )
            ],
        )
    )
    if prepared.workflow in COMMITTED_SOURCE_WORKFLOWS:
        committed = (
            source["kind"] == "git"
            and isinstance(source.get("revision"), str)
            and bool(source["revision"])
            and source["dirty"] is False
            and source["complete"] is True
        )
        prepared.prerequisites.append(
            _prerequisite(
                "clean_source",
                state="satisfied" if committed else "missing",
                field_name="source",
                evidence_refs=[
                    _evidence(
                        "code_source",
                        source["fingerprint"],
                        source_kind=source["kind"],
                        revision=source["revision"],
                        dirty=source["dirty"],
                        complete=source["complete"],
                    )
                ],
                action=None if committed else "prepare_committed_source",
                context={
                    "source_kind": source["kind"],
                    "dirty": source["dirty"],
                    "revision": source["revision"],
                    "reason": (
                        "Measured work needs a clean committed Git revision. Assessment and diagnosis remain available for this folder."
                        if source["kind"] != "git"
                        else "Measured work needs a clean committed Git revision. Commit or move the intended changes before starting."
                        if source["dirty"]
                        else "The source revision could not be pinned completely."
                    ),
                },
            )
        )
    if not source["complete"]:
        prepared.limitations.append(
            _limitation(
                "source_identity_partial",
                context={
                    "kind": source["kind"],
                    "captured_bytes": source["captured_bytes"],
                    "captured_paths": source.get("changed_paths", source.get("file_count", 0)),
                },
            )
        )


@dataclass
class PreparedStart:
    """Validated request plus records needed by the mutating submission step."""

    project_id: str
    workflow: str
    normalized_intent: dict
    source: dict
    options: dict
    operation: str | None
    workspace: Any
    agent: dict | None = None
    goal: dict | None = None
    issue: dict | None = None
    suite: dict | None = None
    objective: str | None = None
    prerequisites: list[dict] = field(default_factory=list)
    limitations: list[dict] = field(default_factory=list)
    revisions: dict[str, str] = field(default_factory=dict)
    source_identity: dict | None = None
    code_source: dict | None = None
    settings: dict = field(default_factory=dict)

    @property
    def state(self) -> str:
        if any(item["blocking"] for item in self.prerequisites):
            return "needs_input"
        return "ready_with_limits" if self.limitations else "ready"

    def projection(self) -> dict:
        result = {
            "state": self.state,
            "normalized_intent": copy.deepcopy(self.normalized_intent),
            "prerequisites": copy.deepcopy(self.prerequisites),
            "limitations": copy.deepcopy(self.limitations),
            "expected_outputs": copy.deepcopy(EXPECTED_OUTPUTS[self.workflow]),
            "revisions": copy.deepcopy(self.revisions),
        }
        if self.source_identity:
            result["source_identity"] = copy.deepcopy(self.source_identity)
        if self.code_source:
            result["code_source"] = copy.deepcopy(self.code_source)
        return result

    def require_ready(self) -> None:
        blocker = next((item for item in self.prerequisites if item["blocking"]), None)
        if blocker:
            raise AuditError(_blocking_message(blocker))


def _blocking_message(blocker: dict) -> str:
    code = blocker["code"]
    context = blocker.get("resolution", {}).get("context", {})
    names = context.get("names", [])
    messages = {
        "input_required": "Choose the evidence or objective for this workflow.",
        "agent_selection": "Select the agent that owns this work; ownership is ambiguous.",
        "agent_confirmation": "Confirm the agent binding before starting work.",
        "issue_ownership": "Issue belongs to another agent.",
        "goal_selection": "Select a goal for this workflow.",
        "trace_selection": "Select a trace snapshot.",
        "problem_description": "Describe the problem to fix.",
        "code_binding": "Bind application code before starting measured code changes.",
        "code_scope": "Repair scope must stay inside the confirmed agent binding.",
        "clean_source": "Use a clean committed Git revision before starting measured work.",
        "coding_backend": "Configure and authenticate the selected coding backend.",
        "measurement_plan": "Accept the measurement plan before preparing an evaluation.",
        "evaluation": "Prepare a reviewed evaluation before running a baseline.",
        "baseline": "Run a current baseline before optimizing this goal.",
        "pending_observation": "Finish, resume or discard the pending observation first.",
        "execution_profile": "Choose a configured execution profile in Settings before starting this workflow.",
        "stale_preparation": "Workflow inputs changed after preparation; review the current scope and evidence.",
    }
    if code == "regression_baselines":
        return "Existing regression goals need current baselines before repair: " + ", ".join(names)
    return messages.get(code, "Complete the required workflow input before starting.")


def _validate_shape(payload: dict, *, require_operation: bool) -> tuple[str, str | None]:
    if not isinstance(payload, dict) or set(payload) - PUBLIC_FIELDS:
        raise AuditError("unsupported task fields")
    workflow = payload.get("workflow")
    if workflow not in REGISTRY:
        raise AuditError("choose a built-in workflow")
    supplied_operation = payload.get("operation_id")
    operation = operation_id(supplied_operation) if supplied_operation is not None else None
    if require_operation and operation is None:
        raise AuditError("operation_id must be a UUID")
    return workflow, operation


def _validate_common_values(payload: dict) -> tuple[dict, dict, dict]:
    source = copy.deepcopy(payload.get("input", {}))
    if not isinstance(source, dict) or set(source) - INPUT_FIELDS:
        raise AuditError("unsupported workflow input fields")
    if source.get("type") is not None and source["type"] not in INPUT_TYPES:
        raise AuditError(
            "input.type must be goal, issue, trace, description, agent, project, or monitor"
        )
    assistant = payload.get("assistant")
    if assistant is not None and assistant not in {"codex", "claude"}:
        raise AuditError("assistant must be codex or claude")
    model = payload.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 200):
        raise AuditError("model must be a bounded nonempty name")
    options = copy.deepcopy(payload.get("options", {}))
    limits = copy.deepcopy(payload.get("limits", {}))
    if not isinstance(options, dict) or set(options) - OPTION_FIELDS:
        raise AuditError("unsupported workflow configuration")
    if not isinstance(limits, dict) or set(limits) - LIMIT_FIELDS:
        raise AuditError("invalid workflow options or limits")
    for key, default in BUDGET_DEFAULTS.items():
        value = limits.get(key, default)
        if type(value) is not int or not 1 <= value <= 86400:
            raise AuditError(f"{key} must be an integer between 1 and 86400")
        limits[key] = value
    scope = copy.deepcopy(payload.get("scope"))
    if scope is not None and (
        not isinstance(scope, list)
        or not scope
        or len(scope) > 100
        or any(not isinstance(item, str) for item in scope)
    ):
        raise AuditError("scope must be a nonempty bounded list of relative paths")
    if scope:
        from agentagon.capabilities.experiments.spec import path

        for item in scope:
            path(item)
            if item.startswith(".agentagon"):
                raise AuditError("private state is not an editable code scope")
    expected = copy.deepcopy(payload.get("expected_revisions"))
    if expected is not None and (
        not isinstance(expected, dict)
        or set(expected) - REVISION_FIELDS
        or any(not isinstance(value, str) or not value for value in expected.values())
    ):
        raise AuditError("expected_revisions must contain supported revision tokens")
    return source, options, limits


def _agent_for(application, project_id, workflow, payload, issue, prerequisites):
    agent_id = payload.get("agent_id") or (issue.get("agent_id") if issue else None)
    if not agent_id and workflow not in {"discover", "assess", "observe"}:
        confirmed = [
            item for item in application.catalog.agents(project_id) if item["status"] == "confirmed"
        ]
        if len(confirmed) == 1:
            agent_id = confirmed[0]["id"]
        else:
            prerequisites.append(
                _prerequisite(
                    "agent_selection",
                    state="missing",
                    field_name="agent_id",
                    action="select_agent",
                    context={"eligible_agent_ids": [item["id"] for item in confirmed]},
                )
            )
            return None
    if not agent_id:
        return None
    agent = application.catalog.agent(project_id, agent_id)
    reference = [
        _evidence(
            "agent_binding",
            agent["id"],
            revision=agent["revision"],
            binding_digest=agent["binding_digest"],
            paths=copy.deepcopy(agent["code_scopes"]),
            shared_dependencies=copy.deepcopy(agent["shared_dependencies"]),
        )
    ]
    if agent["status"] == "suggested" and workflow in SUGGESTED_READ_ONLY_WORKFLOWS:
        prerequisites.append(
            _prerequisite(
                "suggested_agent_read_only",
                evidence_refs=reference,
                context={"agent_id": agent["id"], "workflow": workflow, "read_only": True},
            )
        )
    elif agent["status"] != "confirmed":
        prerequisites.append(
            _prerequisite(
                "agent_confirmation",
                state="missing",
                field_name="agent_id",
                evidence_refs=reference,
                action="restore_agent" if agent["status"] == "archived" else "confirm_agent",
                context={"agent_id": agent["id"], "status": agent["status"]},
            )
        )
    else:
        prerequisites.append(_prerequisite("agent_confirmation", evidence_refs=reference))
    return agent


def _scope_for(agent, requested, prerequisites, *, workflow):
    if agent and agent["status"] == "suggested" and workflow == "audit":
        frozen = copy.deepcopy(agent["code_scopes"])
        if requested is not None and requested != frozen:
            raise AuditError("read-only suggested-agent audit uses the exact displayed code scope")
        if frozen:
            prerequisites.append(
                _prerequisite(
                    "code_scope",
                    field_name="scope",
                    evidence_refs=[
                        _evidence(
                            "agent_binding",
                            agent["id"],
                            revision=agent["revision"],
                            binding_digest=agent["binding_digest"],
                            paths=frozen,
                        )
                    ],
                    context={"permitted_paths": frozen, "read_only": True},
                )
            )
        return frozen or requested
    if not agent or workflow not in {"fix", "optimize"}:
        return requested
    from agentagon.capabilities.experiments.checkouts import under

    if not agent["code_scopes"]:
        prerequisites.append(
            _prerequisite(
                "code_binding",
                state="missing",
                field_name="scope",
                action="bind_code",
                context={"agent_id": agent["id"]},
            )
        )
        return requested
    permitted = requested or copy.deepcopy(agent["code_scopes"])
    valid = bool(permitted) and all(
        any(under(path, scope) for scope in agent["code_scopes"] + agent["shared_dependencies"])
        for path in permitted
    )
    prerequisites.append(
        _prerequisite(
            "code_scope",
            state="satisfied" if valid else "missing",
            field_name="scope",
            evidence_refs=[
                _evidence(
                    "agent_binding",
                    agent["id"],
                    revision=agent["revision"],
                    paths=copy.deepcopy(agent["code_scopes"]),
                    shared_dependencies=copy.deepcopy(agent["shared_dependencies"]),
                )
            ],
            action=None if valid else "narrow_scope",
            context={"permitted_paths": permitted},
        )
    )
    return permitted


def _production_preparation(application, prepared, *, scheduled):
    workflow, source = prepared.workflow, prepared.source
    if source.get("type") is None:
        prepared.prerequisites.append(
            _prerequisite(
                "input_required",
                state="missing",
                field_name="input",
                action="choose_input",
                context={"types": ["project" if workflow == "assess" else "monitor"]},
            )
        )
        return
    required = "project" if workflow == "assess" else "monitor"
    if source.get("type") != required:
        raise AuditError("assessment requires project input; observation requires monitor input")
    if workflow == "assess":
        if source.get("id", prepared.project_id) != prepared.project_id:
            raise AuditError("invalid project assessment input")
        workflow_options = {
            key: value for key, value in prepared.options.items() if key not in LIMIT_FIELDS
        }
        if set(workflow_options) - {"assessment", "agent_review_scope"}:
            raise AuditError("invalid project assessment input")
        configured = workflow_options.get(
            "assessment", application.production.onboarding(prepared.project_id)["scope"]
        )
        prepared.options = {
            "assessment": application.production.scope(prepared.project_id, configured),
            **{key: prepared.options[key] for key in LIMIT_FIELDS},
            **(
                {"agent_review_scope": workflow_options["agent_review_scope"]}
                if "agent_review_scope" in workflow_options
                else {}
            ),
        }
        prepared.source["id"] = prepared.project_id
        prepared.source_identity = {"kind": "project", "id": prepared.project_id}
        connection_id = prepared.options["assessment"].get("connection_id")
        if connection_id:
            connection = application.connection_projection(
                application.connection(connection_id, prepared.project_id)
            )
            if connection.get("status") in {"needs_credentials", "unavailable"}:
                prepared.prerequisites.append(
                    _prerequisite(
                        "trace_connection",
                        state="missing",
                        field_name="options.assessment.connection_id",
                        action="configure_connection",
                        context={
                            "connection_id": connection_id,
                            "status": connection["status"],
                            "reason": (
                                "The selected provider connection needs credentials."
                                if connection["status"] == "needs_credentials"
                                else "The selected provider connection is unavailable."
                            ),
                        },
                    )
                )
        if not any(
            prepared.options["assessment"].get(key) for key in ("connection_id", "snapshot_id")
        ):
            prepared.limitations.append(
                _limitation("code_only_assessment", field_name="options.assessment")
            )
        return
    monitor_id = source.get("id")
    if not monitor_id:
        prepared.prerequisites.append(
            _prerequisite(
                "input_required",
                state="missing",
                field_name="input.id",
                action="select_monitor",
            )
        )
        return
    monitor = application.monitoring.get(prepared.project_id, monitor_id)
    if prepared.agent is None:
        prepared.agent = application.catalog.agent(prepared.project_id, monitor["agent_id"])
        prepared.normalized_intent["agent_id"] = prepared.agent["id"]
        prepared.revisions["agent"] = digest(
            {
                "id": prepared.agent["id"],
                "revision": prepared.agent["revision"],
                "binding_digest": prepared.agent["binding_digest"],
            }
        )
        reference = [
            _evidence("agent_binding", prepared.agent["id"], revision=prepared.agent["revision"])
        ]
        prepared.prerequisites.append(
            _prerequisite(
                "agent_confirmation",
                state="satisfied" if prepared.agent["status"] == "confirmed" else "missing",
                field_name="agent_id",
                evidence_refs=reference,
                action=None if prepared.agent["status"] == "confirmed" else "confirm_agent",
                context={"agent_id": prepared.agent["id"]},
            )
        )
    prepared.source_identity = {
        "kind": "monitor",
        "id": monitor["id"],
        "series": monitor["series"],
        "environment": monitor["selector"].get("environment"),
    }
    prepared.revisions["monitor"] = digest(
        {
            "revision": monitor["revision"],
            "policy_digest": monitor["policy_digest"],
            "series": monitor["series"],
        }
    )
    if prepared.agent["id"] != monitor["agent_id"]:
        raise AuditError("monitor belongs to another agent")
    if monitor.get("task_id"):
        current = application.runtime.get(prepared.project_id, monitor["task_id"])
        if current["state"] not in TERMINAL:
            prepared.prerequisites.append(
                _prerequisite(
                    "pending_observation",
                    state="missing",
                    evidence_refs=[_evidence("task", current["id"], state=current["state"])],
                    action="resume_task",
                    context={"task_id": current["id"]},
                )
            )
    workflow_options = {
        key: value for key, value in prepared.options.items() if key not in LIMIT_FIELDS
    }
    if scheduled:
        if workflow_options != (monitor.get("pending") or {}).get("options"):
            raise AuditError("scheduled observation differs from its saved submission")
    elif workflow_options:
        raise AuditError("observation uses its saved monitor policy")


def prepare(
    application,
    project_id: str,
    payload: dict,
    *,
    scheduled: bool = False,
    require_operation: bool = False,
) -> PreparedStart:
    """Return a normalized, side-effect-free start decision.

    Malformed or unsupported contracts raise ``AuditError``.  Recoverable product
    prerequisites are represented in the returned decision and only become an
    error when submission calls :meth:`PreparedStart.require_ready`.
    """

    workflow, operation = _validate_shape(payload, require_operation=require_operation)
    source, public_options, limits = _validate_common_values(payload)
    project = application.state.project(project_id)
    workspace = application.state.workspace(project_id)
    prerequisites: list[dict] = []
    limitations: list[dict] = []
    revisions = {"project": digest(project)}
    settings = {}
    try:
        settings_record = application.settings(project_id)
        revisions["settings"] = settings_record["revision"]
        settings = settings_record["settings"]
    except (AuditError, OSError):
        limitations.append(_limitation("execution_settings_unavailable"))

    issue = None
    if source.get("type") == "issue" and source.get("id"):
        issue = get_issue(workspace, source["id"])
        revisions["source"] = digest({"id": issue["issue_id"], "revision": issue["revision"]})
    agent = _agent_for(application, project_id, workflow, payload, issue, prerequisites)
    if agent:
        revisions["agent"] = digest(
            {
                "id": agent["id"],
                "revision": agent["revision"],
                "binding_digest": agent["binding_digest"],
            }
        )
    if issue and agent and issue.get("agent_id") not in (None, agent["id"]):
        prerequisites.append(
            _prerequisite(
                "issue_ownership",
                state="missing",
                field_name="agent_id",
                evidence_refs=[_evidence("issue", issue["issue_id"], revision=issue["revision"])],
                action="select_agent",
                context={"agent_id": issue.get("agent_id")},
            )
        )

    permitted = _scope_for(agent, payload.get("scope"), prerequisites, workflow=workflow)
    task_options = copy.deepcopy(public_options)
    task_options.update(limits)
    if agent and agent["status"] == "suggested" and workflow in SUGGESTED_READ_ONLY_WORKFLOWS:
        task_options["agent_review_scope"] = agent_review_scope(agent)
    if permitted:
        task_options["permitted_paths"] = copy.deepcopy(permitted)

    normalized = {
        "workflow": workflow,
        "input": copy.deepcopy(source),
        "options": copy.deepcopy(public_options),
        "limits": copy.deepcopy(limits),
    }
    for key in ("assistant", "model"):
        if key in payload:
            normalized[key] = payload[key]
    if operation:
        normalized["operation_id"] = operation
    if agent:
        normalized["agent_id"] = agent["id"]
    elif payload.get("agent_id"):
        normalized["agent_id"] = payload["agent_id"]
    if permitted:
        normalized["scope"] = copy.deepcopy(permitted)

    prepared = PreparedStart(
        project_id=project_id,
        workflow=workflow,
        normalized_intent=normalized,
        source=source,
        options=task_options,
        operation=operation,
        workspace=workspace,
        agent=agent,
        issue=issue,
        prerequisites=prerequisites,
        limitations=limitations,
        revisions=revisions,
        settings=settings,
    )
    _capture_code_source(prepared)

    if workflow in {"assess", "observe"}:
        _production_preparation(application, prepared, scheduled=scheduled)
    else:
        _regular_preparation(application, prepared)
        _brain_prerequisite(application, prepared)
        _execution_prerequisite(prepared)

    expected = payload.get("expected_revisions")
    if expected is not None and expected != prepared.revisions:
        prepared.prerequisites.append(
            _prerequisite(
                "stale_preparation",
                state="stale",
                field_name="expected_revisions",
                action="review_changes",
                context={
                    "changed": sorted(
                        key
                        for key in set(expected) | set(prepared.revisions)
                        if expected.get(key) != prepared.revisions.get(key)
                    )
                },
            )
        )
    prepared.normalized_intent["input"] = copy.deepcopy(prepared.source)
    prepared.normalized_intent["expected_revisions"] = copy.deepcopy(prepared.revisions)
    if prepared.workflow == "assess":
        prepared.normalized_intent["options"] = {
            key: copy.deepcopy(value)
            for key, value in prepared.options.items()
            if key not in LIMIT_FIELDS and key != "agent_review_scope"
        }
    return prepared


def _regular_preparation(application, prepared: PreparedStart) -> None:
    workflow, source, agent = prepared.workflow, prepared.source, prepared.agent
    source_type = source.get("type")
    if source_type is None:
        prepared.prerequisites.append(
            _prerequisite(
                "input_required",
                state="missing",
                field_name="input",
                action="choose_input",
                context={"types": _input_types_for(workflow)},
            )
        )
        return
    if workflow == "discover" and source_type != "trace":
        raise AuditError("Discover issues requires selected traces")
    if workflow == "fix" and source_type not in {"issue", "trace", "description"}:
        raise AuditError("Fix requires an issue, trace, or problem description")
    if REGISTRY[workflow]["requires_goal"] and source_type != "goal":
        prepared.prerequisites.append(
            _prerequisite(
                "goal_selection",
                state="missing",
                field_name="input",
                action="select_goal",
                context={"agent_id": agent["id"] if agent else None},
            )
        )

    if source_type == "trace":
        if not source.get("id"):
            prepared.prerequisites.append(
                _prerequisite(
                    "trace_selection",
                    state="missing",
                    field_name="input.id",
                    action="select_trace",
                )
            )
        else:
            from agentagon.capabilities.traces import snapshots

            trace = snapshots.load(prepared.workspace, source["id"])
            if trace["kind"] != "traces":
                raise AuditError("select a trace snapshot")
            prepared.options["trace_snapshot_id"] = trace["id"]
            prepared.revisions["source"] = trace["digest"]
            prepared.source_identity = {
                "kind": "trace_snapshot",
                "id": trace["id"],
                "digest": trace["digest"],
                "count": len(trace["items"]),
                "provider": trace["provenance"].get("provider"),
            }
            prepared.prerequisites.append(
                _prerequisite(
                    "trace_selection",
                    evidence_refs=[
                        _evidence("trace_snapshot", trace["id"], digest=trace["digest"])
                    ],
                )
            )
    elif source_type == "issue":
        if not source.get("id"):
            prepared.prerequisites.append(
                _prerequisite(
                    "input_required",
                    state="missing",
                    field_name="input.id",
                    action="select_issue",
                )
            )
        elif prepared.issue:
            prepared.options["issue_id"] = prepared.issue["issue_id"]
            snapshots_for_issue = [
                occurrence.get("source_id")
                for occurrence in prepared.issue["occurrences"]
                if str(occurrence.get("source_id", "")).startswith("snapshot_")
            ]
            if snapshots_for_issue:
                prepared.options["trace_snapshot_id"] = snapshots_for_issue[-1]
            prepared.source_identity = {
                "kind": "issue",
                "id": prepared.issue["issue_id"],
                "revision": prepared.issue["revision"],
                "occurrence_count": len(prepared.issue["occurrences"]),
            }
    elif source_type == "description":
        objective = source.get("text")
        if not isinstance(objective, str) or not objective.strip():
            prepared.prerequisites.append(
                _prerequisite(
                    "problem_description",
                    state="missing",
                    field_name="input.text",
                    action="describe_problem",
                )
            )
        elif len(objective) > 4000:
            raise AuditError("problem description must be at most 4000 characters")
        else:
            prepared.objective = objective.strip()

    if source_type == "goal" and source.get("id") and agent:
        prepared.goal = application.catalog.goal_record(
            prepared.project_id, agent["id"], source["id"]
        )
        prepared.revisions["goal"] = digest(
            {
                "id": prepared.goal["id"],
                "revision": prepared.goal["revision"],
                "version": prepared.goal["version"],
            }
        )
        prepared.source_identity = {
            "kind": "goal",
            "id": prepared.goal["id"],
            "revision": prepared.goal["revision"],
            "version": prepared.goal["version"],
        }
        prepared.prerequisites.append(
            _prerequisite(
                "goal_selection",
                evidence_refs=[
                    _evidence("goal", prepared.goal["id"], revision=prepared.goal["revision"])
                ],
            )
        )
    elif source_type == "goal" and not source.get("id"):
        prepared.prerequisites.append(
            _prerequisite(
                "goal_selection",
                state="missing",
                field_name="input.id",
                action="select_goal",
                context={"agent_id": agent["id"] if agent else None},
            )
        )

    if workflow in {"eval", "baseline", "optimize"} and prepared.goal and agent:
        _measurement_prerequisites(application, prepared)
    if workflow in {"fix", "optimize"} and agent and agent["code_scopes"]:
        _regression_prerequisites(application, prepared)
    if workflow == "audit" and agent and not agent["code_scopes"]:
        if not prepared.options.get("trace_snapshot_id"):
            prepared.prerequisites.append(
                _prerequisite(
                    "code_binding",
                    state="missing",
                    field_name="agent_id",
                    action="bind_code_or_trace",
                    context={"agent_id": agent["id"]},
                )
            )
        else:
            prepared.limitations.append(_limitation("trace_only_audit"))
    if prepared.issue:
        prepared.objective = prepared.issue["summary"]
        if prepared.issue.get("expected_behavior"):
            prepared.objective += (
                "\nConfirmed expected behavior: " + prepared.issue["expected_behavior"]
            )
    if workflow == "discover":
        prepared.objective = "Find supported issues in the selected traces"
    elif workflow == "fix" and source_type == "trace":
        prepared.objective = "Fix the failure shown in the selected trace"


def _input_types_for(workflow: str) -> list[str]:
    if workflow == "discover":
        return ["trace"]
    if workflow == "fix":
        return ["issue", "trace", "description"]
    if workflow == "audit":
        return ["agent", "goal", "trace"]
    return ["goal"]


def _brain_prerequisite(application, prepared: PreparedStart) -> None:
    if prepared.workflow not in BRAIN_REQUIRED_WORKFLOWS:
        return
    configured = application.state.read()["agents"]
    selected = prepared.normalized_intent.get("assistant") or configured.get("default_agent")
    detected = {item["id"]: item for item in application.assistants()["assistants"]}
    backend = detected.get(selected, {})
    identity = {
        "id": selected,
        "available": backend.get("available") is True,
        "authenticated": backend.get("authenticated"),
        "version": backend.get("version"),
        "model": prepared.normalized_intent.get("model")
        or configured.get("models", {}).get(selected),
    }
    prepared.revisions["brain"] = digest(identity)
    ready = identity["available"] and identity["authenticated"] is True
    prepared.prerequisites.append(
        _prerequisite(
            "coding_backend",
            state="satisfied" if ready else "missing",
            field_name="assistant",
            evidence_refs=[_evidence("coding_backend", selected or "unconfigured", **identity)],
            action=None if ready else "configure_coding_backend",
            context={
                **identity,
                "reason": (
                    "The selected coding backend is not installed."
                    if not identity["available"]
                    else "Authentication is required."
                    if identity["authenticated"] is False
                    else "Authentication readiness is unknown; refresh the backend status."
                ),
            },
        )
    )


def _execution_prerequisite(prepared: PreparedStart) -> None:
    if prepared.workflow not in {"fix", "eval", "baseline", "optimize"}:
        return
    profiles = prepared.settings.get("profiles", {}) if prepared.settings else {}
    requested = prepared.options.get("profile")
    selected = requested if requested in profiles else None
    if not requested and len(profiles) == 1:
        selected = next(iter(profiles))
        prepared.options["profile"] = selected
        prepared.normalized_intent["options"]["profile"] = selected
    prepared.prerequisites.append(
        _prerequisite(
            "execution_profile",
            state="satisfied" if selected else "missing",
            field_name="options.profile",
            evidence_refs=([_evidence("execution_profile", selected)] if selected else []),
            action=None if selected else "configure_execution",
            context={"available_profiles": sorted(profiles), "requested": requested},
        )
    )


def _measurement_prerequisites(application, prepared: PreparedStart) -> None:
    workflow, agent, goal = prepared.workflow, prepared.agent, prepared.goal
    accepted = application.designs.accepted(prepared.project_id, agent["id"], goal["id"])
    if accepted:
        prepared.revisions["measurement_design"] = digest(
            {
                "id": accepted["id"],
                "revision": accepted["revision"],
                "binding_digest": accepted["binding_digest"],
            }
        )
    if workflow == "eval":
        prepared.prerequisites.append(
            _prerequisite(
                "measurement_plan",
                state="satisfied" if accepted else "missing",
                evidence_refs=(
                    [_evidence("measurement_design", accepted["id"], revision=accepted["revision"])]
                    if accepted
                    else []
                ),
                action=None if accepted else "accept_measurement_plan",
                context={"goal_id": goal["id"]},
            )
        )
        return
    status = application.catalog.measurement_status(prepared.project_id, agent["id"], goal)
    requirement = "evaluation" if workflow == "baseline" else "baseline"
    ready = status[requirement]["ready"]
    measurement = goal.get("measurement") or {}
    identity = measurement.get("evaluation_id" if requirement == "evaluation" else "baseline_id")
    prepared.prerequisites.append(
        _prerequisite(
            requirement,
            state="satisfied" if ready else "missing",
            evidence_refs=(
                [_evidence(requirement, identity, goal_id=goal["id"])] if ready and identity else []
            ),
            action=None
            if ready
            else ("prepare_evaluation" if requirement == "evaluation" else "run_baseline"),
            context={"goal_id": goal["id"], "reason": status[requirement]["reason"]},
        )
    )


def _regression_prerequisites(application, prepared: PreparedStart) -> None:
    agent = prepared.agent
    permitted = prepared.normalized_intent.get("scope") or agent["code_scopes"]
    goal_id = prepared.goal["id"] if prepared.workflow == "optimize" and prepared.goal else None
    suite = application.catalog.suite(
        prepared.project_id, agent["id"], goal_id, permitted_paths=permitted
    )
    prepared.suite = suite
    prepared.revisions["suite"] = suite["digest"]
    missing_refs = [
        _evidence(
            "goal",
            item.get("goal_id") or item["application_agent_id"],
            agent_id=item["application_agent_id"],
            name=item["name"],
            reason=item["reason"],
        )
        for item in suite["missing"]
    ]
    member_refs = [
        _evidence(
            "baseline",
            item["baseline_id"],
            goal_id=item["goal_id"],
            agent_id=item["application_agent_id"],
            name=item["name"],
        )
        for item in suite["members"]
    ]
    prepared.prerequisites.append(
        _prerequisite(
            "regression_baselines",
            state="missing" if suite["missing"] else "satisfied",
            field_name="scope",
            evidence_refs=[*member_refs, *missing_refs],
            action="establish_baselines" if suite["missing"] else None,
            context={
                "names": [item["name"] for item in suite["missing"]],
                "accepted_baseline_count": len(suite["members"]),
                "focused_regression_required": prepared.workflow == "fix",
            },
        )
    )


def prepare_workflow_start(application, project_id: str, payload: dict) -> dict:
    """Public preparation used by HTTP and MCP, with local intent instrumentation."""

    prepared = prepare(application, project_id, payload)
    from agentagon.domain.funnel import record_intent

    record_intent(application.state, project_id, prepared)
    return prepared.projection()
