"""Engine recovery, cancellation, and capacity use real workers and clean Git fixtures."""

import copy

import pytest
from support.experiments import baseline, executions, passing_review, propose
from support.recovery import gate_path, install_gate, invocation, wait_for_gate

from agentagon.core.records import AuditError
from agentagon.experiments import engine, runners
from agentagon.storage.config import Config


def test_stop_and_explicit_continue_preserve_completed_repetitions_and_cancelled_evidence(
    application, specification
):
    install_gate(
        application, "application['variant'] == 'paused' and int(os.environ['AGENTAGON_SEED']) == 1"
    )
    started = baseline(application, specification)
    run_id = started["run_id"]
    candidate, _ = propose(application, run_id, variant="paused")
    candidate_id = candidate["candidate_id"]
    with invocation(application, run_id, candidate_id) as (thread, outcome):
        assert wait_for_gate() == {"seed": 1, "variant": "paused"}
        running = engine.status(application, run_id, candidate_id)
        assert [trial["state"] for trial in running["candidate"]["trials"]] == [
            "completed",
            "running",
        ]
        before_usage = copy.deepcopy(running["usage"])
        original_completed_id = running["candidate"]["trials"][0]["trial_id"]
        cancelled_id = running["candidate"]["trials"][1]["trial_id"]
        stopped = engine.stop(application, run_id)
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert "error" not in outcome, outcome
        stopped_candidate = engine.status(application, run_id, candidate_id)["candidate"]
        assert stopped["state"] == "stopped"
        assert [trial["state"] for trial in stopped_candidate["trials"]] == [
            "completed",
            "cancelled",
        ]
        cancelled = stopped_candidate["trials"][1]
        assert application.read_artifact(cancelled["artifact"])["state"] == "cancelled"
        assert not (application.root / cancelled["source_path"]).exists()
        # A normal repeat invocation cannot silently authorize replacement execution.
        with pytest.raises(AuditError, match="stopped"):
            engine.run(application, run_id, candidate_id)
        untouched = engine.status(application, run_id, candidate_id)
        assert untouched["usage"]["trials"] == before_usage["trials"]
        assert [item["seed"] for item in executions() if item["variant"] == "paused"] == [0, 1]
    resumed = engine.run(application, run_id, candidate_id, continue_run=True)
    trials = resumed["candidate"]["trials"]
    assert resumed["candidate"]["state"] == "awaiting_review"
    assert [trial["state"] for trial in trials] == [
        "completed",
        "cancelled",
        "completed",
        "completed",
    ]
    assert [trial["repetition"] for trial in trials] == [0, 1, 1, 2]
    assert trials[0]["trial_id"] == original_completed_id
    assert trials[1]["trial_id"] == cancelled_id
    assert len({trial["trial_id"] for trial in trials}) == 4
    assert resumed["usage"]["trials"] == before_usage["trials"] + 2
    assert resumed["usage"]["candidates"] == before_usage["candidates"]
    assert [item["seed"] for item in executions() if item["variant"] == "paused"] == [0, 1, 1, 2]
    assert resumed["review_template"]["trial_ids"] == [trial["trial_id"] for trial in trials]
    assert cancelled["artifact"] in resumed["review_template"]["evidence"]
    reviewed = engine.run(application, run_id, candidate_id, review=passing_review(resumed))
    assert reviewed["candidate"]["state"] == "verified"
    assert len(reviewed["candidate"]["variation"]["latency"]["samples"]) == 3


def test_concurrent_candidates_share_trial_capacity_and_same_candidate_has_one_driver(
    application, specification
):
    install_gate(
        application, "application['variant'] == 'first' and int(os.environ['AGENTAGON_SEED']) == 0"
    )
    profile = Config().profile(application.root, "local")
    profile["limits"]["parallel_candidates"] = 2
    Config().update_profile("project", "local", profile, application.root)
    started = baseline(application, specification)
    run_id = started["run_id"]
    first, _ = propose(application, run_id, variant="first", latency=80)
    second, _ = propose(application, run_id, variant="second", latency=70)
    first_id, second_id = first["candidate_id"], second["candidate_id"]
    with invocation(application, run_id, first_id) as (thread, outcome):
        assert wait_for_gate() == {"seed": 0, "variant": "first"}
        with pytest.raises(AuditError, match="active fix run invocation"):
            engine.run(application, run_id, first_id)
        with pytest.raises(AuditError, match="measurement capacity is occupied"):
            engine.run(application, run_id, second_id)
        blocked = engine.status(application, run_id, second_id)
        assert blocked["candidate"]["trials"] == []
        assert blocked["usage"]["trials"] == 4
        assert [entry["variant"] for entry in executions()] == ["baseline"] * 3 + ["first"]
        gate_path(".release").touch()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert "error" not in outcome, outcome
        assert outcome["result"]["candidate"]["state"] == "awaiting_review"
    measured = engine.run(application, run_id, second_id)
    assert measured["candidate"]["state"] == "awaiting_review"
    assert measured["usage"]["trials"] == 9
    assert [entry["variant"] for entry in executions()] == ["baseline"] * 3 + ["first"] * 3 + [
        "second"
    ] * 3
    assert len(engine.status(application, run_id, first_id)["candidate"]["trials"]) == 3


def test_unknown_delivery_reconciles_same_real_trial_when_budget_is_full(
    application, specification, monkeypatch
):
    profile = Config().profile(application.root, "local")
    profile["limits"]["max_trials"] = 3
    Config().update_profile("project", "local", profile, application.root)
    started = engine.start(application, specification, "local")
    run_id, candidate_id = started["run_id"], started["candidate_id"]
    real_execute = runners.execute
    calls = []
    lost_delivery = []

    def execute_with_lost_delivery(profile, source, attempt_dir, request):
        calls.append((request["attempt_id"], str(attempt_dir)))
        result = real_execute(profile, source, attempt_dir, request)
        if request["seed"] == 2 and not lost_delivery:
            lost_delivery.append(request["attempt_id"])
            # The process completed, but this client lost its response. Its durable record remains.
            return {
                **result,
                "state": "interrupted",
                "finalized": False,
                "results": [],
                "benchmark_output": None,
                "remote_state": "unknown",
            }
        return result

    monkeypatch.setattr(runners, "execute", execute_with_lost_delivery)
    interrupted = engine.run(application, run_id, candidate_id)
    assert interrupted["candidate"]["state"] == "interrupted"
    assert interrupted["usage"]["trials"] == 3
    trials_before = copy.deepcopy(interrupted["candidate"]["trials"])
    assert [trial["state"] for trial in trials_before] == ["completed", "completed", "interrupted"]
    assert [entry["seed"] for entry in executions()] == [0, 1, 2]
    recovered = engine.run(application, run_id, candidate_id)
    assert recovered["candidate"]["state"] == "awaiting_review"
    assert recovered["usage"]["trials"] == 3
    assert [trial["trial_id"] for trial in recovered["candidate"]["trials"]] == [
        trial["trial_id"] for trial in trials_before
    ]
    assert calls[-1] == calls[-2]
    assert [entry["seed"] for entry in executions()] == [0, 1, 2]
    reviewed = engine.run(application, run_id, candidate_id, review=passing_review(recovered))
    assert reviewed["candidate"]["state"] == "verified"
