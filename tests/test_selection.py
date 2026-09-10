import copy
import json

import pytest

from agentagon.core.records import AuditError
from agentagon.operations import import_traces, start
from agentagon.reporting import build_report
from agentagon.telemetry.selection import plan_acquisition

START = "2026-08-10T00:00:00Z"
END = "2026-08-11T00:00:00Z"


def begin(workspace, *, limit="all", mode="traces"):
    return start(
        workspace,
        mode=mode,
        source="braintrust" if mode != "code" else None,
        project="demo" if mode != "code" else None,
        start_time=START if mode != "code" else None,
        end_time=END if mode != "code" else None,
        limit=limit if mode != "code" else None,
        scopes=[],
        host="test",
        model="fixture",
    )["audit_id"]


def inventory_file(tmp_path, **updates):
    value = {
        "source": "braintrust",
        "project": "demo",
        "from": START,
        "to": END,
        "method": "provider root metadata API",
        "tool_version": "fixture-v1",
        "complete": True,
        "roots": [{"id": "root", "started_at": "2026-08-10T12:00:00Z"}],
        **updates,
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def receipt_file(tmp_path, **updates):
    value = {
        "source": "braintrust",
        "project": "demo",
        "method": "provider descendants API",
        "tool_version": "fixture-v1",
        "fetched_at": "2026-08-12T00:00:00Z",
        "completeness": "complete",
        "pagination_complete": True,
        "selected_trace_ids": ["root"],
        "failed_trace_ids": [],
        **updates,
    }
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize("limit", [2, "all"])
def test_plan_deduplicates_roots_filters_half_open_window_and_selects_newest(
    workspace, tmp_path, limit
):
    audit_id = begin(workspace, limit=limit)
    roots = [
        {"id": "before", "started_at": "2026-08-09T23:59:59Z"},
        {"id": "at-start", "started_at": START},
        {"id": "newer-a", "started_at": "2026-08-10T22:00:00Z"},
        {"id": "newer-z", "started_at": "2026-08-10T22:00:00Z"},
        {"id": "newer-a", "started_at": "2026-08-10T22:00:00Z"},
        {"id": "at-end", "started_at": END},
    ]
    result = plan_acquisition(workspace, audit_id, inventory_file(tmp_path, roots=roots))
    expected = ["newer-z", "newer-a"] + (["at-start"] if limit == "all" else [])
    assert result["selected_trace_ids"] == expected
    assert result["estimate"]["eligible_traces"] == 3
    assert result["estimate"]["selected_traces"] == len(expected)
    assert result["estimate"]["eligible_count_kind"] == "exact"
    assert result["estimate"]["span_count"] is None
    assert result["estimate"]["bytes"] is None
    persisted = workspace.read_audit(audit_id)
    assert persisted["acquisition_plan"]["selected_trace_ids"] == expected
    assert persisted["acquisition"] is None
    assert persisted["traces"] == []
    assert workspace.read_artifact(result["inventory"])["roots"] == roots
    assert build_report(workspace, audit_id)["acquisition_plan"]["estimate"] == result["estimate"]


def test_partial_inventory_reports_lower_bound_and_cannot_claim_complete_import(
    workspace, tmp_path, fixtures
):
    audit_id = begin(workspace)
    result = plan_acquisition(workspace, audit_id, inventory_file(tmp_path, complete=False))
    assert result["estimate"]["eligible_count_kind"] == "lower_bound"
    assert not result["estimate"]["inventory_complete"]
    assert result["estimate"]["selected_traces"] == 1
    import_traces(workspace, audit_id, fixtures / "braintrust.json", receipt_file(tmp_path))
    audit = workspace.read_audit(audit_id)
    assert audit["acquisition"]["provenance"]["completeness"] == "partial"
    assert not audit["acquisition"]["provenance"]["pagination_complete"]
    assert audit["traces"][0]["completeness"] == "partial"


def test_equivalent_timezone_windows_match_the_same_instant(workspace, tmp_path):
    audit_id = begin(workspace)
    inventory = inventory_file(tmp_path)
    value = json.loads(inventory.read_text())
    value["from"] = "2026-08-10T05:30:00+05:30"
    value["to"] = "2026-08-11T05:30:00+05:30"
    inventory.write_text(json.dumps(value))
    assert plan_acquisition(workspace, audit_id, inventory)["selected_trace_ids"] == ["root"]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"source": "langsmith"}, "source/project does not match"),
        ({"project": "different"}, "source/project does not match"),
        ({"from": "2026-08-09T00:00:00Z"}, "window does not match"),
        ({"to": "2026-08-12T00:00:00Z"}, "window does not match"),
        ({"from": "2026-08-10T00:00:00"}, "timezone"),
        ({"roots": [{"id": "root", "started_at": "not-a-time"}]}, "timestamp"),
        ({"roots": [{"id": "root", "started_at": "2026-08-10T01:00:00"}]}, "timezone"),
        (
            {
                "roots": [
                    {"id": "root", "started_at": "2026-08-10T01:00:00Z"},
                    {"id": "root", "started_at": "2026-08-10T02:00:00Z"},
                ]
            },
            "conflicting root start times",
        ),
    ],
)
def test_invalid_inventory_does_not_change_audit(workspace, tmp_path, updates, message):
    audit_id = begin(workspace)
    original = copy.deepcopy(workspace.read_audit(audit_id))
    with pytest.raises(AuditError, match=message):
        plan_acquisition(workspace, audit_id, inventory_file(tmp_path, **updates))
    assert workspace.read_audit(audit_id) == original


@pytest.mark.parametrize(
    "updates",
    [
        {"complete": "true"},
        {"roots": [{"id": "", "started_at": START}]},
        {"roots": [{"id": "root", "started_at": None}]},
        {"roots": [{"id": "root", "started_at": START, "body": "trace body"}]},
        {"total": 100},
    ],
)
def test_inventory_requires_the_metadata_contract(workspace, tmp_path, updates):
    audit_id = begin(workspace)
    with pytest.raises(AuditError):
        plan_acquisition(workspace, audit_id, inventory_file(tmp_path, **updates))


def test_download_plan_requires_matching_receipt_before_import(workspace, tmp_path, fixtures):
    audit_id = begin(workspace)
    plan_acquisition(workspace, audit_id, inventory_file(tmp_path))
    export = fixtures / "braintrust.json"
    with pytest.raises(AuditError, match="require an acquisition receipt"):
        import_traces(workspace, audit_id, export)
    for selected in ([], ["root", "extra"], ["different"]):
        with pytest.raises(AuditError, match="do not match the download plan"):
            import_traces(
                workspace, audit_id, export, receipt_file(tmp_path, selected_trace_ids=selected)
            )
    assert workspace.read_audit(audit_id)["acquisition"] is None
    result = import_traces(workspace, audit_id, export, receipt_file(tmp_path))
    assert result["coverage"]["selected_traces"] == 1
    audit = workspace.read_audit(audit_id)
    assert audit["acquisition"]["selected_trace_ids"] == ["root"]
    assert audit["acquisition"]["provenance"]["completeness"] == "complete"


def test_empty_download_plan_does_not_import_unselected_traces(workspace, tmp_path, fixtures):
    audit_id = begin(workspace)
    plan = plan_acquisition(workspace, audit_id, inventory_file(tmp_path, roots=[]))
    assert plan["estimate"]["selected_traces"] == 0
    imported = import_traces(
        workspace,
        audit_id,
        fixtures / "braintrust.json",
        receipt_file(tmp_path, selected_trace_ids=[]),
    )
    assert imported["coverage"]["selected_traces"] == 0
    assert workspace.read_audit(audit_id)["traces"] == []


def test_existing_local_export_needs_no_download_plan(workspace, fixtures):
    audit_id = begin(workspace)
    imported = import_traces(workspace, audit_id, fixtures / "braintrust.json")
    audit = workspace.read_audit(audit_id)
    assert "acquisition_plan" not in audit
    assert imported["coverage"]["selected_traces"] == 1
    assert audit["acquisition"]["provenance"]["method"] == "local_export"
    assert audit["acquisition"]["provenance"]["completeness"] == "unknown"


def test_code_only_and_already_imported_audits_cannot_plan_downloads(workspace, tmp_path, fixtures):
    inventory = inventory_file(tmp_path)
    code_id = begin(workspace, mode="code")
    with pytest.raises(AuditError, match="code-only"):
        plan_acquisition(workspace, code_id, inventory)
    trace_id = begin(workspace)
    import_traces(workspace, trace_id, fixtures / "braintrust.json")
    with pytest.raises(AuditError, match="acquisition or analysis has begun"):
        plan_acquisition(workspace, trace_id, inventory)
