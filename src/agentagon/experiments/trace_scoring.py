"""Grade acquired trace observations against the frozen rubric, as a separate population.

Only explicitly delegated judge metrics are available here. An executable benchmark
check or an unrelated telemetry field never becomes an invented trace measurement.
"""

from agentagon.core.records import AuditError, digest, identifier, timestamp_ns, validate_record
from agentagon.experiments import (
    baselines,
    grading,
    journeys,
    preparation,
    scoring,
    trace_execution,
)
from agentagon.experiments.budget import BudgetExhausted
from agentagon.experiments.evidence import read_result
from agentagon.experiments.host_bridge import HostBridge


def _context(workspace, record):
    prepared = preparation.load(workspace, record["evaluation_id"])
    if (
        preparation.evaluator_identity(workspace, record["evaluation_id"])
        != record["evaluator_digest"]
    ):
        raise AuditError("trace scoring evaluator identity changed")
    definition = prepared["package"]["spec"].get("scoring")
    if definition is None:
        return None, None, None
    definition = scoring.validate(definition)
    judge = definition.get("judge", {})
    if judge.get("kind") != "coding-host" and not judge.get("trace_command"):
        return definition, None, None
    rubric = grading.frozen_rubric(workspace, prepared["package"]["files"], judge["rubric_path"])
    return definition, judge, rubric


def _observations(workspace, record):
    recent = record["recent_traces"]
    if not recent.get("receipt"):
        return [], "Attach a fresh acquisition receipt before scoring traces."
    receipt = workspace.read_artifact(recent["receipt"])
    if (
        receipt["baseline_id"] != record["baseline_id"]
        or receipt["evaluator_digest"] != record["evaluator_digest"]
    ):
        raise AuditError("trace scoring receipt belongs to another baseline or evaluator")
    if any(
        receipt.get(key) != recent.get(key)
        for key in ("provider", "project", "window", "count", "completeness", "alignment")
    ):
        raise AuditError("trace scoring acquisition identity changed")
    if receipt["count"] > recent["max_traces"]:
        raise AuditError("trace scoring exceeds the saved sample cap")
    if receipt["state"] in {"empty", "failed"} or receipt["count"] == 0:
        return [], "Acquisition contains no usable trace evidence."
    observed, seen, total = [], set(), 0
    for artifact in receipt["artifacts"]:
        retained = workspace.read_artifact(artifact)
        traces = retained.get("traces", [retained]) if isinstance(retained, dict) else []
        if not isinstance(traces, list):
            continue
        total += len(traces)
        if total > recent["max_traces"] or total > receipt["count"]:
            raise AuditError("trace artifacts exceed the declared acquisition count")
        for trace in traces:
            try:
                validate_record("trace", trace)
            except AuditError:
                continue
            if (
                digest({key: value for key, value in trace.items() if key != "digest"})
                != trace["digest"]
            ):
                raise AuditError("acquired trace digest changed")
            if trace["id"] in seen:
                raise AuditError("trace scoring cannot count duplicate trace identities")
            seen.add(trace["id"])
            if trace["source"] != receipt["provider"] or trace["project"] != receipt["project"]:
                raise AuditError("trace evidence differs from the approved provider scope")
            started = trace["root_started_ns"]
            if (
                trace["completeness"] != "complete"
                or trace["missing_parents"]
                or started is None
                or not timestamp_ns(receipt["window"]["start"])
                <= started
                <= timestamp_ns(receipt["window"]["end"])
                or not any(span["output"] is not None for span in trace["spans"])
            ):
                continue
            observed.append(
                {"artifact": artifact, "artifact_digest": digest(retained), "trace": trace}
            )
    return (
        observed,
        "Only complete normalized traces with actual outputs inside the saved window are eligible.",
    )


def _binding(record, observation, judge, rubric):
    trace = observation["trace"]
    trial_id = identifier("trial", record["baseline_id"], trace["id"], trace["digest"])
    return {
        "key": f"trace-grading:{record['baseline_id']}:{trace['id']}",
        "run_id": record.get("budget_id") or record["execution_run_id"],
        "source": trace["digest"],
        "evaluator": record["evaluator_digest"],
        "role": "judging",
        "scope": [observation["artifact"], judge["rubric_path"]],
        "host": judge["host"],
        "model": judge["model"],
        "stage": "preparation",
        "payload": {
            "population": "recent_traces",
            "baseline_id": record["baseline_id"],
            "trial_id": trial_id,
            "trace_id": trace["id"],
            "evidence": observation["artifact"],
            "evidence_digest": observation["artifact_digest"],
            "trace_digest": trace["digest"],
            "trajectory": trace,
            "outputs": [span["output"] for span in trace["spans"] if span["output"] is not None],
            "rubric": rubric,
            "rubric_digest": digest(rubric),
            "rubric_version": judge["rubric_version"],
            "metrics": judge["metrics"],
            "label": "coding-agent judged",
        },
    }


def _advance(workspace, baseline_id, *, collect):
    with journeys.locked(workspace, "baselines", baseline_id):
        record = baselines.status(workspace, baseline_id)
        recent = record["recent_traces"]
        definition, judge, rubric = _context(workspace, record)
        observations, reason = _observations(workspace, record)
        source_digest = digest(
            {
                "receipt": recent.get("receipt"),
                "traces": [item["trace"]["digest"] for item in observations],
                "definition": definition,
            }
        )
        # A saved terminal aggregate is an immutable projection, never a cached rerun.
        if recent.get("score_artifact"):
            saved = workspace.read_artifact(recent["score_artifact"])
            if saved["score"] != recent.get("score") or saved["source_digest"] != source_digest:
                raise AuditError("saved trace score changed")
            for outcome in saved["observations"]:
                if outcome.get("artifact"):
                    read_result(workspace, outcome["artifact"])
            return record
        requests, samples, evidence = [], [], []
        execution_pending = execution_failed = False
        if judge and judge.get("trace_command") and observations:
            if not collect:
                execution_pending = True
            else:
                for observation in observations:
                    try:
                        outcome = trace_execution.evaluate(workspace, record, observation)
                    except BudgetExhausted:
                        execution_failed = True
                        break
                    execution_pending |= outcome["state"] == "pending"
                    execution_failed |= outcome["state"] == "failed"
                    if outcome["state"] == "completed":
                        samples.append(outcome["metrics"])
                    evidence.append(outcome)
        elif judge and observations:
            bridge = HostBridge(workspace, record.get("budget_id") or record["execution_run_id"])
            for observation in observations:
                binding = _binding(record, observation, judge, rubric)
                request = bridge.request(
                    binding["key"],
                    **{
                        key: value for key, value in binding.items() if key not in {"key", "run_id"}
                    },
                )
                requests.append(request)
                if collect and request["state"] == "completed":
                    samples.append(grading.judgment(bridge, request, binding, judge))
                    evidence.append(
                        {
                            "request_id": request["request_id"],
                            "binding_digest": request["binding_digest"],
                            "host": request["host"],
                            "model": request["model"],
                            "response": request["response"],
                            "trace_digest": binding["source"],
                            "evidence": observation["artifact"],
                        }
                    )
        pending = execution_pending or any(
            request["state"] in {"pending", "running"} for request in requests
        )
        cancelled = any(request["state"] == "cancelled" for request in requests)
        if pending or (requests and not collect):
            score = {
                "state": "execution_pending" if execution_pending else "host_pending",
                "value": None,
                "eligible": False,
                "judging": "repository evaluator"
                if judge.get("trace_command")
                else "coding-agent judged",
            }
            reason = (
                "Run baseline score-traces to execute or collect the frozen repository trace scorer."
                if execution_pending
                else "Complete the bound trace grading requests in your coding host, then collect trace scores."
            )
        elif definition and samples and not cancelled and not execution_failed:
            score = scoring.summarize(definition, samples, checks=[])
            if judge.get("trace_command"):
                score["judging"] = "repository evaluator"
            reason = "Trace sample graded against the frozen rubric; unsupported metrics and executable check gates remain unknown."
        else:
            score = {"state": "unmeasured", "value": None, "eligible": False}
            if not judge:
                reason = "The frozen evaluator has no repository trace command or coding-host trace judge. No trace metric mapping was inferred."
            elif cancelled:
                reason = "Trace grading was cancelled; no aggregate is claimed."
            elif execution_failed:
                reason = "A trace scoring execution failed or exceeded the overall budget; retained successful observations do not establish a complete aggregate."
        score.update(
            population="recent_traces",
            eligible_count=len(observations),
            measured_count=len(samples),
            acquisition_count=recent.get("count", 0),
        )
        recent.update(
            score=score,
            next_action=reason,
            scoring_requests=[request["request_id"] for request in requests],
        )
        if not pending and collect and recent.get("receipt"):
            recent["score_artifact"] = workspace.artifact(
                {
                    "score": score,
                    "source_digest": source_digest,
                    "receipt": recent["receipt"],
                    "evaluator_digest": record["evaluator_digest"],
                    "observations": evidence,
                }
            )
        baselines._save(workspace, record)
        return record


def prepare(workspace, baseline_id):
    """Queue bounded judging work; never call a model or invent trace eval cases."""
    return _advance(workspace, baseline_id, collect=False)


def advance(workspace, baseline_id):
    """Collect provenance-checked observations and freeze one recent-trace aggregate."""
    return _advance(workspace, baseline_id, collect=True)
