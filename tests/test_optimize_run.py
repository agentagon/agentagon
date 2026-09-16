"""Native optimizer proposals execute in the existing bounded application engine."""

import json

import pytest
from support.experiments import executions, passing_review
from support.optimizer import candidate_text, proposal_response
from test_scoring import definition

from agentagon.core.records import AuditError
from agentagon.experiments import engine, optimize_run
from agentagon.experiments.store import load_run


def _start(
    application,
    specification,
    optimizer="gepa",
    target=None,
    repetitions=1,
    max_trials=None,
    finalist_count=3,
    host_concurrency=1,
    background="",
):
    specification["scoring"] = definition()
    if target is not None:
        specification["scoring"]["target"] = target
    specification["repetitions"] = repetitions
    specification["seeds"] = list(range(repetitions))
    started = engine.start(application, specification, "local")
    optimize_run.configure(
        application,
        started["run_id"],
        host="fake-codex",
        model="fake-model",
        optimizer=optimizer,
        max_trials=max_trials,
        finalist_count=finalist_count,
        host_concurrency=host_concurrency,
        background=background,
    )
    return started["run_id"]


def _host(request):
    if request["role"] == "review":
        return {
            "review": passing_review({"review_template": request["payload"]["review_template"]})
        }
    proposal = json.loads(candidate_text(request))
    app = json.loads(proposal["files"]["app.json"])
    app["quality"] += 0.01
    app["latency"] -= 1
    app["variant"] = f"optimized-{app['quality']}"
    proposal["files"]["app.json"] = json.dumps(app)
    return proposal_response(request, json.dumps(proposal))


@pytest.mark.parametrize("optimizer", ["gepa", "omni", "autoresearch", "meta_harness"])
def test_application_optimizer_keeps_baseline_pending_then_verifies_winner(
    application, specification, optimizer
):
    run_id = _start(application, specification, optimizer)
    pending = optimize_run.advance(application, run_id)
    assert pending["state"] == "host_pending"
    assert pending["pending"][0]["role"] == "review"
    assert len(executions()) == 1
    result = optimize_run.advance(application, run_id, host_handler=_host)
    assert result["state"] == "completed", result
    winner = result["selection"]["winner"]
    assert winner is not None
    data = load_run(application, run_id)
    selected = data["candidates"][winner["candidate_id"]]
    assert selected["verification_of"]
    assert selected["budget_stage"] == "verification"
    assert selected["state"] == "verified"
    assert (
        selected["source_digest"]
        == data["candidates"][selected["verification_of"]]["source_digest"]
    )
    assert len(executions()) == sum(op["units"] for op in result["budget"]["operations"].values())
    before = len(executions())
    assert optimize_run.advance(application, run_id, host_handler=_host)["state"] == "completed"
    assert len(executions()) == before


def test_application_proposal_cannot_touch_evaluator(application, specification):
    run_id = _start(application, specification)

    def invalid_host(request):
        if request["role"] == "review":
            return _host(request)
        return proposal_response(request, json.dumps({"files": {"benchmark.py": "print('fake')"}}))

    result = optimize_run.advance(application, run_id, host_handler=invalid_host)
    assert result["state"] == "completed"
    assert result["selection"]["retained_baseline"]
    assert len(executions()) == 1
    data = load_run(application, run_id)
    assert len(data["candidates"]) == 1
    failed = [op for op in result["budget"]["operations"].values() if op["status"] == "failed"]
    assert "protected" in failed[-1]["result"]["error"]


@pytest.mark.parametrize("max_trials", [19, 20])
def test_repeated_trials_leave_room_for_every_omni_stage(application, specification, max_trials):
    from agentagon.experiments.budget import BudgetLedger

    run_id = _start(
        application, specification, "omni", repetitions=3, max_trials=max_trials, finalist_count=1
    )
    assert optimize_run.advance(application, run_id)["state"] == "host_pending"
    stages = []

    def host(request):
        response = _host(request)
        if request["role"] == "proposal":
            stages.append(request["payload"]["stage"])
            proposal = json.loads(
                response.get("candidate")
                or response["text"].removeprefix("```\n").removesuffix("\n```")
            )
            app = json.loads(proposal["files"]["app.json"])
            app["variant"] = request["request_id"]
            proposal["files"]["app.json"] = json.dumps(app)
            response = proposal_response(request, json.dumps(proposal))
        return response

    result = optimize_run.advance(application, run_id, host_handler=host)
    assert result["state"] == "completed", result
    assert set(stages) == {"gepa", "autoresearch", "meta_harness", "refinement"}
    assert result["selection"]["winner"]
    assert BudgetLedger.spent(result["budget"], "optimization") == 12
    assert BudgetLedger.spent(result["budget"], "verification") == 3
    assert len(executions()) == BudgetLedger.spent(result["budget"]) == 18
    assert optimize_run.advance(application, run_id, host_handler=host)["state"] == "completed"
    assert len(executions()) == 18


def test_configure_requires_frozen_agreed_scoring(application, specification):
    started = engine.start(application, specification, "local")
    with pytest.raises(AuditError, match="agree on scoring"):
        optimize_run.configure(application, started["run_id"], host="fake", model="fake")


def test_target_stops_search_after_independent_final_verification(application, specification):
    run_id = _start(application, specification, optimizer="omni", target=0.62)
    result = optimize_run.advance(application, run_id, host_handler=_host)
    assert result["state"] == "completed"
    assert result["selection"]["winner"]["score"]["target_reached"]
    assert len(executions()) == 3  # Baseline, target proposal, independent final trial.


def test_application_review_pending_replays_without_duplicate_trial(application, specification):
    from agentagon.experiments.host_bridge import HostBridge

    run_id = _start(application, specification, target=0.62)
    first = optimize_run.advance(application, run_id)
    bridge = HostBridge(application, run_id)
    baseline_review = first["pending"][0]
    bridge.start(baseline_review["request_id"])
    bridge.reply(
        baseline_review["request_id"],
        _host(baseline_review),
        host=baseline_review["host"],
        model=baseline_review["model"],
        binding_digest=baseline_review["binding_digest"],
    )
    pending = optimize_run.advance(application, run_id)
    proposal = next(r for r in pending["pending"] if r["role"] == "proposal")
    bridge.start(proposal["request_id"])
    bridge.reply(
        proposal["request_id"],
        _host(proposal),
        host=proposal["host"],
        model=proposal["model"],
        binding_digest=proposal["binding_digest"],
    )
    pending = optimize_run.advance(application, run_id)
    assert any(r["role"] == "review" for r in pending["pending"])
    before = len(executions())
    assert optimize_run.advance(application, run_id)["state"] == "host_pending"
    assert len(executions()) == before
    result = optimize_run.advance(application, run_id, host_handler=_host)
    assert result["state"] == "completed"
    assert result["selection"]["winner"]


def test_saved_intent_budget_and_scoring_are_reused(application, specification):
    from agentagon.experiments import journeys
    from agentagon.experiments.budget import BudgetLedger

    specification["scoring"] = definition()
    intent = journeys.save(
        application,
        {
            "version": 1,
            "goal": "Improve quality",
            "accepted_by": "test-user",
            "scoring": definition(),
            "discovery": {
                "status": "usable",
                "paths": ["benchmark.py"],
                "evidence": ["existing evaluator inspected"],
                "creation_authorized": False,
            },
            "budget": {"max_trials": 20, "max_elapsed_seconds": 300, "trial_timeout_seconds": 5},
        },
    )
    started = engine.start(application, specification, "local")
    with pytest.raises(AuditError, match="expand"):
        optimize_run.configure(
            application,
            started["run_id"],
            intent_id=intent["intent_id"],
            host="fake",
            model="fake",
            max_trials=21,
        )
    configured = optimize_run.configure(
        application, started["run_id"], intent_id=intent["intent_id"], host="fake", model="fake"
    )
    assert configured["budget"]["limits"]["max_trials"] == 20
    assert load_run(application, started["run_id"])["intent_id"] == intent["intent_id"]
    assert load_run(application, started["run_id"])["limits"]["trial_timeout_seconds"] == 5
    assert (
        BudgetLedger(application, started["run_id"]).snapshot()["started_at"]
        <= configured["budget"]["started_at"]
    )


def test_final_verification_recovers_mapping_before_candidate_flag_crash(
    application, specification, monkeypatch
):
    run_id = _start(application, specification, target=0.62)
    original_save = optimize_run.save_run

    def interrupt(workspace, data):
        if any(c.get("verification_of") for c in data["candidates"].values()):
            raise RuntimeError("process stopped before verification flag persisted")
        original_save(workspace, data)

    monkeypatch.setattr(optimize_run, "save_run", interrupt)
    with pytest.raises(RuntimeError, match="verification flag"):
        optimize_run.advance(application, run_id, host_handler=_host)
    assert len(executions()) == 2
    monkeypatch.setattr(optimize_run, "save_run", original_save)
    result = optimize_run.advance(application, run_id, host_handler=_host)
    assert result["state"] == "completed"
    assert result["selection"]["winner"]
    assert len(executions()) == 3


def test_failed_target_verification_continues_search_with_remaining_budget(
    application, specification
):
    run_id = _start(application, specification, target=0.62)
    rejected = []

    def host(request):
        response = _host(request)
        if request["role"] == "review":
            candidate = load_run(application, run_id)["candidates"][
                request["payload"]["candidate_id"]
            ]
            if candidate.get("verification_of") and not rejected:
                response["review"]["verdict"] = "reject"
                response["review"]["rationale"] = (
                    "Independent review found a problem; try another candidate."
                )
                rejected.append(candidate["candidate_id"])
        return response

    result = optimize_run.advance(application, run_id, host_handler=host)
    assert result["state"] == "completed"
    assert result["selection"]["winner"]["score"]["target_reached"]
    assert len(rejected) == 1 and len(executions()) == 5
    assert load_run(application, run_id)["candidates"][rejected[0]]["state"] == "rejected"


def test_process_driver_lock_prevents_concurrent_application_advances(application, specification):
    import fcntl

    from agentagon.experiments.budget import BudgetLedger

    run_id = _start(application, specification)
    with (BudgetLedger(application, run_id).directory / "application.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(AuditError, match="already advancing"):
            optimize_run.advance(application, run_id, host_handler=_host)
    assert executions() == []


@pytest.mark.parametrize("optimizer", ["omni", "gepa"])
def test_optimizers_measure_distinct_candidates_concurrently_in_real_worktrees(
    application, specification, optimizer
):
    from pathlib import Path

    from support.experiments import git

    from agentagon.experiments.orchestration import DEFAULT_SETTINGS
    from agentagon.storage.config import Config

    # The actual benchmark processes rendezvous. A serialized implementation cannot
    # pass: its first process records timed_out before the second process starts.
    benchmark = application.root / "benchmark.py"
    benchmark.write_text(
        benchmark.read_text()
        + """
import time
if application['variant'] != 'baseline':
    rendezvous = Path(os.environ['EXECUTION_LOG']).parent / 'rendezvous'
    rendezvous.mkdir(exist_ok=True)
    mark = rendezvous / application['variant']
    if not mark.exists():
        mark.write_text(str(time.monotonic()))
        deadline = time.monotonic() + 3
        while len(list(rendezvous.iterdir())) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        with Path(os.environ['EXECUTION_LOG']).with_suffix('.overlap').open('a') as stream:
            stream.write(json.dumps({'variant': application['variant'], 'source': str(Path.cwd()),
                'overlap': len(list(rendezvous.iterdir())) >= 2}) + '\\n')
"""
    )
    git(application.root, "add", "benchmark.py")
    git(
        application.root,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@localhost",
        "commit",
        "-qm",
        "Add rendezvous",
    )
    profile = Config().profile(application.root, "local")
    profile["runner"]["independent_capacity"] = True
    profile["limits"].update(parallel_candidates=2, parallel_trials=2, max_candidates=12)
    profile["orchestration"] = {
        **DEFAULT_SETTINGS,
        "host_capacity": 2,
        "resource_slots": 2,
        "round_width": 2,
    }
    Config().update_profile("project", "local", profile, application.root)
    run_id = _start(application, specification, optimizer, finalist_count=1, host_concurrency=2)

    def host(request):
        if request["role"] == "review":
            return _host(request)
        proposal = json.loads(candidate_text(request))
        proposal["files"]["app.json"] = json.dumps(
            {
                "quality": 0.9,
                "latency": 80,
                "variant": request["request_id"],
            }
        )
        return proposal_response(request, json.dumps(proposal))

    result = optimize_run.advance(application, run_id, host_handler=host)
    assert result["selection"]["winner"], result
    import os

    events = [
        json.loads(line)
        for line in Path(os.environ["TEST_EXECUTION_LOG"])
        .with_suffix(".overlap")
        .read_text()
        .splitlines()
    ]
    assert len(events) >= 2 and all(item["overlap"] for item in events), events
    assert len({item["source"] for item in events}) == len(events)
    data = load_run(application, run_id)
    assert len(executions()) == sum(len(c["trials"]) for c in data["candidates"].values())
    assert all(c["state"] in {"verified", "duplicate"} for c in data["candidates"].values())
    count = len(executions())
    optimize_run.advance(application, run_id, host_handler=host)
    assert len(executions()) == count


def test_finalist_count_and_background_are_frozen_before_execution(application, specification):
    run_id = _start(
        application,
        specification,
        finalist_count=2,
        background="Approved trace summary: retry only transient failures.",
    )
    state = optimize_run.status(application, run_id)
    assert state["config"]["finalist_count"] == 2
    assert state["budget"]["limits"]["verification_trials"] == 2
    with pytest.raises(AuditError, match="frozen"):
        optimize_run.configure(
            application,
            run_id,
            host="fake-codex",
            model="fake-model",
            optimizer="gepa",
            finalist_count=1,
        )
    with pytest.raises(AuditError, match="128 KiB"):
        optimize_run.configure(
            application,
            run_id,
            host="fake-codex",
            model="fake-model",
            background="x" * (128 * 1024 + 1),
        )
    assert not executions()


def test_parallel_pending_reviews_resume_without_duplicate_trials(application, specification):
    from agentagon.experiments.host_bridge import HostBridge
    from agentagon.experiments.orchestration import DEFAULT_SETTINGS
    from agentagon.storage.config import Config

    profile = Config().profile(application.root, "local")
    profile["runner"]["independent_capacity"] = True
    profile["limits"].update(parallel_candidates=2, parallel_trials=2, max_candidates=12)
    profile["orchestration"] = {**DEFAULT_SETTINGS, "host_capacity": 2, "resource_slots": 2}
    Config().update_profile("project", "local", profile, application.root)
    run_id = _start(application, specification, "omni", finalist_count=2, host_concurrency=2)
    bridge = HostBridge(application, run_id)
    deferred = False
    for _ in range(40):
        state = optimize_run.advance(application, run_id)
        deferred |= any(v["candidate_id"] is None for v in state["evaluations"].values())
        if state["state"] == "completed":
            break
        assert state["pending"], {
            "state": state["state"],
            "optimizer": state.get("optimizer_state"),
            "running": {
                k: v for k, v in state["budget"]["operations"].items() if v["status"] == "running"
            },
            "evaluations": state["evaluations"],
        }
        for request in state["pending"]:
            reply = _host(request)
            if request["role"] == "proposal":
                proposal = json.loads(
                    reply.get("candidate")
                    or reply["text"].strip().removeprefix("```\n").removesuffix("\n```")
                )
                app = json.loads(proposal["files"]["app.json"])
                app["variant"] = request["request_id"]
                proposal["files"]["app.json"] = json.dumps(app)
                reply = proposal_response(request, json.dumps(proposal))
            bridge.start(request["request_id"])
            bridge.reply(
                request["request_id"],
                reply,
                host=request["host"],
                model=request["model"],
                binding_digest=request["binding_digest"],
            )
    assert state["state"] == "completed" and state["selection"]["winner"], state
    assert deferred  # Pending reviews consumed both candidate slots, then freed them.
    data = load_run(application, run_id)
    attempts = [t["trial_id"] for c in data["candidates"].values() for t in c["trials"]]
    assert len(attempts) == len(set(attempts)) == len(executions())
    running = {
        key: op for key, op in state["budget"]["operations"].items() if op["status"] == "running"
    }
    assert not running, json.dumps(running)


def test_only_completed_owner_intelligence_is_frozen_into_background(application, specification):
    from agentagon.core.records import digest
    from agentagon.experiments.store import save_run

    specification["scoring"] = definition()
    specification.update(repetitions=1, seeds=[0])
    run_id = engine.start(application, specification, "local")["run_id"]
    response = {
        "knowledge_version": "v1",
        "suggestions": [
            {
                "id": "retry",
                "title": "Bound retries",
                "suggestion": "Retry transient failures once.",
            }
        ],
    }
    request_digest = digest({"approved": "abstract context"})
    path = application.artifact(
        {"status": "complete", "request_digest": request_digest, "response": response}
    )
    data = load_run(application, run_id)
    data["intelligence"] = [{"path": path, "status": "complete", "request_digest": request_digest}]
    save_run(application, data)
    first = optimize_run.configure(
        application, run_id, host="fake", model="fake", background="Accepted local evidence"
    )
    assert first["config"]["source_background"] == "Accepted local evidence"
    assert "Retry transient failures once." in first["config"]["background"]
    assert first["config"]["background_receipts"] == [
        {"path": path, "request_digest": request_digest}
    ]
    # Later receipts do not silently rewrite the configured search context.
    data = load_run(application, run_id)
    data["intelligence"].append(
        {
            "path": application.artifact(
                {"status": "complete", "request_digest": "later", "response": response}
            ),
            "status": "complete",
            "request_digest": "later",
        }
    )
    save_run(application, data)
    assert (
        optimize_run.configure(
            application, run_id, host="fake", model="fake", background="Accepted local evidence"
        )["config"]
        == first["config"]
    )
    data["intelligence"] = []
    save_run(application, data)
    with pytest.raises(AuditError, match="not owned"):
        optimize_run.status(application, run_id)
