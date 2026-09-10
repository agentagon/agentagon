import copy
import json

import pytest

from agentagon.core.records import AuditError, digest, timestamp_ns, validate_record
from agentagon.core.signals import measure
from agentagon.operations import import_traces, start
from agentagon.telemetry.normalize import normalize, unpack


@pytest.mark.parametrize("provider", ["braintrust", "langfuse", "langsmith", "phoenix", "otlp"])
def test_equivalent_provider_exports(provider, fixtures, workspace):
    audit_id = start(
        workspace,
        mode="traces",
        source=provider,
        project="demo",
        start_time="2026-08-10T00:00:00Z",
        end_time="2026-08-11T00:00:00Z",
        limit="all",
        scopes=[],
        host="test",
        model="fixture",
    )["audit_id"]
    result = import_traces(workspace, audit_id, fixtures / f"{provider}.json")
    assert result["coverage"]["selected_traces"] == 1
    audit = workspace.read_audit(audit_id)
    trace = workspace.read_artifact(audit["traces"][0]["path"])
    validate_record("trace", trace)
    assert [span["kind"] for span in trace["spans"]] == ["agent", "llm", "tool"]
    assert trace["spans"][2]["parent_span_ids"] == [trace["spans"][1]["span_id"]]
    llm = trace["spans"][1]
    assert llm["messages"][0]["role"] == "user"
    assert llm["messages"][1]["parts"][-1]["tool_name"] == "weather"
    summary = audit["traces"][0]["measurements"]["summary"]
    assert summary["usage"]["input_tokens"]["value"] == 10
    assert summary["usage"]["cost_usd"]["value"] == 0.001
    assert summary["elapsed_ms"] == 3000
    assert summary["error_count"] == 1


def test_window_selects_roots_and_retains_children(workspace, fixtures):
    audit_id = start(
        workspace,
        mode="traces",
        source="braintrust",
        project="demo",
        start_time="2026-08-10T12:00:00Z",
        end_time="2026-08-10T12:00:01Z",
        limit=1,
        scopes=[],
        host="test",
        model="fixture",
    )["audit_id"]
    import_traces(workspace, audit_id, fixtures / "braintrust.json")
    trace = workspace.read_audit(audit_id)["traces"][0]
    assert trace["measurements"]["summary"]["span_count"] == 3


def test_nanoseconds_are_exact_and_invalid_input_rejected():
    assert timestamp_ns("1767225600000000123", nanos=True) == 1767225600000000123
    assert timestamp_ns("2026-01-01T00:00:00.000000123Z") == 1767225600000000123
    for value in (True, float("nan"), "2026-08-10T00:00:00"):
        with pytest.raises(AuditError):
            timestamp_ns(value)


def test_malformed_rows_and_credentials_do_not_look_healthy(
    workspace, imported, fixtures, tmp_path
):
    rows = json.loads((fixtures / "braintrust.json").read_text())
    rows[1]["metadata"]["api_key"] = "do-not-retain-this-secret"
    path = tmp_path / "mixed.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + '\n{"broken":\n', encoding="utf-8")
    import_traces(workspace, imported, path)
    audit = workspace.read_audit(imported)
    assert audit["acquisition"]["diagnostics"]
    assert audit["traces"][0]["completeness"] != "complete"
    assert "do-not-retain-this-secret" not in "".join(
        p.read_text() for p in workspace.state.rglob("*.json")
    )


def test_ancestor_usage_and_parallel_latency_are_not_double_counted(workspace, imported):
    trace = workspace.read_artifact(workspace.read_audit(imported)["traces"][0]["path"])
    parent, child = trace["spans"][:2]
    parent["kind"] = "llm"
    parent["usage"] = {**child["usage"], "input_tokens": 100}
    trace["digest"] = digest(trace)
    result = measure(trace)
    assert result["summary"]["usage"]["input_tokens"]["value"] == 10
    assert result["summary"]["usage"]["input_tokens"]["coverage"] == "partial"
    assert result["summary"]["elapsed_ms"] == 3000


def test_recovered_retry_is_observed_without_erasing_failure(workspace, imported):
    trace = workspace.read_artifact(workspace.read_audit(imported)["traces"][0]["path"])
    retry = copy.deepcopy(trace["spans"][2])
    retry.update(
        span_id="retry",
        id="retry",
        status="ok",
        error=None,
        started_ns=retry["ended_ns"],
        ended_ns=retry["ended_ns"] + 1_000_000_000,
    )
    trace["spans"].append(retry)
    result = measure(trace)
    recovery = next(v for v in result["observations"] if v["facet"] == "agent.recovery")
    assert recovery["value"][0]["recovered"] is True
    assert not any(v["kind"] == "retry_exhausted" for v in result["flags"])


def test_reported_root_usage_survives_missing_llm_usage(workspace, imported):
    trace = workspace.read_artifact(workspace.read_audit(imported)["traces"][0]["path"])
    parent, child = trace["spans"][:2]
    parent["usage"] = child["usage"]
    child["usage"] = {}
    result = measure(trace)["summary"]["usage"]["input_tokens"]
    assert result["value"] == 10
    assert result["measured_spans"] == [parent["id"]]
    assert result["unmeasured_llm_spans"] == [child["id"]]
    assert result["coverage"] == "partial"


def test_source_and_project_namespace_trace_ids(fixtures):
    row = json.loads((fixtures / "braintrust.json").read_text())[0]
    first = normalize(row, "braintrust", "first", "ref")
    second = normalize(row, "braintrust", "second", "ref")
    assert first["trace_key"] != second["trace_key"]


def test_otlp_keeps_resource_scope_and_unknown_attributes(fixtures):
    payload = json.loads((fixtures / "otlp.json").read_text())
    row, locator = next(unpack(payload, "otlp"))
    span = normalize(row, "otlp", "demo", locator)
    assert span["resource"]["service.name"] == "fixture-agent"
    assert span["scope"]["name"] == "openinference"


def test_langsmith_constructor_messages_and_batched_generations(fixtures):
    row = json.loads((fixtures / "langsmith.json").read_text())[1]
    row["inputs"] = {
        "messages": [
            [
                {
                    "type": "constructor",
                    "id": ["langchain", "messages", "HumanMessage"],
                    "kwargs": {"content": "Retrieve weather"},
                }
            ]
        ]
    }
    row["outputs"] = {
        "generations": [
            [
                {
                    "message": {
                        "type": "constructor",
                        "id": ["langchain", "messages", "AIMessage"],
                        "kwargs": {
                            "content": "",
                            "tool_calls": [
                                {"id": "call", "name": "weather", "args": {"city": "London"}}
                            ],
                        },
                    }
                }
            ]
        ]
    }
    span = normalize(row, "langsmith", "demo", "ref")
    validate_record("trace", span, definition="span")
    assert [message["role"] for message in span["messages"]] == ["user", "assistant"]
    assert span["messages"][1]["parts"][1]["arguments"] == {"city": "London"}


def test_genai_message_parts_and_openinference_indexed_tool_calls(fixtures):
    payload = json.loads((fixtures / "otlp.json").read_text())
    row, locator = next(unpack(payload, "otlp"))
    row["attributes"] = {
        "gen_ai.output.messages": [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool_call",
                        "id": "call",
                        "name": "weather",
                        "arguments": {"city": "London"},
                    }
                ],
            }
        ]
    }
    span = normalize(row, "otlp", "demo", locator)
    assert span["messages"][0]["parts"][0]["tool_name"] == "weather"
    row["attributes"] = {
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.tool_calls.0.tool_call.id": "call",
        "llm.output_messages.0.message.tool_calls.0.tool_call.function.name": "weather",
        "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments": '{"city":"London"}',
    }
    span = normalize(row, "otlp", "demo", locator)
    assert span["messages"][0]["parts"][0]["arguments"] == {"city": "London"}


def test_invalid_optional_span_field_preserves_valid_siblings(
    workspace, imported, fixtures, tmp_path
):
    rows = json.loads((fixtures / "braintrust.json").read_text())
    rows[1]["metadata"]["model"] = {"invalid": "model must be a string"}
    path = tmp_path / "bad-model.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    import_traces(workspace, imported, path)
    audit = workspace.read_audit(imported)
    assert audit["traces"][0]["measurements"]["summary"]["span_count"] == 2
    assert audit["traces"][0]["completeness"] == "partial"
    assert len(audit["acquisition"]["diagnostics"]) == 1
