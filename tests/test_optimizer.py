"""Real upstream optimizer qualification with a fake native host, no model SDK."""

import threading
import time

import pytest

from agentagon.core.records import AuditError
from agentagon.experiments.budget import BudgetExhausted, BudgetLedger
from agentagon.experiments.host_bridge import HostBridge
from agentagon.experiments.optimizer import MetaHarnessConfig, OptimizerCoordinator
from agentagon.storage.workspace import Workspace

RUN = "run_" + "a" * 24


@pytest.fixture
def workspace(tmp_path):
    workspace = Workspace(tmp_path)
    workspace.initialize()
    return workspace


def test_protected_budget_trials_retries_and_replay(workspace):
    ledger = BudgetLedger(workspace, RUN)
    assert ledger.create(10, 300)["allocations"] == {
        "preparation": 2,
        "optimization": 6,
        "verification": 2,
    }
    admission = ledger.admit("baseline", "preparation")
    assert admission["replay"] is False
    assert ledger.admit("baseline", "preparation")["replay"] is True
    ledger.finish("baseline", status="failed", result={"error": "crash"})
    ledger.admit("baseline-retry", "preparation")
    ledger.finish("baseline-retry")
    ledger.release_preparation()
    for index in range(6):
        ledger.admit(f"trial-{index}", "optimization")
    with pytest.raises(BudgetExhausted, match="reserve"):
        ledger.admit("seventh", "optimization")
    ledger.admit("verify", "verification")
    ledger.admit("verify-retry", "verification")
    assert ledger.spent(ledger.snapshot()) == 10
    with pytest.raises(BudgetExhausted):
        ledger.admit("verification-overrun", "verification")
    with pytest.raises(AuditError, match="expanded"):
        ledger.create(20, 300)


def test_unused_preparation_flows_without_verification_leak(workspace):
    ledger = BudgetLedger(workspace, RUN)
    ledger.create(10, 300)
    ledger.admit("baseline", "preparation")
    ledger.finish("baseline")
    assert ledger.release_preparation()["allocations"] == {
        "preparation": 1,
        "optimization": 7,
        "verification": 2,
    }
    with pytest.raises(BudgetExhausted, match="released"):
        ledger.admit("late-preparation", "preparation")
    with pytest.raises(AuditError, match="different work"):
        ledger.admit("baseline", "optimization")


def test_time_reserve_and_baseline_only_budget(workspace, monkeypatch):
    monkeypatch.setattr("agentagon.experiments.budget.time.time", lambda: 100)
    ledger = BudgetLedger(workspace, RUN)
    ledger.create(10, 100)
    monkeypatch.setattr("agentagon.experiments.budget.time.time", lambda: 179)
    assert ledger.admit("last-search", "optimization")["timeout_seconds"] == 1
    monkeypatch.setattr("agentagon.experiments.budget.time.time", lambda: 180)
    with pytest.raises(BudgetExhausted, match="time budget"):
        ledger.admit("too-late", "optimization")
    assert ledger.admit("verification", "verification")["timeout_seconds"] == 20
    baseline = BudgetLedger(workspace, "run_" + "b" * 24)
    assert baseline.create(1, 100, journey="baseline")["allocations"]["preparation"] == 1
    with pytest.raises(BudgetExhausted, match="minimum final"):
        BudgetLedger(workspace, "run_" + "c" * 24).create(1, 100)


def test_host_requests_binding_claim_cancel_and_judging(workspace):
    BudgetLedger(workspace, RUN).create(10, 300)
    bridge = HostBridge(workspace, RUN)
    request = bridge.request(
        "proposal:1",
        source="commit",
        evaluator="eval-v1",
        role="proposal",
        scope=["src/"],
        host="codex",
        model="configured",
        payload={"candidate": "seed"},
    )
    assert len(bridge.pending()) == 1
    assert not bridge.start(request["request_id"])["replay"]
    assert HostBridge(workspace, RUN).start(request["request_id"])["replay"]
    with pytest.raises(AuditError, match="provenance"):
        bridge.reply(
            request["request_id"],
            {"candidate": "better"},
            host="claude",
            model="configured",
            binding_digest=request["binding_digest"],
        )
    bridge.reply(
        request["request_id"],
        {"candidate": "better"},
        host="codex",
        model="configured",
        binding_digest=request["binding_digest"],
    )
    assert bridge.pending() == []
    assert BudgetLedger(workspace, RUN).spent(BudgetLedger(workspace, RUN).snapshot()) == 0
    with pytest.raises(AuditError, match="actual trial"):
        bridge.request(
            "judge",
            source="commit",
            evaluator="eval-v1",
            role="judging",
            scope=["outputs/"],
            host="codex",
            model="configured",
            payload={},
        )
    request = bridge.request(
        "review",
        source="commit",
        evaluator="eval-v1",
        role="review",
        scope=["diff"],
        host="codex",
        model="configured",
        payload={},
    )
    bridge.cancel(request["request_id"], reason="run stopped")
    assert bridge.snapshot()["requests"][request["request_id"]]["state"] == "cancelled"
    assert len(bridge.snapshot()["requests"]) == 2


def coordinator(workspace, *, concurrency=1, engine="omni"):
    pytest.importorskip("gepa.oa.ensemble")
    ledger = BudgetLedger(workspace, RUN)
    ledger.create(40, 300)
    # Fake baseline and release its unused preparation budget.
    ledger.admit("baseline", "preparation")
    ledger.finish("baseline", result={"score": 0})
    ledger.release_preparation()
    optimizer = OptimizerCoordinator(workspace, RUN)
    optimizer.create(
        source="commit-a",
        evaluator="frozen-eval",
        seed="0",
        objective="increase integer",
        scope=["candidate"],
        host="codex",
        model="current-model",
        host_concurrency=concurrency,
        engine=engine,
        meta_harness=MetaHarnessConfig(host="other-host", model="override-model"),
    )
    return optimizer


def test_real_omni_all_engines_fresh_refine_and_concurrency(workspace, monkeypatch):
    # Any accidental use of an upstream Claude process or API-backed LM fails.
    monkeypatch.setattr(
        "subprocess.Popen", lambda *args, **kwargs: pytest.fail("optimizer launched a subprocess")
    )
    optimizer = coordinator(workspace, concurrency=2)
    host_calls = []
    evaluations = []
    active = peak = 0
    lock = threading.Lock()

    def evaluate(candidate, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.001)
        evaluations.append(kwargs["operation_id"])
        with lock:
            active -= 1
        return {"state": "measured", "value": int(candidate), "eligible": True}

    def host(request):
        host_calls.append(request)
        return {"candidate": str(int(request["payload"]["candidate"]) + 1)}

    result = optimizer.advance(evaluate, host_handler=host)
    assert result["state"] == "completed"
    assert {r["payload"]["stage"] for r in host_calls} == {
        "gepa",
        "autoresearch",
        "meta_harness",
        "refinement",
    }
    assert all(
        r["host"] == "other-host" and r["model"] == "override-model"
        for r in host_calls
        if r["payload"]["engine"] == "meta_harness"
    )
    assert 1 <= peak <= 2
    assert len(evaluations) == len(set(evaluations))
    assert BudgetLedger.spent(result["budget"], "optimization") == len(evaluations)
    assert BudgetLedger.spent(result["budget"], "verification") == 0
    exploration = [s["result"]["score"] for s in result["stages"][:3]]
    assert result["stages"][3]["result"]["score"] >= max(exploration)
    assert result["selection"] == "requires_agentagon_independent_verification"
    assert optimizer.advance(evaluate, host_handler=host)["state"] == "completed"
    assert len(evaluations) == len(set(evaluations))


def test_real_gepa_host_pending_restart_never_repeats_work(workspace):
    optimizer = coordinator(workspace, engine="gepa")
    evaluations = []

    def evaluate(candidate, **kwargs):
        evaluations.append(kwargs["operation_id"])
        return {"state": "measured", "value": int(candidate), "eligible": True}

    pending = optimizer.advance(evaluate)
    assert pending["state"] == "host_pending"
    original_evaluations = list(evaluations)
    assert OptimizerCoordinator(workspace, RUN).advance(evaluate)["state"] == "host_pending"
    assert evaluations == original_evaluations
    request = pending["pending"][0]
    optimizer.bridge.start(request["request_id"])
    assert OptimizerCoordinator(workspace, RUN).advance(evaluate)["state"] == "host_pending"
    optimizer.bridge.reply(
        request["request_id"],
        {"candidate": "1"},
        host=request["host"],
        model=request["model"],
        binding_digest=request["binding_digest"],
    )
    result = OptimizerCoordinator(workspace, RUN).advance(
        evaluate, host_handler=lambda req: {"candidate": str(int(req["payload"]["candidate"]) + 1)}
    )
    assert result["state"] == "completed"
    assert len(evaluations) == len(set(evaluations))
    assert (
        len(result["budget"]["operations"])
        == len(evaluations) + len(optimizer.bridge.snapshot()["requests"]) + 1
    )


def test_callback_trial_cost_bounds_search_without_spending_verification(workspace):
    optimizer = coordinator(workspace, engine="autoresearch")
    calls = []

    def evaluate(candidate, **kwargs):
        calls.append(candidate)
        return {"state": "measured", "value": int(candidate), "eligible": True}

    result = optimizer.advance(
        evaluate,
        host_handler=lambda req: {"candidate": str(int(req["payload"]["candidate"]) + 1)},
        trials_per_evaluation=3,
    )
    assert result["state"] == "completed"
    assert len(calls) == result["budget"]["allocations"]["optimization"] // 3
    assert BudgetLedger.spent(result["budget"], "optimization") == len(calls) * 3
    assert BudgetLedger.spent(result["budget"], "verification") == 0


def test_failed_measurement_preserved_without_zero_score(workspace):
    optimizer = coordinator(workspace, engine="gepa")
    calls = []

    def evaluate(candidate, **kwargs):
        calls.append(candidate)
        raise RuntimeError("application cannot execute")

    result = optimizer.advance(evaluate)
    assert result["state"] == "measurement_unavailable"
    operations = list(result["budget"]["operations"].values())
    assert operations[-1]["status"] == "failed"
    assert "value" not in operations[-1]["result"]
    assert optimizer.advance(evaluate)["state"] == "measurement_unavailable"
    assert calls == ["0"]


def test_bridge_recovers_reply_persisted_before_projection_crash(workspace, monkeypatch):
    BudgetLedger(workspace, RUN).create(10, 300)
    bridge = HostBridge(workspace, RUN)
    arguments = dict(
        source="commit",
        evaluator="eval",
        role="review",
        scope=["diff"],
        host="codex",
        model="model",
        payload={},
    )
    request = bridge.request("review", **arguments)
    bridge.start(request["request_id"])
    original_write = workspace.write

    def interrupted(path, value):
        if path == bridge.path:
            raise OSError("process stopped after durable reply")
        original_write(path, value)

    monkeypatch.setattr(workspace, "write", interrupted)
    with pytest.raises(OSError):
        bridge.reply(
            request["request_id"],
            {"review": "saved"},
            host="codex",
            model="model",
            binding_digest=request["binding_digest"],
        )
    monkeypatch.setattr(workspace, "write", original_write)
    recovered = HostBridge(workspace, RUN).request("review", **arguments)
    assert recovered["state"] == "completed"
    assert recovered["response"] == {"review": "saved"}
    assert len(BudgetLedger(workspace, RUN).snapshot()["operations"]) == 1


def test_late_host_reply_is_retained_and_marked_exceeded(workspace, monkeypatch):
    monkeypatch.setattr("agentagon.experiments.budget.time.time", lambda: 100)
    BudgetLedger(workspace, RUN).create(10, 100)
    bridge = HostBridge(workspace, RUN)
    request = bridge.request(
        "review",
        source="commit",
        evaluator="eval",
        role="review",
        scope=["diff"],
        host="codex",
        model="model",
        payload={},
    )
    bridge.start(request["request_id"], timeout_seconds=10)
    monkeypatch.setattr("agentagon.experiments.budget.time.time", lambda: 111)
    reply = bridge.reply(
        request["request_id"],
        {"review": "saved"},
        host="codex",
        model="model",
        binding_digest=request["binding_digest"],
    )
    assert reply["deadline_exceeded"]
    assert reply["response"] == {"review": "saved"}


def test_concurrent_trial_admission_cannot_spend_verification_reserve(workspace):
    from concurrent.futures import ThreadPoolExecutor

    ledger = BudgetLedger(workspace, RUN)
    ledger.create(10, 100)

    def admit(index):
        try:
            return ledger.admit(str(index), "optimization")
        except BudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = [r for r in pool.map(admit, range(30)) if r]
    assert len(admitted) == 6
    assert ledger.spent(ledger.snapshot()) == 6
    assert ledger.snapshot()["allocations"]["verification"] == 2
