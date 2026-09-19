"""Run a frozen repository trace scorer through the existing bounded runner."""

import copy
import json
from datetime import UTC, datetime

from agentagon.capabilities.experiments import checkouts, evidence, journeys, preparation, runners
from agentagon.capabilities.experiments.budget import BudgetLedger
from agentagon.capabilities.experiments.spec import finite, path, validate_profile
from agentagon.core.records import AuditError, digest, identifier, load_json


def _validated(trial, result, definition):
    request = trial["request"]
    for key in (
        "attempt_id",
        "source_digest",
        "evaluation_digest",
        "inputs_digest",
        "profile_digest",
    ):
        if result.get(key) != request[key]:
            raise AuditError("trace scorer result has a foreign execution identity")
    if result.get("state") != "completed" or not result.get("finalized"):
        raise AuditError("trace scorer did not finish successfully")
    if result.get("source_manifest_before") != trial["manifest"]:
        raise AuditError("trace scorer staged different source or acquired evidence")
    if any(
        result.get("source_manifest_after", {}).get(name) != value
        for name, value in trial["manifest"].items()
    ):
        raise AuditError("trace scorer modified its frozen source or acquired trace input")
    outcomes = result.get("results", [])
    if not isinstance(outcomes, list) or len(outcomes) != len(request["commands"]):
        raise AuditError("trace scorer did not execute every frozen command")
    for expected, actual in zip(request["commands"], outcomes, strict=True):
        if (
            any(actual.get(key) != expected[key] for key in ("id", "role"))
            or type(actual.get("exit_code")) is not int
            or actual["exit_code"] != 0
            or actual.get("timed_out")
        ):
            raise AuditError("trace scorer command failed or changed identity")
    runtime = result.get("runtime_identity")
    if not isinstance(runtime, dict) or not all(
        isinstance(runtime.get(key), str) and runtime[key]
        for key in ("platform", "machine", "os_release", "python", "python_version")
    ):
        raise AuditError("trace scorer omitted its actual runtime identity")
    try:
        output = json.loads(result.get("benchmark_output") or "", parse_constant=lambda _: None)
    except (ValueError, TypeError) as exc:
        raise AuditError("trace scorer must write JSON to AGENTAGON_RESULT_PATH") from exc
    if not isinstance(output, dict) or not isinstance(output.get("metrics"), dict):
        raise AuditError("trace scorer output requires named metrics")
    if set(output["metrics"]) - set(definition["metrics"]):
        raise AuditError("trace scorer reported a metric outside the frozen scoring definition")
    metrics = {name: finite(value) for name, value in output["metrics"].items()}
    for name, bounds in definition["judge"]["metrics"].items():
        if name in metrics and not bounds["min"] <= metrics[name] <= bounds["max"]:
            raise AuditError("trace scorer metric lies outside the frozen judge bounds")
    return metrics


def evaluate(workspace, record, observation):
    """Caller holds the baseline lock. Retry collects one persisted attempt per trace."""
    prepared = preparation.load(workspace, record["evaluation_id"])
    if (
        preparation.evaluator_identity(workspace, record["evaluation_id"])
        != record["evaluator_digest"]
    ):
        raise AuditError("trace execution evaluator identity changed")
    definition = prepared["package"]["spec"]["scoring"]
    judge = definition["judge"]
    command = judge.get("trace_command")
    if not command or not judge.get("trace_input_path"):
        raise AuditError("the frozen judge has no repository trace scoring command")
    input_path = path(judge["trace_input_path"], "trace input")
    if input_path in checkouts.paths(workspace.root, record["source_revision"]):
        raise AuditError("trace input path must be unused inside the execution snapshot")
    profile = validate_profile(copy.deepcopy(record["profile"]))
    commands = [
        {"id": f"setup-{index}", "role": "setup", "argv": argv, "cwd": "."}
        for index, argv in enumerate(profile["setup"])
    ]
    commands.append(
        {
            "id": "trace-score",
            "role": "benchmark",
            "argv": command["argv"],
            "cwd": command.get("cwd", "."),
        }
    )
    trace = observation["trace"]
    trial_id = identifier(
        "trial", "trace-score", record["baseline_id"], trace["id"], trace["digest"]
    )
    binding = {
        "baseline_id": record["baseline_id"],
        "trace_digest": trace["digest"],
        "artifact_digest": observation["artifact_digest"],
        "source_revision": record["source_revision"],
        "evaluator_digest": record["evaluator_digest"],
        "profile_digest": digest(profile),
        "commands": commands,
        "input_path": input_path,
    }
    root = (
        journeys.directory(workspace, "baselines", record["baseline_id"])
        / "trace-execution"
        / trial_id
    )
    state_file = workspace.checked(root / "state.json")
    ledger = BudgetLedger(workspace, record.get("budget_id") or record["execution_run_id"])
    admission = ledger.admit(
        trial_id,
        "preparation",
        timeout_seconds=min(
            profile["limits"]["trial_timeout_seconds"], record["budget"]["trial_timeout_seconds"]
        ),
        binding=binding,
    )
    if state_file.exists():
        trial = load_json(state_file)
        context = workspace.read_artifact(trial["context"])
        if context["binding"] != binding or any(
            trial[key] != context[key] for key in ("manifest", "request")
        ):
            raise AuditError("trace execution request changed after admission")
    else:
        source = root / "source"
        if source.exists() and not checkouts.remove(workspace.root, source):
            raise AuditError("unable to recover an unfinished trace staging directory")
        checkouts.create(workspace.root, source, record["source_revision"])
        checkouts.copy_frozen(workspace.root, source, prepared["package"]["files"])
        target = source / input_path
        if (
            target.exists()
            or target.is_symlink()
            or not target.resolve().is_relative_to(source.resolve())
        ):
            raise AuditError("trace input path must be unused inside the execution snapshot")
        target.parent.mkdir(parents=True, exist_ok=True)
        workspace.write(target, trace)
        request = {
            "attempt_id": trial_id,
            "source_digest": checkouts.tree(workspace.root, record["source_revision"]),
            "evaluation_digest": record["evaluator_digest"],
            "inputs_digest": digest(
                {"trace": trace["digest"], "artifact": observation["artifact_digest"]}
            ),
            "profile_digest": digest(profile),
            "seed": 0,
            "timeout_seconds": admission["timeout_seconds"],
            "deadline_at": datetime.fromtimestamp(admission["deadline"], UTC).isoformat(),
            "commands": commands,
        }
        trial = {
            "trial_id": trial_id,
            "state": "running",
            "manifest": checkouts.source_manifest(source),
            "request": request,
            "artifact": None,
        }
        trial["context"] = workspace.artifact(
            {"binding": binding, "manifest": trial["manifest"], "request": request}
        )
        workspace.write(state_file, trial)
    if trial.get("artifact"):
        result = evidence.read_result(workspace, trial["artifact"])
    else:
        if admission["status"] != "running":
            raise AuditError("trace execution admission ended before retained output was collected")
        result = runners.execute(profile, root / "source", root / "attempt", trial["request"])
        if not result.get("finalized"):
            trial["state"] = "interrupted"
            workspace.write(state_file, trial)
            return {"state": "pending", "metrics": None, "trial_id": trial_id}
        trial["artifact"] = evidence.retain_result(workspace, root / "attempt", result)
        workspace.write(state_file, trial)
    try:
        metrics = _validated(trial, result, definition)
        state = "completed"
    except AuditError:
        metrics, state = None, "failed"
    operation = ledger.finish(trial_id, status=state, result={"artifact": trial["artifact"]})
    if operation.get("deadline_exceeded"):
        state, metrics = "failed", None
    trial["state"] = state
    workspace.write(state_file, trial)
    if result.get("finalized"):
        checkouts.remove(workspace.root, root / "source")
    return {"state": state, "metrics": metrics, "trial_id": trial_id, "artifact": trial["artifact"]}
