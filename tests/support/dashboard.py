"""Shared dashboard test support."""

import copy

import pytest

from agentagon.workflows.audit.operations import start


def make_audit(workspace, *, goal=None, created_at=None):
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
        goal=goal,
    )["audit_id"]
    if created_at:
        audit = workspace.read_audit(audit_id)
        audit["created_at"] = created_at
        workspace.save_audit(audit)
    return audit_id


@pytest.fixture
def fix_run():
    baseline = {
        "candidate_id": "candidate_baseline",
        "parent_id": None,
        "hypothesis": "Measure the unchanged checkout",
        "state": "baseline",
        "metrics": {"cost_usd": 2.0, "latency_ms": 100.0, "quality": 0.95},
        "variation": {"cost_usd": {"min": 1.9, "max": 2.1, "samples": [1.9, 2.0, 2.1]}},
        "constraints": [],
        "checks": [],
        "trials": [{"artifact": "/private/trial-with-secret"}],
    }
    candidate = {
        **copy.deepcopy(baseline),
        "candidate_id": "candidate_tradeoff",
        "parent_id": "candidate_baseline",
        "hypothesis": "Reuse context | avoid duplicate calls",
        "state": "frontier",
        "metrics": {"cost_usd": 1.4, "latency_ms": 120.0, "quality": 0.96},
        "variation": {"cost_usd": {"min": 1.3, "max": 1.5, "samples": [1.3, 1.4, 1.5]}},
        "constraints": [
            {
                "metric": "quality",
                "op": "gte",
                "bound": 0.0,
                "reference": "baseline_delta",
                "threshold": 0.95,
                "actual": 0.96,
                "passed": True,
                "private_input": "must-never-be-served",
            }
        ],
        "checks": [
            {
                "id": "correctness",
                "passed": True,
                "issue_ids": ["issue_one"],
                "command": "must-never-be-served",
                "stdout": "must-never-be-served",
            }
        ],
        "review": {"verdict": "pass", "private_payload": "must-never-be-served"},
        "branch": "codex/fix-tradeoff",
    }
    return {
        "run_id": "run_" + "a" * 24,
        "created_at": "2026-09-07T10:00:00Z",
        "updated_at": "2026-09-07T10:05:00Z",
        "goal": "Reduce inference cost without losing quality",
        "state": "complete",
        "issue_ids": ["issue_one"],
        "baseline_id": baseline["candidate_id"],
        "frontier": [baseline["candidate_id"], candidate["candidate_id"]],
        "candidates": {baseline["candidate_id"]: baseline, candidate["candidate_id"]: candidate},
        "selected": {
            "candidate_id": candidate["candidate_id"],
            "branch": candidate["branch"],
            "private_source": "must-never-be-served",
        },
        "limits": {
            "max_candidates": 6,
            "max_trials": 18,
            "max_elapsed_seconds": 600,
            "parallel_candidates": 2,
            "parallel_trials": 1,
            "trial_timeout_seconds": 30,
            "stagnation_rounds": 3,
            "api_key": "must-never-be-served",
        },
        "usage": {
            "candidates": 2,
            "trials": 6,
            "elapsed_seconds": 300.5,
            "secret_count": 123456789,
        },
        "profile": {"runner": {"kind": "ssh", "host": "must-never-be-served"}},
        "spec": {"command": "must-never-be-served"},
        "cleanup_pending": True,
    }
