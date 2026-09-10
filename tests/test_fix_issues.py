"""A reviewed branch and a repaired origin are distinct evidence states."""

import json
import sys

import pytest
from support.audit import finish
from support.experiments import git, passing_review, verify

from agentagon.core.records import AuditError
from agentagon.experiments import engine
from agentagon.storage.config import Config
from agentagon.storage.issues import list_issues, update_issue


def prepare_defect(workspace, imported):
    finish(workspace, imported)
    issue_id = list_issues(workspace)[0]["issue_id"]
    (workspace.root / "benchmark.py").write_text(
        "import json, os\nfrom pathlib import Path\n"
        "Path(os.environ['AGENTAGON_RESULT_PATH']).write_text(json.dumps({'metrics': {'latency': 1}}))\n"
    )
    regression = workspace.state / "regression.py"
    regression.write_text("from app import weather\nassert weather('London') == 'sunny'\n")
    git(workspace.root, "add", ".")
    git(
        workspace.root,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@localhost",
        "commit",
        "-qm",
        "Defective baseline",
    )
    Config().update_profile(
        "project",
        "local",
        {
            "runner": {"kind": "local"},
            "limits": {
                "max_candidates": 3,
                "max_trials": 12,
                "max_elapsed_seconds": 120,
                "parallel_candidates": 1,
                "parallel_trials": 1,
                "trial_timeout_seconds": 10,
            },
        },
        workspace.root,
    )
    spec = {
        "issue_ids": [issue_id],
        "editable_paths": ["app.py"],
        "evaluation_paths": ["benchmark.py", "regression.py"],
        "benchmark": {"argv": [sys.executable, "benchmark.py"]},
        "metrics": {"latency": {"unit": "ms", "direction": "min"}},
        "checks": [
            {
                "id": "weather",
                "argv": [sys.executable, "regression.py"],
                "issue_ids": [issue_id],
                "baseline_expected": "fail",
            }
        ],
        "overlays": [
            {"source": ".agentagon/regression.py", "path": "regression.py", "deliver": True}
        ],
    }
    return issue_id, spec


def test_audited_defect_repair_requires_exact_clean_origin_before_resolution(workspace, imported):
    issue_id, spec = prepare_defect(workspace, imported)
    original = git(workspace.root, "rev-parse", "HEAD")
    audit_before = workspace.audit_path(imported).read_bytes()
    started = engine.start(workspace, spec, "local")
    base = verify(workspace, started["run_id"], started["candidate_id"])
    assert base["candidate"]["state"] == "verified"
    assert base["candidate"]["checks"][0]["expected"] == "fail"
    assert base["frontier"] == []  # Reproduction does not make the baseline feasible.
    proposed = engine.new(
        workspace,
        started["run_id"],
        hypothesis="Return a recovered forecast",
        author="repair-author",
    )
    checkout = workspace.root / proposed["candidate"]["worktree"]
    (checkout / "app.py").write_text("def weather(city):\n    return 'sunny'\n")
    repaired = verify(workspace, started["run_id"], proposed["candidate_id"])
    assert repaired["candidate"]["state"] == "verified"
    selected = engine.select(workspace, started["run_id"], proposed["candidate_id"])
    assert list_issues(workspace)[0]["status"] == "open"
    assert git(workspace.root, "rev-parse", "HEAD") == original
    assert git(workspace.root, "status", "--porcelain") == ""
    event = {
        "issue_id": issue_id,
        "status": "resolved_verified",
        "reason": "Applied measured repair",
        "evidence": [],
    }
    with pytest.raises(AuditError, match="current origin differs"):
        update_issue(
            workspace, event, run_id=started["run_id"], candidate_id=proposed["candidate_id"]
        )

    # Only the fixture user applies the result; select itself never changes the origin.
    git(workspace.root, "switch", selected["selected_branch"])
    assert (workspace.root / "regression.py").is_file()
    (workspace.root / "unrelated.txt").write_text("uncommitted user work")
    with pytest.raises(AuditError, match="clean checkout"):
        update_issue(
            workspace, event, run_id=started["run_id"], candidate_id=proposed["candidate_id"]
        )
    (workspace.root / "unrelated.txt").unlink()
    issue = update_issue(
        workspace, event, run_id=started["run_id"], candidate_id=proposed["candidate_id"]
    )
    assert issue["status"] == "resolved_verified"
    receipt = workspace.read_artifact(issue["history"][-1]["verification"])
    assert receipt["provenance"] == "agentagon-engine"
    assert receipt["candidate_id"] == proposed["candidate_id"]
    assert len(receipt["trial_ids"]) == 3
    assert receipt["source_digest"] == git(workspace.root, "rev-parse", "HEAD^{tree}")
    repeated = update_issue(
        workspace, event, run_id=started["run_id"], candidate_id=proposed["candidate_id"]
    )
    assert repeated["history"] == issue["history"]
    assert workspace.audit_path(imported).read_bytes() == audit_before

    (workspace.root / "app.py").write_text("def weather(city):\n    return 'rainy'\n")
    git(workspace.root, "add", ".")
    git(
        workspace.root,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@localhost",
        "commit",
        "-qm",
        "Changed source",
    )
    with pytest.raises(AuditError, match="execute/review"):
        update_issue(
            workspace, event, run_id=started["run_id"], candidate_id=proposed["candidate_id"]
        )
    assert workspace.read_artifact(issue["history"][-1]["verification"]) == receipt


@pytest.mark.parametrize("failure_code", [2, 3, 4, 127])
def test_baseline_check_crash_does_not_establish_reproduction(workspace, imported, failure_code):
    _, spec = prepare_defect(workspace, imported)
    spec["repetitions"] = 1
    (workspace.state / "regression.py").write_text(f"raise SystemExit({failure_code})\n")
    started = engine.start(workspace, spec, "local")
    measured = engine.run(workspace, started["run_id"])
    assert not measured["candidate"]["checks"][0]["passed"]
    reviewed = engine.run(workspace, started["run_id"], review=passing_review(measured))
    assert reviewed["candidate"]["state"] == "rejected"
    assert reviewed["frontier"] == []


def test_handwritten_receipt_cannot_bypass_engine_even_with_linked_candidate(workspace, imported):
    issue_id, _ = prepare_defect(workspace, imported)
    fake = workspace.state / "fabricated.json"
    fake.write_text(json.dumps({"provenance": "agentagon-engine", "metrics": {"latency": 0}}))
    with pytest.raises(AuditError, match="manual verification"):
        update_issue(
            workspace,
            {
                "issue_id": issue_id,
                "status": "resolved_verified",
                "reason": "Fake receipt",
                "evidence": [],
            },
            fake,
            run_id="run_" + "a" * 24,
            candidate_id="candidate_" + "b" * 24,
        )
    assert list_issues(workspace)[0]["status"] == "open"
