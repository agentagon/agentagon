"""Frozen evaluation and execution contracts, with explicit spending limits."""

import copy
import math
import re
from pathlib import PurePosixPath

from agentagon.core.records import AuditError, validate_record

NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$")
ENV = re.compile(r"^[A-Z_][A-Z0-9_]*$")
LIMITS = (
    "max_candidates",
    "max_trials",
    "max_elapsed_seconds",
    "parallel_candidates",
    "parallel_trials",
    "trial_timeout_seconds",
)


def object_keys(value: object, allowed: set[str], required: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise AuditError(f"invalid {label}: missing or unsupported fields")
    return value


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise AuditError(f"{label} must be nonempty text")
    return value


def path(value: object, label: str = "path") -> str:
    value = text(value, label)
    p = PurePosixPath(value)
    if (
        p.is_absolute()
        or ".." in p.parts
        or "\\" in value
        or any(part == ".git" for part in p.parts)
    ):
        raise AuditError(f"{label} must be a checkout-relative path without traversal")
    return str(p)


def finite(value: object) -> float:
    if type(value) not in (int, float):
        raise AuditError("metric values and bounds must be finite numbers")
    try:
        number = float(value)
    except OverflowError as exc:
        raise AuditError("metric values and bounds must be finite numbers") from exc
    if not math.isfinite(number):
        raise AuditError("metric values and bounds must be finite numbers")
    return number


def argv(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise AuditError("commands require a nonempty argv array")
    return [text(part, "command argument") for part in value]


def validate_limits(value: dict) -> dict:
    validate_record("fix-profile", value, definition="limits")
    value = copy.deepcopy(
        object_keys(value, set(LIMITS) | {"stagnation_rounds"}, set(LIMITS), "limits")
    )
    value.setdefault("stagnation_rounds", 3)
    for key, number in value.items():
        if type(number) is not int or number < 1:
            raise AuditError(f"limit {key} must be a positive integer")
    if value["parallel_candidates"] > value["max_candidates"]:
        raise AuditError("parallel candidate limit exceeds total candidate limit")
    if value["parallel_trials"] > value["max_trials"]:
        raise AuditError("parallel trial limit exceeds total trial limit")
    return value


def validate_profile(value: dict) -> dict:
    validate_record("fix-profile", value)
    value = copy.deepcopy(
        object_keys(
            value,
            {
                "runner",
                "limits",
                "env",
                "setup",
                "repetitions",
                "search",
                "scans",
                "evidence",
                "orchestration",
            },
            {"runner", "limits"},
            "profile",
        )
    )
    runner = object_keys(
        value["runner"],
        {
            "kind",
            "host",
            "remote_root",
            "python",
            "template",
            "api_key_env",
            "sandbox_timeout_seconds",
            "independent_capacity",
        },
        {"kind"},
        "runner",
    )
    kind = runner["kind"]
    if kind not in ("local", "ssh", "e2b"):
        raise AuditError("runner kind must be local, ssh or e2b")
    if kind == "ssh":
        host = text(runner.get("host"), "SSH host alias")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]*", host):
            raise AuditError("SSH host must be a configured alias, not shell syntax")
        if not text(runner.get("remote_root"), "remote_root").startswith("/"):
            raise AuditError("SSH remote_root must be absolute")
    if kind == "e2b":
        text(runner.get("template"), "E2B template")
        runner.setdefault("api_key_env", "E2B_API_KEY")
        if not isinstance(runner["api_key_env"], str) or not ENV.fullmatch(runner["api_key_env"]):
            raise AuditError("E2B credentials must use an environment-variable reference")
    if "python" in runner:
        text(runner["python"], "runner python")
    if "independent_capacity" in runner and type(runner["independent_capacity"]) is not bool:
        raise AuditError("independent_capacity must be a boolean")
    value["limits"] = validate_limits(value["limits"])
    if (
        kind in ("local", "ssh")
        and value["limits"]["parallel_trials"] > 1
        and not runner.get("independent_capacity")
    ):
        raise AuditError("parallel shared-host measurements require independent_capacity")
    if "sandbox_timeout_seconds" in runner and (
        type(runner["sandbox_timeout_seconds"]) is not int
        or runner["sandbox_timeout_seconds"] < value["limits"]["trial_timeout_seconds"] + 30
    ):
        raise AuditError(
            "sandbox lifetime must allow at least 30 seconds after the trial deadline for collection"
        )
    value.setdefault("env", {})
    if not isinstance(value["env"], dict):
        raise AuditError("profile env must map environment names to credential references")
    for target, source in value["env"].items():
        if (
            not isinstance(target, str)
            or not isinstance(source, str)
            or not ENV.fullmatch(target)
            or not ENV.fullmatch(source)
        ):
            raise AuditError("profile env contains an invalid environment-variable reference")
        if target.startswith("AGENTAGON_") or target == "E2B_API_KEY":
            raise AuditError("profile env cannot override runner-owned variables")
    value.setdefault("setup", [])
    if not isinstance(value["setup"], list):
        raise AuditError("setup must be an array of argv arrays")
    value["setup"] = [argv(command) for command in value["setup"]]
    if "search" in value:
        from agentagon.experiments.search import validate_policy

        value["search"] = validate_policy(value["search"])
    if "scans" in value:
        from agentagon.experiments.learning import validate_limits as validate_scan_limits

        value["scans"] = validate_scan_limits(value["scans"])
    if "evidence" in value:
        from agentagon.experiments.evidence import validate_limits as validate_evidence_limits

        value["evidence"] = validate_evidence_limits(value["evidence"])
    if "orchestration" in value:
        from agentagon.experiments.orchestration import validate_settings

        value["orchestration"] = validate_settings(value["orchestration"])
    return value


def command(value: dict) -> dict:
    value = copy.deepcopy(object_keys(value, {"argv", "cwd"}, {"argv"}, "command"))
    value["argv"] = argv(value["argv"])
    value["cwd"] = path(value.get("cwd", "."), "command cwd")
    return value


def validate_spec(value: dict) -> dict:
    validate_record("fix-spec", value)
    value = copy.deepcopy(
        object_keys(
            value,
            {
                "goal",
                "issue_ids",
                "editable_paths",
                "evaluation_paths",
                "benchmark",
                "metrics",
                "task_metrics",
                "constraints",
                "checks",
                "overlays",
                "inputs",
                "repetitions",
                "seeds",
                "resources",
            },
            {"editable_paths", "evaluation_paths", "benchmark", "metrics"},
            "evaluation specification",
        )
    )
    value.setdefault("goal", None)
    if "resources" in value:
        resources = object_keys(value["resources"], {"slots"}, {"slots"}, "benchmark resources")
        if type(resources["slots"]) is not int or not 1 <= resources["slots"] <= 1024:
            raise AuditError("benchmark resource slots must be between 1 and 1024")
    if value["goal"] is not None:
        text(value["goal"], "goal")
    value.setdefault("issue_ids", [])
    if not isinstance(value["issue_ids"], list) or any(
        not isinstance(v, str) for v in value["issue_ids"]
    ):
        raise AuditError("issue_ids must be an array of issue IDs")
    value["issue_ids"] = list(dict.fromkeys(value["issue_ids"]))
    if not value["goal"] and not value["issue_ids"]:
        raise AuditError("supply a goal, saved issue IDs, or both")
    for key in ("editable_paths", "evaluation_paths"):
        if not isinstance(value[key], list) or not value[key]:
            raise AuditError(f"{key} must explicitly name at least one path")
        value[key] = [path(p, key) for p in value[key]]
    value["benchmark"] = command(value["benchmark"])
    if not isinstance(value["metrics"], dict) or not value["metrics"]:
        raise AuditError("declare at least one objective metric")
    value.setdefault("task_metrics", {})
    for kind in ("metrics", "task_metrics"):
        if not isinstance(value[kind], dict):
            raise AuditError("task_metrics must map frozen task IDs to direction and unit")
        for name, metric in value[kind].items():
            if not isinstance(name, str) or not NAME.fullmatch(name):
                raise AuditError("invalid metric or task name")
            object_keys(metric, {"direction", "unit"}, {"direction", "unit"}, "metric")
            if metric["direction"] not in ("min", "max"):
                raise AuditError("metric direction must be min or max")
            text(metric["unit"], "metric unit")
    value.setdefault("constraints", [])
    if not isinstance(value["constraints"], list):
        raise AuditError("constraints must be an array")
    for constraint in value["constraints"]:
        object_keys(
            constraint,
            {"metric", "op", "bound", "reference"},
            {"metric", "op", "bound"},
            "constraint",
        )
        constraint.setdefault("reference", "absolute")
        if (
            constraint["metric"] not in value["metrics"]
            or constraint["op"] not in ("gte", "lte")
            or constraint["reference"] not in ("absolute", "baseline_delta", "baseline_ratio")
        ):
            raise AuditError("invalid constraint metric, operation or reference")
        constraint["bound"] = finite(constraint["bound"])
    value.setdefault("checks", [])
    check_ids = set()
    if not isinstance(value["checks"], list):
        raise AuditError("checks must be an array")
    for check in value["checks"]:
        object_keys(
            check,
            {"id", "argv", "cwd", "issue_ids", "baseline_expected", "preflight"},
            {"id", "argv"},
            "check",
        )
        if "preflight" in check and type(check["preflight"]) is not bool:
            raise AuditError("check preflight must be a boolean")
        if (
            not isinstance(check["id"], str)
            or not NAME.fullmatch(check["id"])
            or check["id"] in check_ids
            or check["id"] == "benchmark"
            or check["id"].startswith("setup-")
        ):
            raise AuditError("check IDs must be unique and non-reserved")
        check_ids.add(check["id"])
        check["argv"] = argv(check["argv"])
        check["cwd"] = path(check.get("cwd", "."))
        check.setdefault("issue_ids", [])
        check.setdefault("baseline_expected", "pass")
        if (
            check["baseline_expected"] not in ("pass", "fail")
            or not isinstance(check["issue_ids"], list)
            or any(i not in value["issue_ids"] for i in check["issue_ids"])
        ):
            raise AuditError(
                "check must reference this run's issues and an explicit baseline expectation"
            )
    for issue_id in value["issue_ids"]:
        if not any(issue_id in check["issue_ids"] for check in value["checks"]):
            raise AuditError("each targeted issue needs an acceptance check")
    for key in ("overlays", "inputs"):
        value.setdefault(key, [])
        if not isinstance(value[key], list):
            raise AuditError(f"{key} must be an array")
        for entry in value[key]:
            allowed = {"source", "path", "deliver"} if key == "overlays" else {"source", "path"}
            object_keys(entry, allowed, {"source", "path"}, key)
            entry["source"] = path(entry["source"])
            entry["path"] = path(entry["path"])
            if key == "overlays":
                entry.setdefault("deliver", True)
                if type(entry["deliver"]) is not bool:
                    raise AuditError("overlay deliver must be a boolean")
    destinations = [entry["path"] for key in ("overlays", "inputs") for entry in value[key]]
    if len(destinations) != len(set(destinations)):
        raise AuditError("overlay and input destinations must be distinct")
    value.setdefault("repetitions", 3)
    if type(value["repetitions"]) is not int or not 1 <= value["repetitions"] <= 1000:
        raise AuditError("repetitions must be between 1 and 1000")
    value.setdefault("seeds", list(range(value["repetitions"])))
    if (
        not isinstance(value["seeds"], list)
        or len(value["seeds"]) != value["repetitions"]
        or any(type(v) is not int for v in value["seeds"])
    ):
        raise AuditError("one integer seed is required per repetition")
    return value
