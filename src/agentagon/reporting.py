"""Readable reports from audit records and bounded optimization runs."""

import math

from agentagon.core.records import now
from agentagon.operations import findings, progress
from agentagon.storage.issues import SEVERITY, list_issues
from agentagon.storage.workspace import Workspace


def finding_category(finding: dict) -> str:
    if finding["kind"] == "evaluation_coverage":
        return "Eval recommendation"
    return "Improvement" if finding.get("improvement") else "Defect"


def trace_alignment_text(alignment: dict | None) -> str | None:
    if not alignment:
        return None
    revision = alignment.get("revision") or "the captured revision"
    warning = alignment.get("warning")
    if alignment.get("status") == "mismatch":
        return (
            f"Trace metadata conflicts with {revision}. "
            "Code/trace correlation is limited by this revision mismatch."
            + (f" {warning}" if warning else "")
        )
    if warning:
        return warning
    if alignment.get("status") == "unverified" or (
        alignment.get("status") == "assumed" and alignment.get("scope") == "changes"
    ):
        return (
            "The relationship between supplied traces and the captured local changes is "
            "unverified. Consult each finding's correlation rationale; traces may describe "
            "the baseline rather than the edits."
        )
    if alignment.get("status") == "assumed":
        return (
            f"Supplied traces are assumed to reflect {revision}; "
            "their revision provenance has not been verified."
        )
    return None


def build_report(workspace: Workspace, audit_id: str) -> dict:
    """Build a current report without writing audit state or artifacts."""
    audit = workspace.read_audit(audit_id)
    current = progress(audit)
    registry = {issue["issue_id"]: issue for issue in list_issues(workspace)}
    available = {
        finding["id"]: {**finding, "category": finding_category(finding)}
        for finding in findings(audit)
    }
    grouped = {}
    for group in audit["groups"]:
        entry = grouped.setdefault(
            group["issue_id"],
            {
                "issue_id": group["issue_id"],
                "title": group["title"],
                "summary": group["summary"],
                "rationale": group["rationale"],
                "findings": [],
            },
        )
        entry["findings"].extend(available[key] for key in group["finding_ids"])
    denominator = current["coverage"]["reviewed_traces"]
    for issue in grouped.values():
        trace_ids = {key for finding in issue["findings"] for key in finding["trace_ids"]}
        issue.update(
            status=registry[issue["issue_id"]]["status"],
            history=registry[issue["issue_id"]]["history"],
            affected_trace_ids=sorted(trace_ids),
            reviewed_trace_denominator=denominator,
            reviewed_sample_rate=len(trace_ids) / denominator
            if trace_ids and denominator
            else None,
            severity=max((f["severity"] for f in issue["findings"]), key=SEVERITY.get),
            confidence=max(f["confidence"] for f in issue["findings"]),
        )
    issues = sorted(
        grouped.values(),
        key=lambda issue: (
            -SEVERITY[issue["severity"]],
            -len(issue["affected_trace_ids"]),
            -issue["confidence"],
            issue["issue_id"],
        ),
    )
    limits = [
        "Rates describe distinct reviewed traces, not estimated production prevalence.",
        "No additional session turns were fetched. Missing feedback remains unknown.",
        "Code inspection is scoped review, not proof of semantic completeness.",
        "Semantic judgments and root-cause/efficiency hypotheses are not measured fixes.",
    ]
    acquisition = audit["acquisition"]
    if acquisition is not None:
        provenance = acquisition["provenance"]
        limits += [
            f"Acquisition completeness: {provenance['completeness']}; "
            f"pagination complete: {provenance['pagination_complete']}.",
            f"Import diagnostics: {len(acquisition['diagnostics'])}; "
            f"missing selected traces: {len(acquisition['missing_selected_ids'])}; "
            f"failed fetches: {len(provenance['failed_trace_ids'])}.",
        ]
    plan = audit.get("acquisition_plan")
    if plan is not None:
        estimate = plan["estimate"]
        limits.append(
            f"Download plan: {estimate['selected_traces']} selected traces; "
            f"{estimate['eligible_traces']} eligible ({estimate['eligible_count_kind']}); "
            f"root inventory complete: {estimate['inventory_complete']}."
        )
    limits.append(f"Skipped code files: {len(audit['snapshot']['skipped'])}.")
    if audit["snapshot"].get("code_scope") == "changes":
        limits.append(
            "Completion covers the selected local changes. Related code is context; "
            "unrelated existing defects are outside this review."
        )
    alignment = current.get("trace_alignment") or audit.get("trace_alignment")
    if alignment_limit := trace_alignment_text(alignment):
        limits.append(alignment_limit)
    return {
        **current,
        "workflow": current.get("workflow")
        or ("review" if audit["snapshot"].get("code_scope") == "changes" else "audit"),
        "revision": current.get("revision") or audit["snapshot"].get("revision"),
        "trace_alignment": alignment,
        "generated_at": now(),
        "host": audit["host"],
        "model": audit["model"],
        "window": audit["window"],
        "selection": audit["selection"],
        "limit": audit["limit"],
        "acquisition": audit["acquisition"],
        "acquisition_plan": audit.get("acquisition_plan"),
        "code_scope": audit["snapshot"],
        "issues": issues,
        "ungrouped_findings": [
            f
            for f in available.values()
            if not any(f["id"] in g["finding_ids"] for g in audit["groups"])
        ],
        "measurements": [
            {"trace_id": trace["id"], **trace["measurements"]["summary"]}
            for trace in audit["traces"]
        ],
        "analysis": {"reviews": audit["reviews"], "diagnoses": audit["diagnoses"]},
        "intelligence": [
            workspace.read_artifact(receipt["path"]) for receipt in audit.get("intelligence", [])
        ],
        "limits": limits,
    }


def report(workspace: Workspace, audit_id: str) -> dict:
    with workspace.locked():
        payload = build_report(workspace, audit_id)
        directory = workspace.state / "reports" / audit_id
        workspace.write(directory / "report.json", payload)
        workspace.write_bytes(directory / "report.md", _markdown(payload).encode())
        return {
            **progress(workspace.read_audit(audit_id)),
            "report": str(directory / "report.md"),
            "json_report": str(directory / "report.json"),
        }


def _plain(value: object) -> str:
    return str(value).replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")


def _markdown(report: dict) -> str:
    coverage = report["coverage"]
    is_review = report.get("workflow") == "review"
    workflow = "Review" if is_review else "Audit"
    snapshot = report["code_scope"]
    lines = [
        f"# Agentagon {workflow.lower()}",
        "",
        f"{workflow}: `{report['audit_id']}` · Status: **{report['state']}**",
        "",
        f"Auditing host: {_plain(report['host'])} · Model: {_plain(report['model'])}",
        "",
        f"Reviewed {coverage['reviewed_traces']} of {coverage['selected_traces']} selected traces and "
        f"{coverage['reviewed_code_units']} of {coverage['code_units']} code units.",
        "",
    ]
    if revision := report.get("revision"):
        lines += [f"Baseline revision: `{_plain(revision)}`", ""]
    elif is_review:
        lines += ["Baseline: empty checkout before the first commit.", ""]
    if report["mode"] != "traces":
        scope_name = "Local changes" if is_review else "Selected code"
        lines += [f"Code scope: {scope_name} · {_plain(', '.join(snapshot['scopes']))}", ""]
    if alignment := trace_alignment_text(report.get("trace_alignment")):
        lines += [_plain(alignment), ""]
    if report["goal"]:
        lines += [f"{workflow} goal: {_plain(report['goal'])}", ""]
    if report["window"]:
        lines += [
            f"Window: {report['window']['start']} to {report['window']['end']} (end exclusive). Selection: newest root starts; limit {report['limit']}.",
            "",
        ]
    if report["pending_action"]:
        lines += [
            f"Next action: **{report['pending_action']}**. This {workflow.lower()} is unfinished; an empty issue list is not a clean bill of health.",
            "",
        ]
    if report["intelligence"]:
        lines += ["## Optional intelligence guidance", ""]
        for receipt in report["intelligence"]:
            lines.append(f"- {receipt['phase']}: {receipt['status']}")
            if receipt["status"] == "complete":
                response = receipt["response"]
                lines.append(
                    f"  Knowledge version: `{response['knowledge_version']}`; cards: "
                    + ", ".join(f"`{card['id']}`" for card in response["suggestions"])
                )
        lines += [
            "",
            "Guidance suggests investigations; findings below require local evidence.",
            "",
        ]
    lines += ["## Top issues", ""]
    if not report["issues"]:
        lines += ["No grouped actionable issues in the reviewed evidence.", ""]
    for issue in report["issues"][:5]:
        count = len(issue["affected_trace_ids"])
        support = (
            f"{count}/{issue['reviewed_trace_denominator']} reviewed traces"
            if count
            else "code evidence; runtime prevalence unknown"
        )
        categories = ", ".join(dict.fromkeys(finding_category(f) for f in issue["findings"]))
        lines += [
            f"- **{_plain(issue['title'])}** — {categories}; {issue['severity']}; {support}; {issue['status']}.",
            f"  {_plain(issue['rationale'])}",
        ]
    lines += ["", "## Complete issue inventory", ""]
    for issue in report["issues"]:
        lines += [
            f"### {_plain(issue['title'])}",
            "",
            f"`{issue['issue_id']}` · {issue['status']} · confidence {issue['confidence']:.2f}",
            "",
            _plain(issue["summary"]),
            "",
        ]
        for finding in issue["findings"]:
            category = finding_category(finding)
            action = (
                "Proposed action" if category != "Defect" else "Next investigation / hypothesis"
            )
            lines += [
                f"- **{category}** · {_plain(finding['kind'])} ({finding['basis']}): {_plain(finding['observation'])}",
                f"  Expected: {_plain(finding['expected_behavior'])}",
                f"  Authority: {_plain(finding['authority'])}",
            ]
            if finding["hypothesis"]:
                lines.append(f"  {action}: {_plain(finding['hypothesis'])}")
            if finding.get("correlation_rationale"):
                lines.append(f"  Connection: {_plain(finding['correlation_rationale'])}")
            for ref_id, source in finding["source_references"].items():
                lines.append(f"  Evidence: `{ref_id}` → {_source_location(source)}.")
        lines.append("")
    if report["ungrouped_findings"]:
        lines += ["## Findings awaiting grouping", ""]
        lines += [
            f"- **{finding_category(finding)}** · `{finding['id']}`: {_plain(finding['title'])}"
            for finding in report["ungrouped_findings"]
        ]
        lines.append("")
    if is_review:
        lines += ["## Selected changes", ""]
        for change in snapshot.get("changes", []):
            path = change.get("new_path") or change.get("old_path") or change["path"]
            previous = change.get("old_path")
            location = f"{previous} → {path}" if previous and previous != path else path
            lines.append(f"- {_plain(change['change_type'])}: `{_plain(location)}`")
        lines.append("")
    if snapshot["skipped"]:
        lines += ["## Excluded code", ""]
        for skipped in snapshot["skipped"]:
            lines.append(f"- `{_plain(skipped['path'])}`: {_plain(skipped['reason'])}")
        lines.append("")
    lines += ["## Coverage and limits", ""] + [f"- {limit}" for limit in report["limits"]]
    return "\n".join(lines) + "\n"


def _source_location(source: dict) -> str:
    change = source.get("change")
    if source["kind"] == "code" and change:
        locations = []
        for side, label in (("old", "before"), ("new", "after")):
            path = change.get(f"{side}_path")
            if path is None:
                continue
            start, count = change.get(f"{side}_start"), change.get(f"{side}_count")
            lines = f" lines {start}–{start + count - 1}" if start and count else ""
            locations.append(f"{label}: `{_plain(path)}`{lines}")
        if locations:
            return "; ".join(locations)
    locator = (
        f" lines {source['start_line']}–{source['end_line']}" if source["kind"] == "code" else ""
    )
    return f"`{_plain(source['path'])}`{locator}"


def _fix_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _fix_measurements(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(key): number for key, number in value.items() if _fix_number(number)}


def _fix_text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _fix_variation(value: object) -> dict:
    variation = {}
    for name, values in value.items() if isinstance(value, dict) else []:
        if isinstance(values, dict):
            variation[str(name)] = {
                key: values[key] for key in ("min", "max") if _fix_number(values.get(key))
            }
    return variation


def _fix_candidate(candidate: dict, candidate_id: str, *, invalidated: bool = False) -> dict:
    constraints = []
    for constraint in candidate.get("constraints") or []:
        if not isinstance(constraint, dict):
            continue
        item = {
            key: constraint[key]
            for key in ("bound", "threshold", "actual")
            if _fix_number(constraint.get(key))
        }
        item["metric"] = str(constraint.get("metric", "unknown"))
        operation = constraint.get("op")
        item["op"] = {"gte": ">=", "lte": "<=", ">=": ">=", "<=": "<="}.get(operation)
        item["passed"] = (
            constraint.get("passed") if isinstance(constraint.get("passed"), bool) else None
        )
        if constraint.get("reference") in {"absolute", "baseline_delta", "baseline_ratio"}:
            item["reference"] = constraint["reference"]
        constraints.append(item)
    checks = []
    for check in candidate.get("checks") or candidate.get("gates") or []:
        if not isinstance(check, dict):
            continue
        checks.append(
            {
                "id": str(check.get("id", check.get("name", "check"))),
                "passed": check.get("passed") if isinstance(check.get("passed"), bool) else None,
                "issue_ids": [item for item in check.get("issue_ids", []) if isinstance(item, str)],
            }
        )
    review = candidate.get("review") or {}
    verdict = review.get("verdict") if isinstance(review, dict) else None
    return {
        "id": _fix_text(candidate.get("candidate_id"), candidate_id),
        "parent_id": _fix_text(candidate.get("parent_id")) or None,
        "hypothesis": _fix_text(candidate.get("hypothesis")),
        "state": _fix_text(candidate.get("state"), "unknown"),
        "display_state": "invalidated"
        if invalidated
        else _fix_text(candidate.get("state"), "unknown"),
        "invalidated": invalidated,
        "expansion_exhausted": bool(candidate.get("expansion_exhausted", False)),
        "feasible": bool(candidate.get("feasible", False)),
        "metrics": _fix_measurements(candidate.get("metrics")),
        "variation": _fix_variation(candidate.get("variation")),
        "task_metrics": _fix_measurements(candidate.get("task_metrics")),
        "task_variation": _fix_variation(candidate.get("task_variation")),
        "constraints": constraints,
        "checks": checks,
        "review_verdict": verdict if verdict in {"pass", "reject"} else None,
        "branch": candidate.get("branch") if isinstance(candidate.get("branch"), str) else None,
    }


def _fix_policy(value: object) -> dict:
    policy = value if isinstance(value, dict) else {}
    return {
        "strategy": _fix_text(policy.get("strategy"), "pareto"),
        **({"objective": policy["objective"]} if isinstance(policy.get("objective"), str) else {}),
        **{key: policy[key] for key in ("seed", "k") if type(policy.get(key)) is int},
        **{key: policy[key] for key in ("epsilon", "temperature") if _fix_number(policy.get(key))},
    }


def _fix_search(value: object, policy: dict) -> dict:
    state = value if isinstance(value, dict) else {}
    revisions = []
    for revision in state.get("revisions", []):
        if not isinstance(revision, dict):
            continue
        revisions.append(
            {
                "revision": revision.get("revision")
                if type(revision.get("revision")) is int
                else 0,
                "policy": _fix_policy(revision.get("policy")),
                "at": _fix_text(revision.get("at")),
            }
        )
    decisions = []
    for decision in state.get("decisions", []):
        if not isinstance(decision, dict):
            continue
        item = {
            key: decision[key]
            for key in ("candidate_id", "chosen_parent", "operation_id", "task_id", "at")
            if isinstance(decision.get(key), str)
        }
        item.update(
            {
                key: decision[key]
                for key in ("index", "policy_revision", "seed")
                if type(decision.get(key)) is int
            }
        )
        item["policy"] = _fix_policy(decision.get("policy"))
        item["explicit_parent"] = bool(decision.get("explicit_parent", False))
        item["selection_pool"] = [
            cid for cid in decision.get("selection_pool", []) if isinstance(cid, str)
        ]
        item["eligible"] = [
            {
                "candidate_id": _fix_text(entry.get("candidate_id")),
                "metrics": _fix_measurements(entry.get("metrics")),
                "task_metrics": _fix_measurements(entry.get("task_metrics")),
                **(
                    {"last_expanded": entry["last_expanded"]}
                    if type(entry.get("last_expanded")) is int
                    else {}
                ),
            }
            for entry in decision.get("eligible", [])
            if isinstance(entry, dict)
        ]
        decisions.append(item)
    return {
        "policy": policy,
        "revision": state.get("revision") if type(state.get("revision")) is int else 0,
        "decision_index": state.get("decision_index")
        if type(state.get("decision_index")) is int
        else 0,
        "revisions": revisions,
        "decisions": decisions,
    }


def _fix_learning(run_state: dict) -> dict:
    from agentagon.experiments.learning import pending

    status = pending(run_state)
    scan_pending = {
        **{
            key: status[key]
            for key in ("enabled", "pending", "expired")
            if type(status.get(key)) is bool
        },
        **{key: status[key] for key in ("used", "limit") if type(status.get(key)) is int},
        **{
            key: status[key]
            for key in ("scan_id", "action", "reason")
            if isinstance(status.get(key), str)
        },
    }
    history = []
    for scan in run_state.get("scans", []):
        if not isinstance(scan, dict):
            continue
        item = {
            key: scan[key]
            for key in (
                "scan_id",
                "round_id",
                "state",
                "created_at",
                "deadline_at",
                "ended_at",
                "reason",
            )
            if isinstance(scan.get(key), str)
        }
        item["candidate_ids"] = [
            cid for cid in scan.get("candidate_ids", []) if isinstance(cid, str)
        ]
        item["lesson_count"] = (
            sum(isinstance(insight, dict) for insight in scan.get("insights", []))
            if scan.get("state") == "completed"
            else 0
        )
        history.append(item)
    return {
        "scan_pending": scan_pending,
        "scan_history": history,
        "lesson_count": sum(scan["lesson_count"] for scan in history),
    }


def _fix_deliveries(value: object) -> list[dict]:
    import re

    deliveries = []
    for delivery in value.values() if isinstance(value, dict) else []:
        if not isinstance(delivery, dict):
            continue
        item = {
            key: delivery[key]
            for key in (
                "delivery_id",
                "candidate_id",
                "branch",
                "base",
                "state",
                "created_at",
                "updated_at",
            )
            if isinstance(delivery.get(key), str)
        }
        pr = delivery.get("pr")
        if isinstance(pr, dict):
            projected = {
                "state": _fix_text(pr.get("state"), "unknown"),
                "is_draft": pr.get("is_draft") if type(pr.get("is_draft")) is bool else None,
            }
            if type(pr.get("number")) is int and pr["number"] > 0:
                projected["number"] = pr["number"]
            url = _fix_text(pr.get("url"))
            if (
                "number" in projected
                and re.fullmatch(
                    r"https://[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*", url
                )
                and url.endswith(f"/pull/{projected['number']}")
            ):
                projected["url"] = url
            item["pr"] = projected
        deliveries.append(item)
    return deliveries


def build_fix_report(workspace: Workspace, run_state: dict) -> dict:
    """Project run results without commands, trial payloads, paths or private inputs.

    This is shared by saved reports and the HTTP viewer. Do not pass
    through the engine's status response: it contains private execution state.
    """
    from agentagon.experiments import evaluation
    from agentagon.experiments.controls import projection

    raw_candidates = {
        cid: {**candidate, "candidate_id": _fix_text(candidate.get("candidate_id"), cid)}
        for cid, candidate in (run_state.get("candidates") or {}).items()
        if isinstance(candidate, dict)
    }
    candidates = [
        _fix_candidate(
            candidate,
            cid,
            invalidated=evaluation.invalidated(
                {**run_state, "candidates": raw_candidates}, candidate
            ),
        )
        for cid, candidate in raw_candidates.items()
    ]
    valid_ids = {candidate["id"] for candidate in candidates if not candidate["invalidated"]}
    projected_controls = projection(run_state)
    policy = _fix_policy(projected_controls["search_policy"])
    controls = [
        {
            key: value
            for key, value in operation.items()
            if isinstance(value, str) or (key == "retryable" and type(value) is bool)
        }
        for operation in projected_controls["controls"]
    ]
    selected = run_state.get("selected") or {}
    limits = run_state.get("limits") or {}
    usage = run_state.get("usage") or {}
    profile = run_state.get("profile") or {}
    runner = profile.get("runner") or {}
    return {
        "run_id": run_state["run_id"],
        "revision": run_state.get("revision") if type(run_state.get("revision")) is int else 0,
        "goal": _fix_text(run_state.get("goal")),
        "issue_ids": [item for item in run_state.get("issue_ids", []) if isinstance(item, str)],
        "created_at": run_state.get("created_at"),
        "updated_at": run_state.get("updated_at"),
        "state": _fix_text(run_state.get("state"), "unknown"),
        "baseline_id": _fix_text(run_state.get("baseline_id")) or None,
        "frontier": [
            item
            for item in run_state.get("frontier", [])
            if isinstance(item, str) and item in valid_ids
        ],
        "candidates": candidates,
        "rounds": [
            {
                key: round_[key]
                for key in ("round_id", "size", "candidates", "completed", "branches")
                if key in round_
            }
            for round_ in run_state.get("rounds", [])
            if isinstance(round_, dict)
        ],
        "search_policy": policy,
        "search": _fix_search(run_state.get("search"), policy),
        "objectives": projected_controls["objectives"],
        "task_objectives": [
            {
                "name": name,
                "direction": _fix_text(value.get("direction")),
                "unit": _fix_text(value.get("unit")),
            }
            for name, value in run_state.get("spec", {}).get("task_metrics", {}).items()
            if isinstance(value, dict)
        ],
        "controls": controls,
        "pending_controls": [
            operation
            for operation in controls
            if operation.get("state") in {"queued", "acknowledged"}
        ],
        **_fix_learning(run_state),
        "deliveries": _fix_deliveries(run_state.get("deliveries")),
        "limits": {
            key: limits[key]
            for key in (
                "max_candidates",
                "max_trials",
                "max_elapsed_seconds",
                "parallel_candidates",
                "parallel_trials",
                "trial_timeout_seconds",
                "stagnation_rounds",
            )
            if _fix_number(limits.get(key))
        },
        "usage": {
            key: usage[key]
            for key in ("candidates", "trials", "elapsed_seconds")
            if _fix_number(usage.get(key))
        },
        "runner_kind": runner.get("kind")
        if runner.get("kind") in {"local", "ssh", "e2b"}
        else None,
        "selected_branch": selected.get("branch")
        if isinstance(selected.get("branch"), str)
        else None,
        "cleanup_pending": bool(run_state.get("cleanup_pending", False)),
    }


def _fix_cell(value: object) -> str:
    return _plain(value).replace("|", "\\|").replace("`", "\\`")


def render_fix_markdown(data: dict) -> str:
    """Render the safe run projection; no engine state or extra artifacts needed."""
    candidates = data.get("candidates", [])
    baseline = next((item for item in candidates if item["id"] == data.get("baseline_id")), {})
    baseline_metrics = baseline.get("metrics", {})
    dimensions = sorted({name for item in candidates for name in item.get("metrics", {})})
    lines = [
        "# Agentagon fix run",
        "",
        f"Run: `{_fix_cell(data['run_id'])}` · Status: **{_fix_cell(data['state'])}** · Revision: {data.get('revision', 0)}",
        "",
        _fix_cell(data.get("goal", "")),
        "",
        "Metrics show each candidate's measured value and absolute change from the baseline.",
        "",
        "| Candidate / parent | State | "
        + " | ".join(_fix_cell(name) for name in dimensions)
        + (" | " if dimensions else "")
        + "Constraints | Checks | Review |",
        "| --- | --- | " + "--- | " * len(dimensions) + "--- | --- | --- |",
    ]
    for candidate in candidates:
        state = candidate.get("display_state", candidate["state"])
        if candidate.get("expansion_exhausted"):
            state += "; expansion exhausted"
        cells = [
            _fix_cell(candidate["id"] + " / " + (candidate.get("parent_id") or "baseline")),
            _fix_cell(state),
        ]
        for name in dimensions:
            value = candidate.get("metrics", {}).get(name)
            previous = baseline_metrics.get(name)
            label = "—" if value is None else f"{value:g}"
            if (
                value is not None
                and previous is not None
                and candidate["id"] != data.get("baseline_id")
            ):
                label += f" ({value - previous:+g})"
            spread = candidate.get("variation", {}).get(name, {})
            if "min" in spread and "max" in spread and spread["min"] != spread["max"]:
                label += f"; range {spread['min']:g}–{spread['max']:g}"
            cells.append(label)
        for key in ("constraints", "checks"):
            items = candidate.get(key, [])
            labels = []
            for item in items:
                status = (
                    "pass"
                    if item.get("passed") is True
                    else "fail"
                    if item.get("passed") is False
                    else "pending"
                )
                if key == "constraints":
                    name = item.get("metric", "constraint")
                    if "threshold" in item:
                        name += f" {item.get('op') or ''} {item['threshold']:g}"
                else:
                    name = item.get("id", "check")
                labels.append(f"{_fix_cell(name)}: {status}")
            cells.append("; ".join(labels) if labels else "not recorded")
        cells.append(candidate.get("review_verdict") or "not recorded")
        lines.append("| " + " | ".join(cells) + " |")
    hypotheses = [candidate for candidate in candidates if candidate.get("hypothesis")]
    if hypotheses:
        lines += ["", "## Candidate hypotheses", ""]
        lines += [
            f"- {_fix_cell(candidate['id'])}: {_fix_cell(candidate['hypothesis'])}"
            for candidate in hypotheses
        ]
    task_candidates = [candidate for candidate in candidates if candidate.get("task_metrics")]
    if task_candidates:
        lines += ["", "## Task measurements", ""]
        for candidate in task_candidates:
            values = []
            for task_id, value in sorted(candidate["task_metrics"].items()):
                label = f"{_fix_cell(task_id)}: {value:g}"
                spread = candidate.get("task_variation", {}).get(task_id, {})
                if "min" in spread and "max" in spread and spread["min"] != spread["max"]:
                    label += f" (range {spread['min']:g}–{spread['max']:g})"
                values.append(label)
            lines.append(f"- {_fix_cell(candidate['id'])}: " + "; ".join(values))
    lines += ["", "## Result", ""]
    if data.get("selected_branch"):
        lines.append(f"Reviewable branch: `{_fix_cell(data['selected_branch'])}`.")
    else:
        lines.append("No branch has been selected for review.")
    lines.append(
        "Frontier: "
        + (", ".join(_fix_cell(item) for item in data.get("frontier", [])) or "none")
        + "."
    )
    if data.get("cleanup_pending"):
        lines.append("Workspace cleanup is pending.")
    policy = data.get("search_policy", {})
    if policy:
        search = data.get("search", {})
        parameters = [
            f"{key}={_fix_cell(value)}" for key, value in policy.items() if key != "strategy"
        ]
        lines += [
            "",
            "## Search and controls",
            "",
            f"Strategy: `{_fix_cell(policy['strategy'])}`"
            + (" · " + ", ".join(parameters) if parameters else "")
            + f". Policy revision: {search.get('revision', 0)}; parent decisions: {search.get('decision_index', 0)}.",
            f"Pending controls: {len(data.get('pending_controls', []))}.",
        ]
        for operation in data.get("controls", []):
            detail = next(
                (operation[key] for key in ("text", "hypothesis", "reason") if operation.get(key)),
                "",
            )
            lines.append(
                f"- `{_fix_cell(operation.get('operation_id', ''))}`: {_fix_cell(operation.get('action', 'control'))}"
                + f" — {_fix_cell(operation.get('state', 'unknown'))}"
                + (f"; {_fix_cell(detail)}" if detail else "")
            )
    scan_pending = data.get("scan_pending", {})
    if scan_pending:
        lines += ["", "## Learning", ""]
        if scan_pending.get("enabled"):
            lines.append(
                f"Scans used: {scan_pending.get('used', 0)}/{scan_pending.get('limit', 0)}."
            )
            if scan_pending.get("pending"):
                lines.append(
                    f"Next action: {_fix_cell(scan_pending.get('action', 'inspect pending scan'))}."
                )
        else:
            lines.append("Scans are disabled for this run.")
        lines.append(f"Recorded lessons: {data.get('lesson_count', 0)} (advisory, from this run).")
        for scan in data.get("scan_history", []):
            lines.append(
                f"- `{_fix_cell(scan.get('scan_id', ''))}`: {_fix_cell(scan.get('state', 'unknown'))}"
                + f"; {scan.get('lesson_count', 0)} lessons"
                + (f"; {_fix_cell(scan['reason'])}" if scan.get("reason") else "")
            )
    if data.get("deliveries"):
        lines += ["", "## Delivery", ""]
        for delivery in data["deliveries"]:
            label = (
                f"- `{_fix_cell(delivery.get('candidate_id', ''))}`: {_fix_cell(delivery.get('state', 'unknown'))}"
                + f"; branch `{_fix_cell(delivery.get('branch', ''))}`"
            )
            pr = delivery.get("pr", {})
            if pr.get("number"):
                title = ("Draft PR" if pr.get("is_draft") else "PR") + f" #{pr['number']}"
                label += f"; [{title}]({pr['url']})" if pr.get("url") else f"; {title}"
            lines.append(label)
    lines += ["", "## Run bounds", ""]
    lines += [f"- {_fix_cell(key)}: {value:g}" for key, value in data.get("limits", {}).items()]
    lines += [f"- Used {_fix_cell(key)}: {value:g}" for key, value in data.get("usage", {}).items()]
    lines += [
        "",
        "Results describe the recorded trials. Raw evaluation inputs and outputs are private.",
        "",
    ]
    return "\n".join(lines)
