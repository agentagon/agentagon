"""Local review reports retain recommendations and immutable change locations."""

from pathlib import Path

from support.audit import finish_change_review

from agentagon.core.records import load_json
from agentagon.dashboard import _detail
from agentagon.operations import start
from agentagon.reporting import build_report, report


def test_review_report_preserves_recommendations_and_deletion_citations(workspace):
    audit_id = finish_change_review(workspace)
    saved = report(workspace, audit_id)
    payload = load_json(Path(saved["json_report"]))
    markdown = Path(saved["report"]).read_text()
    assert payload["workflow"] == "review"
    assert payload["code_scope"]["code_scope"] == "changes"
    assert payload["revision"] == payload["code_scope"]["revision"]
    assert payload["state"] == "complete"
    assert {f["category"] for f in payload["issues"][0]["findings"]} == {
        "Improvement",
        "Eval recommendation",
    }
    assert "# Agentagon review" in markdown
    assert "Baseline revision:" in markdown
    assert "Code scope: Local changes" in markdown
    assert "**Defect**" not in markdown
    assert "**Improvement**" in markdown and "**Eval recommendation**" in markdown
    assert "Proposed action: Add an eval case to evals/weather.json" in markdown
    assert "before: `app.py` lines 1–2" in markdown
    assert "after: `app.py`" not in markdown
    assert "unrelated existing defects are outside this review" in markdown

    # A historical report remains readable after further work on the checkout.
    (workspace.root / "app.py").write_text("# Further unreviewed changes\n")
    assert build_report(workspace, audit_id)["revision"] == payload["revision"]


def test_trace_alignment_is_explicit_without_exposing_trace_payloads(workspace, imported):
    assumed = build_report(workspace, imported)
    assert assumed["trace_alignment"]["status"] == "assumed"
    assert any("provenance has not been verified" in limit for limit in assumed["limits"])
    audit = workspace.read_audit(imported)
    audit["trace_alignment"].update(status="mismatch", mismatched_trace_ids=["private-trace-id"])
    workspace.save_audit(audit)
    detail = _detail(workspace, imported)
    assert detail["trace_alignment"]["mismatched_traces"] == 1
    assert "mismatched_trace_ids" not in detail["trace_alignment"]
    assert any("revision mismatch" in limit for limit in detail["limits"])


def test_review_report_does_not_assume_traces_reflect_local_changes(workspace):
    (workspace.root / "app.py").unlink()
    audit_id = start(
        workspace,
        mode="combined",
        source="braintrust",
        project="demo",
        start_time="2026-08-10T00:00:00Z",
        end_time="2026-08-11T00:00:00Z",
        limit="all",
        scopes=["app.py"],
        host="test",
        model="fixture",
        code_scope="changes",
    )["audit_id"]
    saved = report(workspace, audit_id)
    payload = load_json(Path(saved["json_report"]))
    markdown = Path(saved["report"]).read_text()
    assert payload["trace_alignment"]["status"] == "unverified"
    assert "local changes is unverified" in markdown
    assert "Consult each finding's correlation rationale" in markdown
    assert "traces may describe the baseline rather than the edits" in markdown
    assert "Supplied traces are assumed to reflect" not in markdown
    assert _detail(workspace, audit_id)["trace_alignment"]["status"] == "unverified"


def test_legacy_report_keeps_original_scope_without_inventing_revision(workspace, imported):
    audit = workspace.read_audit(imported)
    audit["snapshot"].pop("code_scope")
    audit["snapshot"].pop("revision")
    audit.pop("trace_alignment")
    workspace.save_audit(audit)
    payload = build_report(workspace, imported)
    assert payload["workflow"] == "audit"
    assert payload["revision"] is None
    assert "code_scope" not in payload["code_scope"]
    assert payload["trace_alignment"] is None
