"""Compare retained baseline evidence without running or resuming measurements."""

import math

from agentagon.core.records import AuditError, digest
from agentagon.experiments import baselines, engine, scoring
from agentagon.experiments.store import load_run


def _completed(workspace, baseline_id):
    record = baselines.status(workspace, baseline_id)
    measurement = record.get("measurement")
    if (
        record.get("state") != "completed"
        or not isinstance(measurement, dict)
        or not record.get("measurement_artifact")
    ):
        raise AuditError("compare requires two completed, independently reviewed baselines")
    run = load_run(workspace, record["execution_run_id"])
    candidate = run["candidates"].get(measurement.get("candidate_id"))
    if (
        not candidate
        or candidate["candidate_id"] != run["baseline_id"]
        or run.get("evaluator_id") != record["evaluation_id"]
        or run.get("evaluator_digest") != record["evaluator_digest"]
        or run["origin_revision"] != record["source_revision"]
        or candidate["source_revision"] != run["baseline_revision"]
        or any(
            measurement.get(key) != record[key]
            for key in ("execution_run_id", "source_revision", "evaluator_digest")
        )
    ):
        raise AuditError("baseline measurement does not match its verified execution")
    # This verifier only reads retained reviews, trial artifacts, budgets and Git objects.
    engine._verified_evidence(workspace, run, candidate)
    score = scoring.candidate_score(run, candidate)
    if (
        record.get("benchmark_score") != score
        or measurement.get("score") != score
        or measurement.get("metrics") != candidate["metrics"]
        or measurement.get("checks") != candidate["checks"]
    ):
        raise AuditError("baseline score or measurement differs from retained execution evidence")
    return record, run


def _value(score):
    if score is None or score.get("state") != "measured" or score.get("value") is None:
        return None
    value = score["value"]
    if type(value) not in (int, float) or not math.isfinite(value):
        raise AuditError("baseline score must be finite")
    return value


def compare(workspace, left_id, right_id):
    """Return right-minus-left benchmark scores for compatible completed baselines."""
    if left_id == right_id:
        raise AuditError("choose two different baseline measurements")
    left, left_run = _completed(workspace, left_id)
    right, right_run = _completed(workspace, right_id)
    if left["evaluator_digest"] != right["evaluator_digest"]:
        raise AuditError("baseline evaluator definitions differ; choose the same frozen evaluator")
    definition = left_run["spec"].get("scoring")
    if definition != right_run["spec"].get("scoring"):
        raise AuditError("baseline scoring definitions differ")
    if (
        left["profile"] != right["profile"]
        or left_run["profile"] != right_run["profile"]
        or left_run["limits"] != right_run["limits"]
    ):
        raise AuditError("baseline execution profiles or limits differ")
    left_public, right_public = (
        baselines.public_projection(left),
        baselines.public_projection(right),
    )
    left_score, right_score = left["benchmark_score"], right["benchmark_score"]
    left_value, right_value = _value(left_score), _value(right_score)
    measured = left_value is not None and right_value is not None
    delta = right_value - left_value if measured else None
    if delta is not None and not math.isfinite(delta):
        raise AuditError("baseline score difference exceeds the finite numeric range")
    return {
        "compatible": True,
        "left": left_public,
        "right": right_public,
        "compatibility": {
            "evaluator_digest": left["evaluator_digest"],
            "scoring_digest": digest(definition) if definition is not None else None,
            "profile_digest": digest(left_run["profile"]),
            "same_source_revision": left["source_revision"] == right["source_revision"],
        },
        "benchmark": {
            "population": "fixed_benchmark",
            "direction": "higher_is_better",
            "state": "unscored"
            if definition is None
            else "measured"
            if measured
            else "unavailable",
            "left": left_value,
            "right": right_value,
            "delta": delta,
            "left_eligible": left_score.get("eligible") if left_score else None,
            "right_eligible": right_score.get("eligible") if right_score else None,
        },
        "recent_traces": {
            "population": "recent_traces",
            "controlled_comparison": False,
            "left": left_public["recent_traces"],
            "right": right_public["recent_traces"],
            "limitations": [
                "Recent traces are separate populations with their own windows, coverage and deployment alignment.",
                "Differences in recent trace scores do not establish a controlled application improvement.",
            ],
        },
        "limitations": [
            "Benchmark delta is right minus left under the same frozen evaluator, scoring and execution settings.",
            "Missing scores remain unknown. A higher score cannot override failed behavior gates.",
            "This comparison reports saved measurements; it does not establish statistical significance or run new work.",
        ],
    }
