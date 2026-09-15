"""Dataset audits retain evidence even when execution is unavailable."""

import json

import pytest
from click.testing import CliRunner
from support.evaluation import BUDGET, draft, review_for
from support.experiments import git

from agentagon.cli.main import main
from agentagon.core.records import AuditError, digest, identifier
from agentagon.dashboard import _benchmark_drafts
from agentagon.experiments import benchmarks, preparation
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace


def assessment(dataset_paths=None, entrypoint_paths=None):
    return {
        "goal": "Assess retry issue coverage and evaluation gaps",
        "author": "audit-host",
        "dataset_paths": dataset_paths or [],
        "entrypoint_paths": entrypoint_paths or [],
        "assessments": {
            topic: {
                "status": "unknown",
                "rationale": "Requires owner evidence and sensitivity validation.",
                "evidence": [],
            }
            for topic in benchmarks.ASSESSMENTS
        },
        "proposed_cases": ["Check a bounded retry followed by success."],
    }


def non_git(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    work = Workspace(root)
    work.initialize()
    (root / "cases.json").write_text('[{"input":"timeout","expected":"retry"}]')
    (root / "evaluate.py").write_text("raise RuntimeError('must not execute while auditing')\n")
    return work


def test_non_git_dataset_audit_pins_original_inputs_without_execution(tmp_path):
    work = non_git(tmp_path)
    supplied = assessment(["cases.json"], ["evaluate.py"])
    supplied["assessments"]["coverage"] = {
        "status": "concern",
        "rationale": "The dataset omits success after a retry.",
        "evidence": [{"path": "cases.json", "detail": "One timeout input; no successful retry."}],
    }
    result = benchmarks.draft(work, supplied)
    assert result["state"] == "draft" and result["measurement_status"] == "not_measured"
    assert {item["code"] for item in result["readiness"]["missing"]} == {
        "source_not_ready",
        "execution_plan_missing",
        "profile_missing",
        "budget_missing",
    }
    dataset = result["snapshot"]["files"][0]
    original = (work.root / dataset["artifact"]).read_bytes()
    assert original == (work.root / "cases.json").read_bytes()
    assert benchmarks.draft(work, supplied)["benchmark_id"] == result["benchmark_id"]
    (work.root / "cases.json").write_text("[]")
    current = benchmarks.status(work, result["benchmark_id"])
    assert "input_changed" in {item["code"] for item in current["readiness"]["missing"]}
    assert (work.root / dataset["artifact"]).read_bytes() == original


def test_dirty_source_and_missing_dataset_do_not_block_audit(workspace):
    (workspace.root / "app.py").write_text("uncommitted = True\n")
    supplied = assessment()
    result = benchmarks.draft(workspace, supplied)
    assert result["snapshot"]["files"] == []
    assert result["snapshot"]["assessment"]["proposed_cases"] == supplied["proposed_cases"]
    assert {item["code"] for item in result["readiness"]["missing"]} >= {
        "source_not_ready",
        "dataset_missing",
        "entrypoint_missing",
    }
    supplied["proposed_cases"] = []
    with pytest.raises(AuditError, match="missing dataset requires proposed cases"):
        benchmarks.draft(workspace, supplied)


def test_quality_claims_require_declared_evidence_and_unknown_fields_are_rejected(tmp_path):
    work = non_git(tmp_path)
    supplied = assessment(["cases.json"], ["evaluate.py"])
    supplied["assessments"]["coverage"]["status"] = "supported"
    with pytest.raises(AuditError, match="require pinned evidence"):
        benchmarks.draft(work, supplied)
    supplied["assessments"]["coverage"]["evidence"] = [
        {"path": "absent.json", "detail": "Unseen data"}
    ]
    with pytest.raises(AuditError, match="declared benchmark input"):
        benchmarks.draft(work, supplied)
    supplied = assessment(["cases.json"], ["evaluate.py"])
    supplied["quality_score"] = 99
    with pytest.raises(AuditError, match="unsupported fields"):
        benchmarks.draft(work, supplied)


def test_private_symlink_and_oversized_inputs_are_rejected(tmp_path):
    work = non_git(tmp_path)
    (work.root / "alias.json").symlink_to(work.root / "cases.json")
    with pytest.raises(AuditError, match="regular files"):
        benchmarks.draft(work, assessment(["alias.json"]))
    (work.root / ".env").write_text("KEY=value")
    with pytest.raises(AuditError, match="credential files"):
        benchmarks.draft(work, assessment([".env"]))
    (work.root / "large.json").write_bytes(b"x" * (benchmarks.MAX_FILE_BYTES + 1))
    with pytest.raises(AuditError, match="draft limit"):
        benchmarks.draft(work, assessment(["large.json"]))


def test_ready_draft_hands_off_to_existing_independent_freeze_gates(application, specification):
    (application.root / "cases.json").write_text('[{"minimum_quality":0.4}]')
    git(application.root, "add", "cases.json")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Add dataset",
    )
    specification["evaluation_paths"].append("cases.json")
    _, plan = draft(application, specification)
    supplied = assessment(["cases.json"], ["benchmark.py", "checks.py"])
    supplied["goal"], supplied["plan"] = specification["goal"], plan
    result = benchmarks.draft(application, supplied)
    ready = benchmarks.status(
        application, result["benchmark_id"], profile_name="local", budget=BUDGET
    )
    assert ready["readiness"] == {"state": "ready_for_preparation", "missing": []}
    started = benchmarks.prepare(
        application, result["benchmark_id"], "local", BUDGET, author="eval-host"
    )
    assert started["state"] == "draft" and started["usage"]["trials"] == 0
    repeated = benchmarks.prepare(
        application, result["benchmark_id"], "local", BUDGET, author="eval-host"
    )
    assert repeated["evaluation_id"] == started["evaluation_id"] and repeated["reused"]
    assert len(benchmarks.load(application, result["benchmark_id"])["evaluations"]) == 1
    changed_budget = benchmarks.prepare(
        application,
        result["benchmark_id"],
        "local",
        {**BUDGET, "max_trials": BUDGET["max_trials"] + 1},
        author="eval-host",
    )
    assert changed_budget["state"] == "not_ready"
    assert changed_budget["missing"][-1]["code"] == "preparation_changed"
    profile = Config().profile(application.root, "local")
    Config().update_profile(
        "project",
        "local",
        {**profile, "setup": [["echo", "changed profile"]]},
        application.root,
    )
    changed_profile = benchmarks.prepare(
        application, result["benchmark_id"], "local", BUDGET, author="eval-host"
    )
    assert changed_profile["state"] == "not_ready"
    Config().update_profile("project", "local", profile, application.root)
    assert started["inventory"]["benchmark_draft"]["snapshot_digest"] == result["snapshot_digest"]
    prep_cases = application.root / started["worktree"] / "cases.json"
    original_cases = prep_cases.read_bytes()
    prep_cases.write_text("[]")
    with pytest.raises(AuditError, match="cannot change the assessed dataset"):
        preparation.check(application, started["evaluation_id"], plan)
    prep_cases.write_bytes(original_cases)
    with pytest.raises(AuditError, match="passing baseline"):
        preparation.freeze(
            application,
            started["evaluation_id"],
            {
                "evaluation_id": started["evaluation_id"],
                "validation_id": "validation_missing",
                "validation_digest": "missing",
                "reviewer": "reviewer",
                "verdict": "pass",
                "rationale": "Not validated",
                "assessments": dict.fromkeys(preparation.ASSESSMENTS, True),
                "evidence": ["unvalidated-evidence"],
            },
        )
    checked = preparation.check(
        application, started["evaluation_id"], application.read_artifact(started["plan_artifact"])
    )
    assert (
        preparation.freeze(application, started["evaluation_id"], review_for(checked))["state"]
        == "frozen"
    )
    listed = benchmarks.list_drafts(application)
    assert listed[0]["state"] == "frozen"
    assert listed[0]["measurement_status"] == "baseline_recorded"
    reused = benchmarks.prepare(
        application, result["benchmark_id"], "local", BUDGET, author="eval-host"
    )
    assert reused["evaluation_id"] == started["evaluation_id"]
    assert reused["state"] == "frozen" and reused["usage"]["trials"] == checked["usage"]["trials"]
    assert len(benchmarks.load(application, result["benchmark_id"])["evaluations"]) == 1
    cases = application.root / "cases.json"
    original = cases.read_bytes()
    cases.write_text("[]")
    historical = benchmarks.status(application, result["benchmark_id"])
    assert historical["measurement_status"] == "historical_baseline"
    assert historical["measurement_revision"] == started["origin_revision"]
    assert {item["code"] for item in historical["readiness"]["missing"]} >= {
        "input_changed",
        "source_not_ready",
    }
    displayed = _benchmark_drafts(application)[0]
    assert displayed["measurement_status"] == "historical_baseline"
    assert displayed["validation_label"] == "Historical baseline; current inputs need preparation"
    assert "input_changed" in {item["code"] for item in displayed["readiness"]["missing"]}
    cases.write_bytes(original)
    app = application.root / "app.json"
    app.write_text(app.read_text().replace('"quality": 0.8', '"quality": 0.9'))
    git(application.root, "add", "app.json")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Change application revision",
    )
    historical = benchmarks.status(application, result["benchmark_id"])
    assert historical["measurement_status"] == "historical_baseline"
    assert historical["readiness"]["missing"] == [
        {
            "code": "source_changed",
            "action": "Create a new benchmark draft for the changed application revision.",
        }
    ]
    assert (
        benchmarks.prepare(
            application, result["benchmark_id"], "local", BUDGET, author="eval-host"
        )["state"]
        == "not_ready"
    )
    renewed = benchmarks.draft(application, supplied)
    assert renewed["benchmark_id"] != result["benchmark_id"]


def test_explicit_draft_renewal_and_lost_link_resume(application, specification, monkeypatch):
    _, plan = draft(application, specification)
    supplied = assessment(["checks.py"], ["benchmark.py"])
    supplied["goal"], supplied["plan"] = specification["goal"], plan
    saved = benchmarks.draft(application, supplied)
    original_write = application.write
    interrupted = False

    def lose_link(file, value):
        nonlocal interrupted
        if "benchmarks" in file.parts and value.get("evaluations") and not interrupted:
            interrupted = True
            raise OSError("interrupted after evaluation start")
        original_write(file, value)

    monkeypatch.setattr(application, "write", lose_link)
    with pytest.raises(OSError, match="interrupted after evaluation start"):
        benchmarks.prepare(application, saved["benchmark_id"], "local", BUDGET, author="eval-host")
    evaluation_files = list((application.state / "evaluations").glob("*/state.json"))
    resumed = benchmarks.prepare(
        application, saved["benchmark_id"], "local", BUDGET, author="eval-host"
    )
    assert resumed["reused"] and resumed["usage"]["trials"] == 0
    assert list((application.state / "evaluations").glob("*/state.json")) == evaluation_files
    assert len(benchmarks.load(application, saved["benchmark_id"])["evaluations"]) == 1
    renewed = benchmarks.draft(application, supplied, new=True)
    assert renewed["benchmark_id"] != saved["benchmark_id"]
    changed = benchmarks.prepare(
        application,
        renewed["benchmark_id"],
        "local",
        {**BUDGET, "max_trials": BUDGET["max_trials"] + 1},
        author="eval-host",
    )
    assert changed["evaluation_id"] != resumed["evaluation_id"]


def test_legacy_draft_without_source_revision_remains_readable_and_resumable(
    application, specification
):
    _, plan = draft(application, specification)
    supplied = assessment(["checks.py"], ["benchmark.py"])
    supplied["goal"], supplied["plan"] = specification["goal"], plan
    saved = benchmarks.draft(application, supplied)
    legacy = benchmarks.load(application, saved["benchmark_id"])
    legacy["snapshot"].pop("source_revision")
    legacy["snapshot_digest"] = digest(legacy["snapshot"])
    legacy["benchmark_id"] = identifier("benchmark", str(application.root), legacy["snapshot"])
    application.write(
        benchmarks._directory(application, legacy["benchmark_id"]) / "state.json", legacy
    )
    assert benchmarks.status(application, legacy["benchmark_id"])["state"] == "draft"
    started = benchmarks.prepare(
        application, legacy["benchmark_id"], "local", BUDGET, author="author"
    )
    repeated = benchmarks.prepare(
        application, legacy["benchmark_id"], "local", BUDGET, author="author"
    )
    assert started["evaluation_id"] == repeated["evaluation_id"]
    assert repeated["reused"]
    (application.root / "new-source.txt").write_text("New committed application input")
    git(application.root, "add", "new-source.txt")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Change application source",
    )
    current = benchmarks.status(application, legacy["benchmark_id"])
    assert "source_changed" in {item["code"] for item in current["readiness"]["missing"]}


def test_changed_draft_cannot_prepare_and_corrupted_evidence_cannot_load(application):
    supplied = assessment(["app.json"], ["benchmark.py"])
    result = benchmarks.draft(application, supplied)
    (application.root / "app.json").write_text("{}")
    blocked = benchmarks.prepare(
        application, result["benchmark_id"], "local", BUDGET, author="author"
    )
    assert blocked["state"] == "not_ready"
    assert "input_changed" in {item["code"] for item in blocked["missing"]}
    blob = application.root / result["snapshot"]["files"][0]["artifact"]
    blob.write_text("changed evidence")
    with pytest.raises(AuditError, match="input evidence changed"):
        benchmarks.load(application, result["benchmark_id"])


def test_cli_records_a_profileless_dataset_draft(tmp_path):
    work = non_git(tmp_path)
    supplied = tmp_path / "assessment.json"
    supplied.write_text(json.dumps(assessment(["cases.json"], ["evaluate.py"])))
    runner = CliRunner()
    prefix = ["--workspace", str(work.root), "benchmark"]
    saved = runner.invoke(main, [*prefix, "draft", "--assessment", str(supplied)])
    assert saved.exit_code == 0, saved.output
    benchmark_id = json.loads(saved.output)["benchmark_id"]
    shown = runner.invoke(main, [*prefix, "status", benchmark_id])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["measurement_status"] == "not_measured"
