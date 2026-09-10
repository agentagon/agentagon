"""Saved issue evidence follows a draft without becoming executable ground truth."""

import copy
import json

import pytest
from click.testing import CliRunner
from support.audit import finish
from support.dashboard import make_audit
from support.evaluation import BUDGET

from agentagon.cli.main import main
from agentagon.core.records import AuditError, digest
from agentagon.experiments import preparation
from agentagon.operations import findings
from agentagon.storage.config import Config
from agentagon.storage.issues import list_issues


def test_cli_handoff_retains_selected_evidence_and_requires_expectation_review(
    workspace, imported, application, tmp_path
):
    finish(workspace, imported)
    issue = list_issues(workspace)[0]["issue_id"]
    Config().update_profile(
        "project", "local", Config().profile(application.root, "local"), workspace.root
    )
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps(BUDGET))
    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(workspace.root),
            "eval",
            "start",
            "--audit",
            imported,
            "--issue",
            issue,
            "--profile",
            "local",
            "--budget-file",
            str(budget),
            "--author",
            "test-author",
        ],
    )
    assert result.exit_code == 0, result.output
    draft = json.loads(result.output)
    selected = draft["inventory"]["selected_evidence"]
    assert draft["usage"]["trials"] == 0
    assert draft["state"] == "draft"
    assert selected["audit_id"] == imported
    assert selected["issue_ids"] == [issue]
    source = findings(workspace.read_audit(imported))[0]
    case = selected["findings"][0]
    assert case["observed_behavior"] == source["observation"]
    assert case["proposed_expected_behavior"] == source["expected_behavior"]
    assert case["expectation_status"] == "needs_review"
    assert case["evidence"] == source["evidence"]
    assert selected["trace_digests"]
    assert "ordinary successful" in selected["case_requirements"][1]
    assert selected["selection_digest"] == digest(
        {k: v for k, v in selected.items() if k != "selection_digest"}
    )
    # Later audit updates cannot silently change the draft's selected observations.
    audit = workspace.read_audit(imported)
    for diagnosis in audit["diagnoses"].values():
        diagnosis["findings"][0]["observation"] = "Later interpretation"
    workspace.save_audit(audit)
    assert (
        preparation.load(workspace, draft["evaluation_id"])["inventory"]["selected_evidence"]
        == selected
    )


def test_handoff_rejects_issue_from_another_audit_before_creating_a_draft(
    workspace, imported, application
):
    finish(workspace, imported)
    issue = list_issues(workspace)[0]["issue_id"]
    unrelated = make_audit(workspace)
    Config().update_profile(
        "project", "local", Config().profile(application.root, "local"), workspace.root
    )
    with pytest.raises(AuditError, match="issues grouped in that saved audit"):
        preparation.start(
            workspace, "local", BUDGET, author="test-author", issue_ids=[issue], audit_id=unrelated
        )
    assert not (workspace.state / "evaluations").exists()


def test_selected_evidence_is_bound_to_validation_and_frozen_package(application, specification):
    from support.evaluation import draft, review_for

    started, plan = draft(application, specification)
    # A representative saved selection tests the review binding independently of trace imports.
    selected = {"audit_id": "audit-test", "findings": [{"expectation_status": "needs_review"}]}
    selected["selection_digest"] = digest(selected)
    state_path = preparation.directory(application, started["evaluation_id"]) / "state.json"
    state = json.loads(state_path.read_text())
    state["inventory"]["selected_evidence"] = selected
    application.write(state_path, state)
    checked = preparation.check(application, started["evaluation_id"], plan)
    review = review_for(checked)
    modified = copy.deepcopy(checked)
    changed = modified["inventory"]["selected_evidence"]
    changed["findings"][0]["expectation_status"] = "unreviewed_change"
    changed["selection_digest"] = digest(
        {k: v for k, v in changed.items() if k != "selection_digest"}
    )
    application.write(state_path, modified)
    with pytest.raises(AuditError, match="changed after validation"):
        preparation.freeze(application, started["evaluation_id"], review)
    application.write(state_path, checked)
    frozen = preparation.freeze(application, started["evaluation_id"], review)
    assert frozen["package"]["selected_evidence"] == selected
    renewed = preparation.start(
        application, "local", BUDGET, author="next-author", from_id=started["evaluation_id"]
    )
    assert renewed["inventory"]["selected_evidence"] == selected
