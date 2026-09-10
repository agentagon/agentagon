import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from support.audit import cluster, diagnose_failure, finish, respond, review_unknown

from agentagon.core.records import AuditError, load_json
from agentagon.core.signals import measure
from agentagon.operations import import_traces, prepare, start, submit
from agentagon.reporting import report
from agentagon.storage.issues import list_issues, update_issue
from agentagon.telemetry.normalize import normalize, redact, unpack


def test_malformed_nested_span_does_not_discard_healthy_siblings(workspace, fixtures, tmp_path):
    payload = load_json(fixtures / "otlp.json")
    payload["resourceSpans"][0]["scopeSpans"][0]["spans"].insert(1, "broken")
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    audit = start(
        workspace,
        mode="traces",
        source="otlp",
        project="demo",
        start_time="2026-08-10T00:00:00Z",
        end_time="2026-08-11T00:00:00Z",
        limit="all",
        scopes=[],
        host="test",
        model="test",
    )["audit_id"]
    import_traces(workspace, audit, path)
    state = workspace.read_audit(audit)
    assert state["traces"][0]["measurements"]["summary"]["span_count"] == 3
    assert len(state["acquisition"]["diagnostics"]) == 1


def test_identical_reimport_is_idempotent_after_review(workspace, imported, fixtures, tmp_path):
    receipt = tmp_path / "acquisition.json"
    finish(workspace, imported)
    before = workspace.read_audit(imported)
    import_traces(workspace, imported, fixtures / "braintrust.json", receipt)
    assert workspace.read_audit(imported) == before


def test_partial_pagination_and_failed_trace_ids_survive_report(
    workspace, imported, fixtures, tmp_path
):
    receipt = tmp_path / "acquisition.json"
    data = load_json(receipt)
    data.update(
        completeness="partial",
        pagination_complete=False,
        selected_trace_ids=["root", "unfetched"],
        failed_trace_ids=["unfetched"],
    )
    receipt.write_text(json.dumps(data), encoding="utf-8")
    import_traces(workspace, imported, fixtures / "braintrust.json", receipt)
    result, _ = finish(workspace, imported)
    assert result["state"] == "complete_with_limits"
    payload = load_json(Path(report(workspace, imported)["json_report"]))
    assert payload["acquisition"]["missing_selected_ids"] == ["unfetched"]
    assert payload["issues"][0]["reviewed_trace_denominator"] == 1


def test_historical_tool_calls_in_input_are_not_new_requests(workspace, imported):
    trace = workspace.read_artifact(workspace.read_audit(imported)["traces"][0]["path"])
    llm = trace["spans"][1]
    old = copy.deepcopy(llm["messages"][1])
    old["id"] = llm["id"] + "/input/3"
    old["parts"][-1]["tool_call_id"] = "previous-call"
    llm["messages"].insert(0, old)
    evidence = measure(trace)
    calls = next(o for o in evidence["observations"] if o["facet"] == "llm.tool_request_execution")
    assert calls["value"]["requested"] == 1
    assert calls["value"]["matched"] == 1


def test_provider_v2_optional_fields_and_multi_parent_ids(fixtures):
    row = load_json(fixtures / "langfuse.json")["data"][1]
    row.update(inputUsage=12, outputUsage=3, providedModelName="example-model")
    row["usageDetails"] = {"input_cached_tokens": 2}
    span = normalize(row, "langfuse", "demo", "ref")
    assert span["usage"]["input_tokens"] == 12
    assert span["model"] == "example-model"
    row = load_json(fixtures / "langsmith.json")[1]
    row["parent_run_ids"] = ["root", "other-parent"]
    assert normalize(row, "langsmith", "demo", "ref")["parent_span_ids"] == ["root", "other-parent"]


def test_phoenix_context_identity_and_snake_case_nanos(fixtures):
    row = load_json(fixtures / "phoenix.json")["spans"][0]
    row["context"] = {"span_id": row.pop("span_id"), "trace_id": row.pop("trace_id")}
    row["start_time_unix_nano"] = "1786363200000000123"
    row["end_time_unix_nano"] = "1786363201000000123"
    unpacked, locator = next(unpack(row, "phoenix"))
    span = normalize(unpacked, "phoenix", "demo", locator)
    assert span["started_ns"] == 1786363200000000123


def test_later_runtime_occurrence_reopens_resolved_issue(workspace, imported, fixtures, tmp_path):
    finish(workspace, imported)
    issue_id = list_issues(workspace)[0]["issue_id"]
    with patch("agentagon.storage.issues.now", return_value="2026-08-11T00:00:00+00:00"):
        update_issue(
            workspace,
            {
                "issue_id": issue_id,
                "status": "resolved_user",
                "reason": "Customer reports fixed",
                "evidence": [],
            },
        )
    assert list_issues(workspace)[0]["status"] == "resolved_user"
    rows = load_json(fixtures / "braintrust.json")
    for row in rows:
        row["span_id"] += "-later"
        row["root_span_id"] += "-later"
        row["span_parents"] = [parent + "-later" for parent in row["span_parents"]]
        row["metrics"]["start"] += 172800
        row["metrics"]["end"] += 172800
    path = tmp_path / "later.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    later = start(
        workspace,
        mode="traces",
        source="braintrust",
        project="demo",
        start_time="2026-08-12T00:00:00Z",
        end_time="2026-08-13T00:00:00Z",
        limit="all",
        scopes=[],
        host="test",
        model="fixture",
    )["audit_id"]
    import_traces(workspace, later, path)
    finish(workspace, later)
    issue = list_issues(workspace)[0]
    assert issue["status"] == "reopened"
    assert issue["historical_affected_traces"] == 2
    assert issue["history"][-2]["status"] == "resolved_user"
    assert issue["history"][-1]["status"] == "reopened"


def test_multiple_findings_from_one_trace_do_not_inflate_issue_count(workspace, imported):
    respond(workspace, imported, "evidence", review_unknown)

    def two_findings(response, packet):
        diagnose_failure(response, packet)
        second = copy.deepcopy(response["items"][0]["findings"][0])
        second.update(key="related-handling", title="Related timeout handling observation")
        response["items"][0]["findings"].append(second)

    respond(workspace, imported, "diagnosis", two_findings)
    respond(workspace, imported, "clustering", cluster)
    issue = list_issues(workspace)[0]
    assert len(issue["occurrences"]) == 2
    assert issue["historical_affected_traces"] == 1
    payload = load_json(Path(report(workspace, imported)["json_report"]))
    assert len(payload["issues"][0]["affected_trace_ids"]) == 1


def test_empty_export_has_no_fabricated_findings(workspace, imported, tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("[]", encoding="utf-8")
    result = import_traces(workspace, imported, path)
    assert result["coverage"]["selected_traces"] == 0
    assert list_issues(workspace) == []


def test_serialized_credentials_are_redacted_and_unknown_keys_are_kept():
    value = {
        "input": '{"api_key":"private","ordinary":"retained"}',
        "LANGSMITH_API_KEY": "private-key",
    }
    result = redact(value)
    assert "private" not in json.dumps(result)
    assert "retained" in result["input"]


def test_atomic_write_failure_preserves_previous_audit(workspace, imported):
    before = workspace.audit_path(imported).read_bytes()
    audit = workspace.read_audit(imported)
    audit["generation"] += 1
    with patch(
        "agentagon.storage.workspace.os.replace", side_effect=OSError("simulated disk failure")
    ):
        with pytest.raises(OSError):
            workspace.save_audit(audit)
    assert workspace.audit_path(imported).read_bytes() == before
    assert not list(workspace.state.rglob(".pending-*"))


def test_incompatible_state_is_rejected_without_rewrite(workspace):
    path = workspace.state / "workspace.json"
    path.write_text('{"contract_version":"99"}', encoding="utf-8")
    with pytest.raises(AuditError, match="unsupported workspace"):
        workspace.require_initialized()
    assert "99" in path.read_text()


def test_bounded_packet_keeps_all_judgments_and_validates_referenced_details(workspace, imported):
    prepared = prepare(workspace, imported, "evidence", max_bytes=1024)
    path = Path(prepared["packet"])
    assert path.stat().st_size <= 1024
    packet = load_json(path)
    details = workspace.read_artifact(packet["details_path"])
    response = load_json(Path(prepared["response_template"]))
    assert len(response["items"][0]["judgments"]) == len(details["units"][0]["required_judgments"])
    review_unknown(response, details)
    Path(prepared["response_template"]).write_text(json.dumps(response), encoding="utf-8")
    assert (
        submit(workspace, imported, Path(prepared["response_template"]))["pending_action"]
        == "diagnosis"
    )
    diagnosis = prepare(workspace, imported, "diagnosis")
    assert "required_judgments" not in load_json(Path(diagnosis["packet"]))["units"][0]


def test_historical_verification_is_preserved_but_new_handwritten_receipts_are_rejected(
    workspace, imported, tmp_path
):
    finish(workspace, imported)
    issue_id = list_issues(workspace)[0]["issue_id"]
    trials = []
    for role, exit_code in (("baseline", 1), ("candidate", 0), ("control", 0)):
        log = workspace.root / f"{role}.log"
        log.write_text(f"{role}: exit {exit_code}\n", encoding="utf-8")
        trials.append(
            {
                "role": role,
                "exit_code": exit_code,
                "command": "pytest test_weather.py",
                "log_path": log.name,
            }
        )
    receipt = tmp_path / "verification.json"
    data = {
        "checked_at": "2026-08-13T00:00:00Z",
        "source_revision": "fixture-candidate",
        "reason": "Same defect test and control executed",
        "trials": trials,
    }
    receipt.write_text(json.dumps(data), encoding="utf-8")
    event = {
        "issue_id": issue_id,
        "status": "resolved_verified",
        "reason": "Verified locally",
        "evidence": [],
    }
    # A pre-engine historical record remains visible with its original provenance.
    original_receipt = workspace.artifact(data)
    historical = {
        **event,
        "event_id": "event_" + "a" * 24,
        "at": "2026-08-13T00:00:00Z",
        "verification": original_receipt,
    }
    workspace.write(workspace.state / "cases" / "events" / "historical.json", historical)
    issue = list_issues(workspace)[0]
    assert issue["status"] == "resolved_verified"
    stored = workspace.read_artifact(issue["history"][-1]["verification"])
    assert stored == data
    assert "provenance" not in stored
    data["trials"][1]["exit_code"] = 1
    receipt.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AuditError, match="manual verification receipts"):
        update_issue(workspace, event, receipt)
    assert len(list_issues(workspace)[0]["history"]) == 1
