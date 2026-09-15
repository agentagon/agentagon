"""Coding-host judgments bound to actual runner outputs and a frozen rubric.

Judgments fill only metrics explicitly delegated by the evaluator. Runner
identity, execution success and checks remain authoritative and are rechecked
before every use of a saved grading observation.
"""

import hashlib
import json

from agentagon.core.records import AuditError, digest, identifier
from agentagon.experiments import checkouts, evidence
from agentagon.experiments.host_bridge import HostBridge
from agentagon.experiments.spec import finite

LABEL = "coding-agent judged"


def _bridge(workspace, data):
    bridge = HostBridge(
        workspace, data.get("budget_id") or data.get("run_id") or data["evaluation_id"]
    )
    bridge.ledger.snapshot()
    return bridge


def frozen_rubric(workspace, files, rubric_path):
    """Read the same immutable rubric for preparation, candidates and traces."""
    frozen = next((entry for entry in files if entry["path"] == rubric_path), None)
    if not frozen or frozen.get("deleted"):
        raise AuditError("grading requires a retained frozen rubric")
    try:
        content = workspace.read_blob(frozen["artifact"])
    except AuditError as exc:
        raise AuditError("frozen grading rubric checksum or path changed") from exc
    if hashlib.sha256(content).hexdigest() != frozen["digest"]:
        raise AuditError("frozen grading rubric checksum changed")
    rubric = content.decode("utf-8")
    if not rubric.strip():
        raise AuditError("coding-host grading requires a nonempty frozen rubric")
    return rubric


def _context(workspace, data, candidate, trial):
    from agentagon.experiments import engine

    judge = data["spec"].get("scoring", {}).get("judge", {})
    if judge.get("kind") != "coding-host":
        raise AuditError("this evaluator does not delegate grading to the coding host")
    if digest(data["spec"]) != data["evaluation_digest"]:
        raise AuditError("frozen evaluator changed before grading")
    if checkouts.tree(workspace.root, candidate["source_revision"]) != candidate["source_digest"]:
        raise AuditError("grading source does not match the sealed candidate")
    if not trial.get("artifact"):
        raise AuditError("grading requires retained actual runner output")
    result = evidence.read_result(workspace, trial["artifact"])
    metrics, _, _ = engine._validate_result(data, candidate, trial, result)
    output = json.loads(result["benchmark_output"])
    outputs = output.get("outputs")
    if not isinstance(outputs, (list, dict)) or not outputs:
        raise AuditError("coding-host grading requires nonempty actual application outputs")
    if set(judge["metrics"]) & set(metrics):
        raise AuditError("runner metrics cannot be replaced by coding-host judgments")
    rubric_path = judge["rubric_path"]
    if any(entry["path"] == rubric_path for entry in data["frozen"]):
        rubric = frozen_rubric(workspace, data["frozen"], rubric_path)
    else:
        rubric = checkouts.git(
            workspace.root, "show", f"{candidate['source_revision']}:{rubric_path}"
        )
    if not rubric.strip():
        raise AuditError("coding-host grading requires a nonempty frozen rubric")
    payload = {
        "trial_id": trial["trial_id"],
        "candidate_id": candidate["candidate_id"],
        "execution_run_id": data["run_id"],
        "evidence": trial["artifact"],
        "evidence_digest": digest(result),
        "rubric_version": judge["rubric_version"],
        "rubric_path": rubric_path,
        "rubric": rubric,
        "rubric_digest": digest(rubric),
        "outputs": outputs,
        "metrics": judge["metrics"],
        "label": LABEL,
    }
    binding = {
        "key": f"grading:{data['run_id']}:{trial['trial_id']}",
        "run_id": data.get("budget_id") or data["run_id"],
        "source": candidate["source_digest"],
        "evaluator": data["evaluation_digest"],
        "role": "judging",
        "scope": [trial["artifact"], rubric_path],
        "host": judge["host"],
        "model": judge["model"],
        "payload": payload,
        "stage": candidate.get(
            "budget_stage",
            "preparation" if candidate["candidate_id"] == data["baseline_id"] else "optimization",
        ),
    }
    return judge, metrics, binding


def prepare(workspace, data, candidate, trial, result):
    """Reserve one durable grading request after runner evidence has been retained."""
    judge, _, binding = _context(workspace, data, candidate, trial)
    # The input is supplied by collection; the retained observation is authoritative.
    if result.get("benchmark_output") != evidence.read_result(workspace, trial["artifact"]).get(
        "benchmark_output"
    ):
        raise AuditError("grading output differs from the retained runner observation")
    return _prepare(workspace, data, trial, judge, binding)


def _prepare(workspace, data, trial, judge, binding):
    bridge = _bridge(workspace, data)
    arguments = {k: v for k, v in binding.items() if k not in {"key", "run_id"}}
    request = bridge.request(binding["key"], **arguments)
    expected = {
        "request_id": request["request_id"],
        "binding_digest": request["binding_digest"],
        "label": LABEL,
        "host": judge["host"],
        "model": judge["model"],
    }
    existing = trial.get("grading")
    if existing and any(existing.get(key) != value for key, value in expected.items()):
        raise AuditError("trial grading request changed")
    trial["grading"] = {**(existing or {}), **expected}
    return request


def observed(workspace, data, candidate, trial, metrics):
    """Recompute merged measurements from a bound, admitted immutable host reply."""
    judge, runner_metrics, binding = _context(workspace, data, candidate, trial)
    if runner_metrics != metrics:
        raise AuditError("grading must extend the actual runner metrics")
    return _observed(workspace, data, trial, judge, binding, metrics)


def _observed(workspace, data, trial, judge, binding, metrics):
    grading = trial.get("grading") or {}
    bridge = _bridge(workspace, data)
    request = bridge.snapshot()["requests"].get(grading.get("request_id"))
    if not request:
        raise AuditError("trial has no bound host grading request")
    if (
        grading.get("binding_digest") != digest(binding)
        or grading.get("host") != judge["host"]
        or grading.get("model") != judge["model"]
        or grading.get("label") != LABEL
    ):
        raise AuditError("grading request no longer matches trial, rubric or host provenance")
    values = judgment(bridge, request, binding, judge)
    observation = {
        "version": 1,
        "label": LABEL,
        "request_id": request["request_id"],
        "binding_digest": request["binding_digest"],
        "source": request["source"],
        "evaluator": request["evaluator"],
        "evidence": binding["payload"]["evidence"],
        "evidence_digest": binding["payload"]["evidence_digest"],
        "host": request["host"],
        "model": request["model"],
        "response": request["response"],
    }
    if grading.get("observation"):
        if workspace.read_artifact(grading["observation"]) != observation:
            raise AuditError("saved grading observation changed")
    else:
        grading["observation"] = workspace.artifact(observation)
    return {**metrics, **values}


def judgment(bridge, request, binding, judge):
    """Validate one admitted judgment against its exact evidence, host and rubric."""
    if request.get("binding_digest") != digest(binding) or any(
        request.get(key) != value for key, value in binding.items()
    ):
        raise AuditError("grading request no longer matches trial, rubric or host provenance")
    if request["state"] != "completed":
        raise AuditError("coding-host grading is pending or cancelled")
    if request.get("deadline_exceeded"):
        raise AuditError("grading reply exceeded its admitted time budget; evidence is retained")
    response = request.get("response")
    operation = bridge.ledger.snapshot()["operations"].get(request["request_id"])
    if (
        not operation
        or operation.get("status") != "completed"
        or operation.get("kind") != "judging"
        or operation.get("deadline_exceeded")
        or operation.get("binding") != {"request_digest": request["binding_digest"]}
        or operation.get("result") != response
    ):
        raise AuditError("grading reply lacks matching admitted host-work evidence")
    if (
        not isinstance(response, dict)
        or set(response) != {"trial_id", "rubric_version", "metrics", "explanation"}
        or response["trial_id"] != binding["payload"]["trial_id"]
        or response["rubric_version"] != judge["rubric_version"]
        or not isinstance(response["explanation"], str)
        or not response["explanation"].strip()
        or not isinstance(response["metrics"], dict)
        or set(response["metrics"]) != set(judge["metrics"])
    ):
        raise AuditError(
            "grading reply requires exact observation trial ID, rubric metrics and an explanation"
        )
    values = {}
    for name, bounds in judge["metrics"].items():
        value = finite(response["metrics"][name])
        if not bounds["min"] <= value <= bounds["max"]:
            raise AuditError(f"grading metric {name} is outside the frozen rubric bounds")
        values[name] = value
    return values


def resume(workspace, data, candidate):
    """Collect completed grading replies without dispatching another execution."""
    changed = False
    for trial in candidate["trials"]:
        if trial["state"] != "awaiting_grading":
            continue
        bridge = _bridge(workspace, data)
        request = bridge.snapshot()["requests"].get(trial.get("grading", {}).get("request_id"))
        if not request:
            raise AuditError("pending trial lost its bound grading request")
        if request["state"] in {"pending", "running"}:
            continue
        if request["state"] == "cancelled":
            trial["state"] = candidate["state"] = "cancelled"
            trial["error"] = "coding-host grading was cancelled"
            changed = True
            continue
        _, metrics, _ = _context(workspace, data, candidate, trial)
        trial["metrics"] = observed(workspace, data, candidate, trial, metrics)
        trial["state"] = "completed"
        candidate["state"] = "sealed"
        changed = True
    return changed


def _evaluation_context(workspace, data, record, trial):
    from agentagon.experiments import evaluation, preparation

    spec = record["plan"]["spec"]
    judge = spec.get("scoring", {}).get("judge", {})
    if judge.get("kind") != "coding-host":
        raise AuditError("this evaluation does not delegate grading to the coding host")
    binding = {key: record[key] for key in ("plan", "source_revision", "files", "mutations")}
    if "selected_evidence" in record:
        binding["selected_evidence"] = record["selected_evidence"]
    expected_validation = identifier(
        "validation",
        digest(
            {
                **binding,
                "source_revision": checkouts.tree(workspace.root, record["source_revision"]),
            }
        ),
    )
    request = trial["request"]
    expected_trial = identifier(
        "trial", data["evaluation_id"], expected_validation, trial["case_id"], trial["repetition"]
    )
    if (
        record["validation_id"] != expected_validation
        or data["current_validation"] != expected_validation
        or trial["trial_id"] != expected_trial
        or request["attempt_id"] != expected_trial
        or request["evaluation_id"] != data["evaluation_id"]
        or request["evaluation_digest"] != digest(record["plan"])
        or request["inputs_digest"] != digest(record["files"])
        or request["profile_digest"] != data["profile_digest"]
        or request["source_digest"] != digest(trial["manifest"])
        or request["commands"] != evaluation.commands(data["profile"], spec, gate_checks=False)
        or type(trial["repetition"]) is not int
        or not 0 <= trial["repetition"] < spec["repetitions"]
        or request["seed"] != spec["seeds"][trial["repetition"]]
    ):
        raise AuditError("grading trial does not match the frozen preparation validation")
    result = evidence.read_result(workspace, trial["artifact"])
    case = next(
        (
            c
            for c in [*record["plan"]["negative_cases"], *record["plan"].get("metric_cases", [])]
            if c["id"] == trial["case_id"]
        ),
        None,
    )
    preparation._validate_admission(workspace, data, trial)
    outcome = preparation._outcome(spec, trial, result, case)
    outputs = json.loads(result["benchmark_output"]).get("outputs")
    if not isinstance(outputs, (list, dict)) or not outputs:
        raise AuditError("coding-host grading requires nonempty actual application outputs")
    if set(judge["metrics"]) & set(outcome["metrics"]):
        raise AuditError("runner metrics cannot be replaced by coding-host judgments")
    rubric = frozen_rubric(workspace, record["files"], judge["rubric_path"])
    binding = {
        "key": f"grading:{data['evaluation_id']}:{trial['trial_id']}",
        "run_id": data.get("budget_id") or data["evaluation_id"],
        "source": request["source_digest"],
        "evaluator": request["evaluation_digest"],
        "role": "judging",
        "scope": [trial["artifact"], judge["rubric_path"]],
        "host": judge["host"],
        "model": judge["model"],
        "payload": {
            "evaluation_id": data["evaluation_id"],
            "validation_id": record["validation_id"],
            "trial_id": trial["trial_id"],
            "evidence": trial["artifact"],
            "evidence_digest": digest(result),
            "rubric_version": judge["rubric_version"],
            "rubric_path": judge["rubric_path"],
            "rubric": rubric,
            "rubric_digest": digest(rubric),
            "outputs": outputs,
            "metrics": judge["metrics"],
            "label": LABEL,
        },
        "stage": "preparation",
    }
    return judge, outcome["metrics"], binding


def prepare_evaluation(workspace, data, record, trial):
    """Request a grading pass for one already-executed sensitivity trial."""
    judge, _, binding = _evaluation_context(workspace, data, record, trial)
    return _prepare(workspace, data, trial, judge, binding)


def observed_evaluation(workspace, data, record, trial, metrics):
    """Recheck preparation execution and merge its immutable host observation."""
    judge, runner_metrics, binding = _evaluation_context(workspace, data, record, trial)
    if metrics != runner_metrics:
        raise AuditError("grading must extend the actual runner metrics")
    return _observed(workspace, data, trial, judge, binding, metrics)


def resume_evaluation(workspace, data, record, trial):
    """Return false while the same preparation trial waits for its host judgment."""
    request = (
        _bridge(workspace, data)
        .snapshot()["requests"]
        .get(trial.get("grading", {}).get("request_id"))
    )
    if not request:
        raise AuditError("pending evaluation lost its bound grading request")
    if request["state"] in {"pending", "running"}:
        return False
    _, metrics, _ = _evaluation_context(workspace, data, record, trial)
    trial["outcome"]["metrics"] = observed_evaluation(workspace, data, record, trial, metrics)
    trial["state"] = "passed"
    return True
