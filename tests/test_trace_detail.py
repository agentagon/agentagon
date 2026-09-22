import copy
import json
import threading
import uuid

import pytest

from agentagon.capabilities.traces import snapshots
from agentagon.capabilities.traces.detail import (
    MAX_DETAIL_SPANS,
    project_trace_snapshot,
    trace_detail,
    trace_usability,
)
from agentagon.capabilities.traces.imports import import_trace, preview_trace
from agentagon.core.records import AuditError


def _preview(items, *, provider="braintrust", complete=True, connection_id=None):
    return {
        "kind": "traces",
        "connection_id": connection_id,
        "selection": {"project": "demo", "environment": "production"},
        "items": items,
        "provenance": {
            "provider": provider,
            "project": "demo",
            "source": "supplied_data",
            "endpoint": "https://private.example.invalid/never-expose",
        },
        "completeness": {"complete": complete, "count": len(items)},
    }


def _save(workspace, items, **kwargs):
    return snapshots.save(workspace, "project_detail", _preview(items, **kwargs))


def test_trace_detail_exposes_bounded_normalized_evidence_without_private_source(
    workspace, fixtures
):
    rows = json.loads((fixtures / "braintrust.json").read_text())
    rows[0]["input"]["api_key"] = "private-value"
    rows[0]["metadata"].update(environment="production", revision="a" * 40, ordinary="retained")
    record = _save(workspace, rows)
    before = snapshots.load(workspace, record["id"])

    result = trace_detail(workspace, record["id"])

    assert result["readiness"] == {
        "state": "ready",
        "diagnosis_ready": True,
        "blockers": [],
        "limitations": [],
        "selection_required": False,
        "viewer_ready": True,
    }
    assert result["source"]["provider"] == "braintrust"
    assert result["source"]["provider_project"] == "demo"
    assert "endpoint" not in result["source"]
    assert "private.example.invalid" not in json.dumps(result)
    assert result["trace"]["environment"] == "production"
    assert result["trace"]["release"] == "a" * 40
    assert result["timeline"]["root_span_ids"] == ["root"]
    assert [span["span_id"] for span in result["timeline"]["spans"]] == [
        "root",
        "llm",
        "tool",
    ]
    assert result["timeline"]["spans"][0]["input"]["api_key"] == "[REDACTED]"
    assert result["errors"] == [
        {
            "span_id": "tool",
            "name": "weather",
            "status": "error",
            "error": "Weather service timed out",
            "content_truncated": False,
        }
    ]
    assert result["handling"]["redaction"]["redacted_value_count"] == 1
    assert result["handling"]["redaction"]["limitation"] == ("This is not a general PII detector.")
    assert snapshots.load(workspace, record["id"]) == before


def test_partial_and_empty_snapshots_never_overstate_diagnosis_readiness(workspace, fixtures):
    rows = json.loads((fixtures / "braintrust.json").read_text())
    partial = project_trace_snapshot(_preview([rows[0], {"unsupported": True}]))
    assert partial["readiness"]["state"] == "partial"
    assert partial["readiness"]["diagnosis_ready"] is True
    assert partial["coverage"]["normalized_spans"] == 1
    assert partial["coverage"]["normalization_failures"] == 1
    assert {value["code"] for value in partial["readiness"]["limitations"]} == {"unusable_records"}

    empty = project_trace_snapshot(_preview([{"unsupported": True}]))
    assert empty["readiness"]["state"] == "empty"
    assert empty["readiness"]["diagnosis_ready"] is False
    assert empty["readiness"]["blockers"][0]["code"] == "no_usable_spans"
    assert empty["timeline"]["spans"] == []
    assert empty["diagnostics"][0]["code"] == "unsupported_record"

    unsupported = project_trace_snapshot(_preview(rows, provider="unknown-observability-platform"))
    assert unsupported["readiness"]["state"] == "unsupported"
    assert unsupported["readiness"]["diagnosis_ready"] is False
    assert unsupported["readiness"]["blockers"][0]["code"] == "unsupported_provider"


def test_multiple_trace_selection_and_span_content_are_bounded(workspace):
    rows = []
    for trace_index, trace_id in enumerate(("first", "second")):
        for span_index in range(205):
            row = {
                "span_id": f"span-{trace_index}-{span_index}",
                "root_span_id": trace_id,
                "span_parents": [] if span_index == 0 else [f"span-{trace_index}-0"],
                "span_attributes": {"type": "agent" if span_index == 0 else "task"},
                "metrics": {
                    "start": 1000 + trace_index + span_index,
                    "end": 1001 + trace_index + span_index,
                },
                "input": "x" * 20_000 if span_index == 0 else None,
                "metadata": {},
            }
            rows.append(row)
    record = _save(workspace, rows)

    index = trace_detail(workspace, record["id"])
    assert index["readiness"]["selection_required"] is True
    assert index["readiness"]["viewer_ready"] is False
    assert index["timeline"]["spans"] == []

    result = trace_detail(workspace, record["id"], "second", max_spans=25)
    assert result["selected_trace_id"] == "second"
    assert result["coverage"]["selected_trace_spans"] == 205
    assert result["coverage"]["returned_spans"] == 25
    assert result["coverage"]["omitted_spans"] == 180
    assert result["timeline"]["spans"][0]["content_truncated"] is True
    codes = {value["code"] for value in result["readiness"]["limitations"]}
    assert codes == {"span_projection_limit", "content_projection_limit"}
    with pytest.raises(AuditError, match="trace not found"):
        trace_detail(workspace, record["id"], "absent")
    with pytest.raises(AuditError, match="max_spans"):
        trace_detail(workspace, record["id"], "second", max_spans=MAX_DETAIL_SPANS + 1)

    selected = snapshots.select_trace(workspace, "project_detail", record["id"], "second")
    frozen = trace_detail(workspace, selected["id"])
    assert frozen["selected_trace_id"] == "second"
    assert frozen["coverage"]["trace_count"] == 1
    assert frozen["source"]["source_snapshot_id"] == record["id"]
    with pytest.raises(AuditError, match="trace not found"):
        snapshots.select_trace(workspace, "project_detail", record["id"], "absent")


class _State:
    def __init__(self, workspace):
        self._workspace = workspace

    def workspace(self, _project_id):
        return self._workspace


class _Application:
    def __init__(self, workspace, preview=None):
        self.state = _State(workspace)
        self.lock = threading.RLock()
        self._preview = preview
        self.previews = {}

    def preview(self, _project_id, _payload):
        return copy.deepcopy(self._preview)


def test_import_uses_public_usability_contract_for_local_and_provider_data(workspace, fixtures):
    rows = json.loads((fixtures / "braintrust.json").read_text())
    app = _Application(workspace)
    result = import_trace(
        app,
        "project_detail",
        {
            "operation_id": str(uuid.uuid4()),
            "provider": "braintrust",
            "data": rows,
        },
    )
    assert result["diagnosis_ready"] is True
    assert result["completeness"]["normalized_spans"] == 3

    invalid_optional = copy.deepcopy(rows[0])
    invalid_optional["metadata"]["model"] = {"unsupported": "model"}
    with pytest.raises(AuditError, match="no supported trace spans"):
        import_trace(
            app,
            "project_detail",
            {
                "operation_id": str(uuid.uuid4()),
                "provider": "braintrust",
                "data": [invalid_optional],
            },
        )

    connection_id = "connection_" + "a" * 24
    provider_app = _Application(
        workspace,
        _preview([], connection_id=connection_id),
    )
    with pytest.raises(AuditError, match="no supported trace spans"):
        import_trace(
            provider_app,
            "project_detail",
            {
                "operation_id": str(uuid.uuid4()),
                "connection_id": connection_id,
                "trace_id": "missing",
            },
        )

    assert trace_usability(_preview(rows))["diagnosis_ready"] is True


def test_trace_preview_detects_format_and_retains_nothing_until_confirmation(workspace, fixtures):
    rows = json.loads((fixtures / "braintrust.json").read_text())
    rows.append({"unsupported": True})
    rows[0]["input"]["api_key"] = "private-preview-value"
    app = _Application(workspace)

    result = preview_trace(app, "project_detail", {"provider": "auto", "data": rows})

    assert result["diagnosis_ready"] is True
    assert result["provider"] == "braintrust"
    assert result["provider_detected"] is True
    assert result["normalized_spans"] == 3
    assert result["unusable_records"] == 1
    assert result["preview_id"] in app.previews
    assert app.previews[result["preview_id"]]["items"][0]["input"]["api_key"] == "[REDACTED]"
    assert not list((workspace.state / "runtime" / "imports").glob("*.json"))

    otlp = preview_trace(
        app,
        "project_detail",
        {
            "provider": "auto",
            "data": [
                {
                    "traceId": "a" * 32,
                    "spanId": "1" * 16,
                    "name": "weather",
                    "startTimeUnixNano": "1000000000",
                    "endTimeUnixNano": "2000000000",
                }
            ],
        },
    )
    assert otlp["provider"] == "otlp"
    assert otlp["provider_detected"] is True
    assert otlp["diagnosis_ready"] is True

    unsupported = preview_trace(app, "project_detail", {"provider": "auto", "data": [{}]})
    assert unsupported["state"] == "unsupported"
    assert unsupported["diagnosis_ready"] is False
    assert unsupported["preview_id"] is None
