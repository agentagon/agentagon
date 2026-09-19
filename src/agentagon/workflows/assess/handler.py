"""Assessment response validation; discovery retains the authoritative diagnoses."""

from agentagon.core.records import AuditError
from agentagon.workflows.discover.handler import accept as discover


def issue_checks(workspace, job, result):
    from agentagon.domain.issues import get_issue

    checks = result.get("issue_checks", [])
    samples = {s["trace_id"]: s for s in job["preparation"].get("samples", [])}
    accepted = {
        m["issue_id"]
        for m in job["options"].get("monitor_policy", {}).get("measurements", [])
        if m["metric"] == "issue_recurrence" and m["accepted"]
    }
    if not isinstance(checks, list) or len(checks) > 2000:
        raise AuditError("issue checks must be a bounded list")
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != {
            "issue_id",
            "trace_id",
            "present",
            "reason",
        }:
            raise AuditError("issue check requires issue_id, trace_id, present and reason")
        if not isinstance(check["issue_id"], str) or not isinstance(check["trace_id"], str):
            raise AuditError("invalid issue check identity")
        key = (check["issue_id"], check["trace_id"])
        if check["issue_id"] not in accepted or check["trace_id"] not in samples or key in seen:
            raise AuditError("issue check is outside the accepted measurement or selected traces")
        issue = get_issue(workspace, check["issue_id"])
        if issue.get("agent_id") != job["application_agent_id"]:
            raise AuditError("issue check belongs to another agent")
        if (
            type(check["present"]) is not bool
            or not isinstance(check["reason"], str)
            or not 1 <= len(check["reason"]) <= 2000
        ):
            raise AuditError("issue check requires explicit evidence reasoning")
        seen.add(key)
    return [
        {**check, "evidence_digest": samples[check["trace_id"]]["evidence_digest"]}
        for check in checks
    ]


def accept(workspace, job, result):
    if result.get("needs_input"):
        return "needs_input", result, str(result["needs_input"])
    prepared = job["preparation"]
    checks = issue_checks(workspace, job, result)
    if prepared.get("snapshot_id") and prepared.get("brain_requested"):
        state, saved, action = discover(workspace, job, result)
    else:
        state, saved, action = (
            "completed",
            {"summary": result.get("summary", "Assessment complete"), "issue_ids": []},
            None,
        )
    candidates = result.get("candidates", [])
    supplied = {c["id"]: c for c in prepared.get("candidates", [])}
    if not isinstance(candidates, list) or (
        prepared.get("brain_requested") and len(candidates) != len(supplied)
    ):
        raise AuditError("invalid candidate review")
    if not prepared.get("brain_requested") and candidates:
        raise AuditError("candidate review requires the managed coding backend")
    seen = set()
    for candidate in candidates:
        if (
            not isinstance(candidate, dict)
            or set(candidate) != {"id", "file", "name", "keep", "responsibility"}
            or candidate.get("id") not in supplied
            or candidate["id"] in seen
            or candidate.get("file") != supplied[candidate["id"]]["file"]
            or type(candidate.get("keep")) is not bool
            or not isinstance(candidate.get("name"), str)
            or not candidate["name"].strip()
            or len(candidate["name"]) > 160
            or not isinstance(candidate.get("responsibility"), str)
            or len(candidate["responsibility"]) > 1000
            or (candidate["keep"] and not candidate["responsibility"].strip())
            or (not candidate["keep"] and bool(candidate["responsibility"].strip()))
        ):
            raise AuditError(
                "candidate review must preserve every supplied identity and include a "
                "responsibility for each retained agent"
            )
        seen.add(candidate["id"])
    saved.update(candidates=candidates, limitations=prepared["limitations"], issue_checks=checks)
    return ("completed_with_limits" if prepared["limitations"] else state), saved, action
