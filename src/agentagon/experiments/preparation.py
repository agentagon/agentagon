"""Isolated evaluation preparation with bounded execution and independent review."""

import copy
import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from agentagon.core.records import AuditError, digest, identifier, load_json, now, validate_record
from agentagon.experiments import checkouts, evaluation, runners
from agentagon.experiments.evidence import DEFAULT_LIMITS
from agentagon.experiments.spec import NAME, finite, object_keys, path, text, validate_spec
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace

ASSESSMENTS = {"relevance", "sensitivity", "provenance", "leakage", "delivery_scope"}


def directory(workspace: Workspace, evaluation_id: str) -> Path:
    if not isinstance(evaluation_id, str) or not re.fullmatch(r"eval_[0-9a-f]{24}", evaluation_id):
        raise AuditError("invalid evaluation ID")
    return workspace.checked(workspace.state / "evaluations" / evaluation_id)


@contextmanager
def locked(workspace: Workspace, evaluation_id: str):
    root = directory(workspace, evaluation_id)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(root / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield root


def load(workspace: Workspace, evaluation_id: str) -> dict:
    file = directory(workspace, evaluation_id) / "state.json"
    if not file.exists():
        raise AuditError("evaluation not found in this checkout")
    data = load_json(file)
    if data.get("origin") != str(workspace.root) or data.get("evaluation_id") != evaluation_id:
        raise AuditError("evaluation origin mismatch")
    if digest(data["profile"]) != data["profile_digest"]:
        raise AuditError("preparation execution profile changed")
    selected = data.get("inventory", {}).get("selected_evidence")
    if selected and digest(
        {k: v for k, v in selected.items() if k != "selection_digest"}
    ) != selected.get("selection_digest"):
        raise AuditError("selected audit evidence changed")
    if data.get("package") and digest(data["package"]) != data["package_digest"]:
        raise AuditError("frozen evaluation package changed")
    return data


def _save(workspace: Workspace, data: dict) -> None:
    data["updated_at"] = now()
    workspace.write(directory(workspace, data["evaluation_id"]) / "state.json", data)


def _budget(value: dict) -> dict:
    validate_record("evaluation-budget", value)
    keys = {"max_trials", "max_elapsed_seconds", "trial_timeout_seconds"}
    value = object_keys(value, keys, keys, "preparation budget")
    if any(type(v) is not int or v < 1 for v in value.values()):
        raise AuditError("preparation budget values must be positive integers")
    return dict(value)


def _selected_evidence(workspace: Workspace, audit_id: str, issue_ids: list[str]) -> dict:
    """Retain one audit's selected findings without turning observations into labels."""
    from agentagon.operations import findings

    audit = workspace.read_audit(audit_id)
    groups = [group for group in audit["groups"] if group["issue_id"] in issue_ids]
    if not issue_ids or {group["issue_id"] for group in groups} != set(issue_ids):
        raise AuditError("--audit requires issues grouped in that saved audit")
    selected_ids = {key for group in groups for key in group["finding_ids"]}
    selected = [finding for finding in findings(audit) if finding["id"] in selected_ids]
    trace_ids = {key for finding in selected for key in finding["trace_ids"]}
    selection = {
        "audit_id": audit_id,
        "issue_ids": issue_ids,
        "revision": audit["snapshot"].get("revision"),
        "snapshot_digest": digest(audit["snapshot"]),
        "trace_alignment": audit.get("trace_alignment"),
        "trace_digests": {
            trace["id"]: trace["digest"] for trace in audit["traces"] if trace["id"] in trace_ids
        },
        "findings": [
            {
                "finding_id": finding["id"],
                "title": finding["title"],
                "observed_behavior": finding["observation"],
                "proposed_expected_behavior": finding["expected_behavior"],
                "expectation_status": "needs_review",
                "authority": finding["authority"],
                "basis": finding["basis"],
                "evidence": finding["evidence"],
                "source_references": finding["source_references"],
            }
            for finding in selected
        ],
        "case_requirements": [
            "Review expected behavior with the application owner; observed outputs are not labels.",
            "Include ordinary successful behavior and relevant boundary cases.",
            "Establish permitted inputs, tool responses and resettable state before replay.",
            "Check the relationship between captured evidence and the current application.",
        ],
    }
    return {**selection, "selection_digest": digest(selection)}


def start(
    workspace: Workspace,
    profile_name: str,
    budget: dict,
    *,
    goal: str | None = None,
    issue_ids: list[str] | None = None,
    author: str,
    from_id: str | None = None,
    audit_id: str | None = None,
) -> dict:
    workspace.require_initialized()
    revision = checkouts.clean_revision(workspace.root)
    budget = _budget(budget)
    profile = Config().profile(workspace.root, profile_name)
    profile.setdefault("evidence", dict(DEFAULT_LIMITS))
    text(author, "benchmark author")
    prior = load(workspace, from_id) if from_id else None
    if prior and prior["state"] != "frozen":
        raise AuditError("--from requires a frozen evaluation package")
    goal = goal or (prior["goal"] if prior else None)
    issue_ids = list(dict.fromkeys(issue_ids or (prior["issue_ids"] if prior else [])))
    if not goal and not issue_ids:
        raise AuditError("evaluation preparation needs a goal or saved issues")
    if goal:
        text(goal, "goal")
    from agentagon.storage.issues import list_issues

    issues = {issue["issue_id"]: issue for issue in list_issues(workspace)}
    if set(issue_ids) - set(issues):
        raise AuditError("evaluation references unknown saved issues")
    selected_evidence = _selected_evidence(workspace, audit_id, issue_ids) if audit_id else None
    if not audit_id and prior and issue_ids == prior["issue_ids"]:
        selected_evidence = copy.deepcopy(prior["inventory"].get("selected_evidence"))
    evaluation_id = identifier("eval", str(workspace.root), revision, uuid.uuid4().hex)
    with locked(workspace, evaluation_id) as root:
        prep = root / "preparation"
        checkouts.create(workspace.root, prep, revision)
        if prior:
            checkouts.copy_frozen(workspace.root, prep, prior["package"]["files"])
        names = [name for name in checkouts.paths(workspace.root, revision) if name]
        found = [
            name
            for name in names
            if any(word in name.lower() for word in ("test", "bench", "eval", "trace"))
        ]
        data = {
            "version": 1,
            "evaluation_id": evaluation_id,
            "origin": str(workspace.root),
            "origin_revision": revision,
            "created_at": now(),
            "state": "draft",
            "goal": goal,
            "issue_ids": issue_ids,
            "author": author,
            "from_id": from_id,
            "profile_name": profile_name,
            "profile": profile,
            "profile_digest": digest(profile),
            "budget": budget,
            "usage": {"trials": 0},
            "checks": [],
            "worktree": str(prep.relative_to(workspace.root)),
            "inventory": {
                "possible_evaluations": found[:500],
                "truncated": len(found) > 500,
                "issues": [issues[i] for i in issue_ids],
                **({"selected_evidence": selected_evidence} if selected_evidence else {}),
            },
            "previous_plan": prior["package"]["plan"] if prior else None,
        }
        _save(workspace, data)
    return status(workspace, evaluation_id)


def status(workspace: Workspace, evaluation_id: str) -> dict:
    data = load(workspace, evaluation_id)
    return {
        **data,
        "next_action": "start_fix"
        if data["state"] == "frozen"
        else ("independent_review" if data["state"] == "awaiting_review" else "prepare_and_check"),
    }


def _plan(value: dict, data: dict) -> dict:
    validate_record("evaluation-plan", value)
    value = copy.deepcopy(
        object_keys(
            value,
            {
                "spec",
                "provenance",
                "coverage",
                "negative_cases",
                "deliver_paths",
                "metric_cases",
                "metric_comparisons",
            },
            {"spec", "provenance", "coverage", "negative_cases"},
            "evaluation plan",
        )
    )
    value["spec"].setdefault("goal", data["goal"])
    value["spec"].setdefault("issue_ids", data["issue_ids"])
    value["spec"] = validate_spec(value["spec"])
    spec = value["spec"]
    if spec["goal"] != data["goal"] or set(spec["issue_ids"]) != set(data["issue_ids"]):
        raise AuditError("evaluation plan must retain the draft goal and saved issues")
    if "." in spec["evaluation_paths"]:
        raise AuditError("preparation must name evaluation paths explicitly")
    text(value["provenance"], "data and ground-truth provenance")
    coverage = object_keys(
        value["coverage"],
        {"status", "rationale", "holdout_paths"},
        {"status", "rationale", "holdout_paths"},
        "coverage",
    )
    if coverage["status"] not in {"holdout", "limited"}:
        raise AuditError("coverage status must be holdout or limited")
    text(coverage["rationale"], "coverage rationale")
    if not isinstance(coverage["holdout_paths"], list):
        raise AuditError("holdout paths must be an array")
    coverage["holdout_paths"] = [path(p) for p in coverage["holdout_paths"]]
    if (coverage["status"] == "holdout") != bool(coverage["holdout_paths"]):
        raise AuditError("holdout coverage requires separate frozen holdout paths")
    value.setdefault("deliver_paths", [])
    if not isinstance(value["deliver_paths"], list):
        raise AuditError("deliver_paths must be an array")
    value["deliver_paths"] = [path(p) for p in value["deliver_paths"]]
    if not isinstance(value["negative_cases"], list) or not value["negative_cases"]:
        raise AuditError("declare deliberately incorrect cases that targeted checks must reject")
    checks = {check["id"] for check in spec["checks"]}
    ids = set()
    for case in [*value["negative_cases"], *value.get("metric_cases", [])]:
        negative = "expected_checks" in case
        fields = {"id", "description", "mutations"}
        if negative:
            fields.add("expected_checks")
        object_keys(
            case,
            fields,
            fields,
            "negative case" if negative else "metric case",
        )
        if (
            not isinstance(case["id"], str)
            or not NAME.fullmatch(case["id"])
            or case["id"] in ids
            or case["id"] == "baseline"
        ):
            raise AuditError("evaluation cases need unique non-baseline IDs")
        ids.add(case["id"])
        text(case["description"], "case behavior")
        if negative and (
            not isinstance(case["expected_checks"], list)
            or not case["expected_checks"]
            or any(c not in checks for c in case["expected_checks"])
        ):
            raise AuditError("negative cases must name checks that reject incorrect behavior")
        if not isinstance(case["mutations"], list) or not case["mutations"]:
            raise AuditError("evaluation cases need explicit application mutations")
        for mutation in case["mutations"]:
            object_keys(mutation, {"source", "path"}, {"source", "path"}, "case mutation")
            mutation["source"], mutation["path"] = path(mutation["source"]), path(mutation["path"])
            if not any(
                checkouts.under(mutation["path"], scope) for scope in spec["editable_paths"]
            ):
                raise AuditError("case mutations must stay within application edit scope")
            protected = spec["evaluation_paths"] + [
                e["path"] for key in ("inputs", "overlays") for e in spec[key]
            ]
            if any(checkouts.under(mutation["path"], scope) for scope in protected):
                raise AuditError("evaluation cases cannot weaken or replace evaluation files")
    if bool(value.get("metric_cases")) != bool(value.get("metric_comparisons")):
        raise AuditError("metric cases and comparisons must be declared together")
    metric_ids = {case["id"] for case in value.get("metric_cases", [])}
    for comparison in value.get("metric_comparisons", []):
        if comparison["metric"] not in spec["metrics"]:
            raise AuditError("metric comparison must name a declared objective")
        pair = {comparison["better"], comparison["worse"]}
        if len(pair) != 2 or pair - (metric_ids | {"baseline"}):
            raise AuditError("metric comparison needs distinct metric cases or baseline")
        if "baseline" in pair and any(c["baseline_expected"] != "pass" for c in spec["checks"]):
            raise AuditError(
                "metric comparisons require correct cases; this baseline expects failures"
            )
        comparison["min_delta"] = finite(comparison.get("min_delta", 0))
        if comparison["min_delta"] < 0:
            raise AuditError("metric comparison min_delta must be nonnegative")
    return value


def _snapshot(workspace: Workspace, data: dict, plan: dict) -> tuple[str, list[dict]]:
    spec = plan["spec"]
    prep = workspace.checked(workspace.root / data["worktree"])
    # Imported data is read only from explicitly supplied checkout-local files.
    from agentagon.experiments.engine import _freeze

    imported = _freeze(workspace, spec)
    checkouts.copy_frozen(workspace.root, prep, imported)
    revision = checkouts.snapshot(prep, data["origin_revision"], "Prepare evaluation benchmark")
    scopes = spec["evaluation_paths"] + [e["path"] for e in imported]
    changed = checkouts.changes(workspace.root, data["origin_revision"], revision)
    if any(not (prep / name).is_file() for name in changed):
        raise AuditError(
            "evaluation preparation cannot delete files; frozen packages overlay files"
        )
    if any(not any(checkouts.under(name, scope) for scope in scopes) for name in changed):
        raise AuditError(
            "evaluation preparation changed application source outside evaluation paths"
        )
    files = []
    imported_by_path = {entry["path"]: entry for entry in imported}
    for name in checkouts.paths(prep, revision):
        if not name or not any(checkouts.under(name, scope) for scope in scopes):
            continue
        file = checkouts.checked_file(prep, name)
        content = file.read_bytes()
        files.append(
            {
                "path": name,
                "artifact": workspace.blob(content, ".input"),
                "digest": hashlib.sha256(content).hexdigest(),
                "mode": file.stat().st_mode & 0o777,
                "kind": imported_by_path.get(name, {}).get("kind", "overlays"),
                "deliver": any(checkouts.under(name, scope) for scope in plan["deliver_paths"]),
            }
        )
    names = {entry["path"] for entry in files}
    if any(
        not any(checkouts.under(name, scope) for name in names)
        for scope in spec["evaluation_paths"]
    ):
        raise AuditError(
            "every evaluation path must contain a frozen file; deletions are unsupported"
        )
    if any(scope not in names for scope in plan["coverage"]["holdout_paths"]):
        raise AuditError("holdout inputs must be separate frozen files")
    if any(
        not any(checkouts.under(name, scope) for name in names) for scope in plan["deliver_paths"]
    ):
        raise AuditError("deliver_paths must name frozen regression tests")
    if any(entry["deliver"] and entry["kind"] == "inputs" for entry in files):
        raise AuditError("private evaluation inputs cannot be deliverable")
    # A benchmark-only branch excludes private inputs; instrumentation is reviewable here.
    return revision, files


def _outcome(spec: dict, trial: dict, result: dict, case: dict | None) -> dict:
    if result.get("state") != "completed" or not result.get("finalized"):
        raise AuditError("preparation trial did not complete")
    for key in (
        "attempt_id",
        "source_digest",
        "evaluation_digest",
        "inputs_digest",
        "profile_digest",
    ):
        if result.get(key) != trial["request"][key]:
            raise AuditError("preparation trial identity mismatch")
    if result.get("source_manifest_before") != trial["manifest"] or any(
        result.get("source_manifest_after", {}).get(k) != v for k, v in trial["manifest"].items()
    ):
        raise AuditError("preparation trial changed or ran different frozen source")
    commands = result.get("results", [])
    if len(commands) != len(trial["request"]["commands"]):
        raise AuditError("preparation did not execute every required command")
    definitions = {c["id"]: c for c in spec["checks"]}
    for expected, actual in zip(trial["request"]["commands"], commands, strict=True):
        if (
            actual.get("id") != expected["id"]
            or actual.get("role") != expected["role"]
            or type(actual.get("exit_code")) is not int
            or actual.get("timed_out")
        ):
            raise AuditError("invalid preparation command outcome")
        code = actual["exit_code"]
        if expected["role"] != "check":
            if code != 0:
                raise AuditError("setup or benchmark failed; this is not check sensitivity")
        else:
            if case and "expected_checks" in case:
                wanted = 1 if expected["id"] in case["expected_checks"] else None
            elif case:
                wanted = 0
            else:
                wanted = 1 if definitions[expected["id"]]["baseline_expected"] == "fail" else 0
            if code not in (0, 1) or wanted is not None and code != wanted:
                raise AuditError(
                    "check did not match its expected baseline or incorrect-case outcome"
                )
    try:
        output = json.loads(result.get("benchmark_output") or "")
        metrics, _ = evaluation.aggregate({**spec, "repetitions": 1}, [output["metrics"]])
        evaluation.task_metrics(spec, output)
    except (ValueError, KeyError, TypeError) as exc:
        raise AuditError("preparation benchmark must emit every declared numeric metric") from exc
    return {
        "metrics": metrics,
        "checks": [
            {"id": c["id"], "exit_code": c["exit_code"]} for c in commands if c["role"] == "check"
        ],
    }


def _metric_comparisons(plan: dict, trials: list[dict]) -> list[dict]:
    """Compare complete, correct cases using the same medians as fix verification."""
    spec = plan["spec"]
    metrics = {}
    for case_id in {
        c[key] for c in plan.get("metric_comparisons", []) for key in ("better", "worse")
    }:
        samples = [t for t in trials if t["case_id"] == case_id]
        if (
            len(samples) != spec["repetitions"]
            or {t["repetition"] for t in samples} != set(range(spec["repetitions"]))
            or any(t["state"] != "passed" for t in samples)
            or any(check["exit_code"] != 0 for t in samples for check in t["outcome"]["checks"])
        ):
            raise AuditError(
                "metric comparisons require every repetition to pass correctness checks"
            )
        metrics[case_id], _ = evaluation.aggregate(spec, [t["outcome"]["metrics"] for t in samples])
    results = []
    for comparison in plan.get("metric_comparisons", []):
        name = comparison["metric"]
        better, worse = (metrics[comparison[key]][name] for key in ("better", "worse"))
        # Compare the retained decimal values without rounding an exact boundary down
        # (for example, a 0.9 vs 0.8 score with min_delta 0.1).
        delta = Decimal(str(better)) - Decimal(str(worse))
        if spec["metrics"][name]["direction"] == "min":
            delta = -delta
        improvement = finite(float(delta))
        results.append(
            {
                **comparison,
                "better_value": better,
                "worse_value": worse,
                "improvement": improvement,
                "passed": delta > 0 and delta >= Decimal(str(comparison["min_delta"])),
            }
        )
    return results


def check(workspace: Workspace, evaluation_id: str, plan: dict) -> dict:
    # Hold one draft lock during its bounded validation; concurrent retries cannot double-spend.
    with locked(workspace, evaluation_id) as root:
        data = load(workspace, evaluation_id)
        if data["state"] == "frozen":
            raise AuditError("frozen evaluators cannot change; use eval start --from")
        if checkouts.clean_revision(workspace.root) != data["origin_revision"]:
            raise AuditError("application source changed; create a newly validated evaluation")
        plan = _plan(plan, data)
        revision, files = _snapshot(workspace, data, plan)
        mutations = {}
        for case in [*plan["negative_cases"], *plan.get("metric_cases", [])]:
            entries = []
            for mutation in case["mutations"]:
                file = checkouts.checked_file(workspace.root, mutation["source"])
                content = file.read_bytes()
                entries.append(
                    {
                        "path": mutation["path"],
                        "artifact": workspace.blob(content, ".input"),
                        "digest": hashlib.sha256(content).hexdigest(),
                        "mode": file.stat().st_mode & 0o777,
                    }
                )
            mutations[case["id"]] = entries
        binding = {
            "plan": plan,
            "source_revision": revision,
            "files": files,
            "mutations": mutations,
        }
        if selected := data["inventory"].get("selected_evidence"):
            binding["selected_evidence"] = selected
        validation_id = identifier(
            "validation",
            digest({**binding, "source_revision": checkouts.tree(workspace.root, revision)}),
        )
        record = next((c for c in data["checks"] if c["validation_id"] == validation_id), None)
        if record is None:
            record = {"validation_id": validation_id, **binding, "trials": [], "state": "checking"}
            data["checks"].append(record)
        revision = record["source_revision"]
        data["current_validation"] = validation_id
        data["state"] = "checking"
        _save(workspace, data)
        spec = plan["spec"]
        cases = [None, *plan["negative_cases"], *plan.get("metric_cases", [])]
        for case in cases:
            case_id = case["id"] if case else "baseline"
            for repetition, seed in enumerate(spec["seeds"]):
                attempt_id = identifier("trial", evaluation_id, validation_id, case_id, repetition)
                trial = next((t for t in record["trials"] if t["trial_id"] == attempt_id), None)
                if trial and trial.get("state") in {"passed", "failed"}:
                    continue
                if not trial:
                    remaining = (
                        data["budget"]["max_elapsed_seconds"]
                        - (
                            datetime.fromisoformat(now())
                            - datetime.fromisoformat(data["created_at"])
                        ).total_seconds()
                    )
                    if data["usage"]["trials"] >= data["budget"]["max_trials"] or remaining <= 0:
                        data["state"] = "budget_exhausted"
                        _save(workspace, data)
                        raise AuditError(
                            "preparation budget exhausted; start a draft with an explicit new budget"
                        )
                    source = root / "executions" / attempt_id
                    checkouts.create(workspace.root, source, revision)
                    checkouts.copy_frozen(workspace.root, source, mutations.get(case_id, []))
                    manifest = checkouts.source_manifest(source)
                    request = {
                        "attempt_id": attempt_id,
                        "evaluation_id": evaluation_id,
                        "source_digest": digest(manifest),
                        "evaluation_digest": digest(plan),
                        "inputs_digest": digest(files),
                        "profile_digest": data["profile_digest"],
                        "seed": seed,
                        "timeout_seconds": min(remaining, data["budget"]["trial_timeout_seconds"]),
                        "deadline_at": (
                            datetime.fromisoformat(data["created_at"])
                            + timedelta(seconds=data["budget"]["max_elapsed_seconds"])
                        ).isoformat(),
                        "commands": evaluation.commands(data["profile"], spec, gate_checks=False),
                        "evidence_limits": data["profile"]["evidence"],
                    }
                    trial = {
                        "trial_id": attempt_id,
                        "case_id": case_id,
                        "repetition": repetition,
                        "state": "running",
                        "manifest": manifest,
                        "request": request,
                        "source_path": str(source.relative_to(workspace.root)),
                    }
                    record["trials"].append(trial)
                    data["usage"]["trials"] += 1
                    _save(workspace, data)
                source = workspace.checked(workspace.root / trial["source_path"])
                result = runners.execute(
                    data["profile"], source, root / "attempts" / attempt_id, trial["request"]
                )
                trial["artifact"] = workspace.artifact(result)
                if not result.get("finalized"):
                    trial["state"] = data["state"] = "interrupted"
                    _save(workspace, data)
                    return status(workspace, evaluation_id)
                try:
                    trial["outcome"] = _outcome(spec, trial, result, case)
                    trial["state"] = "passed"
                except AuditError as exc:
                    trial["state"], trial["error"] = "failed", str(exc)
                _save(workspace, data)
                checkouts.remove(workspace.root, source)
        record["state"] = (
            "passed" if all(t["state"] == "passed" for t in record["trials"]) else "failed"
        )
        if plan.get("metric_comparisons"):
            try:
                record["metric_comparisons"] = _metric_comparisons(plan, record["trials"])
                if not all(c["passed"] for c in record["metric_comparisons"]):
                    record["state"] = "failed"
            except AuditError as exc:
                record["state"] = "failed"
                record["metric_comparison_error"] = str(exc)
        data["state"] = "awaiting_review" if record["state"] == "passed" else "check_failed"
        record["review_template"] = {
            "evaluation_id": evaluation_id,
            "validation_id": validation_id,
            "validation_digest": digest(
                {k: v for k, v in record.items() if k != "review_template"}
            ),
            "reviewer": "",
            "verdict": "pending",
            "rationale": "",
            "assessments": dict.fromkeys(sorted(ASSESSMENTS), False),
            "evidence": [t["artifact"] for t in record["trials"]],
        }
        _save(workspace, data)
    return status(workspace, evaluation_id)


def freeze(workspace: Workspace, evaluation_id: str, review: dict) -> dict:
    validate_record("evaluation-review", review)
    with locked(workspace, evaluation_id) as root:
        data = load(workspace, evaluation_id)
        if data["state"] == "frozen":
            if data["review"] != review:
                raise AuditError("frozen evaluation review cannot be replaced")
            return status(workspace, evaluation_id)
        if data["state"] != "awaiting_review":
            raise AuditError(
                "passing baseline and incorrect-case trials are required before freeze"
            )
        if checkouts.clean_revision(workspace.root) != data["origin_revision"]:
            raise AuditError("application source changed; revalidate in a new draft")
        record = next(c for c in data["checks"] if c["validation_id"] == data["current_validation"])
        if record.get("selected_evidence") != data["inventory"].get("selected_evidence"):
            raise AuditError(
                "selected audit evidence changed after validation; run eval check again"
            )
        template = record["review_template"]
        object_keys(review, set(template), set(template), "benchmark review")
        if any(
            review[k] != template[k]
            for k in ("evaluation_id", "validation_id", "validation_digest", "evidence")
        ):
            raise AuditError("benchmark review is stale or references different trial evidence")
        if (
            digest({k: v for k, v in record.items() if k != "review_template"})
            != template["validation_digest"]
        ):
            raise AuditError("preparation validation changed since review")
        text(review["reviewer"], "independent benchmark reviewer")
        text(review["rationale"], "review rationale")
        if (
            review["reviewer"] == data["author"]
            or review["verdict"] != "pass"
            or review["assessments"] != dict.fromkeys(ASSESSMENTS, True)
        ):
            raise AuditError("independent passing review of every benchmark assessment is required")
        revision, files = _snapshot(workspace, data, record["plan"])
        if files != record["files"] or checkouts.tree(workspace.root, revision) != checkouts.tree(
            workspace.root, record["source_revision"]
        ):
            raise AuditError("benchmark changed after validation; run eval check again")
        # Re-read the content-addressed observations instead of trusting supplied review prose.
        observed_trials = []
        cases = [*record["plan"]["negative_cases"], *record["plan"].get("metric_cases", [])]
        for trial in record["trials"]:
            result = workspace.read_artifact(trial["artifact"])
            case = next((c for c in cases if c["id"] == trial["case_id"]), None)
            observed_trials.append(
                {**trial, "outcome": _outcome(record["plan"]["spec"], trial, result, case)}
            )
        comparisons = _metric_comparisons(record["plan"], observed_trials)
        if comparisons != record.get("metric_comparisons", []) or not all(
            c["passed"] for c in comparisons
        ):
            raise AuditError(
                "metric comparison evidence changed or did not distinguish the declared cases"
            )
        spec = copy.deepcopy(record["plan"]["spec"])
        spec["overlays"], spec["inputs"] = [], []
        for entry in files:
            exported = {"source": entry["artifact"], "path": entry["path"]}
            if entry["kind"] == "overlays":
                exported["deliver"] = entry["deliver"]
            spec[entry["kind"]].append(exported)
        review_tree = root / "review"
        checkouts.create(workspace.root, review_tree, data["origin_revision"])
        checkouts.copy_frozen(
            workspace.root, review_tree, [e for e in files if e["kind"] != "inputs"]
        )
        review_revision = checkouts.snapshot(
            review_tree, data["origin_revision"], "Add reviewed evaluation benchmark"
        )
        branch = f"codex/eval/{evaluation_id}"
        existing = checkouts.git(
            workspace.root, "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}"
        )
        if existing and checkouts.tree(workspace.root, existing) != checkouts.tree(
            workspace.root, review_revision
        ):
            raise AuditError("evaluation review branch already contains different work")
        if not existing:
            checkouts.git(
                workspace.root,
                "update-ref",
                f"refs/heads/{branch}",
                review_revision,
                "0" * len(review_revision),
            )
        data["package"] = {
            "plan": record["plan"],
            "spec": spec,
            "files": files,
            "source_revision": data["origin_revision"],
            "validation_id": record["validation_id"],
            "review_branch": branch,
            **(
                {"selected_evidence": record["selected_evidence"]}
                if "selected_evidence" in record
                else {}
            ),
        }
        data["package_digest"] = digest(data["package"])
        data["review"], data["state"] = copy.deepcopy(review), "frozen"
        workspace.write(root / "fix-spec.json", spec)
        _save(workspace, data)
    return status(workspace, evaluation_id)


def fix_spec(workspace: Workspace, evaluation_id: str) -> dict:
    data = load(workspace, evaluation_id)
    if data["state"] != "frozen":
        raise AuditError("fix requires a reviewed frozen evaluation")
    if checkouts.clean_revision(workspace.root) != data["package"]["source_revision"]:
        raise AuditError(
            "evaluation source provenance changed; use eval start --from and revalidate"
        )
    for entry in data["package"]["files"]:
        content = checkouts.checked_file(workspace.root, entry["artifact"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["digest"]:
            raise AuditError("frozen evaluation input changed")
    return copy.deepcopy(data["package"]["spec"])
