"""Deterministic v2 evidence calculations. Flags are candidates, not findings."""

from collections import defaultdict
from typing import Any

from agentagon.core.records import catalog, digest, identifier


def measure(trace: dict) -> dict:
    spans = trace["spans"]
    by_id = {span["span_id"]: span for span in spans}
    children: dict[str, list[dict]] = defaultdict(list)
    for span in spans:
        for parent in span["parent_span_ids"]:
            children[parent].append(span)
    metrics = []
    observations = []
    flags = []

    def observe(span: dict, facet: str, value: Any, evidence: list[str] | None = None) -> None:
        observations.append(
            {
                "id": identifier("observation", trace["digest"], span["id"], facet),
                "span_id": span["id"],
                "facet": facet,
                "value": value,
                "evidence": evidence or [span["id"]],
                "status": "computed",
            }
        )

    def flag(span: dict, kind: str, evidence: list[str]) -> None:
        flags.append(
            {
                "id": identifier("flag", trace["digest"], span["id"], kind, evidence),
                "kind": kind,
                "evidence": evidence,
                "status": "candidate",
            }
        )

    for span in spans:
        start, end = span["started_ns"], span["ended_ns"]
        state = (
            "not_applicable"
            if start is None or end is None
            else "error"
            if end < start
            else "computed"
        )
        metrics.append(
            {
                "span_id": span["id"],
                "name": "latency",
                "unit": "ms",
                "status": state,
                "value": (end - start) / 1_000_000 if state == "computed" else None,
            }
        )
        if span["kind"] == "tool":
            observe(
                span,
                "tool.outcome",
                {"ok": "success", "error": "failure"}.get(span["status"], "unknown"),
            )
            if span["status"] == "error":
                flag(span, "tool_failure", [span["id"]])
        if span["kind"] == "llm":
            requests = [
                part
                for msg in span["messages"]
                if "/output/" in msg["id"]
                for part in msg["parts"]
                if part["kind"] == "tool_call"
            ]
            executions = [child for child in children[span["span_id"]] if child["kind"] == "tool"]
            used: set[str] = set()
            matched = 0
            for request in requests:
                candidates = [
                    child
                    for child in executions
                    if child["id"] not in used
                    and (
                        (
                            request.get("tool_call_id")
                            and child.get("tool_call_id") == request["tool_call_id"]
                        )
                        or (
                            not request.get("tool_call_id")
                            and child["name"] == request.get("tool_name")
                        )
                    )
                ]
                if len(candidates) == 1:
                    matched += 1
                    used.add(candidates[0]["id"])
            observe(
                span,
                "llm.tool_request_execution",
                {
                    "requested": len(requests),
                    "matched": matched,
                    "unmatched": len(requests) - matched,
                    "completeness": trace["completeness"],
                },
            )
            if len(requests) > matched:
                flag(span, "orphaned_tool_request", [span["id"], *[s["id"] for s in executions]])
        if span["kind"] == "agent":
            owned = _owned_tools(span, spans, by_id)
            agents = [child for child in children[span["span_id"]] if child["kind"] == "agent"]
            observe(
                span,
                "agent.delegation",
                {"delegated": bool(agents), "agent_ids": [s["id"] for s in agents]},
            )
            parallel = [
                [a["id"], b["id"]]
                for i, a in enumerate(owned + agents)
                for b in (owned + agents)[i + 1 :]
                if _overlap(a, b)
            ]
            observe(
                span,
                "agent.parallelism",
                {"parallel": bool(parallel), "overlapping_pairs": parallel},
            )
            groups: dict[str, list[dict]] = defaultdict(list)
            for tool in owned:
                groups[digest([tool["name"], tool["input"]])].append(tool)
            recovery = []
            for group in groups.values():
                ordered = sorted(
                    group, key=lambda s: (s["started_ns"] is None, s["started_ns"] or 0, s["id"])
                )
                failed = [s for s in ordered if s["status"] == "error"]
                recovered = any(
                    a["status"] == "error"
                    and b["status"] == "ok"
                    and a["ended_ns"] is not None
                    and b["started_ns"] is not None
                    and a["ended_ns"] <= b["started_ns"]
                    for a in ordered
                    for b in ordered
                )
                if failed:
                    recovery.append(
                        {
                            "tool": ordered[0]["name"],
                            "recovered": recovered,
                            "span_ids": [s["id"] for s in ordered],
                        }
                    )
                if len(group) > 1:
                    flag(span, "repeated_tool_call", [s["id"] for s in ordered])
                if len(failed) > 1 and not recovered:
                    flag(span, "retry_exhausted", [s["id"] for s in ordered])
            observe(span, "agent.recovery", recovery)
            if _ancestor_with_name(span, by_id):
                flag(span, "delegation_cycle", [span["id"]])

    roots = [span for span in spans if not span["parent_span_ids"]]
    starts = [s["started_ns"] for s in spans if s["started_ns"] is not None]
    ends = [s["ended_ns"] for s in spans if s["ended_ns"] is not None]
    duration = (
        (max(ends) - min(starts)) / 1_000_000
        if starts and ends and max(ends) >= min(starts)
        else None
    )
    usage = {
        key: _usage_total(spans, children, key)
        for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd")
    }
    return {
        "metrics": metrics,
        "observations": observations,
        "flags": flags,
        "summary": {
            "span_count": len(spans),
            "tool_calls": sum(s["kind"] == "tool" for s in spans),
            "error_count": sum(s["status"] == "error" for s in spans),
            "elapsed_ms": duration,
            "root_count": len(roots),
            "usage": usage,
        },
        "required_judgments": required_judgments(trace),
    }


def _overlap(a: dict, b: dict) -> bool:
    if any(s[k] is None for s in (a, b) for k in ("started_ns", "ended_ns")):
        return False
    return max(a["started_ns"], b["started_ns"]) < min(a["ended_ns"], b["ended_ns"])


def _owned_tools(agent: dict, spans: list[dict], by_id: dict) -> list[dict]:
    result = []
    for span in spans:
        if span["kind"] != "tool":
            continue
        pending = list(span["parent_span_ids"])
        seen = set()
        owners = set()
        while pending:
            key = pending.pop()
            if key in seen or key not in by_id:
                continue
            seen.add(key)
            parent = by_id[key]
            if parent["kind"] == "agent":
                owners.add(parent["id"])
            else:
                pending.extend(parent["parent_span_ids"])
        if agent["id"] in owners:
            result.append(span)
    return result


def _ancestor_with_name(span: dict, by_id: dict) -> bool:
    pending = list(span["parent_span_ids"])
    seen = set()
    while pending:
        key = pending.pop()
        if key in seen or key not in by_id:
            continue
        seen.add(key)
        ancestor = by_id[key]
        if ancestor["kind"] == "agent" and span["name"] and ancestor["name"] == span["name"]:
            return True
        pending.extend(ancestor["parent_span_ids"])
    return False


def _usage_total(spans: list[dict], children: dict, key: str) -> dict:
    eligible = [s for s in spans if s["kind"] == "llm"]
    candidates = [s for s in spans if s["usage"].get(key) is not None]
    selected = []
    excluded = []
    for span in candidates:
        pending = list(children[span["span_id"]])
        seen = set()
        has_measured_descendant = False
        while pending:
            child = pending.pop()
            if child["id"] in seen:
                continue
            seen.add(child["id"])
            if child in candidates:
                has_measured_descendant = True
            pending.extend(children[child["span_id"]])
        (excluded if has_measured_descendant else selected).append(span)
    return {
        "value": sum(s["usage"][key] for s in selected) if selected else None,
        "basis": "provider_reported",
        "measured_spans": [s["id"] for s in selected],
        "excluded_ancestor_aggregates": [s["id"] for s in excluded],
        "unmeasured_llm_spans": [s["id"] for s in eligible if s["usage"].get(key) is None],
        "coverage": "unknown"
        if not selected
        else "partial"
        if excluded or any(s["usage"].get(key) is None for s in eligible)
        else "reported",
    }


def required_judgments(trace: dict) -> list[dict]:
    definitions = catalog()["semantic"]
    result = []
    for definition in definitions:
        subjects = (
            [trace["id"]]
            if definition["scope"] == "trace"
            else [s["id"] for s in trace["spans"] if s["kind"] == "llm"]
        )
        for subject in subjects:
            result.append(
                {"subject": subject, "facet": definition["id"], "status": "not_evaluated"}
            )
    return result
