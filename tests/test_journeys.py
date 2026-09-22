"""Shared accepted intent remains versioned even before execution is possible."""

import copy
import json

import pytest
from click.testing import CliRunner
from test_scoring import definition

from agentagon.capabilities.experiments import journeys
from agentagon.cli.internal import main
from agentagon.core.records import AuditError
from agentagon.storage.workspace import Workspace


def intent_definition():
    return {
        "version": 1,
        "goal": "Improve quality with a latency limit",
        "accepted_by": "user",
        "scoring": definition(),
        "discovery": {
            "status": "missing",
            "paths": [],
            "evidence": ["No evaluator in repository inventory"],
            "creation_authorized": False,
        },
        "budget": {"max_trials": 24, "max_elapsed_seconds": 600, "trial_timeout_seconds": 10},
    }


def test_intent_versions_survive_non_git_discovery_and_do_not_authorize_creation(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.initialize()
    intent = journeys.save(workspace, intent_definition())
    assert not intent["definition"]["discovery"]["creation_authorized"]
    assert journeys.save(workspace, intent_definition())["intent_id"] == intent["intent_id"]
    revised = copy.deepcopy(intent_definition())
    revised["scoring"]["metrics"]["quality"]["weight"] = 3
    assert journeys.save(workspace, revised)["intent_id"] != intent["intent_id"]
    assert len(journeys.status(workspace)["intents"]) == 2
    assert journeys.invitation(workspace) == {"show": False}


def test_saved_intent_tampering_rejected(application):
    saved = journeys.save(application, intent_definition())
    path = journeys.directory(application, "intents", saved["intent_id"]) / "state.json"
    data = json.loads(path.read_text())
    data["definition"]["goal"] = "different"
    path.write_text(json.dumps(data))
    with pytest.raises(AuditError, match="identity"):
        journeys.load(application, saved["intent_id"])


def test_intent_cli_records_validated_definition(application):
    file = application.state / "intent.json"
    file.write_text(json.dumps(intent_definition()))
    result = CliRunner().invoke(
        main, ["--workspace", str(application.root), "journey", "save", "--file", str(file)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["intent_id"].startswith("intent_")


def test_eval_creation_gate_and_first_baseline_share_the_overall_budget(application, specification):
    from support.evaluation import draft, review_for
    from test_baselines import complete

    from agentagon.capabilities.experiments import baselines, preparation
    from agentagon.capabilities.experiments.budget import BudgetLedger

    definition = intent_definition()
    definition["goal"] = specification["goal"]
    specification["scoring"] = definition["scoring"]
    pending = journeys.save(application, definition)
    with pytest.raises(AuditError, match="confirmation"):
        draft(application, specification, intent_id=pending["intent_id"])
    definition["discovery"]["creation_authorized"] = True
    accepted = journeys.save(application, definition)
    started, plan = draft(application, specification, intent_id=accepted["intent_id"])
    checked = preparation.check(application, started["evaluation_id"], plan)
    evaluated = preparation.freeze(application, started["evaluation_id"], review_for(checked))
    bound = journeys.save(application, definition, evaluated["evaluation_id"])
    initial = baselines.start(application, evaluated["evaluation_id"], bound["intent_id"])
    assert initial["budget_id"] == started["evaluation_id"]
    # The first measured baseline uses the same intent budget as authoring/sensitivity.
    waiting = baselines.advance(application, initial["baseline_id"])
    from support.experiments import passing_review

    from agentagon.capabilities.experiments.host_bridge import HostBridge

    bridge = HostBridge(application, waiting["budget_id"])
    request = next(r for r in bridge.pending() if r["role"] == "review")
    bridge.start(request["request_id"])
    bridge.reply(
        request["request_id"],
        passing_review({"review_template": request["payload"]["review_template"]}),
        host=request["host"],
        model=request["model"],
        binding_digest=request["binding_digest"],
    )
    assert baselines.advance(application, initial["baseline_id"])["state"] == "completed"
    ledger = BudgetLedger(application, initial["budget_id"])
    assert ledger.spent(ledger.snapshot()) == 9
    # A later explicit rerun is a separate baseline-only journey and budget.
    repeated = baselines.rerun(application, initial["baseline_id"])
    assert repeated["budget_id"] == repeated["execution_run_id"]
    complete(application, repeated)
