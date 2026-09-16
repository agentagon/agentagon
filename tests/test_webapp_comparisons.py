"""Baseline comparisons retain compatibility, private evidence and read-only behavior."""

import copy
import json

import pytest
from support.evaluation import draft, review_for
from support.experiments import executions, git
from test_baselines import complete, frozen

from agentagon.core.records import AuditError
from agentagon.experiments import baselines, engine, preparation
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace
from agentagon.webapp.comparisons import compare


@pytest.fixture
def measured_pair(application, specification):
    specification["repetitions"] = 1
    evaluator = frozen(application, specification)
    left = complete(application, baselines.start(application, evaluator["evaluation_id"]))
    (application.root / "app.json").write_text(
        json.dumps({"latency": 80, "quality": 0.9, "variant": "improved"})
    )
    git(application.root, "add", "app.json")
    git(
        application.root,
        "-c",
        "user.name=Comparison Tests",
        "-c",
        "user.email=tests@localhost",
        "commit",
        "-qm",
        "Improve application",
    )
    right = complete(application, baselines.rerun(application, left["baseline_id"]))
    return left, right


def test_compatible_comparison_is_read_only_and_trace_populations_stay_separate(
    application, measured_pair, monkeypatch
):
    left, right = measured_pair
    right["recent_traces"].update(state="partial", count=2, completeness="partial")
    baselines._save(application, right)
    before = {path: path.read_bytes() for path in application.state.rglob("*") if path.is_file()}
    trials = len(executions())

    def forbidden(*_args, **_kwargs):
        pytest.fail("comparison must not start or resume execution")

    monkeypatch.setattr(baselines, "advance", forbidden)
    monkeypatch.setattr(baselines, "start", forbidden)
    monkeypatch.setattr(engine, "run", forbidden)
    result = compare(application, left["baseline_id"], right["baseline_id"])
    assert result["compatible"] and not result["compatibility"]["same_source_revision"]
    assert result["left"] == baselines.public_projection(left)
    assert result["right"] == baselines.public_projection(right)
    assert result["benchmark"]["state"] == "measured"
    assert result["benchmark"]["delta"] == pytest.approx(0.4)
    assert result["benchmark"]["left_eligible"] is True
    assert result["recent_traces"]["controlled_comparison"] is False
    assert result["recent_traces"]["right"]["completeness"] == "partial"
    assert result["recent_traces"]["left"]["window"] == left["recent_traces"]["window"]
    assert "delta" not in result["recent_traces"]
    assert "profile" not in result["left"] and "measurement" not in result["left"]
    assert "artifacts" not in json.dumps(result)
    assert trials == len(executions())
    assert before == {
        path: path.read_bytes() for path in application.state.rglob("*") if path.is_file()
    }
    reverse = compare(application, right["baseline_id"], left["baseline_id"])
    assert reverse["benchmark"]["delta"] == pytest.approx(-0.4)


@pytest.mark.parametrize("state", ["ready", "host_pending", "failed"])
def test_comparison_rejects_unfinished_or_failed_records(application, measured_pair, state):
    left, right = measured_pair
    right["state"] = state
    baselines._save(application, right)
    with pytest.raises(AuditError, match="completed"):
        compare(application, left["baseline_id"], right["baseline_id"])


def test_comparison_rejects_modified_score_and_unverified_evidence(application, measured_pair):
    left, right = measured_pair
    changed = copy.deepcopy(right)
    changed["benchmark_score"] = {**changed["benchmark_score"], "value": 999}
    baselines._save(application, changed)
    with pytest.raises(AuditError, match="score or measurement"):
        compare(application, left["baseline_id"], right["baseline_id"])
    baselines._save(application, right)
    from agentagon.experiments.store import load_run, save_run

    run = load_run(application, right["execution_run_id"])
    run["candidates"][run["baseline_id"]]["state"] = "awaiting_review"
    save_run(application, run)
    with pytest.raises(AuditError, match="passing review"):
        compare(application, left["baseline_id"], right["baseline_id"])


def test_comparison_rejects_a_different_execution_profile(application, measured_pair):
    left, _ = measured_pair
    profile = Config().profile(application.root, "local")
    profile["limits"]["max_elapsed_seconds"] += 1
    right = complete(
        application,
        baselines.start(application, left["evaluation_id"], execution_profile=profile),
    )
    with pytest.raises(AuditError, match="execution profiles or limits"):
        compare(application, left["baseline_id"], right["baseline_id"])


def test_comparison_rejects_a_new_scoring_definition(application, specification, measured_pair):
    left, _ = measured_pair
    # Frozen evaluations use a copy; modifying the next definition leaves the old one intact.
    specification["scoring"]["metrics"]["quality"]["weight"] = 3
    started, plan = draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    evaluator = preparation.freeze(application, started["evaluation_id"], review_for(checked))
    right = complete(application, baselines.start(application, evaluator["evaluation_id"]))
    with pytest.raises(AuditError, match="evaluator definitions differ"):
        compare(application, left["baseline_id"], right["baseline_id"])


def test_unscored_completed_baselines_do_not_become_numeric_zero(application, specification):
    specification["repetitions"] = 1
    started, plan = draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    evaluator = preparation.freeze(application, started["evaluation_id"], review_for(checked))
    left = complete(application, baselines.start(application, evaluator["evaluation_id"]))
    right = complete(application, baselines.rerun(application, left["baseline_id"]))
    result = compare(application, left["baseline_id"], right["baseline_id"])
    assert result["compatibility"]["same_source_revision"]
    assert result["benchmark"]["state"] == "unscored"
    assert result["benchmark"]["left"] is None
    assert result["benchmark"]["right"] is None
    assert result["benchmark"]["delta"] is None


def test_comparison_rejects_same_record_and_foreign_checkout(application, measured_pair, tmp_path):
    left, right = measured_pair
    with pytest.raises(AuditError, match="two different"):
        compare(application, left["baseline_id"], left["baseline_id"])
    other = tmp_path / "other"
    other.mkdir()
    workspace = Workspace(other)
    workspace.initialize()
    baselines._save(workspace, left)
    baselines._save(workspace, right)
    with pytest.raises(AuditError, match="identity changed"):
        compare(workspace, left["baseline_id"], right["baseline_id"])
