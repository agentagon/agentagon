"""Safe saved report projections for search, controls, learning and delivery."""

import copy
import json

import pytest

from agentagon.reporting import build_fix_report, render_fix_markdown


def test_fix_parity_report_keeps_history_and_counts_without_private_payloads(workspace, fix_run):
    policy = {"strategy": "top_k", "objective": "quality", "k": 2, "seed": 7}
    fix_run.update(
        revision=12,
        search={
            "policy": policy,
            "revision": 2,
            "decision_index": 1,
            "revisions": [{"revision": 2, "policy": policy, "at": "2026-09-07T10:00:00Z"}],
            "decisions": [
                {
                    "index": 0,
                    "policy_revision": 2,
                    "seed": 7,
                    "policy": policy,
                    "chosen_parent": "candidate_baseline",
                    "candidate_id": "candidate_tradeoff",
                    "explicit_parent": False,
                    "selection_pool": ["candidate_baseline"],
                    "eligible": [
                        {
                            "candidate_id": "candidate_baseline",
                            "metrics": {"quality": 0.95},
                            "task_metrics": {"task_a": 0.9},
                            "last_expanded": 0,
                            "private_payload": "must-never-be-served",
                        }
                    ],
                    "private_payload": "must-never-be-served",
                }
            ],
        },
        controls=[
            {
                "operation_id": "control_one",
                "action": "directive",
                "state": "queued",
                "request": {
                    "text": "Keep the quality floor",
                    "private_payload": "must-never-be-served",
                },
                "result": {"source_path": "must-never-be-served"},
            }
        ],
        scans=[
            {
                "scan_id": "scan_one",
                "round_id": "round_one",
                "state": "completed",
                "candidate_ids": ["candidate_tradeoff"],
                "packet": "must-never-be-served",
                "insights": [
                    {"summary": "must-never-be-served", "evidence": ["must-never-be-served"]}
                ],
            },
            {
                "scan_id": "scan_two",
                "round_id": "round_two",
                "state": "prepared",
                "deadline_at": "2099-01-01T00:00:00+00:00",
                "candidate_ids": [],
                "packet_digest": "must-never-be-served",
                "packet": "must-never-be-served",
                "response_template": {"secret": "must-never-be-served"},
            },
        ],
        deliveries={
            "delivery_one": {
                "delivery_id": "delivery_one",
                "candidate_id": "candidate_tradeoff",
                "branch": "codex/fix-tradeoff",
                "base": "main",
                "state": "published",
                "artifacts": {"pr_body": "must-never-be-served"},
                "remote_url": "must-never-be-served",
                "pr": {
                    "url": "https://github.com/example/agent/pull/23",
                    "number": 23,
                    "state": "OPEN",
                    "is_draft": True,
                    "private_payload": "must-never-be-served",
                },
            }
        },
    )
    fix_run["profile"]["scans"] = {
        "max_scans": 3,
        "max_input_bytes": 4096,
        "scan_timeout_seconds": 30,
    }
    fix_run["spec"]["task_metrics"] = {"task_a": {"direction": "max", "unit": "fraction"}}
    candidate = fix_run["candidates"]["candidate_tradeoff"]
    candidate["task_metrics"] = {"task_a": 0.9}
    candidate["task_variation"] = {"task_a": {"min": 0.8, "max": 1.0, "samples": [0.8, 0.9, 1.0]}}
    before = copy.deepcopy(fix_run)
    report = build_fix_report(workspace, fix_run)
    assert report["revision"] == 12
    assert report["search_policy"] == policy
    assert report["search"]["decisions"][0]["eligible"][0]["task_metrics"] == {"task_a": 0.9}
    assert report["pending_controls"] == report["controls"]
    assert report["controls"][0]["text"] == "Keep the quality floor"
    assert report["scan_pending"]["scan_id"] == "scan_two"
    assert report["scan_pending"]["used"] == 2
    assert report["lesson_count"] == 1
    assert report["scan_history"][0]["lesson_count"] == 1
    assert report["task_objectives"] == [{"name": "task_a", "direction": "max", "unit": "fraction"}]
    assert report["candidates"][1]["task_variation"] == {"task_a": {"min": 0.8, "max": 1.0}}
    assert report["deliveries"][0]["pr"]["is_draft"] is True
    serialized = json.dumps(report)
    assert "must-never-be-served" not in serialized
    assert "samples" not in serialized
    markdown = render_fix_markdown(report)
    assert "Pending controls: 1" in markdown
    assert "Recorded lessons: 1" in markdown
    assert "task_a: 0.9" in markdown
    assert "[Draft PR #23](https://github.com/example/agent/pull/23)" in markdown
    assert "must-never-be-served" not in markdown
    assert fix_run == before
    assert not (workspace.state / "runs").exists()


def test_fix_report_inherits_invalidation_and_retains_exhaustion_separately(workspace, fix_run):
    fix_run["candidates"]["candidate_baseline"]["invalidated"] = True
    fix_run["candidates"]["candidate_tradeoff"]["expansion_exhausted"] = True
    report = build_fix_report(workspace, fix_run)
    assert report["frontier"] == []
    assert all(candidate["invalidated"] for candidate in report["candidates"])
    candidate = report["candidates"][1]
    assert candidate["state"] == "frontier"
    assert candidate["display_state"] == "invalidated"
    assert candidate["expansion_exhausted"]
    assert "invalidated; expansion exhausted" in render_fix_markdown(report)


@pytest.mark.parametrize(
    "url",
    [
        "file:///private/not-a-pr",
        "javascript:alert(1)",
        "https://user:secret@github.com/example/agent/pull/1",
        "https://github.com/example/agent/pull/2",
    ],
)
def test_fix_delivery_projection_omits_unsafe_or_mismatched_links(workspace, url):
    report = build_fix_report(
        workspace,
        {
            "run_id": "run_empty",
            "deliveries": {
                "delivery_one": {
                    "state": "published",
                    "pr": {"number": 1, "url": url, "is_draft": True},
                }
            },
        },
    )
    assert "url" not in report["deliveries"][0]["pr"]
    assert report["revision"] == 0
    assert report["search_policy"] == {"strategy": "pareto", "seed": 0}
    assert report["scan_pending"]["enabled"] is False
    assert report["lesson_count"] == 0
