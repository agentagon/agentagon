"""Descriptive production comparisons with explicit populations and unknown coverage."""

import math
import random
import statistics

from agentagon.capabilities.traces.alignment import _revisions
from agentagon.core.records import digest, timestamp_ns
from agentagon.workflows.discover.handler import trace_context


def rows(workspace, snapshot_id, selector):
    snapshot, spans, invalid = trace_context(workspace, snapshot_id)
    grouped = {}
    for span in spans:
        grouped.setdefault(span["trace_id"], []).append(span)
    filters = dict(selector.get("filters", {}))
    if selector.get("name") and "agent_name" not in filters:
        filters.setdefault("name", selector["name"])
    if selector.get("environment"):
        filters["environment"] = selector["environment"]
    result = []
    incomplete = 0
    for trace_id, items in grouped.items():
        roots = [s for s in items if not s["parent_span_ids"]]
        if len(roots) != 1:
            incomplete += 1
            continue
        ids = {s["span_id"] for s in items}
        if any(p not in ids for s in items for p in s["parent_span_ids"]):
            incomplete += 1
        root = roots[0]
        metadata = {
            **root["resource"],
            **root["attributes"],
            **root["metadata"],
            "name": root["name"],
        }
        if not all(metadata.get(k.removeprefix("metadata.")) == v for k, v in filters.items()):
            continue
        if (
            root["started_ns"] is None
            or root["ended_ns"] is None
            or root["ended_ns"] < root["started_ns"]
        ):
            incomplete += 1
            continue
        versions = set()
        for span in items:
            for field in ("metadata", "attributes", "resource"):
                versions.update(_revisions(span[field]))
        status = root["status"]
        result.append(
            {
                "trace_id": trace_id,
                "evidence_digest": digest(items),
                "identity": digest(
                    [
                        snapshot["provenance"]["provider"],
                        snapshot["connection_id"],
                        snapshot["provenance"].get("project"),
                        trace_id,
                    ]
                ),
                "started_ns": root["started_ns"],
                "ended_ns": root["ended_ns"],
                "latency_ms": max(0, root["ended_ns"] - root["started_ns"]) / 1_000_000,
                # Only a reported root total is used: summing parent and child usage double counts.
                "cost_usd": root["usage"].get("cost_usd"),
                "failure_rate": 1 if status == "error" else 0 if status == "ok" else None,
                "revision": next(iter(versions)) if len(versions) == 1 else None,
                "release": metadata.get("release")
                or metadata.get("deployment.version")
                or metadata.get("service.version"),
                "metadata": metadata,
                "snapshot_id": snapshot_id,
            }
        )
    return result, {
        "complete": bool(snapshot["completeness"].get("complete"))
        and not invalid
        and not incomplete,
        "invalid_spans": invalid,
        "source": snapshot_id,
    }


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def values(rows_, definition, issue_traces):
    selected = [
        r
        for r in rows_
        if all(r["metadata"].get(k) == v for k, v in definition["population"].items())
    ]
    result = []
    for row in selected:
        metric = definition["metric"]
        if metric == "quality":
            value = row["metadata"].get(definition["quality_key"])
        elif metric == "issue_recurrence":
            # Unreviewed traces remain unknown, rather than implying issue absence.
            value = issue_traces.get(definition["issue_id"], {}).get(row["trace_id"])
        else:
            value = row.get(metric)
        if _number(value):
            result.append(float(value))
    return result, len(selected)


def estimate(samples, aggregation):
    if not samples:
        return None
    if aggregation == "p95":
        return sorted(samples)[max(0, math.ceil(len(samples) * 0.95) - 1)]
    return statistics.fmean(samples)


def interval(samples, aggregation, seed):
    if len(samples) < 2:
        return None
    if all(v in (0, 1) for v in samples) and aggregation == "mean":
        n, p, z = len(samples), statistics.fmean(samples), 1.96
        centre = (p + z * z / (2 * n)) / (1 + z * z / n)
        width = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        return [max(0, centre - width), min(1, centre + width)]
    rng = random.Random(seed)
    boot = sorted(estimate(rng.choices(samples, k=len(samples)), aggregation) for _ in range(400))
    return [boot[10], boot[389]]


def covers(windows, start, end):
    """Require continuous acquired coverage, including periods with no matching traces."""
    cursor = start
    for window in sorted(windows, key=lambda w: timestamp_ns(w["start"])):
        if not window.get("complete"):
            continue
        left, right = timestamp_ns(window["start"]), timestamp_ns(window["end"])
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= end:
            return True
    return False


def compare(all_rows, monitor, deployments, end, issue_traces=None):
    end_ns = timestamp_ns(end)
    matches = [
        d
        for d in deployments
        if d["agent_id"] == monitor["agent_id"]
        and d["environment"] == monitor["selector"]["environment"]
        and d.get("deployed_at")
        and timestamp_ns(d["deployed_at"]) <= end_ns
    ]
    deployment = max(matches, key=lambda d: timestamp_ns(d["deployed_at"])) if matches else None
    results = []
    for definition in monitor["measurements"]:
        hour = 3600 * 1_000_000_000
        start = end_ns - definition["window_hours"] * hour
        if deployment:
            deployed = timestamp_ns(deployment["deployed_at"])
            before = [
                r
                for r in all_rows
                if deployed - definition["reference_hours"] * hour <= r["started_ns"] < deployed
            ]
            after = [r for r in all_rows if max(deployed, start) <= r["started_ns"] < end_ns]
            reference_versions = {r["revision"] for r in before}
            reference_versions.discard(None)
            # Unversioned and mixed reference releases cannot become an attributed comparison.
            reference_version = (
                next(iter(reference_versions)) if len(reference_versions) == 1 else None
            )
            aligned = [r for r in after if r["revision"] == deployment["deployed_revision"]]
        else:
            before, after = [], [r for r in all_rows if start <= r["started_ns"] < end_ns]
            aligned, reference_version = after, None
        left, left_total = values(before, definition, issue_traces or {})
        right, right_total = values(aligned, definition, issue_traces or {})
        left_value, right_value = (
            estimate(left, definition["aggregation"]),
            estimate(right, definition["aggregation"]),
        )
        coverage = len(right) / right_total if right_total else 0
        left_coverage = len(left) / left_total if left_total else 0
        status = "insufficient_evidence"
        limitations = []
        population_aligned = bool(
            deployment
            and deployment.get("exact_tested_revision")
            and reference_version
            and all(r["revision"] for r in before)
            and all(r["revision"] == deployment["deployed_revision"] for r in after)
        )
        if deployment and not population_aligned:
            status = "not_comparable"
            limitations.append(
                "Missing or mixed release metadata, or deployment differs from the tested revision."
            )
        time_coverage = monitor.get("coverage_complete", False)
        if deployment and "acquisition_windows" in monitor:
            windows = monitor["acquisition_windows"]
            time_coverage = (
                time_coverage
                and covers(windows, deployed - definition["reference_hours"] * hour, deployed)
                and covers(windows, max(deployed, start), end_ns)
            )
        lci = interval(left, definition["aggregation"], digest([monitor["id"], definition, "left"]))
        rci = interval(
            right, definition["aggregation"], digest([monitor["id"], definition, "right"])
        )
        eligible = (
            population_aligned
            and definition["accepted"]
            and time_coverage
            and min(len(left), len(right)) >= definition["minimum_samples"]
            and min(coverage, left_coverage) >= definition["minimum_coverage"]
        )
        delta = (
            right_value - left_value if left_value is not None and right_value is not None else None
        )
        if eligible and lci and rci:
            low, high = rci[0] - lci[1], rci[1] - lci[0]
            if definition["direction"] == "higher":
                low, high = -high, -low
            threshold = definition["material_change"]
            if high < -threshold:
                status = "improved"
            elif low > threshold:
                status = "regressed"
            elif -threshold <= low <= high <= threshold:
                status = "no_material_change"
        if not definition["accepted"]:
            limitations.append("Accept measurement criteria to classify production outcomes.")
        if not deployment:
            limitations.append("No declared deployment; collecting a descriptive baseline.")
        if not time_coverage:
            limitations.append("Acquisition is partial or contains a coverage gap.")
        results.append(
            {
                "name": definition["name"],
                "definition": definition,
                "status": status,
                "reference": left_value,
                "current": right_value,
                "delta": delta,
                "reference_count": len(left),
                "count": len(right),
                "population": right_total,
                "coverage": coverage,
                "reference_coverage": left_coverage,
                "reference_interval": lci,
                "interval": rci,
                "deployment_id": deployment["id"] if deployment else None,
                "cohorts": sorted({r["revision"] or "unknown" for r in after}),
                "limitations": limitations,
                "causal": False,
                "time_coverage_complete": time_coverage,
                "target_met": (
                    right_value <= definition["target"]
                    if definition["direction"] == "lower"
                    else right_value >= definition["target"]
                )
                if eligible and right_value is not None and definition.get("target") is not None
                else None,
                "release_coverage": len(aligned) / len(after) if after else 0,
            }
        )
    return results
