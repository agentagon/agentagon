"""Managed hosts are exercised through local fakes; no model calls are made."""

import asyncio
import importlib.util
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agentagon.core.records import AuditError
from agentagon.webapp import agents


def fake_codex(tmp_path, scenario="success"):
    executable = tmp_path / "fake-codex"
    log = tmp_path / "requests.jsonl"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, time\n"
        f"log = {str(log)!r}\nscenario = {scenario!r}\n"
        "def send(value):\n"
        " print(json.dumps(value), flush=True)\n"
        "def read():\n"
        " line = sys.stdin.readline()\n"
        " if not line: raise SystemExit()\n"
        " with open(log, 'a') as stream: stream.write(line)\n"
        " return json.loads(line)\n"
        "while True:\n"
        " request = read()\n"
        " method = request.get('method')\n"
        " if method == 'initialize': send({'id': request['id'], 'result': {}})\n"
        " elif method == 'account/read':\n"
        "  account = None if scenario == 'no-auth' else {'type': 'apiKey'}\n"
        "  send({'id': request['id'], 'result': {'account': account, 'requiresOpenaiAuth': True}})\n"
        " elif method == 'model/list':\n"
        "  cursor = request['params'].get('cursor')\n"
        "  entry = {'id': 'host-picker', 'model': 'host-model', 'displayName': 'OpenAI Host', 'description': 'Available model', 'isDefault': True, 'hidden': False, 'defaultReasoningEffort': 'medium', 'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}]}\n"
        "  if cursor: entry.update(id='alt-picker', model='openai-alt', isDefault=False)\n"
        "  next_cursor = 'next' if scenario == 'pages' and not cursor else None\n"
        "  if scenario == 'repeated-cursor': next_cursor = 'again'\n"
        "  send({'id': request['id'], 'result': {'data': [entry], 'nextCursor': next_cursor}})\n"
        " elif method in ['thread/start', 'thread/resume']:\n"
        "  session = request['params'].get('threadId', 'session-1')\n"
        "  if scenario == 'wrong-session': session = 'some-other-session'\n"
        "  send({'id': request['id'], 'result': {'thread': {'id': session}, 'model': 'host-model'}})\n"
        " elif method == 'turn/start':\n"
        "  send({'id': request['id'], 'result': {'turn': {'id': 'turn-1'}}})\n"
        "  if scenario == 'hold': time.sleep(30)\n"
        "  if scenario == 'oversize':\n"
        "   print('x' * 1100000, flush=True); time.sleep(30)\n"
        "  if scenario == 'disconnect': raise SystemExit()\n"
        "  send({'id': 'approval-1', 'method': 'item/commandExecution/requestApproval',\n"
        "        'params': {'command': 'python -m pytest', 'cwd': '.', 'threadId': session}})\n"
        "  read()\n"
        "  send({'id': 'question-1', 'method': 'item/tool/requestUserInput',\n"
        "        'params': {'questions': [{'id': 'direction', 'question': 'Which direction?',\n"
        "        'options': [{'label': 'Minimal'}]}], 'threadId': session}})\n"
        "  read()\n"
        "  send({'method': 'item/started', 'params': {'threadId': session,\n"
        "        'item': {'type': 'commandExecution', 'id': 'tool-1'}}})\n"
        "  send({'method': 'item/agentMessage/delta', 'params': {'delta': 'super-'}})\n"
        "  send({'method': 'item/agentMessage/delta', 'params': {'delta': 'secret-value'}})\n"
        "  send({'method': 'item/completed', 'params': {'threadId': session,\n"
        "        'item': {'type': 'agentMessage', 'text': 'Finished super-secret-value'}}})\n"
        "  turn = {'id': 'turn-1', 'status': 'completed'}\n"
        "  if scenario == 'fail': turn.update(status='failed', error={'message': 'bad super-secret-value'})\n"
        "  send({'method': 'turn/completed', 'params': {'threadId': session, 'turn': turn}})\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable, log


def request(tmp_path, **kwargs):
    return {"agent": "codex", "cwd": str(tmp_path), "prompt": "Inspect this project", **kwargs}


def test_codex_streams_approval_question_and_redacted_result(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "super-secret-value")
    executable, log = fake_codex(tmp_path)
    config = tmp_path / "config.json"
    config.write_text("{}")
    events, questions = [], []

    def ask(question):
        questions.append(question)
        return {"decision": "decline"} if question["kind"] == "approval" else {"text": "Minimal"}

    result = agents.run_agent(
        request(
            tmp_path,
            executable=str(executable),
            env={"AGENTAGON_CONFIG": str(config), "AGENTAGON_APP_STATE": str(tmp_path)},
        ),
        events.append,
        ask,
        threading.Event(),
    )
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert result == {
        "state": "completed",
        "session_id": "session-1",
        "text": "Finished [redacted]",
    }
    assert events[0] == {"type": "session", "session_id": "session-1", "model": "host-model"}
    assert any(event["type"] == "progress" for event in events)
    assert "super-secret-value" not in json.dumps(events + questions)
    assert {"id": "approval-1", "result": {"decision": "decline"}} in messages
    assert {
        "id": "question-1",
        "result": {"answers": {"direction": {"answers": ["Minimal"]}}},
    } in messages
    assert questions[1]["questions"][0]["options"] == [{"label": "Minimal"}]
    start = next(message for message in messages if message.get("method") == "thread/start")
    assert start["params"]["approvalPolicy"] == "on-request"
    assert start["params"]["sandbox"] == "workspace-write"


def test_codex_resumes_exact_id_and_does_not_start_on_mismatch(tmp_path):
    executable, log = fake_codex(tmp_path, "wrong-session")
    with pytest.raises(AuditError, match="different session"):
        agents.run_agent(
            request(tmp_path, executable=str(executable), session_id="saved-session"),
            lambda event: None,
            lambda event: {},
            threading.Event(),
        )
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    resume = next(message for message in messages if message.get("method") == "thread/resume")
    assert resume["params"]["threadId"] == "saved-session"
    assert not any(message.get("method") in ("thread/start", "turn/start") for message in messages)


def test_codex_resume_can_approve_once(tmp_path):
    executable, log = fake_codex(tmp_path)
    result = agents.run_agent(
        request(tmp_path, executable=str(executable), session_id="saved-session", model=""),
        lambda event: None,
        lambda event: {"decision": "accept", "answers": {"direction": ["Minimal"]}},
        threading.Event(),
    )
    assert result["session_id"] == "saved-session"
    assert result["state"] == "completed"
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert {"id": "approval-1", "result": {"decision": "accept"}} in messages
    assert not any(message.get("method") == "thread/start" for message in messages)


def test_codex_without_auth_never_creates_session_or_starts_work(tmp_path):
    executable, log = fake_codex(tmp_path, "no-auth")
    events = []
    with pytest.raises(AuditError, match="codex login"):
        agents.run_agent(
            request(tmp_path, executable=str(executable)),
            events.append,
            lambda event: {},
            threading.Event(),
        )
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert not events
    assert not any(message.get("method") in {"thread/start", "turn/start"} for message in messages)


def test_codex_model_catalog_paginates_without_creating_session(tmp_path):
    executable, log = fake_codex(tmp_path, "pages")
    models = agents.codex_models(str(executable))
    assert [entry["id"] for entry in models] == ["host-model", "openai-alt"]
    assert models[0] == {
        "id": "host-model",
        "name": "OpenAI Host",
        "description": "Available model",
        "default": True,
    }
    assert agents.validate_codex_model("openai-alt", str(executable)) == "openai-alt"
    with pytest.raises(AuditError, match="not available"):
        agents.validate_codex_model("alt-picker", str(executable))
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(message.get("method") in {"thread/start", "turn/start"} for message in messages)


def test_codex_model_catalog_rejects_repeated_cursor_and_stale_model(tmp_path):
    executable, _ = fake_codex(tmp_path, "repeated-cursor")
    with pytest.raises(AuditError, match="pagination"):
        agents.codex_models(str(executable))
    executable, log = fake_codex(tmp_path)
    with pytest.raises(AuditError, match="not available"):
        agents.run_agent(
            request(tmp_path, executable=str(executable), model="unavailable-model"),
            lambda event: None,
            lambda event: {},
            threading.Event(),
        )
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(message.get("method") in {"thread/start", "turn/start"} for message in messages)


def test_codex_selected_model_and_private_terminal_text(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "super-secret-value")
    executable, log = fake_codex(tmp_path)
    events = []
    result = agents.run_agent(
        request(
            tmp_path, executable=str(executable), model="host-model", response_mode="raw-final"
        ),
        events.append,
        lambda event: {},
        threading.Event(),
    )
    assert result["raw_final_text"] == "Finished super-secret-value"
    assert result["text"] == "Finished [redacted]"
    assert "super-secret-value" not in json.dumps(events)
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert (
        next(message for message in messages if message.get("method") == "thread/start")["params"][
            "model"
        ]
        == "host-model"
    )


def test_terminal_response_is_not_combined_with_progress():
    run = agents._Run(
        {"timeout_seconds": 10, "response_mode": "raw-final"},
        lambda event: None,
        lambda event: {},
        threading.Event(),
    )
    run.message("Investigating", phase="commentary")
    run.message('```json\n{"files":{}}\n```', phase="final_answer")
    run.message("Other host commentary", phase="commentary")
    assert run.result("completed")["raw_final_text"] == '```json\n{"files":{}}\n```'


@pytest.mark.parametrize(
    "scenario, error",
    [
        ("disconnect", "disconnected"),
        ("oversize", "output exceeded"),
        ("fail", "turn failed"),
    ],
)
def test_codex_failures_are_not_success(tmp_path, scenario, error):
    executable, _ = fake_codex(tmp_path, scenario)
    with pytest.raises(AuditError, match=error) as caught:
        agents.run_agent(
            request(tmp_path, executable=str(executable), api_key="super-secret-value"),
            lambda event: None,
            lambda event: {},
            threading.Event(),
        )
    assert "super-secret-value" not in str(caught.value)


def test_codex_cancellation_while_approval_is_waiting(tmp_path):
    executable, _ = fake_codex(tmp_path)
    cancelled = threading.Event()
    waiting = threading.Event()
    release = threading.Event()

    def ask(event):
        waiting.set()
        release.wait(5)
        return {"decision": "accept"}

    def stop():
        waiting.wait(3)
        cancelled.set()

    threading.Thread(target=stop, daemon=True).start()
    before = time.monotonic()
    try:
        result = agents.run_agent(
            request(tmp_path, executable=str(executable)), lambda event: None, ask, cancelled
        )
    finally:
        release.set()
    assert result["state"] == "cancelled"
    assert result["session_id"] == "session-1"
    assert time.monotonic() - before < 3


def test_codex_timeout_stops_owned_process(tmp_path):
    executable, _ = fake_codex(tmp_path, "hold")
    before = time.monotonic()
    with pytest.raises(AuditError, match="time limit"):
        agents.run_agent(
            request(tmp_path, executable=str(executable), timeout_seconds=0.2),
            lambda event: None,
            lambda event: {},
            threading.Event(),
        )
    assert time.monotonic() - before < 3


@pytest.mark.parametrize("invalid", ["relative", "file", "missing", "unknown"])
def test_host_environment_rejects_invalid_app_paths_before_launch(tmp_path, invalid):
    config = tmp_path / "config.json"
    config.write_text("{}")
    environment = {
        "relative": {"AGENTAGON_APP_STATE": "relative"},
        "file": {"AGENTAGON_APP_STATE": str(config)},
        "missing": {"AGENTAGON_APP_STATE": str(tmp_path / "missing")},
        "unknown": {"ARBITRARY_OVERRIDE": str(tmp_path)},
    }[invalid]
    with pytest.raises(AuditError, match="environment|application state"):
        agents.run_agent(
            request(tmp_path, env=environment),
            lambda event: pytest.fail("host should not start"),
            lambda question: {},
            threading.Event(),
        )


def fake_claude(monkeypatch, scenario="success"):
    record = {}

    def named(name, **data):
        return type(name, (SimpleNamespace,), {})(**data)

    class Client:
        def __init__(self, *, options):
            record["options"] = options

        async def __aenter__(self):
            if scenario == "connect-hold":
                await asyncio.sleep(30)
            return self

        async def __aexit__(self, *args):
            record["closed"] = True

        async def query(self, prompt):
            record["prompt"] = prompt
            if scenario == "query-hold":
                await asyncio.sleep(30)

        async def interrupt(self):
            record["interrupted"] = True

        async def receive_response(self):
            options = record["options"]
            session = getattr(options, "resume", "claude-session")
            yield named(
                "SystemMessage",
                subtype="init",
                data={"session_id": session, "model": "claude-host-model"},
            )
            if scenario == "hold":
                await asyncio.sleep(30)
            if scenario == "disconnect":
                return
            record["approval"] = await options.can_use_tool(
                "Write", {"file_path": "example.py", "content": "value = 1"}, SimpleNamespace()
            )
            record["answer"] = await options.can_use_tool(
                "AskUserQuestion", {"questions": [{"question": "Which option?"}]}, SimpleNamespace()
            )
            yield named(
                "AssistantMessage", content=[named("TextBlock", text="Done sdk-secret-value")]
            )
            yield named(
                "ResultMessage",
                session_id=session,
                is_error=scenario == "fail",
                subtype="error" if scenario == "fail" else "success",
                result="sdk-secret-value",
            )

    module = SimpleNamespace(
        ClaudeSDKClient=Client,
        ClaudeAgentOptions=SimpleNamespace,
        PermissionResultAllow=lambda **data: named("PermissionResultAllow", **data),
        PermissionResultDeny=lambda **data: named("PermissionResultDeny", **data),
        HookMatcher=SimpleNamespace,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    return record


def test_claude_api_key_approvals_exact_resume_and_redaction(tmp_path, monkeypatch):
    record = fake_claude(monkeypatch)
    events = []
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription-secret")
    config = tmp_path / "frozen-config.json"
    config.write_text("{}")
    result = agents.run_agent(
        request(
            tmp_path,
            agent="claude",
            api_key="sdk-secret-value",
            session_id="exact-saved-id",
            env={"AGENTAGON_CONFIG": str(config), "AGENTAGON_APP_STATE": str(tmp_path)},
        ),
        events.append,
        lambda event: {"decision": "decline"}
        if event["kind"] == "approval"
        else {"text": "Minimal"},
        threading.Event(),
    )
    options = record["options"]
    assert options.resume == "exact-saved-id"
    assert options.permission_mode == "default"
    assert options.env["ANTHROPIC_API_KEY"] == "sdk-secret-value"
    assert options.env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert options.env["AGENTAGON_CONFIG"] == str(config)
    assert options.env["AGENTAGON_APP_STATE"] == str(tmp_path)
    assert options.setting_sources == ["user", "project", "local"]
    assert type(record["approval"]).__name__ == "PermissionResultDeny"
    assert record["answer"].updated_input["answers"] == {"Which option?": "Minimal"}
    assert result["text"] == "Done [redacted]"
    assert events[0] == {
        "type": "session",
        "session_id": "exact-saved-id",
        "model": "claude-host-model",
    }
    assert record["closed"] is True


def test_claude_private_terminal_response_uses_result_not_progress(tmp_path, monkeypatch):
    fake_claude(monkeypatch)
    events = []
    result = agents.run_agent(
        request(tmp_path, agent="claude", api_key="sdk-secret-value", response_mode="raw-final"),
        events.append,
        lambda event: {},
        threading.Event(),
    )
    assert result["raw_final_text"] == "sdk-secret-value"
    assert result["text"] == "Done [redacted]"
    assert "sdk-secret-value" not in json.dumps(events)


@pytest.mark.parametrize("scenario", ["connect-hold", "query-hold", "hold"])
def test_claude_deadline_covers_connection_query_and_response(tmp_path, monkeypatch, scenario):
    record = fake_claude(monkeypatch, scenario)
    started = time.monotonic()
    with pytest.raises(AuditError, match="time limit"):
        agents.run_agent(
            request(tmp_path, agent="claude", api_key="sdk-secret-value", timeout_seconds=0.1),
            lambda event: None,
            lambda event: {},
            threading.Event(),
        )
    assert time.monotonic() - started < 3
    if scenario != "connect-hold":
        assert record["closed"] is True


def test_claude_requires_api_key_even_if_subscription_present(tmp_path, monkeypatch):
    record = fake_claude(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription-secret")
    with pytest.raises(AuditError, match="API key"):
        agents.run_agent(
            request(tmp_path, agent="claude"),
            lambda event: None,
            lambda event: {},
            threading.Event(),
        )
    assert not record


def test_claude_missing_sdk_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    with pytest.raises(AuditError, match=r"Install agentagon\[claude\]"):
        agents.run_agent(
            request(tmp_path, agent="claude", api_key="sdk-secret-value"),
            lambda event: None,
            lambda event: {},
            threading.Event(),
        )


@pytest.mark.parametrize("scenario", ["fail", "disconnect"])
def test_claude_failure_does_not_complete(tmp_path, monkeypatch, scenario):
    record = fake_claude(monkeypatch, scenario)
    with pytest.raises(AuditError) as caught:
        agents.run_agent(
            request(tmp_path, agent="claude", api_key="sdk-secret-value"),
            lambda event: None,
            lambda event: {},
            threading.Event(),
        )
    assert "sdk-secret-value" not in str(caught.value)
    assert record["closed"] is True


def test_claude_read_only_and_cancellation(tmp_path, monkeypatch):
    record = fake_claude(monkeypatch, "hold")
    cancelled = threading.Event()
    result = agents.run_agent(
        request(tmp_path, agent="claude", api_key="sdk-secret-value", sandbox="read-only"),
        lambda event: cancelled.set() if event["type"] == "session" else None,
        lambda event: {},
        cancelled,
    )
    assert result["state"] == "cancelled"
    assert record["interrupted"] is True
    assert record["closed"] is True
    assert "Bash" not in record["options"].tools
    assert "Agent" in record["options"].disallowed_tools


def test_detection_does_not_expose_credentials(monkeypatch):
    monkeypatch.setattr(agents.shutil, "which", lambda name: None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "never-return-this")
    detected = agents.detect_agents()
    assert detected[1]["available"] is False
    assert detected[1]["sdk_available"] is False
    assert detected[1]["authenticated"] is True
    assert "never-return-this" not in json.dumps(detected)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cwd": "."},
        {"timeout_seconds": float("nan")},
        {"sandbox": "danger-full-access"},
        {"prompt": ""},
    ],
)
def test_invalid_requests_are_rejected_before_execution(tmp_path, kwargs):
    with pytest.raises(AuditError):
        agents.run_agent(
            request(tmp_path, **kwargs), lambda event: None, lambda event: {}, threading.Event()
        )


@pytest.fixture
def reflection(tmp_path):
    from agentagon.experiments.budget import BudgetLedger
    from agentagon.experiments.host_bridge import HostBridge
    from agentagon.storage.workspace import Workspace

    workspace = Workspace(tmp_path)
    workspace.initialize()
    owner = "run_" + "b" * 24
    BudgetLedger(workspace, owner).create(10, 100)
    bridge = HostBridge(workspace, owner)
    request = bridge.request(
        "gepa:reflection:0",
        source="commit",
        evaluator="frozen-eval",
        role="proposal",
        scope=["app.py"],
        host="codex",
        model="host-model",
        payload={
            "protocol": "gepa-reflection-v1",
            "engine": "gepa",
            "stage": "gepa",
            "prompt": "Exact upstream prompt\n\nIncluding fences ``` and unicode µ",
        },
    )
    return workspace, bridge, request


def collect_reflection(reflection, execute, **kwargs):
    workspace, bridge, request = reflection
    return agents.run_reflection(
        workspace,
        bridge.run_id,
        request["request_id"],
        emit=kwargs.pop("emit", lambda event: None),
        ask=kwargs.pop("ask", lambda question: {}),
        cancelled=threading.Event(),
        timeout_seconds=30,
        execute=execute,
        **kwargs,
    )


def test_reflection_exact_prompt_private_raw_checkpoint_and_completed_replay(reflection):
    workspace, bridge, pending = reflection
    calls, events = [], []
    final = '```json\n{"files":{"app.py":"api_key = load()"}}\n```'

    def host(request, emit, ask, cancelled):
        calls.append(request)
        assert request["prompt"] == pending["payload"]["prompt"]
        assert request["sandbox"] == "read-only"
        assert request["session_id"] is None
        emit({"type": "session", "session_id": "dedicated", "model": "host-model"})
        assert ask({"kind": "approval", "command": "edit source"}) == {"decision": "decline"}
        return {
            "state": "completed",
            "session_id": "dedicated",
            "raw_final_text": final,
            "text": "redacted progress",
        }

    result = collect_reflection(reflection, host, emit=events.append)
    assert result == {"state": "completed", "session_id": "dedicated"}
    saved = bridge.snapshot()["requests"][pending["request_id"]]
    assert saved["response"] == {"text": final}
    assert workspace.read_artifact(saved["terminal_output"]) == {"text": final}
    assert events == [
        {
            "type": "reflection_session",
            "session_id": "dedicated",
            "model": "host-model",
            "request_id": pending["request_id"],
        }
    ]
    assert collect_reflection(reflection, host) == result
    assert len(calls) == 1


def test_reflection_interruption_resumes_only_its_saved_session(reflection):
    sessions = []

    def host(request, emit, ask, cancelled):
        sessions.append(request["session_id"])
        emit({"type": "session", "session_id": "saved-reflection", "model": "host-model"})
        return {
            "state": "interrupted" if len(sessions) == 1 else "completed",
            "session_id": "saved-reflection",
            "raw_final_text": "```\nreplacement\n```",
        }

    assert collect_reflection(reflection, host)["state"] == "interrupted"
    assert collect_reflection(reflection, host)["state"] == "completed"
    assert sessions == [None, "saved-reflection"]


def test_reflection_reply_crash_replays_checkpoint_without_another_host_turn(
    reflection, monkeypatch
):
    from agentagon.experiments.host_bridge import HostBridge

    calls = []

    def host(request, emit, ask, cancelled):
        calls.append(request)
        emit({"type": "session", "session_id": "saved-reflection", "model": "host-model"})
        return {
            "state": "completed",
            "session_id": "saved-reflection",
            "raw_final_text": "replacement",
        }

    reply = HostBridge.reply

    def failed_reply(*args, **kwargs):
        raise OSError("simulated interruption after checkpoint")

    monkeypatch.setattr(HostBridge, "reply", failed_reply)
    with pytest.raises(OSError, match="checkpoint"):
        collect_reflection(reflection, host)
    monkeypatch.setattr(HostBridge, "reply", reply)
    assert collect_reflection(reflection, host)["state"] == "completed"
    assert len(calls) == 1


def test_reflection_unresolved_claim_never_launches_a_duplicate(reflection):
    _, bridge, pending = reflection
    bridge.start(pending["request_id"])
    with pytest.raises(AuditError, match="before its native identity"):
        collect_reflection(reflection, lambda *args: pytest.fail("duplicate dispatch"))


@pytest.mark.parametrize("identity", ["wrong-model", "author-session"])
def test_reflection_rejects_wrong_model_or_author_session(reflection, identity):
    def host(request, emit, ask, cancelled):
        emit(
            {
                "type": "session",
                "session_id": "author-session" if identity == "author-session" else "dedicated",
                "model": "wrong" if identity == "wrong-model" else "host-model",
            }
        )
        pytest.fail("reflection should stop before authoring")

    with pytest.raises(AuditError):
        collect_reflection(reflection, host, forbidden_session_id="author-session")
