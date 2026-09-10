"""Validate host judgments against the exact evidence packet they reviewed."""

from agentagon.core.records import AuditError, catalog, identifier

CONCERNS = {
    "llm.hallucination": {"present"},
    "llm.goal_alignment": {"misaligned"},
    "llm.user_revision": {"present"},
    "llm.reasoning_validity": {"invalid"},
    "llm.verification": {"unverified"},
    "llm.tone": {"inappropriate"},
    "llm.tool_selection": {"inappropriate"},
    "llm.task_resolution": {"unresolved"},
    "trace.fulfillment": {"partial", "unfulfilled"},
    "trace.user_experience": {"mixed", "negative"},
}


def flags_for(unit: dict, review: dict) -> list[dict]:
    flags = list(unit.get("measurements", {}).get("flags", []))
    for judgment in review["judgments"]:
        if judgment["status"] == "computed" and judgment["value"] in CONCERNS.get(
            judgment["facet"], ()
        ):
            flags.append(
                {
                    "id": identifier(
                        "flag", unit["id"], unit["digest"], judgment["subject"], judgment["facet"]
                    ),
                    "kind": judgment["facet"],
                    "evidence": judgment["evidence"],
                    "status": "candidate",
                }
            )
    return flags


def check_judgments(item: dict, unit: dict, evidence: dict) -> bool:
    definitions = {
        definition["id"]: definition
        for section in ("semantic", "code")
        for definition in catalog()[section]
    }
    expected = {(v["subject"], v["facet"]) for v in unit["required_judgments"]}
    received = [(v["subject"], v["facet"]) for v in item["judgments"]]
    if len(set(received)) != len(received) or set(received) != expected:
        raise AuditError("judgments must cover every required subject/facet exactly once")
    for value in item["judgments"]:
        _references(value["evidence"], evidence)
        if any(evidence[ref]["unit_id"] != unit["id"] for ref in value["evidence"]):
            raise AuditError("judgment cites evidence outside its review unit")
        if value["status"] != "computed":
            if value["value"] is not None:
                raise AuditError("uncomputed judgments must use a null value")
            continue
        if not value["evidence"] or not value["value"]:
            raise AuditError("computed judgments need a value and supporting evidence")
        allowed = definitions[value["facet"]]["values"]
        if allowed is not None and value["value"] not in allowed:
            raise AuditError("judgment value does not match the fixed rubric")
        if value["facet"] == "trace.user_experience":
            if not any(evidence[ref].get("feedback_eligible") for ref in value["evidence"]):
                raise AuditError(
                    "user experience requires a user message responding to an earlier assistant message; otherwise use unknown"
                )
    return all(value["status"] not in {"not_evaluated", "error"} for value in item["judgments"])


def check_diagnosis(item: dict, unit: dict, flags: list[dict], evidence: dict) -> None:
    if item["status"] == "not_evaluated":
        if item["findings"] or item["flag_dispositions"]:
            raise AuditError("unevaluated diagnosis cannot contain conclusions")
        return
    keys = [finding["key"] for finding in item["findings"]]
    if len(set(keys)) != len(keys):
        raise AuditError("finding keys must be unique within a unit")
    for finding in item["findings"]:
        _references(finding["evidence"], evidence)
        refs = [evidence[ref] for ref in finding["evidence"]]
        if not any(ref["unit_id"] == unit["id"] for ref in refs):
            raise AuditError("finding does not cite its review unit")
        code = any(ref["kind"] == "code" for ref in refs)
        runtime = any(ref["kind"] != "code" for ref in refs)
        if finding["basis"] == "implementation_only" and (not code or runtime):
            raise AuditError("implementation-only findings require code evidence only")
        if finding["basis"] == "runtime_only" and (not runtime or code):
            raise AuditError("runtime-only findings require runtime evidence only")
        if finding["basis"] == "correlated" and not (
            code and runtime and finding["correlation_rationale"]
        ):
            raise AuditError(
                "correlation requires code, runtime evidence, and a connecting rationale"
            )
    expected = {flag["id"] for flag in flags}
    dispositions = item["flag_dispositions"]
    if {v["flag_id"] for v in dispositions} != expected or len(dispositions) != len(expected):
        raise AuditError("account for every diagnostic flag once, including dismissed flags")
    for disposition in dispositions:
        if any(key not in keys for key in disposition["finding_keys"]):
            raise AuditError("flag points to a missing finding")
        if (disposition["status"] == "finding") != bool(disposition["finding_keys"]):
            raise AuditError("only a finding disposition may reference finding keys")


def _references(references: list[str], evidence: dict) -> None:
    if any(reference not in evidence for reference in references):
        raise AuditError("unknown evidence reference")
