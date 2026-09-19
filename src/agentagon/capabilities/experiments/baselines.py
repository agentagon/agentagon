"""Fresh immutable measurements referencing the existing evaluation lifecycle."""

import copy
import hashlib
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from agentagon.capabilities.experiments import checkouts, engine, journeys, preparation, scoring
from agentagon.capabilities.experiments.budget import BudgetLedger
from agentagon.capabilities.experiments.host_bridge import HostBridge
from agentagon.capabilities.experiments.store import load_run, save_run
from agentagon.capabilities.experiments.store import locked as run_locked
from agentagon.core.records import AuditError, digest, identifier, load_json, now
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace


def _save(workspace, record):
    workspace.write(
        journeys.directory(workspace, "baselines", record["baseline_id"]) / "state.json", record
    )


def status(workspace: Workspace, baseline_id: str) -> dict:
    file = journeys.directory(workspace, "baselines", baseline_id) / "state.json"
    if not file.exists():
        raise AuditError("baseline not found in this checkout")
    record = load_json(file)
    if (
        record.get("origin") != str(workspace.root)
        or record.get("version") != 1
        or record.get("baseline_id") != baseline_id
        or digest(record["identity"]) != record.get("identity_digest")
        or any(record.get(key) != value for key, value in record["identity"].items())
    ):
        raise AuditError("baseline measurement identity changed")
    if (
        record.get("measurement_artifact")
        and workspace.read_artifact(record["measurement_artifact"]) != record["measurement"]
    ):
        raise AuditError("completed baseline measurement changed")
    traces = record["recent_traces"]
    if traces.get("score_artifact"):
        saved = workspace.read_artifact(traces["score_artifact"])
        if (
            saved["score"] != traces.get("score")
            or saved["receipt"] != traces.get("receipt")
            or saved["evaluator_digest"] != record["evaluator_digest"]
        ):
            raise AuditError("completed trace score changed")
    return record


def list_baselines(workspace: Workspace) -> list[dict]:
    return sorted(
        [
            status(workspace, p.parent.name)
            for p in (workspace.state / "baselines").glob("*/state.json")
        ],
        key=lambda b: (b["created_at"], b["baseline_id"]),
        reverse=True,
    )


def _trace_request(definition, at):
    acquisition = definition.get("acquisition")
    if not acquisition:
        end = datetime.fromisoformat(at)
        return {
            "state": "unavailable",
            "score": None,
            "next_action": "Supply a fresh trace export or connect an authorized provider.",
            "population": "recent_traces",
            "window": {"start": (end - timedelta(days=1)).isoformat(), "end": at},
            "max_traces": 100,
            "completeness": "unknown",
            "alignment": "unknown",
        }
    end = datetime.fromisoformat(at)
    return {
        "state": "pending" if acquisition["authorized"] else "needs_authorization",
        "score": None,
        "population": "recent_traces",
        "provider": acquisition["provider"],
        "project": acquisition["project"],
        "window": {
            "start": (end - timedelta(seconds=acquisition["lookback_seconds"])).isoformat(),
            "end": at,
        },
        "filters": acquisition["filters"],
        "max_traces": acquisition["max_traces"],
        "completeness": "unknown",
        "alignment": "unknown",
        "next_action": "Refresh within the saved provider scope using the coding host, then attach the acquisition receipt.",
    }


def start(
    workspace: Workspace,
    evaluation_id: str,
    intent_id: str | None = None,
    profile_name: str | None = None,
    request_id: str | None = None,
    *,
    execution_profile: dict | None = None,
) -> dict:
    workspace.require_initialized()
    if request_id is not None and (
        not isinstance(request_id, str) or not 1 <= len(request_id) <= 200
    ):
        raise AuditError("baseline request ID must be 1–200 characters")
    baseline_id = identifier("baseline", str(workspace.root), request_id or uuid.uuid4().hex)
    with journeys.locked(workspace, "baselines", baseline_id):
        file = journeys.directory(workspace, "baselines", baseline_id) / "state.json"
        if file.exists():
            saved = status(workspace, baseline_id)
            if (saved["evaluation_id"], saved["intent_id"], saved["requested_profile"]) != (
                evaluation_id,
                intent_id,
                profile_name,
            ):
                raise AuditError("baseline request identity was reused with different settings")
            return saved
        evaluator = preparation.load(workspace, evaluation_id)
        evaluator_digest = preparation.evaluator_identity(workspace, evaluation_id)
        intent = journeys.load(workspace, intent_id) if intent_id else None
        definition = intent["definition"] if intent else {}
        if intent and evaluator["package"]["spec"].get("scoring") != definition["scoring"]:
            raise AuditError("baseline intent scoring differs from the frozen evaluator")
        selected_profile = profile_name or evaluator["profile_name"]
        if execution_profile is None:
            profile = Config().profile(workspace.root, selected_profile)
        else:
            from agentagon.capabilities.experiments.spec import validate_profile

            profile = validate_profile(execution_profile)
        limits = definition.get("budget", profile["limits"])
        repetitions = evaluator["package"]["spec"]["repetitions"]
        if limits["max_trials"] < repetitions:
            raise AuditError("baseline budget cannot cover the frozen evaluator repetitions")
        created_at = now()
        error = None
        revision = None
        branch = None
        try:
            revision = checkouts.clean_revision(workspace.root)
            branch = checkouts.git(workspace.root, "branch", "--show-current") or "detached HEAD"
            preparation.fix_spec(workspace, evaluation_id, reuse=True)
        except AuditError as exc:
            error = str(exc)
        run_id = identifier("run", baseline_id)
        budget_id = run_id
        if (
            evaluator.get("budget_id")
            and evaluator.get("intent_id")
            and intent
            and not any(b["evaluation_id"] == evaluation_id for b in list_baselines(workspace))
        ):
            prepared_intent = journeys.load(workspace, evaluator["intent_id"])
            if prepared_intent["definition_digest"] == intent["definition_digest"]:
                budget_id = evaluator["budget_id"]
        ledger = BudgetLedger(workspace, budget_id)
        if (
            ledger.path.exists()
            and ledger.spent(ledger.snapshot()) + repetitions > limits["max_trials"]
        ):
            error = "The remaining overall budget cannot cover the baseline repetitions."
        identity = {
            "source_revision": revision,
            "branch": branch,
            "evaluation_id": evaluation_id,
            "evaluator_digest": evaluator_digest,
            "intent_id": intent_id,
            "profile": profile,
            "budget": limits,
            "created_at": created_at,
            "execution_run_id": run_id,
            "budget_id": budget_id,
        }
        record = {
            "version": 1,
            "baseline_id": baseline_id,
            "origin": str(workspace.root),
            "identity": identity,
            "identity_digest": digest(identity),
            **identity,
            "requested_profile": profile_name,
            "profile_name": selected_profile,
            "state": "blocked" if error else "ready",
            "pending_action": error or "Run the saved baseline job.",
            "benchmark_score": None,
            "recent_traces": _trace_request(definition, created_at),
            "measurement": None,
            "measurement_artifact": None,
            "host": definition.get("host", {"name": "current", "model": "configured"}),
        }
        _save(workspace, record)
        if not error and not ledger.path.exists():
            ledger.create(
                limits["max_trials"],
                limits["max_elapsed_seconds"],
                journey="baseline",
                baseline_trials=repetitions,
            )
    return record


def rerun(workspace: Workspace, baseline_id: str, request_id: str | None = None) -> dict:
    previous = status(workspace, baseline_id)
    return start(
        workspace,
        previous["evaluation_id"],
        previous["intent_id"],
        previous["profile_name"],
        request_id,
        execution_profile=previous["profile"],
    )


def _bridge_review(workspace, record, result):
    run_id = record["execution_run_id"]
    candidate = result["candidate"]
    host = record["host"]
    bridge = HostBridge(workspace, record.get("budget_id", run_id))
    request = bridge.request(
        f"baseline-review:{run_id}",
        source=candidate["source_digest"],
        evaluator=record["evaluator_digest"],
        role="review",
        scope=[candidate["worktree"]],
        host=host["name"],
        model=host["model"],
        payload={
            "candidate_id": candidate["candidate_id"],
            "review_template": result["review_template"],
        },
        stage="preparation",
    )
    if request["state"] == "completed":
        if request.get("deadline_exceeded"):
            raise AuditError("baseline review exceeded the accepted overall budget")
        return engine.run(workspace, run_id, review=request["response"])
    record["host_request_id"] = request["request_id"]
    return result


def advance(workspace: Workspace, baseline_id: str) -> dict:
    """Finite CLI-backed job; reads never call this operation."""
    with journeys.locked(workspace, "baselines", baseline_id):
        record = status(workspace, baseline_id)
        if record["state"] in {"completed", "blocked", "failed"}:
            return record
        run_id = record["execution_run_id"]
        try:
            try:
                load_run(workspace, run_id)
            except AuditError:
                spec = preparation.fix_spec(workspace, record["evaluation_id"], reuse=True)
                engine.start(
                    workspace,
                    spec,
                    record["profile_name"],
                    run_id=run_id,
                    expected_revision=record["source_revision"],
                    budget_id=record.get("budget_id", run_id),
                    execution_profile=record["profile"],
                )
                with run_locked(workspace, run_id):
                    data = load_run(workspace, run_id)
                    data["limits"]["trial_timeout_seconds"] = min(
                        data["limits"]["trial_timeout_seconds"],
                        record["budget"]["trial_timeout_seconds"],
                    )
                    data.update(
                        evaluator_id=record["evaluation_id"],
                        evaluator_digest=record["evaluator_digest"],
                        intent_id=record["intent_id"],
                    )
                    save_run(workspace, data)
            record["state"], record["pending_action"] = "running", "Measure frozen benchmark."
            _save(workspace, record)
            traces = record["recent_traces"]
            if traces["state"] == "pending":
                request = HostBridge(workspace, record.get("budget_id", run_id)).request(
                    f"trace-refresh:{run_id}",
                    source=record["source_revision"],
                    evaluator=record["evaluator_digest"],
                    role="acquisition",
                    scope=["authorized provider acquisition"],
                    host=record["host"]["name"],
                    model=record["host"]["model"],
                    payload={
                        k: traces[k]
                        for k in ("provider", "project", "window", "filters", "max_traces")
                    },
                    stage="preparation",
                )
                traces["host_request_id"] = request["request_id"]
            result = engine.run(workspace, run_id)
            if result["candidate"]["state"] == "awaiting_review":
                result = _bridge_review(workspace, record, result)
            candidate = result["candidate"]
            data = load_run(workspace, run_id)
            record["benchmark_score"] = scoring.candidate_score(data, candidate)
            if candidate["state"] == "verified":
                record["measurement"] = {
                    "execution_run_id": run_id,
                    "candidate_id": candidate["candidate_id"],
                    "source_revision": record["source_revision"],
                    "evaluator_digest": record["evaluator_digest"],
                    "measured_at": now(),
                    "score": record["benchmark_score"],
                    "metrics": candidate["metrics"],
                    "checks": candidate["checks"],
                    "evidence": [t["artifact"] for t in candidate["trials"]],
                }
                record["measurement_artifact"] = workspace.artifact(record["measurement"])
                record["state"], record["pending_action"] = "completed", None
            elif candidate["state"] in {"failed", "rejected", "cancelled"}:
                record["state"], record["pending_action"] = (
                    "failed",
                    "Inspect retained execution evidence; rerun creates a new measurement.",
                )
            else:
                record["state"], record["pending_action"] = (
                    "host_pending",
                    "Complete the bound host grading/review request, then run this baseline job again.",
                )
        except (AuditError, OSError) as exc:
            record["state"], record["pending_action"] = (
                "host_pending",
                str(exc)
                if isinstance(exc, AuditError)
                else "Local execution could not complete; inspect the saved job.",
            )
        _save(workspace, record)
        return record


def attach_traces(workspace: Workspace, baseline_id: str, receipt: dict) -> dict:
    """Keep acquisition evidence distinct; scores need frozen, execution-bound observations."""
    from agentagon.core.records import validate_record

    validate_record("baseline-acquisition", receipt)
    with journeys.locked(workspace, "baselines", baseline_id):
        record = status(workspace, baseline_id)
        traces = record["recent_traces"]
        if (
            receipt["baseline_id"] != baseline_id
            or receipt["evaluator_digest"] != record["evaluator_digest"]
        ):
            raise AuditError("trace receipt belongs to a different measurement or evaluator")
        if (
            traces.get("provider") is not None
            and (
                receipt["provider"] != traces["provider"] or receipt["project"] != traces["project"]
            )
        ) or receipt["window"] != traces.get("window"):
            raise AuditError("trace acquisition differs from the saved provider and rolling window")
        if receipt["count"] > traces["max_traces"]:
            raise AuditError("trace acquisition exceeds the approved sample cap")
        artifacts = receipt["artifacts"]
        for artifact in artifacts:
            workspace.read_artifact(artifact)
        if receipt.get("acquisition_artifact"):
            workspace.read_artifact(receipt["acquisition_artifact"])
        if traces.get("receipt"):
            if workspace.read_artifact(traces["receipt"]) != receipt:
                raise AuditError("acquisition receipt is immutable; rerun for fresh traces")
        else:
            traces.update(
                provider=receipt["provider"],
                project=receipt["project"],
                state=receipt["state"],
                completeness=receipt["completeness"],
                alignment=receipt["alignment"],
                count=receipt["count"],
                receipt=workspace.artifact(receipt),
                score=None,
                next_action="Score eligible saved traces using the frozen behavior definitions; acquisition alone does not establish a score.",
            )
        _save(workspace, record)
    from agentagon.capabilities.experiments import trace_scoring

    return trace_scoring.prepare(workspace, baseline_id)


def public_projection(record: dict) -> dict:
    fields = (
        "baseline_id",
        "evaluation_id",
        "evaluator_digest",
        "intent_id",
        "execution_run_id",
        "source_revision",
        "branch",
        "created_at",
        "profile_name",
        "state",
        "pending_action",
        "benchmark_score",
    )
    result = {k: copy.deepcopy(record.get(k)) for k in fields}
    result["recent_traces"] = {
        k: copy.deepcopy(record["recent_traces"][k])
        for k in (
            "state",
            "population",
            "provider",
            "project",
            "window",
            "completeness",
            "alignment",
            "count",
            "score",
            "next_action",
        )
        if k in record["recent_traces"]
    }
    return result


def import_traces(
    workspace: Workspace,
    baseline_id: str,
    export_path: Path,
    acquisition_path: Path | None = None,
    source: str | None = None,
    project: str | None = None,
) -> dict:
    """Normalize a bounded provider export through the existing ingestion pipeline."""
    from agentagon.capabilities.traces.ingest import import_export
    from agentagon.core.records import PROVIDERS

    record = status(workspace, baseline_id)
    recent = record["recent_traces"]
    source = source or recent.get("provider")
    project = project or recent.get("project")
    if source not in PROVIDERS or not isinstance(project, str) or not project.strip():
        raise AuditError("a trace export requires a supported source and project")
    if (
        not export_path.is_file()
        or export_path.is_symlink()
        or export_path.stat().st_size > 32 * 1024 * 1024
    ):
        raise AuditError("trace export must be a regular file of at most 32 MiB")
    if acquisition_path and (
        not acquisition_path.is_file()
        or acquisition_path.is_symlink()
        or acquisition_path.stat().st_size > 1024 * 1024
    ):
        raise AuditError("acquisition receipt must be a regular file of at most 1 MiB")
    input_digest = digest(
        {
            "files": [(export_path.name, hashlib.sha256(export_path.read_bytes()).hexdigest())],
            "acquisition": load_json(acquisition_path) if acquisition_path else None,
        }
    )
    if recent.get("receipt"):
        previous = workspace.read_artifact(recent["receipt"])
        if (
            previous.get("acquisition_artifact")
            and workspace.read_artifact(previous["acquisition_artifact"]).get("input_digest")
            == input_digest
            and source == previous["provider"]
            and project == previous["project"]
        ):
            from agentagon.capabilities.experiments import trace_scoring

            return trace_scoring.prepare(workspace, baseline_id)
        raise AuditError("acquisition receipt is immutable; rerun for a different export")
    # This is an ingestion context, not another audit or evaluator lifecycle.
    context = {
        "mode": "traces",
        "source": source,
        "project": project,
        "window": recent["window"],
        "limit": recent["max_traces"],
        "snapshot": {"revision": record["source_revision"]},
        "acquisition": None,
        "reviews": {},
        "diagnoses": {},
        "groups": [],
        "generation": 0,
    }
    imported = import_export(workspace, context, export_path, acquisition_path)
    normalized = context["traces"]
    revisions = {
        revision
        for trace in normalized
        for revision in trace["revision_alignment"]["reported_revisions"]
    }
    alignment = "unknown"
    if any(trace["revision_alignment"]["status"] == "mismatch" for trace in normalized):
        alignment = "mismatched"
    elif normalized and all(
        trace["revision_alignment"]["reported_revisions"] for trace in normalized
    ):
        alignment = "matched"
    completeness = imported["provenance"]["completeness"]
    partial = bool(
        imported["diagnostics"]
        or imported["missing_selected_ids"]
        or imported["provenance"]["failed_trace_ids"]
        or (completeness == "complete" and not imported["provenance"]["pagination_complete"])
        or any(t["completeness"] == "partial" for t in normalized)
    )
    if partial:
        completeness = "partial"
    receipt = {
        "baseline_id": baseline_id,
        "evaluator_digest": record["evaluator_digest"],
        "provider": source,
        "project": project,
        "window": recent["window"],
        "state": "failed"
        if imported["diagnostics"] and not normalized
        else "empty"
        if not normalized
        else "complete"
        if completeness == "complete"
        else "partial",
        "completeness": completeness,
        "count": len(normalized),
        "alignment": alignment,
        "deployment_revision": next(iter(revisions)) if len(revisions) == 1 else None,
        "artifacts": [trace["path"] for trace in normalized],
        "acquisition_artifact": workspace.artifact(imported),
    }
    return attach_traces(workspace, baseline_id, receipt)
