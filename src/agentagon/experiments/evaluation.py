"""Measured multiobjective comparisons, without scalar admission shortcuts."""

import statistics

from agentagon.core.records import AuditError
from agentagon.experiments.spec import finite


def commands(
    profile: dict, spec: dict, *, baseline: bool = False, gate_checks: bool = True
) -> list[dict]:
    """Run opt-in preflights first; preparation always executes the full sequence."""
    result = [
        {"id": f"setup-{i}", "role": "setup", "argv": argv, "cwd": "."}
        for i, argv in enumerate(profile["setup"])
    ]
    checks = []
    for check in spec["checks"]:
        command = {"id": check["id"], "role": "check", "argv": check["argv"], "cwd": check["cwd"]}
        if check.get("preflight"):
            if gate_checks:
                command["gate_exit_code"] = (
                    1 if baseline and check["baseline_expected"] == "fail" else 0
                )
            result.append(command)
        else:
            checks.append(command)
    result.append({"id": "benchmark", "role": "benchmark", **spec["benchmark"]})
    result.extend(checks)
    return result


def aggregate(spec: dict, samples: list[dict]) -> tuple[dict, dict]:
    if len(samples) != spec["repetitions"]:
        raise AuditError("all declared repetitions must complete before verification")
    metrics, variation = {}, {}
    for name in spec["metrics"]:
        if any(name not in sample for sample in samples):
            raise AuditError("benchmark omitted a declared metric")
        values = [finite(sample[name]) for sample in samples]
        metrics[name] = statistics.median(values)
        variation[name] = {"min": min(values), "max": max(values), "samples": values}
    return metrics, variation


def task_metrics(spec: dict, output: dict) -> dict:
    """Extract declared task measurements; unrelated legacy task evidence is retained raw."""
    definitions = spec.get("task_metrics", {})
    if not definitions:
        return {}
    tasks = output.get("tasks")
    if not isinstance(tasks, dict) or set(definitions) - set(tasks):
        raise AuditError("benchmark omitted required frozen task measurements")
    return {task_id: finite(tasks[task_id]) for task_id in definitions}


def aggregate_tasks(spec: dict, samples: list[dict]) -> tuple[dict, dict]:
    if not spec.get("task_metrics"):
        return {}, {}
    return aggregate({**spec, "metrics": spec["task_metrics"]}, samples)


def constraints(spec: dict, metrics: dict, baseline: dict) -> list[dict]:
    results = []
    for rule in spec["constraints"]:
        name, bound = rule["metric"], rule["bound"]
        if rule["reference"] == "baseline_delta":
            bound = baseline[name] + bound
        elif rule["reference"] == "baseline_ratio":
            bound = baseline[name] * bound
        actual = metrics[name]
        results.append(
            {
                **rule,
                "threshold": bound,
                "actual": actual,
                "passed": actual >= bound if rule["op"] == "gte" else actual <= bound,
            }
        )
    return results


def dominates(spec: dict, left: dict, right: dict) -> bool:
    no_worse = []
    better = []
    for name, definition in spec["metrics"].items():
        a, b = left[name], right[name]
        no_worse.append(a <= b if definition["direction"] == "min" else a >= b)
        better.append(a < b if definition["direction"] == "min" else a > b)
    return all(no_worse) and any(better)


def invalidated(data: dict, candidate: dict) -> bool:
    """An invalidated ancestor invalidates its descendants without erasing their history."""
    seen = set()
    while candidate is not None:
        candidate_id = candidate["candidate_id"]
        if candidate.get("invalidated") or candidate_id in seen:
            return True
        seen.add(candidate_id)
        parent_id = candidate.get("parent_id")
        if parent_id is not None and parent_id not in data["candidates"]:
            return True
        candidate = data["candidates"].get(parent_id)
    return False


def frontier(data: dict) -> list[str]:
    candidates = [
        c
        for c in data["candidates"].values()
        if c["state"] == "verified" and c.get("feasible") and not invalidated(data, c)
    ]
    return sorted(
        c["candidate_id"]
        for c in candidates
        if not any(
            other["candidate_id"] != c["candidate_id"]
            and dominates(data["spec"], other["metrics"], c["metrics"])
            for other in candidates
        )
    )
