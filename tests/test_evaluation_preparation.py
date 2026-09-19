"""A goal becomes an immutable, sensitivity-tested evaluation and verified fix run."""

import copy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from support.evaluation import BUDGET, draft, review_for
from support.experiments import git, passing_review, verify

from agentagon.capabilities.experiments import delivery, engine, preparation
from agentagon.cli.internal import main
from agentagon.core.records import AuditError


def test_existing_benchmark_freezes_then_drives_verified_fix(application, specification):
    original = git(application.root, "rev-parse", "HEAD")
    started, plan = draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    assert checked["state"] == "awaiting_review"
    assert checked["usage"]["trials"] == 6
    again = preparation.check(application, started["evaluation_id"], plan)
    assert again["usage"]["trials"] == 6
    assert again["checks"][-1]["validation_id"] == checked["checks"][-1]["validation_id"]
    frozen = preparation.freeze(application, started["evaluation_id"], review_for(again))
    assert frozen["state"] == "frozen"
    assert not any(file["deliver"] for file in frozen["package"]["files"])
    assert git(application.root, "rev-parse", "HEAD") == original
    assert not git(application.root, "status", "--porcelain")
    fix = engine.start(
        application, preparation.fix_spec(application, started["evaluation_id"]), "local"
    )
    measured = engine.run(application, fix["run_id"])
    result = engine.run(application, fix["run_id"], review=passing_review(measured))
    assert result["candidate"]["state"] == "verified"
    assert (
        preparation.freeze(application, started["evaluation_id"], review_for(again))[
            "package_digest"
        ]
        == frozen["package_digest"]
    )


def test_missing_benchmark_is_added_only_to_preparation_and_review_branch(
    application, specification
):
    benchmark = (application.root / "benchmark.py").read_text()
    checks = (application.root / "checks.py").read_text()
    git(application.root, "rm", "benchmark.py", "checks.py")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Remove fixture evaluator",
    )
    started, plan = draft(application, specification)
    prep = application.root / started["worktree"]
    (prep / "benchmark.py").write_text(benchmark)
    (prep / "checks.py").write_text(checks)
    plan["deliver_paths"] = ["checks.py"]
    checked = preparation.check(application, started["evaluation_id"], plan)
    frozen = preparation.freeze(application, started["evaluation_id"], review_for(checked))
    assert not (application.root / "benchmark.py").exists()
    assert set(
        git(
            application.root, "diff", "--name-only", "HEAD", frozen["package"]["review_branch"]
        ).splitlines()
    ) == {"benchmark.py", "checks.py"}
    fix = engine.start(
        application, preparation.fix_spec(application, started["evaluation_id"]), "local"
    )
    assert (
        verify(application, fix["run_id"], fix["candidate_id"])["candidate"]["state"] == "verified"
    )
    run = engine.status(application, fix["run_id"], fix["candidate_id"])
    sealed_files = git(
        application.root, "ls-tree", "--name-only", run["candidate"]["source_revision"]
    ).splitlines()
    assert "checks.py" in sealed_files
    assert "benchmark.py" not in sealed_files


def test_vacuous_or_crashed_check_cannot_prove_sensitivity(application, specification):
    started, plan = draft(application, specification)
    prep = application.root / started["worktree"]
    (prep / "checks.py").write_text("pass\n")
    checked = preparation.check(application, started["evaluation_id"], plan)
    assert checked["state"] == "check_failed"
    assert checked["checks"][-1]["trials"][-1]["state"] == "failed"
    with pytest.raises(AuditError, match="passing baseline"):
        preparation.freeze(application, started["evaluation_id"], review_for(checked))


def test_holdout_inputs_are_executed_but_excluded_from_review_and_delivery(
    application, specification
):
    started, plan = draft(application, specification)
    private = application.state / "holdout.json"
    private.write_text('{"minimum_quality":0.4}')
    plan["spec"]["inputs"] = [
        {"source": str(private.relative_to(application.root)), "path": "holdout.json"}
    ]
    plan["coverage"] = {
        "status": "holdout",
        "rationale": "A separate synthetic threshold fixture exercises frozen private inputs.",
        "holdout_paths": ["holdout.json"],
    }
    prep = application.root / started["worktree"]
    (prep / "checks.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "assert json.loads(Path('app.json').read_text())['quality'] >= "
        "json.loads(Path('holdout.json').read_text())['minimum_quality']\n"
    )
    checked = preparation.check(application, started["evaluation_id"], plan)
    assert checked["state"] == "awaiting_review"
    frozen = preparation.freeze(application, started["evaluation_id"], review_for(checked))
    files = {entry["path"]: entry for entry in frozen["package"]["files"]}
    assert files["holdout.json"]["kind"] == "inputs" and not files["holdout.json"]["deliver"]
    branch_files = git(
        application.root, "ls-tree", "-r", "--name-only", frozen["package"]["review_branch"]
    ).splitlines()
    assert "holdout.json" not in branch_files
    fix = engine.start(
        application, preparation.fix_spec(application, started["evaluation_id"]), "local"
    )
    assert (
        verify(application, fix["run_id"], fix["candidate_id"])["candidate"]["state"] == "verified"
    )
    assert not (application.root / fix["candidate"]["worktree"] / "holdout.json").exists()


def test_application_edits_ground_truth_and_protected_mutations_are_rejected(
    application, specification
):
    started, plan = draft(application, specification)
    evaluation_id = started["evaluation_id"]
    invalid = copy.deepcopy(plan)
    invalid["provenance"] = ""
    with pytest.raises(AuditError, match="provenance"):
        preparation.check(application, evaluation_id, invalid)
    invalid = copy.deepcopy(plan)
    invalid["negative_cases"][0]["mutations"][0]["path"] = "checks.py"
    with pytest.raises(AuditError, match="application edit scope"):
        preparation.check(application, evaluation_id, invalid)
    prep = application.root / started["worktree"]
    (prep / "app.json").write_text('{"quality":0.9}')
    with pytest.raises(AuditError, match="changed application source"):
        preparation.check(application, evaluation_id, plan)
    assert preparation.load(application, evaluation_id)["usage"]["trials"] == 0


def test_review_must_be_independent_current_and_based_on_unchanged_benchmark(
    application, specification
):
    started, plan = draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    review = review_for(checked)
    with pytest.raises(AuditError, match="independent"):
        preparation.freeze(
            application, started["evaluation_id"], {**review, "reviewer": "benchmark-author"}
        )
    with pytest.raises(AuditError, match="stale"):
        preparation.freeze(
            application, started["evaluation_id"], {**review, "validation_digest": "foreign"}
        )
    prep = application.root / started["worktree"]
    (prep / "checks.py").write_text("pass\n")
    with pytest.raises(AuditError, match="changed after validation"):
        preparation.freeze(application, started["evaluation_id"], review)


def test_new_source_requires_new_draft_and_old_package_cannot_change(application, specification):
    started, plan = draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    preparation.freeze(application, started["evaluation_id"], review_for(checked))
    with pytest.raises(AuditError, match="cannot change"):
        preparation.check(application, started["evaluation_id"], plan)
    (application.root / "new.py").write_text("x = 1\n")
    git(application.root, "add", "new.py")
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
    with pytest.raises(AuditError, match="provenance changed"):
        preparation.fix_spec(application, started["evaluation_id"])
    renewed = preparation.start(
        application, "local", BUDGET, author="new-author", from_id=started["evaluation_id"]
    )
    assert renewed["evaluation_id"] != started["evaluation_id"]
    assert renewed["state"] == "draft" and renewed["usage"]["trials"] == 0
    assert renewed["origin_revision"] != started["origin_revision"]


def test_dataset_repair_deletes_scoped_files_in_a_new_version(application, specification):
    (application.root / "duplicate-case.json").write_text('{"quality":0.8}')
    git(application.root, "add", "duplicate-case.json")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Add duplicate dataset case",
    )
    specification["evaluation_paths"].extend(["duplicate-case.json", "new-duplicate.json"])
    original, plan = draft(application, specification)
    (application.root / original["worktree"] / "new-duplicate.json").write_text('{"quality":0.8}')
    checked = preparation.check(application, original["evaluation_id"], plan)
    frozen = preparation.freeze(application, original["evaluation_id"], review_for(checked))
    renewed = preparation.start(
        application,
        "local",
        BUDGET,
        author="dataset-repair-author",
        from_id=original["evaluation_id"],
    )
    prep = application.root / renewed["worktree"]
    (prep / "duplicate-case.json").unlink()
    (prep / "new-duplicate.json").unlink()
    plan["deliver_paths"] = ["duplicate-case.json", "new-duplicate.json"]
    updated = preparation.check(application, renewed["evaluation_id"], plan)
    revised = preparation.freeze(application, renewed["evaluation_id"], review_for(updated))
    assert (
        preparation.load(application, original["evaluation_id"])["package_digest"]
        == frozen["package_digest"]
    )
    assert (application.root / "duplicate-case.json").exists()
    assert (
        "duplicate-case.json"
        not in git(
            application.root, "ls-tree", "-r", "--name-only", revised["package"]["review_branch"]
        ).splitlines()
    )
    comparison = revised["package"]["version_comparison"]
    assert comparison["same_application_revision"]
    assert comparison["changed_paths"] == ["duplicate-case.json", "new-duplicate.json"]
    assert revised["package"]["spec"]["deletions"] == [
        {"path": "duplicate-case.json", "deliver": True},
        {"path": "new-duplicate.json", "deliver": True},
    ]
    delivered = delivery.deliver(application, evaluation_id=renewed["evaluation_id"])
    assert delivered["state"] == "prepared"
    patch = Path(delivered["artifacts"]["diff"]).read_text()
    assert "deleted file mode" in patch and "a/duplicate-case.json" in patch
    fix = engine.start(
        application, preparation.fix_spec(application, renewed["evaluation_id"]), "local"
    )
    run = engine.status(application, fix["run_id"], fix["candidate_id"])
    assert not (application.root / run["candidate"]["worktree"] / "duplicate-case.json").exists()
    assert (
        verify(application, fix["run_id"], fix["candidate_id"])["candidate"]["state"] == "verified"
    )
    again = preparation.start(
        application, "local", BUDGET, author="next-author", from_id=renewed["evaluation_id"]
    )
    assert not (application.root / again["worktree"] / "duplicate-case.json").exists()


def test_preparation_cannot_delete_application_files(application, specification):
    started, plan = draft(application, specification)
    (application.root / started["worktree"] / "app.json").unlink()
    with pytest.raises(AuditError, match="changed application source"):
        preparation.check(application, started["evaluation_id"], plan)
    plan["spec"]["deletions"] = [{"path": "app.json"}]
    with pytest.raises(AuditError, match="explicitly named evaluation paths"):
        preparation.check(application, started["evaluation_id"], plan)
    plan["spec"]["deletions"] = [{"path": "checks.py", "kind": "inputs", "deliver": True}]
    with pytest.raises(AuditError, match="private input deletions"):
        preparation.check(application, started["evaluation_id"], plan)


@pytest.mark.parametrize("retain_scope", [False, True])
def test_retired_private_input_never_becomes_a_public_deletion(
    application, specification, retain_scope
):
    started, plan = draft(application, specification)
    private = application.state / "private-cases.json"
    private.write_text('[{"expected":"private fixture"}]')
    plan["spec"]["inputs"] = [
        {"source": str(private.relative_to(application.root)), "path": "private-cases.json"}
    ]
    checked = preparation.check(application, started["evaluation_id"], plan)
    preparation.freeze(application, started["evaluation_id"], review_for(checked))
    renewed = preparation.start(
        application,
        "local",
        BUDGET,
        author="dataset-repair-author",
        from_id=started["evaluation_id"],
    )
    prep = application.root / renewed["worktree"]
    (prep / "private-cases.json").unlink()
    # Keep one visible evaluator correction so its local delivery can be inspected.
    (prep / "checks.py").write_text(
        (prep / "checks.py").read_text() + "# Corrected evaluator documentation\n"
    )
    plan["spec"]["inputs"] = []
    if retain_scope:
        plan["spec"]["evaluation_paths"].append("private-cases.json")
    checked = preparation.check(application, renewed["evaluation_id"], plan)
    frozen = preparation.freeze(application, renewed["evaluation_id"], review_for(checked))
    removed = [
        entry for entry in frozen["package"]["files"] if entry["path"] == "private-cases.json"
    ]
    assert removed == (
        [{"path": "private-cases.json", "deleted": True, "kind": "inputs", "deliver": False}]
        if retain_scope
        else []
    )
    if retain_scope:
        assert preparation.fix_spec(application, renewed["evaluation_id"])["deletions"] == [
            {"path": "private-cases.json", "deliver": False, "kind": "inputs"}
        ]
    delivered = delivery.deliver(application, evaluation_id=renewed["evaluation_id"])
    for key in ("diff", "summary", "pr_body"):
        assert "private-cases.json" not in Path(delivered["artifacts"][key]).read_text()


def test_preparation_budget_cannot_be_spent_twice_by_retry(application, specification):
    started, plan = draft(application, specification, budget={**BUDGET, "max_trials": 1})
    for _ in range(2):
        with pytest.raises(AuditError, match="budget exhausted"):
            preparation.check(application, started["evaluation_id"], plan)
    data = preparation.load(application, started["evaluation_id"])
    assert data["usage"]["trials"] == 1
    assert len(data["checks"]) == 1


def test_cli_routes_missing_benchmark_and_frozen_package(application, specification, tmp_path):
    runner = CliRunner()
    prefix = ["--workspace", str(application.root)]
    result = runner.invoke(
        main, [*prefix, "fix", "start", "--profile", "local", "--goal", "Improve quality"]
    )
    assert json.loads(result.output)["state"] == "needs_evaluation"
    started, plan = draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    preparation.freeze(application, started["evaluation_id"], review_for(checked))
    result = runner.invoke(
        main,
        [*prefix, "fix", "start", "--profile", "local", "--evaluation", started["evaluation_id"]],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["run_id"]
