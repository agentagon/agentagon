import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from support.audit import cluster, respond, review_unknown

from agentagon.cli.main import main
from agentagon.core.records import AuditError, load_json
from agentagon.operations import import_traces, prepare, start, submit
from agentagon.reporting import build_report
from agentagon.storage.config import Config
from agentagon.storage.issues import list_issues


def commit(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--no-gpg-sign",
            "-qm",
            "test: update baseline",
        ],
        check=True,
    )


def start_review(workspace, *, mode="code", goal=None):
    return start(
        workspace,
        mode=mode,
        source="braintrust" if mode == "combined" else None,
        project="demo" if mode == "combined" else None,
        start_time="2026-08-10T00:00:00Z" if mode == "combined" else None,
        end_time="2026-08-11T00:00:00Z" if mode == "combined" else None,
        limit="all" if mode == "combined" else None,
        scopes=[],
        host="test",
        model="test",
        code_scope="changes",
        goal=goal,
    )["audit_id"]


def recommendation(unit_id: str) -> dict:
    return {
        "key": "retry-eval",
        "basis": "implementation_only",
        "kind": "evaluation_coverage",
        "title": "Cover the new retry limit",
        "observation": "This change introduces a retry limit without a corresponding case.",
        "expected_behavior": "Verify bounded retries and the final failure response.",
        "authority": "The new retry contract",
        "severity": "medium",
        "confidence": 0.8,
        "evidence": [unit_id],
        "hypothesis": "Add evals/test_retry.py: simulate repeated timeouts; assert two calls and a final failure response.",
        "improvement": True,
        "correlation_rationale": None,
    }


def review_and_recommend(workspace, audit_id, action=None):
    respond(workspace, audit_id, "evidence", review_unknown)

    def diagnose(response, packet):
        for item in response["items"]:
            finding = recommendation(item["subject"])
            if action:
                finding["hypothesis"] = action
            item.update(
                status="reviewed",
                summary="An evaluation would cover the changed behavior.",
                findings=[finding],
            )

    respond(workspace, audit_id, "diagnosis", diagnose)
    respond(workspace, audit_id, "clustering", cluster)


def test_changes_default_to_code_and_preserve_trace_preferences(workspace):
    Config().update(
        scope="project",
        root=workspace.root,
        values={"traces.state": "enabled", "traces.source": "braintrust", "traces.project": "demo"},
        unset=[],
    )
    (workspace.root / "app.py").write_text("RETRIES = 2\n", encoding="utf-8")
    result = CliRunner().invoke(
        main, ["--workspace", str(workspace.root), "audit", "start", "--code-scope", "changes"]
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert (output["workflow"], output["mode"], output["code_scope"]) == (
        "review",
        "code",
        "changes",
    )
    assert output["pending_action"] == "evidence"
    assert Config().effective(workspace.root)["traces"]["state"] == "enabled"
    assert not workspace.read_audit(output["audit_id"])["source"]


def test_review_packets_cover_edits_not_unrelated_old_code(workspace):
    app = workspace.root / "app.py"
    lines = ["# Existing unrelated issue\n"] + [f"value_{i} = {i}\n" for i in range(400)]
    app.write_text("".join(lines), encoding="utf-8")
    commit(workspace.root)
    lines[350] = "value_349 = 900\n"
    app.write_text("".join(lines), encoding="utf-8")
    audit_id = start_review(workspace)
    packet = load_json(Path(prepare(workspace, audit_id, "evidence")["packet"]))
    assert len(packet["units"]) == 1
    text = packet["units"][0]["content"]["text"]
    assert "value_349 = 900" in text
    assert "Existing unrelated issue" not in text
    assert "value_1 = 1" not in text
    assert "introduced or worsened" in packet["emphasis"]
    assert "text" not in workspace.read_audit(audit_id)["snapshot"]["changes"][0]["hunks"][0]


@pytest.mark.parametrize("target", ["new", "existing"])
def test_review_can_report_eval_recommendation_without_a_defect(workspace, target):
    if target == "existing":
        (workspace.root / "evals").mkdir()
        (workspace.root / "evals/test_retry.py").write_text(
            "# Existing case covers only first-attempt success.\n", encoding="utf-8"
        )
        commit(workspace.root)
    (workspace.root / "app.py").write_text("RETRIES = 2\n", encoding="utf-8")
    before = (workspace.root / "app.py").read_bytes()
    audit_id = start_review(workspace)
    action = (
        f"{'Update' if target == 'existing' else 'Add'} evals/test_retry.py: "
        "simulate repeated timeouts; assert two calls and a final failure response."
    )
    review_and_recommend(workspace, audit_id, action)
    report = build_report(workspace, audit_id)
    assert report["state"] == "complete"
    finding = report["issues"][0]["findings"][0]
    assert finding["kind"] == "evaluation_coverage"
    assert finding["improvement"]
    assert "simulate repeated timeouts" in finding["hypothesis"]
    assert finding["source_references"][finding["evidence"][0]]["change"]
    assert list_issues(workspace)[0]["status"] == "open"
    assert (workspace.root / "app.py").read_bytes() == before
    assert (workspace.root / "evals").exists() == (target == "existing")
    if target == "existing":
        assert (workspace.root / "evals/test_retry.py").read_text() == (
            "# Existing case covers only first-attempt success.\n"
        )


def test_deletion_is_reviewable_and_historical_report_survives_new_work(workspace):
    (workspace.root / "app.py").unlink()
    audit_id = start_review(workspace)
    review_and_recommend(workspace, audit_id)
    first = build_report(workspace, audit_id)
    ref = next(iter(first["issues"][0]["findings"][0]["source_references"].values()))
    assert ref["change"]["change_type"] == "deleted"
    assert ref["change"]["new_path"] is None
    assert ref["start_line"] == 1
    (workspace.root / "app.py").write_text("# different later work\n", encoding="utf-8")
    assert build_report(workspace, audit_id)["issues"] == first["issues"]


def test_changed_review_input_rejects_both_prepare_and_submit(workspace):
    app = workspace.root / "app.py"
    app.write_text("RETRIES = 2\n", encoding="utf-8")
    audit_id = start_review(workspace)
    prepared = prepare(workspace, audit_id, "evidence")
    app.write_text("RETRIES = 3\n", encoding="utf-8")
    with pytest.raises(AuditError, match="changed"):
        prepare(workspace, audit_id, "evidence")
    with pytest.raises(AuditError, match="changed"):
        submit(workspace, audit_id, Path(prepared["response_template"]))


def test_empty_review_never_falls_back_to_full_audit(workspace):
    with pytest.raises(AuditError, match="no local changes"):
        start_review(workspace)
    assert workspace.audits() == []


def test_duplicate_submission_does_not_bypass_changed_input_check(workspace):
    app = workspace.root / "app.py"
    app.write_text("RETRIES = 2\n", encoding="utf-8")
    audit_id = start_review(workspace)
    _, response_path = respond(workspace, audit_id, "evidence", review_unknown)
    assert submit(workspace, audit_id, Path(response_path))["duplicate"]
    app.write_text("RETRIES = 3\n", encoding="utf-8")
    with pytest.raises(AuditError, match="changed"):
        submit(workspace, audit_id, Path(response_path))


def test_excluded_only_review_remains_limited(workspace):
    (workspace.root / "image.bin").write_bytes(b"\x00binary")
    audit_id = start_review(workspace)
    report = build_report(workspace, audit_id)
    assert report["state"] == "complete_with_limits"
    assert report["skipped_code_files"] == 1
    assert not report["issues"]
    assert report["code_scope"]["skipped"] == [{"path": "image.bin", "reason": "binary"}]


def test_full_audit_accepts_local_changes_without_switching_to_review(workspace):
    (workspace.root / "app.py").write_text("# edited\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["--workspace", str(workspace.root), "audit", "start"])
    assert result.exit_code == 0, result.output
    audit = workspace.audits()[0]
    assert audit["snapshot"]["code_scope"] == "full"
    assert audit["snapshot"]["local_changes"] is True
    assert workspace.read_artifact(audit["code_units"][0]["content_path"])["text"] == "# edited"


def test_trace_only_cannot_select_changes(workspace):
    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(workspace.root),
            "audit",
            "start",
            "--code-scope",
            "changes",
            "--mode",
            "traces",
        ],
    )
    assert result.exit_code == 1
    assert "requires code or combined" in result.output


def test_review_runtime_finding_requires_change_anchor(workspace, fixtures):
    (workspace.root / "app.py").write_text("RETRIES = 2\n", encoding="utf-8")
    audit_id = start_review(workspace, mode="combined")
    import_traces(workspace, audit_id, fixtures / "braintrust.json")
    respond(workspace, audit_id, "evidence", review_unknown)
    prepared = prepare(workspace, audit_id, "diagnosis")
    response = load_json(Path(prepared["response_template"]))
    packet = load_json(Path(prepared["packet"]))
    for item, unit in zip(response["items"], packet["units"], strict=True):
        item.update(status="reviewed", summary="Inspected evidence")
        item["flag_dispositions"] = [
            {
                "flag_id": f["id"],
                "status": "insufficient_evidence",
                "reason": "Unrelated to change",
                "finding_keys": [],
            }
            for f in unit["flags"]
        ]
        if unit["kind"] == "trace":
            finding = recommendation(item["subject"])
            finding.update(basis="runtime_only", kind="correctness", improvement=False)
            item["findings"] = [finding]
    path = Path(prepared["response_template"])
    path.write_text(json.dumps(response), encoding="utf-8")
    with pytest.raises(AuditError, match="captured code change"):
        submit(workspace, audit_id, path)


def test_metadata_mismatch_blocks_code_trace_correlation(workspace, fixtures, tmp_path):
    audit_id = start(
        workspace,
        mode="combined",
        source="braintrust",
        project="demo",
        start_time="2026-08-10T00:00:00Z",
        end_time="2026-08-11T00:00:00Z",
        limit="all",
        scopes=[],
        host="test",
        model="test",
    )["audit_id"]
    rows = load_json(fixtures / "braintrust.json")
    rows[0].setdefault("metadata", {})["git.commit.sha"] = "f" * 40
    export = tmp_path / "mismatched.json"
    export.write_text(json.dumps(rows), encoding="utf-8")
    imported = import_traces(workspace, audit_id, export)
    assert imported["trace_alignment"]["status"] == "mismatch"
    respond(workspace, audit_id, "evidence", review_unknown)
    prepared = prepare(workspace, audit_id, "diagnosis")
    packet = load_json(Path(prepared["packet"]))
    response = load_json(Path(prepared["response_template"]))
    code = next(u for u in packet["units"] if u["kind"] == "code")
    trace = next(u for u in packet["units"] if u["kind"] == "trace")
    for item, unit in zip(response["items"], packet["units"], strict=True):
        item.update(status="reviewed", summary="Inspected evidence")
        item["flag_dispositions"] = [
            {
                "flag_id": f["id"],
                "status": "insufficient_evidence",
                "reason": "Different source revision",
                "finding_keys": [],
            }
            for f in unit["flags"]
        ]
        if unit["kind"] == "code":
            finding = recommendation(code["id"])
            finding.update(
                basis="correlated",
                evidence=[code["id"], trace["id"]],
                correlation_rationale="Claims the trace executes this code",
            )
            item["findings"] = [finding]
    path = Path(prepared["response_template"])
    path.write_text(json.dumps(response), encoding="utf-8")
    with pytest.raises(AuditError, match="cannot correlate"):
        submit(workspace, audit_id, path)


def test_trace_alignment_remains_an_assumption(workspace, imported):
    report = build_report(workspace, imported)
    assert report["trace_alignment"]["status"] == "assumed"
    assert report["trace_alignment"]["revision"] == report["revision"]


def test_mismatch_corpus_does_not_overflow_bounded_review_packet(workspace, fixtures, tmp_path):
    (workspace.root / "app.py").write_text("RETRIES = 2\n", encoding="utf-8")
    audit_id = start_review(workspace, mode="combined")
    root = load_json(fixtures / "braintrust.json")[0]
    rows = [
        {
            **root,
            "span_id": f"root-{index}",
            "root_span_id": f"root-{index}",
            "metadata": {"git.commit.sha": "f" * 40},
        }
        for index in range(200)
    ]
    export = tmp_path / "mismatched-corpus.json"
    export.write_text(json.dumps(rows), encoding="utf-8")
    import_traces(workspace, audit_id, export)

    prepared = prepare(workspace, audit_id, "evidence", batch_size=1, max_bytes=2000)
    path = Path(prepared["packet"])
    assert path.stat().st_size <= 2000
    packet = load_json(path)
    details = workspace.read_artifact(packet["details_path"])
    assert len(details["trace_alignment"]["mismatched_trace_ids"]) == 200
    assert details["trace_alignment"]["status"] == "mismatch"
    response = load_json(Path(prepared["response_template"]))
    review_unknown(response, details)
    Path(prepared["response_template"]).write_text(json.dumps(response), encoding="utf-8")
    assert (
        submit(workspace, audit_id, Path(prepared["response_template"]))["coverage"][
            "reviewed_traces"
        ]
        == 1
    )


def test_long_review_goal_remains_available_without_overflowing_packet(workspace):
    (workspace.root / "app.py").write_text("RETRIES = 2\n", encoding="utf-8")
    goal = "Assess recovery behavior. " * 140
    audit_id = start_review(workspace, goal=goal)
    prepared = prepare(workspace, audit_id, "evidence", max_bytes=1024)
    path = Path(prepared["packet"])
    assert path.stat().st_size <= 1024
    packet = load_json(path)
    details = workspace.read_artifact(packet["details_path"])
    assert details["goal"] == goal.strip()
    assert "introduced or worsened" in details["emphasis"]
    response = load_json(Path(prepared["response_template"]))
    review_unknown(response, details)
    Path(prepared["response_template"]).write_text(json.dumps(response), encoding="utf-8")
    assert submit(workspace, audit_id, Path(prepared["response_template"]))["pending_action"] == (
        "diagnosis"
    )
