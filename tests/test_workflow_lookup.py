"""Integration coverage for Intelligence lookups owned by evals and fix runs."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from support.evaluation import draft

from agentagon import usage
from agentagon.cli.main import main
from agentagon.core.records import AuditError
from agentagon.experiments import engine, preparation, store
from agentagon.lookup.client import lookup
from agentagon.operations import start
from agentagon.reporting import build_fix_report
from agentagon.storage.config import Config

RESPONSE = {
    "knowledge_version": "synthetic-workflow-guidance-v1",
    "suggestions": [
        {
            "id": "bounded-retries",
            "title": "Bound retries",
            "suggestion": "Inspect retry paths using local evidence.",
        }
    ],
}


@pytest.fixture(autouse=True)
def configured_intelligence(monkeypatch):
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")
    Config().update("user", values={"intelligence.endpoint": "https://guidance.example"})


@pytest.fixture
def workflow_owners(application, specification):
    evaluation, _ = draft(application, specification)
    run = engine.start(application, specification, "local")
    return application, evaluation["evaluation_id"], run["run_id"]


def _invoke(root, *arguments):
    result = CliRunner().invoke(main, ["--workspace", str(root), *arguments])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _state_path(workspace, workflow, owner_id):
    directory = "evaluations" if workflow == "eval" else "runs"
    return workspace.state / directory / owner_id / "state.json"


def test_eval_and_fix_lookup_route_to_workflow_endpoints_and_payloads(workflow_owners):
    workspace, evaluation_id, run_id = workflow_owners
    evaluation_before = preparation.load(workspace, evaluation_id)
    run_before = store.load_run(workspace, run_id)
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append((request.method, request.url.path, payload))
        if request.url.path == "/v1/eval":
            assert "goal" in payload and "focus" not in payload
        elif request.url.path == "/v1/fix":
            assert "focus" in payload and "goal" not in payload
        else:
            pytest.fail(f"unexpected Intelligence path: {request.url.path}")
        return httpx.Response(200, json=RESPONSE)

    transport = httpx.MockTransport(handler)
    evaluation_result = lookup(
        workspace,
        evaluation_id,
        workflow="eval",
        context="Document retrieval application with a reusable benchmark.",
        goal="Measure retrieval coverage without exposing benchmark cases.",
        limit=50,
        transport=transport,
    )
    fix_result = lookup(
        workspace,
        run_id,
        workflow="fix",
        context="Document retrieval application with bounded candidate runs.",
        focus="Reduce repeated retrieval calls while preserving answer quality.",
        limit=3,
        transport=transport,
    )

    assert evaluation_result["status"] == fix_result["status"] == "complete"
    assert calls == [
        (
            "POST",
            "/v1/eval",
            {
                "context": "Document retrieval application with a reusable benchmark.",
                "goal": "Measure retrieval coverage without exposing benchmark cases.",
                "limit": 5,
            },
        ),
        (
            "POST",
            "/v1/fix",
            {
                "context": "Document retrieval application with bounded candidate runs.",
                "focus": "Reduce repeated retrieval calls while preserving answer quality.",
                "limit": 3,
            },
        ),
    ]
    evaluation_after = preparation.load(workspace, evaluation_id)
    run_after = store.load_run(workspace, run_id)
    assert len(evaluation_after["intelligence"]) == 1
    assert len(run_after["intelligence"]) == 1
    assert evaluation_after.get("package") == evaluation_before.get("package")
    assert evaluation_after["budget"] == evaluation_before["budget"]
    assert run_after["spec"] == run_before["spec"]
    assert run_after["limits"] == run_before["limits"]
    assert run_after["candidates"] == run_before["candidates"]


def test_workflow_cli_lookup_redacts_files_and_uses_explicit_fields(
    workflow_owners, tmp_path, monkeypatch
):
    workspace, evaluation_id, run_id = workflow_owners
    monkeypatch.delenv("AGENTAGON_API_KEY")
    context = tmp_path / "context.txt"
    context.write_text(
        "Abstract retrieval context for person@example.com password=hunter2 "
        "https://private.example/project /Users/guru/project",
        encoding="utf-8",
    )
    goal = tmp_path / "goal.txt"
    goal.write_text(
        "Evaluate retrieval quality for person@example.com password=hunter2",
        encoding="utf-8",
    )
    focus = tmp_path / "focus.txt"
    focus.write_text(
        "Reduce repeated calls for person@example.com password=hunter2",
        encoding="utf-8",
    )

    evaluation = _invoke(
        workspace.root,
        "eval",
        "lookup",
        evaluation_id,
        "--context-file",
        str(context),
        "--goal-file",
        str(goal),
        "--limit",
        "50",
    )
    fix = _invoke(
        workspace.root,
        "fix",
        "lookup",
        run_id,
        "--context-file",
        str(context),
        "--focus-file",
        str(focus),
    )

    assert evaluation["status"] == fix["status"] == "missing_key"
    assert evaluation["request"]["limit"] == 5
    assert set(evaluation["request"]) == {"context", "goal", "limit"}
    assert set(fix["request"]) == {"context", "focus", "limit"}
    serialized = json.dumps({"evaluation": evaluation, "fix": fix})
    for private in ("person@example.com", "hunter2", "private.example", "/Users/guru"):
        assert private not in serialized
    assert "Improve latency and quality without regressing either hard floor" not in serialized

    evaluation_receipt = workspace.read_artifact(evaluation["receipt"])
    fix_receipt = workspace.read_artifact(fix["receipt"])
    assert evaluation_receipt["request"] == evaluation["request"]
    assert fix_receipt["request"] == fix["request"]


def test_successful_cache_isolated_by_owner_workflow_fields_and_phase(
    workspace, imported, workflow_owners
):
    application, evaluation_id, run_id = workflow_owners
    second_audit = start(
        workspace,
        mode="code",
        source=None,
        project=None,
        start_time=None,
        end_time=None,
        limit=None,
        scopes=[],
        host="test",
        model="fixture",
        goal="A separate audit owner",
    )["audit_id"]
    calls = []

    def handler(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={**RESPONSE, "knowledge_version": f"synthetic-cache-v{len(calls)}"},
        )

    transport = httpx.MockTransport(handler)
    initial = lookup(
        workspace,
        imported,
        context="Audit context",
        focus="Audit focus",
        transport=transport,
    )
    cached = lookup(
        workspace,
        imported,
        context="Audit context",
        focus="Audit focus",
        transport=transport,
    )
    follow_up = lookup(
        workspace,
        imported,
        context="Audit context",
        focus="Audit focus",
        phase="follow_up",
        transport=transport,
    )
    changed_focus = lookup(
        workspace,
        imported,
        context="Audit context",
        focus="Different focus",
        transport=transport,
    )
    other_owner = lookup(
        workspace,
        second_audit,
        context="Audit context",
        focus="Audit focus",
        transport=transport,
    )
    evaluation = lookup(
        application,
        evaluation_id,
        workflow="eval",
        context="Evaluation context",
        goal="Evaluation goal",
        transport=transport,
    )
    evaluation_cached = lookup(
        application,
        evaluation_id,
        workflow="eval",
        context="Evaluation context",
        goal="Evaluation goal",
        transport=transport,
    )
    fix = lookup(
        application,
        run_id,
        workflow="fix",
        focus="Fix focus",
        transport=transport,
    )
    first_bytes = Path(workspace.root / initial["receipt"]).read_bytes()
    refreshed = lookup(
        workspace,
        imported,
        context="Audit context",
        focus="Audit focus",
        refresh=True,
        transport=transport,
    )
    after_refresh = lookup(
        workspace,
        imported,
        context="Audit context",
        focus="Audit focus",
        transport=transport,
    )

    assert not initial["cached"]
    assert cached["cached"] and cached["receipt"] == initial["receipt"]
    assert not follow_up["cached"] and not changed_focus["cached"] and not other_owner["cached"]
    assert not evaluation["cached"] and evaluation_cached["cached"]
    assert not fix["cached"] and not refreshed["cached"]
    assert after_refresh["cached"] and after_refresh["response"] == refreshed["response"]
    assert refreshed["receipt"] != initial["receipt"]
    assert Path(workspace.root / initial["receipt"]).read_bytes() == first_bytes
    assert len(calls) == 7


def test_eval_and_fix_status_expose_only_indexes_and_fix_report_hides_intelligence(workflow_owners):
    workspace, evaluation_id, run_id = workflow_owners
    private_context = "private@example.com password=do-not-serve"
    private_goal = "Protected evaluation goal details"
    private_focus = "Protected fix focus details"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=RESPONSE))
    lookup(
        workspace,
        evaluation_id,
        workflow="eval",
        context=private_context,
        goal=private_goal,
        transport=transport,
    )
    lookup(
        workspace,
        run_id,
        workflow="fix",
        context=private_context,
        focus=private_focus,
        transport=transport,
    )

    eval_path = _state_path(workspace, "eval", evaluation_id)
    run_path = _state_path(workspace, "fix", run_id)
    eval_before = eval_path.read_bytes()
    run_before = run_path.read_bytes()
    eval_state = preparation.load(workspace, evaluation_id)
    run_state = engine.status(workspace, run_id)
    eval_index = eval_state["intelligence"]
    run_index = run_state["intelligence_receipts"]
    assert all(set(item) == {"phase", "request_digest", "status", "path"} for item in eval_index)
    assert all(set(item) == {"phase", "request_digest", "status", "path"} for item in run_index)

    assert _invoke(workspace.root, "eval", "status", evaluation_id)["intelligence"] == eval_index
    fix_status = _invoke(workspace.root, "status", "--run", run_id)
    assert fix_status["intelligence_receipts"] == run_index
    assert fix_status["intelligence"]["configured"] is True
    serialized = json.dumps({"eval": eval_state, "fix": fix_status})
    assert private_context not in serialized and private_goal not in serialized
    assert private_focus not in serialized
    assert eval_path.read_bytes() == eval_before
    assert run_path.read_bytes() == run_before

    dashboard = build_fix_report(workspace, store.load_run(workspace, run_id))
    assert "intelligence" not in dashboard
    assert private_context not in json.dumps(dashboard)
    assert private_focus not in json.dumps(dashboard)


def test_missing_configuration_and_network_failures_are_saved_for_each_workflow(
    workflow_owners, monkeypatch
):
    workspace, evaluation_id, run_id = workflow_owners
    calls = []

    def unexpected(request):
        calls.append(request)
        return httpx.Response(503, text="private service body")

    monkeypatch.delenv("AGENTAGON_API_KEY")
    missing_key = lookup(
        workspace,
        evaluation_id,
        workflow="eval",
        goal="Measure benchmark coverage",
        transport=httpx.MockTransport(unexpected),
    )
    monkeypatch.setenv("AGENTAGON_API_KEY", "synthetic-key")
    Config().update("user", unset=("intelligence.endpoint",))
    missing_endpoint = lookup(
        workspace,
        run_id,
        workflow="fix",
        focus="Reduce repeated calls",
        transport=httpx.MockTransport(unexpected),
    )
    Config().update("user", values={"intelligence.endpoint": "https://guidance.example"})
    unavailable = lookup(
        workspace,
        run_id,
        workflow="fix",
        focus="Handle service outage gracefully",
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("private network detail"))
        ),
    )

    assert missing_key["status"] == "missing_key"
    assert missing_endpoint["status"] == "missing_endpoint"
    assert missing_endpoint["endpoint"] is None
    assert unavailable["status"] == "unavailable"
    assert calls == []
    assert "private network detail" not in json.dumps(unavailable)
    assert [
        item["status"] for item in preparation.load(workspace, evaluation_id)["intelligence"]
    ] == ["missing_key"]
    assert [
        item["status"] for item in engine.status(workspace, run_id)["intelligence_receipts"]
    ] == [
        "missing_endpoint",
        "unavailable",
    ]


def test_wrong_owner_and_cross_workflow_fields_fail_before_network(workflow_owners):
    workspace, evaluation_id, run_id = workflow_owners
    eval_before = _state_path(workspace, "eval", evaluation_id).read_bytes()
    run_before = _state_path(workspace, "fix", run_id).read_bytes()
    unknown_evaluation = "eval_" + "f" * 24
    unknown_run = "run_" + "f" * 24
    unknown_evaluation_path = workspace.state / "evaluations" / unknown_evaluation
    unknown_run_path = workspace.state / "runs" / unknown_run
    calls = []

    def unexpected(request):
        calls.append(request)
        return httpx.Response(200, json=RESPONSE)

    transport = httpx.MockTransport(unexpected)
    with pytest.raises(AuditError):
        lookup(workspace, evaluation_id, workflow="fix", focus="wrong owner", transport=transport)
    with pytest.raises(AuditError):
        lookup(workspace, run_id, workflow="eval", goal="wrong owner", transport=transport)
    with pytest.raises(AuditError):
        lookup(
            workspace,
            unknown_evaluation,
            workflow="eval",
            goal="unknown evaluation",
            transport=transport,
        )
    with pytest.raises(AuditError):
        lookup(
            workspace,
            unknown_run,
            workflow="fix",
            focus="unknown fix run",
            transport=transport,
        )
    with pytest.raises(AuditError):
        lookup(
            workspace,
            evaluation_id,
            workflow="eval",
            context="valid context",
            focus="unsupported eval focus",
            transport=transport,
        )
    with pytest.raises(AuditError):
        lookup(
            workspace,
            run_id,
            workflow="fix",
            context="context without a fix focus",
            transport=transport,
        )
    with pytest.raises(AuditError):
        lookup(
            workspace,
            run_id,
            workflow="fix",
            focus="valid fix focus",
            goal="unsupported fix goal",
            transport=transport,
        )

    assert calls == []
    assert not unknown_evaluation_path.exists()
    assert not unknown_run_path.exists()
    assert _state_path(workspace, "eval", evaluation_id).read_bytes() == eval_before
    assert _state_path(workspace, "fix", run_id).read_bytes() == run_before


@pytest.mark.parametrize("workflow", ["eval", "fix"])
def test_workflow_lookup_telemetry_uses_owner_receipts_and_deduplicates_cached_returns(
    workflow_owners, workflow
):
    workspace, evaluation_id, run_id = workflow_owners
    owner_id, owner_field, fields = (
        (
            evaluation_id,
            "evaluation_id",
            {"context": "benchmark context", "goal": "measure coverage"},
        )
        if workflow == "eval"
        else (run_id, "run_id", {"context": "fix context", "focus": "reduce retries"})
    )
    first = lookup(
        workspace,
        owner_id,
        workflow=workflow,
        **fields,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=RESPONSE)),
    )
    cached = lookup(
        workspace,
        owner_id,
        workflow=workflow,
        **fields,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=RESPONSE)),
    )

    def prepare_event(event, receipt, **extra):
        records, _, usage_path = usage._prepare(
            event,
            {
                "workspace": workspace,
                owner_field: owner_id,
                "receipt": receipt,
                **extra,
            },
        )
        return [record["payload"]["event"] for record in records], usage_path

    fresh_events, usage_path = prepare_event(
        "intelligence_lookup_completed",
        first["receipt"],
        duration_ms=0,
        cached=False,
    )
    cached_events, _ = prepare_event(
        "intelligence_lookup_completed",
        cached["receipt"],
        duration_ms=0,
        cached=True,
    )
    assert not first["cached"] and cached["cached"]
    assert fresh_events == ["intelligence_lookup_completed", "knowledge_returned"]
    assert cached_events == ["intelligence_lookup_completed"]
    returned_events, returned_usage_path = prepare_event(
        "knowledge_returned", first["receipt"], entry_id="bounded-retries"
    )
    assert returned_events == ["knowledge_returned"]
    assert returned_usage_path == usage_path
    assert usage_path == _state_path(workspace, workflow, owner_id).with_name("usage.json")

    wrong_field, wrong_id = (
        ("run_id", run_id) if workflow == "eval" else ("evaluation_id", evaluation_id)
    )
    with pytest.raises(AuditError):
        usage._prepare(
            "intelligence_lookup_completed",
            {
                "workspace": workspace,
                wrong_field: wrong_id,
                "receipt": first["receipt"],
                "duration_ms": 0,
                "cached": False,
            },
        )
    for event in ("knowledge_investigated", "knowledge_cited"):
        with pytest.raises(AuditError):
            usage._prepare(
                event,
                {
                    "workspace": workspace,
                    owner_field: owner_id,
                    "receipt": first["receipt"],
                    "entry_id": "bounded-retries",
                    **({"finding_id": "finding"} if event == "knowledge_cited" else {}),
                },
            )


@pytest.mark.parametrize("workflow", ["eval", "fix"])
@pytest.mark.parametrize("stage", ["before_request", "during_request"])
def test_busy_workflow_lookup_returns_promptly_without_overwriting_state(
    workflow_owners, workflow, stage
):
    workspace, evaluation_id, run_id = workflow_owners
    owner_id = evaluation_id if workflow == "eval" else run_id
    fields = {"goal": "Measure coverage"} if workflow == "eval" else {"focus": "Reduce retries"}
    owner_lock = preparation.locked if workflow == "eval" else store.locked
    state_path = _state_path(workspace, workflow, owner_id)
    original = state_path.read_bytes()
    calls = []

    # Release the real operation lock before joining the worker even if it times out.
    with ThreadPoolExecutor(max_workers=1) as executor, ExitStack() as held:
        if stage == "before_request":
            held.enter_context(owner_lock(workspace, owner_id))

        def handler(request):
            calls.append(request)
            if stage == "during_request":
                held.enter_context(owner_lock(workspace, owner_id))
            return httpx.Response(200, json=RESPONSE)

        result = executor.submit(
            lookup,
            workspace,
            owner_id,
            workflow=workflow,
            **fields,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(AuditError, match="lookup owner is busy"):
            result.result(timeout=3)

    assert len(calls) == (0 if stage == "before_request" else 1)
    assert state_path.read_bytes() == original
    resumed = lookup(
        workspace,
        owner_id,
        workflow=workflow,
        **fields,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=RESPONSE)),
    )
    assert resumed["status"] == "complete"
