import copy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from support.audit import cluster, diagnose_failure, finish, respond, review_unknown

from agentagon.cli.main import main
from agentagon.core.analysis import check_diagnosis, flags_for
from agentagon.core.records import AuditError, load_json
from agentagon.operations import import_traces, prepare, start, status, submit
from agentagon.reporting import report
from agentagon.storage.issues import list_issues, update_issue


def test_complete_trace_audit_retains_report_and_resumes(workspace, imported):
    result, response_path = finish(workspace, imported)
    assert result["state"] == "complete"
    assert status(workspace, imported)["pending_action"] is None
    duplicate = submit(workspace, imported, Path(response_path))
    assert duplicate["duplicate"] is True
    issues = list_issues(workspace)
    assert len(issues) == 1
    assert issues[0]["historical_affected_traces"] == 1
    generated = report(workspace, imported)
    payload = load_json(Path(generated["json_report"]))
    assert payload["issues"][0]["reviewed_sample_rate"] == 1
    assert "weather" in Path(generated["report"]).read_text().lower()


def test_unfilled_template_does_not_mark_evidence_reviewed(workspace, imported):
    prepared = prepare(workspace, imported, "evidence")
    result = submit(workspace, imported, Path(prepared["response_template"]))
    assert result["coverage"]["reviewed_traces"] == 0
    assert result["pending_action"] == "evidence"


def test_fabricated_evidence_and_unavailable_feedback_are_rejected(workspace, imported):
    prepared = prepare(workspace, imported, "evidence")
    path = Path(prepared["response_template"])
    response = load_json(path)
    review_unknown(response, None)
    value = response["items"][0]["judgments"][0]
    value.update(status="computed", value="Weather retrieval", evidence=["invented-reference"])
    path.write_text(json.dumps(response))
    with pytest.raises(AuditError, match="unknown evidence"):
        submit(workspace, imported, path)
    value.update(status="unknown", value=None, evidence=[])
    user_experience = next(
        j for j in response["items"][0]["judgments"] if j["facet"] == "trace.user_experience"
    )
    packet = load_json(Path(prepared["packet"]))
    user = next(key for key, v in packet["evidence_index"].items() if v.get("role") == "user")
    user_experience.update(status="computed", value="positive", evidence=[user])
    path.write_text(json.dumps(response))
    with pytest.raises(AuditError, match="user experience requires"):
        submit(workspace, imported, path)
    assert not workspace.read_audit(imported)["reviews"]


def test_dismissed_flags_can_yield_no_findings(workspace, imported):
    respond(workspace, imported, "evidence", review_unknown)

    def dismiss(response, packet):
        for item, unit in zip(response["items"], packet["units"], strict=True):
            item.update(
                status="reviewed",
                summary="No supported actionable problem after contextual review.",
                flag_dispositions=[
                    {
                        "flag_id": flag["id"],
                        "status": "dismissed",
                        "reason": "An expected controlled failure",
                        "finding_keys": [],
                    }
                    for flag in unit["flags"]
                ],
            )

    result, _ = respond(workspace, imported, "diagnosis", dismiss)
    assert result["state"] == "complete"
    assert list_issues(workspace) == []


@pytest.mark.parametrize(
    ("facet", "value", "needs_diagnosis"),
    [
        ("trace.fulfillment", "fulfilled", False),
        ("trace.fulfillment", "partial", True),
        ("trace.fulfillment", "unfulfilled", True),
        ("trace.user_experience", "positive", False),
        ("trace.user_experience", "mixed", True),
        ("trace.user_experience", "negative", True),
        ("llm.user_sentiment", "frustrated", False),
        ("llm.hallucination", "present", True),
        ("llm.hallucination", "absent", False),
    ],
)
def test_judgment_concerns_require_explanation_before_diagnosis_completes(
    facet, value, needs_diagnosis
):
    unit = {"id": "trace-1", "digest": "trace-digest"}
    evidence = {"result": {"unit_id": unit["id"], "kind": "message"}}
    review = {
        "judgments": [
            {
                "subject": unit["id"],
                "facet": facet,
                "status": "computed",
                "value": value,
                "evidence": ["result"],
            }
        ]
    }
    flags = flags_for(unit, review)
    diagnosis = {"status": "reviewed", "findings": [], "flag_dispositions": []}
    if needs_diagnosis:
        assert len(flags) == 1
        assert flags[0]["status"] == "candidate"
        assert flags[0]["evidence"] == ["result"]
        with pytest.raises(AuditError, match="account for every diagnostic flag"):
            check_diagnosis(diagnosis, unit, flags, evidence)
        diagnosis["flag_dispositions"] = [
            {
                "flag_id": flags[0]["id"],
                "status": "dismissed",
                "reason": "The observed concern does not establish an agent-caused defect.",
                "finding_keys": [],
            }
        ]
    else:
        assert flags == []
    check_diagnosis(diagnosis, unit, flags, evidence)


@pytest.mark.parametrize("status", ["unknown", "not_applicable", "not_evaluated", "error"])
def test_uncomputed_judgment_is_not_a_negative_signal(status):
    unit = {"id": "trace-1", "digest": "trace-digest"}
    review = {
        "judgments": [
            {
                "subject": unit["id"],
                "facet": "trace.fulfillment",
                "status": status,
                "value": None,
                "evidence": [],
            }
        ]
    }
    assert flags_for(unit, review) == []


def test_packet_tampering_and_duplicate_group_membership_rejected(workspace, imported):
    respond(workspace, imported, "evidence", review_unknown)
    respond(workspace, imported, "diagnosis", diagnose_failure)
    prepared = prepare(workspace, imported, "clustering")
    path = Path(prepared["response_template"])
    packet = load_json(Path(prepared["packet"]))
    response = load_json(path)
    cluster(response, packet)
    response["groups"].append(copy.deepcopy(response["groups"][0]))
    path.write_text(json.dumps(response))
    with pytest.raises(AuditError, match="every packet finding"):
        submit(workspace, imported, path)
    assert list_issues(workspace) == []
    response["groups"].pop()
    response["packet_digest"] = "wrong"
    path.write_text(json.dumps(response))
    with pytest.raises(AuditError, match="unknown, stale"):
        submit(workspace, imported, path)


def test_old_evidence_does_not_reopen_or_inflate_issue(workspace, imported, fixtures):
    finish(workspace, imported)
    issue_id = list_issues(workspace)[0]["issue_id"]
    update_issue(
        workspace,
        {
            "issue_id": issue_id,
            "status": "resolved_user",
            "reason": "Customer reports upstream repaired",
            "evidence": [],
        },
    )
    other = start(
        workspace,
        mode="traces",
        source="braintrust",
        project="demo",
        start_time="2026-08-10T00:00:00Z",
        end_time="2026-08-11T00:00:00Z",
        limit=1,
        scopes=[],
        host="test",
        model="fixture",
    )["audit_id"]
    import_traces(workspace, other, fixtures / "braintrust.json")
    finish(workspace, other)
    issue = list_issues(workspace)[0]
    assert len(list_issues(workspace)) == 1
    assert issue["historical_affected_traces"] == 1
    assert issue["status"] == "resolved_user"
    with pytest.raises(AuditError, match="requires --run and --candidate"):
        update_issue(
            workspace,
            {
                "issue_id": issue_id,
                "status": "resolved_verified",
                "reason": "Fixed",
                "evidence": [],
            },
        )


def test_code_only_audit_and_changed_input_detection(workspace):
    audit_id = start(
        workspace,
        mode="code",
        source=None,
        project=None,
        start_time=None,
        end_time=None,
        limit=None,
        scopes=["app.py"],
        host="test",
        model="fixture",
    )["audit_id"]
    prepared = prepare(workspace, audit_id, "evidence")
    path = Path(prepared["response_template"])
    response = load_json(path)
    review_unknown(response, None)
    path.write_text(json.dumps(response))
    (workspace.root / "app.py").write_text("# changed after preparation\n")
    with pytest.raises(AuditError, match="code input changed"):
        submit(workspace, audit_id, path)


def test_cli_requires_explicit_trace_scope_and_reports_json(workspace):
    runner = CliRunner()
    result = runner.invoke(
        main, ["--workspace", str(workspace.root), "audit", "start", "--mode", "traces"]
    )
    assert result.exit_code == 1
    assert "require source" in json.loads(result.stderr)["error"]
    result = runner.invoke(
        main,
        [
            "--workspace",
            str(workspace.root),
            "audit",
            "start",
            "--mode",
            "code",
            "--scope",
            "app.py",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["pending_action"] == "evidence"


def test_code_only_issue_has_handoff_without_runtime_prevalence(workspace):
    audit_id = start(
        workspace,
        mode="code",
        source=None,
        project=None,
        start_time=None,
        end_time=None,
        limit=None,
        scopes=["app.py"],
        host="test",
        model="fixture",
    )["audit_id"]
    respond(workspace, audit_id, "evidence", review_unknown)

    def diagnose(response, packet):
        item = response["items"][0]
        item.update(status="reviewed", summary="The weather function unconditionally raises.")
        item["findings"] = [
            {
                "key": "always-raises",
                "basis": "implementation_only",
                "kind": "correctness",
                "title": "Weather cannot return a result",
                "observation": "Every call raises TimeoutError.",
                "expected_behavior": "Provide weather data when the dependency is available.",
                "authority": "Function contract inferred from the function name; confirm desired behavior.",
                "severity": "high",
                "confidence": 0.9,
                "evidence": [packet["units"][0]["id"]],
                "hypothesis": "Replace the stub after confirming the expected provider.",
                "improvement": False,
                "correlation_rationale": None,
            }
        ]

    respond(workspace, audit_id, "diagnosis", diagnose)
    result, _ = respond(workspace, audit_id, "clustering", cluster)
    assert result["state"] == "complete"
    generated = report(workspace, audit_id)
    data = load_json(Path(generated["json_report"]))
    issue = data["issues"][0]
    assert issue["reviewed_sample_rate"] is None
    assert issue["reviewed_trace_denominator"] == 0
    finding = issue["findings"][0]
    assert finding["trace_ids"] == []
    assert next(iter(finding["source_references"].values()))["path"] == "app.py"
    assert data["host"] == "test" and data["model"] == "fixture"
    assert "behavior.." not in Path(generated["report"]).read_text()
