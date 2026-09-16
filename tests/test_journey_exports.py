"""Shareable journey reports retain scores while excluding private evidence."""

import copy
import json
from pathlib import Path

from agentagon.experiments import baselines, preparation, store
from agentagon.reporting import build_fix_report, build_journey_report, export_journey_report


def test_baseline_export_filters_nested_private_payloads(workspace, monkeypatch):
    record = {
        "baseline_id": "baseline_" + "a" * 24,
        "evaluation_id": "eval_" + "b" * 24,
        "state": "completed",
        "source_revision": "c" * 40,
        "evaluator_digest": "d" * 64,
        "benchmark_score": {
            "state": "measured",
            "value": 0.8,
            "eligible": True,
            "raw_trace": "secret-trace",
            "components": {
                "quality": {"value": 0.8, "unit": "fraction", "private_input": "secret-input"}
            },
        },
        "recent_traces": {
            "state": "partial",
            "count": 3,
            "score": {"state": "missing", "value": None, "private_inputs": "secret-input"},
            "payload": "secret-trace",
        },
        "credentials": "secret-key",
    }
    monkeypatch.setattr(baselines, "list_baselines", lambda ws: [record])
    monkeypatch.setattr(
        preparation, "load", lambda ws, eid: {"package": {"spec": {"goal": "Improve quality"}}}
    )
    before = {str(p): p.read_bytes() for p in workspace.state.rglob("*") if p.is_file()}
    report = build_journey_report(workspace, baseline_id=record["baseline_id"])
    assert {str(p): p.read_bytes() for p in workspace.state.rglob("*") if p.is_file()} == before
    assert report["measurement"]["benchmark_score"]["score"] == 0.8
    assert report["measurement"]["recent_traces"]["score"]["score"] is None
    exported = export_journey_report(workspace, baseline_id=record["baseline_id"])
    for name in exported.values():
        text = Path(name).read_text()
        assert "secret-" not in text
        assert "Improve quality" in text
    assert (
        json.loads(Path(exported["json"]).read_text())["measurement"]["source_revision"] == "c" * 40
    )


def test_fix_export_excludes_trial_artifacts_and_host_prose(workspace, fix_run, monkeypatch):
    fix_run["origin_revision"] = "c" * 40
    fix_run["evaluation_digest"] = "d" * 64
    monkeypatch.setattr(store, "load_run", lambda ws, rid: fix_run)
    report = build_journey_report(workspace, run_id=fix_run["run_id"])
    text = json.dumps(report)
    assert "must-never-be-served" not in text
    assert "/private/trial-with-secret" not in text
    assert "Reuse context" not in text
    assert report["measurement"]["evaluator_digest"] == "d" * 64


def test_top_three_reports_only_verified_gate_passing_improvements(workspace, fix_run):
    from test_scoring import definition

    fix_run["spec"]["scoring"] = definition()
    base_id = fix_run["baseline_id"]
    base = fix_run["candidates"][base_id]
    base.update(
        state="verified",
        feasible=True,
        trials=[{"state": "completed", "metrics": {"quality": 0.8, "latency": 100}}],
    )
    fix_run["candidates"] = {base_id: base}
    for name, quality, latency, state in [
        ("first", 1, 10, "verified"),
        ("second", 0.95, 10, "verified"),
        ("third", 0.9, 10, "verified"),
        ("fourth", 0.85, 10, "verified"),
        ("unreviewed", 1, 1, "awaiting_review"),
        ("gate_failed", 0.5, 0, "verified"),
    ]:
        candidate = copy.deepcopy(base)
        candidate.update(
            candidate_id=name,
            parent_id=base_id,
            state=state,
            review={"verdict": "pass"},
            trials=[{"state": "completed", "metrics": {"quality": quality, "latency": latency}}],
        )
        fix_run["candidates"][name] = candidate
    report = build_fix_report(workspace, fix_run)
    assert [candidate["id"] for candidate in report["comparisons"]["alternatives"]] == [
        "first",
        "second",
        "third",
    ]
    assert len(report["candidates"]) == 7


def test_optimizer_report_requires_final_verification_and_respects_selection(workspace, fix_run):
    from test_scoring import definition

    base_id = fix_run["baseline_id"]
    base = fix_run["candidates"][base_id]
    base.update(
        state="verified",
        feasible=True,
        trials=[{"state": "completed", "metrics": {"quality": 0.8, "latency": 100}}],
    )
    search = {
        **copy.deepcopy(base),
        "candidate_id": "search",
        "parent_id": base_id,
        "source_digest": "source-a",
        "trials": [{"state": "completed", "metrics": {"quality": 1, "latency": 10}}],
    }
    fix_run.update(
        optimizer_configured=True, candidates={base_id: base, "search": search}, selected=None
    )
    fix_run["spec"]["scoring"] = definition()
    comparison = build_fix_report(workspace, fix_run)["comparisons"]
    assert comparison["result"] == "final_verification_pending" and not comparison["alternatives"]
    final = {
        **copy.deepcopy(search),
        "candidate_id": "final",
        "verification_of": "search",
        "state": "failed",
    }
    fix_run["candidates"]["final"] = final
    assert build_fix_report(workspace, fix_run)["comparisons"]["result"] == "baseline_retained"
    final["state"] = "verified"
    fix_run["candidates"]["duplicate"] = {**copy.deepcopy(final), "candidate_id": "duplicate"}
    chosen = {
        **copy.deepcopy(final),
        "candidate_id": "chosen",
        "source_digest": "source-b",
        "trials": [{"state": "completed", "metrics": {"quality": 0.9, "latency": 10}}],
    }
    fix_run["candidates"]["chosen"] = chosen
    fix_run["selected"] = {"candidate_id": "chosen"}
    alternatives = build_fix_report(workspace, fix_run)["comparisons"]["alternatives"]
    assert alternatives[0]["id"] == "chosen"
    assert len(alternatives) == 2
    assert "search" not in {candidate["id"] for candidate in alternatives}
    for index in range(3):
        candidate_id = f"additional-{index}"
        fix_run["candidates"][candidate_id] = {
            **copy.deepcopy(final),
            "candidate_id": candidate_id,
            "source_digest": f"source-{index}",
        }
    alternatives = build_fix_report(workspace, fix_run)["comparisons"]["alternatives"]
    assert len(alternatives) == 5
    assert alternatives[0]["id"] == "chosen"
