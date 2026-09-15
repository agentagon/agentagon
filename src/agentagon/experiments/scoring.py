"""Agreed scores retain original measurements and independent pass/fail gates."""

import copy
import statistics

from agentagon.core.records import AuditError, validate_record
from agentagon.experiments.spec import finite, path

AGGREGATIONS = {
    "mean": statistics.mean,
    "median": statistics.median,
    "min": min,
    "max": max,
    "sum": sum,
}


def validate(definition: dict, spec: dict | None = None) -> dict:
    validate_record("score-definition", definition)
    result = copy.deepcopy(definition)
    metrics = result["metrics"]
    mode = result["mode"]
    selected = result.get("primary" if mode == "primary" else "custom_metric")
    if mode != "weighted" and selected not in metrics:
        raise AuditError("scoring requires a declared primary or custom output metric")
    if mode == "weighted" and any("weight" not in m for m in metrics.values()):
        raise AuditError("weighted scoring requires a positive weight for every metric")
    for metric in metrics.values():
        for key in ("weight", "scale"):
            if key in metric and finite(metric[key]) <= 0:
                raise AuditError("scoring weights and scales must be positive")
    ids = [b["id"] for b in result["behaviors"]]
    if len(ids) != len(set(ids)):
        raise AuditError("behavior IDs must be unique")
    for behavior in result["behaviors"]:
        if "metric" in behavior and behavior["metric"] not in metrics:
            raise AuditError("behavior refers to an undeclared score metric")
        if "bound" in behavior:
            finite(behavior["bound"])
    if "target" in result:
        finite(result["target"])
    result["source_paths"] = [path(p, "scoring source") for p in result["source_paths"]]
    judge = result.get("judge")
    if judge:
        if ("trace_command" in judge) != ("trace_input_path" in judge):
            raise AuditError("trace scoring requires both a frozen command and input path")
        if "trace_command" in judge:
            from agentagon.experiments.spec import command

            judge["trace_command"] = command(judge["trace_command"])
            judge["trace_input_path"] = path(judge["trace_input_path"], "trace input path")
            if (
                judge["trace_input_path"] == "."
                or ".agentagon" in judge["trace_input_path"].split("/")
                or judge["trace_input_path"] in result["source_paths"]
            ):
                raise AuditError("trace input must be a separate private execution file")
        judge["rubric_path"] = path(judge["rubric_path"], "judge rubric")
        if judge["rubric_path"] not in result["source_paths"]:
            raise AuditError("judge rubric must be part of the scoring source paths")
        if set(judge["metrics"]) - set(metrics):
            raise AuditError("judge metrics must be declared score metrics")
        for bounds in judge["metrics"].values():
            if finite(bounds["min"]) >= finite(bounds["max"]):
                raise AuditError("judge metric bounds must have min below max")
        if judge["kind"] == "coding-host" and not all(judge.get(k) for k in ("host", "model")):
            raise AuditError("coding-host grading requires explicit host and model provenance")
    if spec is not None:
        from agentagon.experiments.checkouts import under

        if set(metrics) - set(spec["metrics"]):
            raise AuditError("score metrics must be frozen evaluator outputs")
        for name, metric in metrics.items():
            if any(metric[k] != spec["metrics"][name][k] for k in ("direction", "unit")):
                raise AuditError("scoring metric directions and units must match the evaluator")
        for source in result["source_paths"]:
            if not any(under(source, p) for p in spec["evaluation_paths"]):
                raise AuditError("scoring source must be protected by an evaluation path")
        checks = {c["id"] for c in spec.get("checks", [])}
        if any(b.get("check") not in checks for b in result["behaviors"] if "check" in b):
            raise AuditError("behavior refers to an undeclared executable check")
    return result


def summarize(definition: dict, samples: list[dict], checks: list[dict] = ()) -> dict:
    """Never turn absent data or failed execution into a numeric zero."""
    definition = validate(definition)
    components = {}
    missing = []
    for name, metric in definition["metrics"].items():
        values = [sample.get(name) for sample in samples]
        if not values or any(v is None for v in values):
            missing.append(name)
            components[name] = {**metric, "value": None, "state": "missing"}
        else:
            value = AGGREGATIONS[metric["aggregation"]]([finite(v) for v in values])
            components[name] = {**metric, "value": finite(value), "state": "measured"}
    observed = {check["id"]: check for check in checks}
    behaviors = []
    for behavior in definition["behaviors"]:
        if "check" in behavior:
            check = observed.get(behavior["check"])
            passed = (
                check.get("passed") and check.get("expected", "pass") == "pass"
                if check is not None
                else None
            )
        else:
            value = components[behavior["metric"]]["value"]
            passed = (
                None
                if value is None
                else (
                    value >= behavior["bound"]
                    if behavior["op"] == "gte"
                    else value <= behavior["bound"]
                )
            )
        behaviors.append({**behavior, "passed": passed})
    state = "measured"
    if missing:
        state = (
            "failed"
            if any(definition["metrics"][m]["missing"] == "fail" for m in missing)
            else "missing"
        )
    value = None
    if not missing:
        mode = definition["mode"]
        names = (
            list(components)
            if mode == "weighted"
            else [definition["primary" if mode == "primary" else "custom_metric"]]
        )
        value = sum(
            components[name]["value"]
            * (1 if components[name]["direction"] == "max" else -1)
            * (components[name].get("weight", 1) if mode == "weighted" else 1)
            / components[name].get("scale", 1)
            for name in names
        )
        value = finite(value)
    eligible = state == "measured" and all(b["passed"] is True for b in behaviors if b["required"])
    target = definition.get("target")
    # Targets use original units for a primary/custom metric, normalized score for weighted mode.
    target_value = value
    if target is not None and definition["mode"] != "weighted":
        chosen = (
            definition.get("primary")
            if definition["mode"] == "primary"
            else definition["custom_metric"]
        )
        target_value = components[chosen]["value"]
        if components[chosen]["direction"] == "min":
            target_value = -target_value if target_value is not None else None
            target = -target
    return {
        "state": state,
        "value": value,
        "eligible": eligible,
        "components": components,
        "behaviors": behaviors,
        "missing_metrics": missing,
        "target_reached": eligible and target is not None and target_value >= target,
        "judging": "coding-agent judged"
        if definition.get("judge", {}).get("kind") == "coding-host"
        else "evaluator",
    }


def candidate_score(data: dict, candidate: dict) -> dict | None:
    definition = data["spec"].get("scoring")
    if definition is None:
        return None
    score = summarize(
        definition,
        [t["metrics"] for t in candidate["trials"] if t["state"] == "completed"],
        candidate.get("checks", []),
    )
    score["eligible"] &= candidate.get("feasible", False)
    score["target_reached"] &= score["eligible"]
    return score


def qualifying(data: dict) -> list[dict]:
    """Return scored independently reviewed improvements, including dominated alternatives."""
    from agentagon.experiments.evaluation import invalidated

    baseline = data["candidates"][data["baseline_id"]]
    base = candidate_score(data, baseline)
    if baseline["state"] != "verified" or not base or base["value"] is None:
        return []
    ranked = []
    for candidate in data["candidates"].values():
        if candidate["state"] != "verified" or invalidated(data, candidate):
            continue
        score = candidate_score(data, candidate)
        if score and score["eligible"] and score["value"] > base["value"]:
            ranked.append({"candidate_id": candidate["candidate_id"], "score": score})
    return sorted(ranked, key=lambda c: (-c["score"]["value"], c["candidate_id"]))
