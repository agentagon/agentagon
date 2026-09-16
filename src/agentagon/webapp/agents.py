"""Owned coding-agent sessions for the local application.

The adapter never resumes a host's most recent session. The application stores the
session event before subsequent work and supplies that exact id when resuming.
Only explicit permission responses approve host requests; closing a browser does
not cancel a session (the application owns the cancellation event).
"""

import asyncio
import fcntl
import importlib
import importlib.metadata
import importlib.util
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from agentagon.core.records import AuditError

_MAX_OUTPUT = 8 * 1024 * 1024
_MAX_LINE = 1024 * 1024
_SECRET_NAME = re.compile(r"key|token|secret|password|credential", re.I)
_READ_TOOLS = ["Read", "Glob", "Grep", "AskUserQuestion"]


def detect_agents() -> list[dict]:
    """Inspect local capabilities without starting a model session or exposing auth data."""
    result = []
    for name in ("codex", "claude"):
        executable = shutil.which(name)
        version = None
        authenticated = None
        if executable:
            try:
                probe = subprocess.run(
                    [executable, "--version"], capture_output=True, text=True, timeout=3
                )
                match = re.search(r"\d+\.\d+\.\d+[\w.+-]*", probe.stdout)
                version = match[0] if match and probe.returncode == 0 else None
                if name == "codex":
                    authenticated = (
                        subprocess.run(
                            [executable, "login", "status"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=3,
                        ).returncode
                        == 0
                    )
            except (OSError, subprocess.SubprocessError):
                pass
        sdk_available = importlib.util.find_spec("claude_agent_sdk") is not None
        item = {
            "agent": name,
            "available": bool(executable) if name == "codex" else sdk_available,
            "executable": executable,
            "version": version,
            "authentication": "local-cli" if name == "codex" else "api-key",
            "authenticated": authenticated
            if name == "codex"
            else bool(os.environ.get("ANTHROPIC_API_KEY")),
        }
        if name == "claude":
            item["sdk_available"] = sdk_available
            item["sdk_version"] = None
            if sdk_available:
                with suppress(importlib.metadata.PackageNotFoundError):
                    item["sdk_version"] = importlib.metadata.version("claude-agent-sdk")
            else:
                item["unavailable_reason"] = (
                    "Install agentagon[claude] to use the Claude Agent SDK."
                )
        elif not executable:
            item["unavailable_reason"] = "Install the Codex CLI and sign in with codex login."
        result.append(item)
    return result


def _codex_catalog(client):
    models, cursors, cursor = [], set(), None
    for _ in range(20):
        page = client.rpc(
            "model/list",
            {"limit": 100, "includeHidden": False, **({"cursor": cursor} if cursor else {})},
        )
        if not isinstance(page.get("data"), list):
            raise AuditError("Codex returned an invalid model catalog.")
        for model in page["data"]:
            if not isinstance(model, dict) or not isinstance(model.get("model"), str):
                raise AuditError("Codex returned an invalid model entry.")
            if model.get("hidden"):
                continue
            models.append(
                {
                    "id": model["model"],
                    "catalog_id": model.get("id", model["model"]),
                    "display_name": model.get("displayName") or model["model"],
                    "description": model.get("description", ""),
                    "is_default": model.get("isDefault", False),
                    "reasoning_efforts": [
                        item["reasoningEffort"]
                        for item in model.get("supportedReasoningEfforts", [])
                        if isinstance(item, dict) and isinstance(item.get("reasoningEffort"), str)
                    ],
                    "default_reasoning_effort": model.get("defaultReasoningEffort"),
                }
            )
        cursor = page.get("nextCursor")
        if not cursor:
            return models
        if not isinstance(cursor, str) or cursor in cursors:
            raise AuditError("Codex model pagination did not advance.")
        cursors.add(cursor)
    raise AuditError("Codex model catalog exceeded the page limit.")


def codex_models(executable=None):
    """Read the installed host's actual catalog without creating a model session."""
    run = _Run(
        {"cwd": str(Path.cwd()), "timeout_seconds": 15, "executable": executable},
        lambda event: None,
        lambda question: {"decision": "decline"},
        threading.Event(),
    )
    client = None
    try:
        client = _Codex(run)
        client.rpc("initialize", {"clientInfo": {"name": "agentagon", "version": "0.1.3"}})
        client.send({"method": "initialized"})
        return _codex_catalog(client)
    except (OSError, AuditError) as exc:
        raise AuditError(run.safe(str(exc))) from None
    finally:
        if client:
            client.close()


def _choose_codex_model(model, models):
    if not isinstance(model, str) or not model.strip():
        raise AuditError("Choose an available Codex model.")
    selected = next(
        (entry for entry in models if model in (entry["id"], entry["catalog_id"])), None
    )
    if selected is None:
        raise AuditError(
            "Selected model is not available in this Codex installation. Refresh the model list and choose an available OpenAI model."
        )
    return selected["id"]


def validate_codex_model(model, executable=None):
    """Return the host's canonical model identifier, rejecting stale selections."""
    return _choose_codex_model(model, codex_models(executable))


def run_reflection(workspace, owner_id, request_id, **kwargs):
    """Serialize collection of a durable native reflection across service processes."""
    from agentagon.experiments.host_bridge import HostBridge

    if not isinstance(request_id, str) or not re.fullmatch(r"host_[0-9a-f]{24}", request_id):
        raise AuditError("invalid native reflection request identity")
    bridge = HostBridge(workspace, owner_id)
    # snapshot verifies the owner before a path is created or a process is launched.
    if request_id not in bridge.snapshot()["requests"]:
        raise AuditError("reflection request not found in this workflow")
    descriptor = os.open(
        bridge.directory / f"{request_id}.native.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AuditError("reflection native session is already being collected") from exc
        return _run_reflection(workspace, owner_id, request_id, **kwargs)


def _run_reflection(
    workspace,
    owner_id,
    request_id,
    *,
    emit,
    ask,
    cancelled,
    timeout_seconds,
    execute=None,
    api_key=None,
    executable=None,
    environment=None,
    forbidden_session_id=None,
):
    """Run or reconcile one stateless GEPA call; private raw text stays in evidence."""
    from agentagon.experiments.host_bridge import HostBridge

    bridge = HostBridge(workspace, owner_id)
    saved = bridge.snapshot()["requests"].get(request_id)
    if (
        saved is None
        or saved["role"] != "proposal"
        or saved["payload"].get("protocol") != "gepa-reflection-v1"
    ):
        raise AuditError("reflection requires an exact pending GEPA request")
    saved = bridge.request(
        saved["key"],
        **{
            key: saved[key]
            for key in ("source", "evaluator", "role", "scope", "host", "model", "payload", "stage")
        },
    )
    if saved["state"] == "completed":
        return {
            "state": "completed",
            "session_id": saved.get("native_session", {}).get("session_id"),
        }
    if saved["state"] == "cancelled" or cancelled.is_set():
        return {
            "state": "cancelled",
            "session_id": saved.get("native_session", {}).get("session_id"),
        }
    prompt = saved["payload"].get("prompt")
    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or len(prompt.encode("utf-8")) > _MAX_OUTPUT
    ):
        raise AuditError(
            "GEPA reflection requires a bounded text prompt; multimodal prompts are unsupported"
        )
    agent = "claude" if saved["host"] == "claude-code" else saved["host"]
    if agent not in {"codex", "claude"}:
        raise AuditError("GEPA reflection requires a supported native coding host")
    claimed = bridge.start(request_id, timeout_seconds=timeout_seconds)
    session = claimed.get("native_session", {}).get("session_id")
    if claimed.get("terminal_output"):
        response = workspace.read_artifact(claimed["terminal_output"])
    else:
        if claimed["replay"] and not session:
            raise AuditError(
                "reflection was interrupted before its native identity was saved; reconcile or cancel the request rather than launching a duplicate"
            )
        remaining = min(timeout_seconds, claimed["deadline"] - time.time())
        if remaining <= 0:
            raise AuditError(
                "reflection deadline expired; cancel the retained request or start a new bounded task"
            )

        def progress(event):
            if event.get("type") == "session":
                if event["session_id"] == forbidden_session_id:
                    raise AuditError("reflection cannot reuse the workflow author's session")
                bridge.bind_session(
                    request_id, session_id=event["session_id"], model=event.get("model")
                )
                event = {**event, "type": "reflection_session"}
            emit({**event, "request_id": request_id})

        request = {
            "agent": agent,
            "model": saved["model"],
            "cwd": str(workspace.root),
            "prompt": prompt,
            "session_id": session,
            "sandbox": "read-only",
            "response_mode": "raw-final",
            "timeout_seconds": remaining,
            "env": environment or {},
        }
        if api_key:
            request["api_key"] = api_key
        if executable:
            request["executable"] = executable
        outcome = (execute or run_agent)(
            request,
            progress,
            lambda question: {"decision": "decline"}
            if question.get("kind") == "approval"
            else ask(question),
            cancelled,
        )
        session = (
            bridge.snapshot()["requests"][request_id].get("native_session", {}).get("session_id")
        )
        if outcome.get("session_id") != session:
            raise AuditError("reflection output did not belong to its bound native session")
        if outcome.get("state") != "completed":
            return {"state": outcome.get("state", "interrupted"), "session_id": session}
        if cancelled.is_set():
            return {"state": "cancelled", "session_id": session}
        checkpoint = bridge.checkpoint_output(request_id, outcome.get("raw_final_text"))
        response = workspace.read_artifact(checkpoint["terminal_output"])
    bridge.reply(
        request_id,
        response,
        host=saved["host"],
        model=saved["model"],
        binding_digest=saved["binding_digest"],
    )
    return {"state": "completed", "session_id": session}


class _Stopped(Exception):
    pass


class _Run:
    def __init__(self, request, emit, ask, cancelled):
        self.request = request
        self.emit_callback = emit
        self.ask_callback = ask
        self.cancelled = cancelled
        self.deadline = time.monotonic() + request["timeout_seconds"]
        self.session_id = request.get("session_id")
        self.secrets = {
            value for key, value in os.environ.items() if _SECRET_NAME.search(key) and value
        }
        if request.get("api_key"):
            self.secrets.add(request["api_key"])
        self.output_bytes = 0
        self.text = []
        self.raw_final = None
        self.raw_last = None

    def safe(self, value):
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, "[redacted]")
            return re.sub(
                r"(?i)((?:api[_-]?key|authorization|password|secret|access[_-]?token)"
                r"[\s\"']*[:=][\s\"']*)(?:Bearer\s+)?[^\s\"',}]+",
                r"\1[redacted]",
                value,
            )
        if isinstance(value, dict):
            return {
                str(key): "[redacted]" if _SECRET_NAME.search(str(key)) else self.safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (tuple, list)):
            return [self.safe(item) for item in value]
        return value

    def check(self):
        if self.cancelled.is_set():
            raise _Stopped
        if time.monotonic() >= self.deadline:
            raise AuditError(
                "Coding-agent time limit reached. Resume the saved session explicitly."
            )

    def emit(self, event):
        self.emit_callback(self.safe(event))

    def session(self, session_id, model=None):
        if not isinstance(session_id, str) or not session_id:
            raise AuditError("Coding agent did not provide a session id.")
        if self.session_id and session_id != self.session_id:
            raise AuditError("Coding agent resumed a different session; no further work was sent.")
        self.session_id = session_id
        self.emit(
            {
                "type": "session",
                "session_id": session_id,
                **({"model": model} if isinstance(model, str) and model else {}),
            }
        )

    def message(self, text, *, phase=None):
        if not isinstance(text, str) or not text:
            return
        # Emit complete assistant messages so credentials split across token deltas
        # cannot leak into separately persisted events. Tool progress remains live.
        self.output_bytes += len(text.encode("utf-8"))
        if self.output_bytes > _MAX_OUTPUT:
            raise AuditError("Coding-agent output exceeded the application limit.")
        if self.request.get("response_mode") == "raw-final":
            if phase == "final_answer":
                self.raw_final = text
            elif phase is None:
                self.raw_last = text
        text = self.safe(text)
        self.text.append(text)
        self.emit({"type": "message", "text": text})

    def ask(self, request):
        # Browser responses may take arbitrarily long. A daemon waiter allows the
        # owning job to cancel or time out while an approval is still pending.
        answer_queue = queue.Queue(maxsize=1)

        def wait():
            try:
                answer_queue.put((True, self.ask_callback(self.safe(request))))
            except Exception as exc:
                answer_queue.put((False, exc))

        threading.Thread(target=wait, daemon=True, name="agentagon-agent-input").start()
        while True:
            self.check()
            try:
                ok, answer = answer_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if not ok:
                raise AuditError("The application could not resolve the agent's input request.")
            self.check()
            return answer if isinstance(answer, dict) else {}

    def result(self, state):
        result = {"session_id": self.session_id, "text": "\n\n".join(self.text), "state": state}
        if self.request.get("response_mode") == "raw-final" and state == "completed":
            result["raw_final_text"] = (
                self.raw_final if self.raw_final is not None else self.raw_last
            )
        return result


def run_agent(
    request: dict,
    emit: Callable[[dict], None],
    ask: Callable[[dict], dict],
    cancelled: threading.Event,
) -> dict:
    """Run one turn, forwarding progress and interactive permission requests.

    Approval answers accept only ``approved: True`` or ``decision: 'accept'``.
    Question answers accept an ``answers`` mapping or a plain ``text`` response.
    Credentials are transient input and are redacted from all outgoing events.
    """
    if not isinstance(request, dict) or request.get("agent") not in ("codex", "claude"):
        raise AuditError("Choose Codex or Claude as the coding agent.")
    request = dict(request)
    if request.get("response_mode", "messages") not in {"messages", "raw-final"}:
        raise AuditError("unsupported coding-agent response mode")
    # An empty model selector means the host's configured default.
    if request.get("model") == "":
        request["model"] = None
    cwd = request.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise AuditError("Coding-agent cwd must be an existing absolute project directory.")
    request["cwd"] = str(Path(cwd).resolve())
    if not isinstance(request.get("prompt"), str) or not request["prompt"].strip():
        raise AuditError("A coding-agent prompt is required.")
    request.setdefault("sandbox", "workspace-write")
    if request["sandbox"] not in ("read-only", "workspace-write"):
        raise AuditError("Coding-agent sandbox must be read-only or workspace-write.")
    timeout = request.get("timeout_seconds", 1800)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (float, int))
        or not math.isfinite(timeout)
        or timeout <= 0
        or timeout > 86400
    ):
        raise AuditError("Coding-agent timeout must be between 0 and 86400 seconds.")
    request["timeout_seconds"] = timeout
    environment = request.get("env", {})
    if not isinstance(environment, dict) or set(environment) - {"AGENTAGON_CONFIG"}:
        raise AuditError("Unsupported coding-agent environment override.")
    for value in environment.values():
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_file():
            raise AuditError("Coding-agent configuration must be an existing absolute file.")
    for key in ("session_id", "model", "api_key", "executable"):
        if key in request and request[key] is not None:
            if not isinstance(request[key], str) or not request[key].strip():
                raise AuditError(f"Coding-agent {key} must be a nonempty string.")
    run = _Run(request, emit, ask, cancelled)
    try:
        run.check()
        if request["agent"] == "codex":
            return _codex(run)
        return asyncio.run(_claude(run))
    except _Stopped:
        return run.result("cancelled")
    except AuditError as exc:
        raise AuditError(run.safe(str(exc))) from None
    except Exception as exc:
        raise AuditError(f"Coding-agent session failed: {run.safe(str(exc))}") from None


def _approved(answer):
    return answer.get("approved") is True or answer.get("decision") == "accept"


def _question_answers(params, answer, *, codex):
    answers = answer.get("answers")
    if not isinstance(answers, dict):
        answers = {}
    normalized = {}
    for question in params.get("questions", []):
        key = question.get("id") if codex else question.get("question")
        if not isinstance(key, str):
            continue
        value = answers.get(key, answer.get("text", ""))
        if isinstance(value, dict):
            value = value.get("answers", [])
        if codex:
            values = value if isinstance(value, list) else [value]
            normalized[key] = {"answers": [str(item) for item in values if item != ""]}
        else:
            normalized[key] = value if isinstance(value, (str, list)) else str(value)
    return normalized


class _Codex:
    def __init__(self, run):
        self.run = run
        self.messages = queue.Queue(maxsize=128)
        self.closed = threading.Event()
        self.sequence = 0
        self.turn_id = None
        self.finished = None
        self.pending_completion = None
        self.stderr = bytearray()
        executable = run.request.get("executable") or shutil.which("codex")
        if not executable:
            raise AuditError("Codex CLI was not found. Install it and run codex login.")
        self.process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            cwd=run.request["cwd"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={**os.environ, **run.request.get("env", {})},
        )
        threading.Thread(target=self.read, daemon=True, name="agentagon-codex-output").start()
        threading.Thread(target=self.drain, daemon=True, name="agentagon-codex-errors").start()

    def enqueue(self, message):
        while not self.closed.is_set():
            try:
                self.messages.put(message, timeout=0.1)
                return
            except queue.Full:
                continue

    def read(self):
        total = 0
        try:
            while not self.closed.is_set():
                line = self.process.stdout.readline(_MAX_LINE + 1)
                if not line:
                    self.enqueue(None)
                    return
                total += len(line)
                if len(line) > _MAX_LINE or total > _MAX_OUTPUT:
                    self.enqueue(AuditError("Codex output exceeded the application limit."))
                    return
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError
                except (UnicodeError, ValueError):
                    self.enqueue(AuditError("Codex emitted an invalid protocol message."))
                    return
                self.enqueue(message)
        except (OSError, ValueError):
            self.enqueue(None)

    def drain(self):
        try:
            while chunk := self.process.stderr.read(4096):
                # Keep only a bounded diagnostic tail. Never emit raw stderr.
                self.stderr.extend(chunk)
                del self.stderr[:-16384]
        except (OSError, ValueError):
            pass

    def send(self, value):
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        self.process.stdin.flush()

    def receive(self):
        while True:
            self.run.check()
            try:
                message = self.messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if message is None:
                raise AuditError("Codex disconnected before the turn completed. Resume explicitly.")
            if isinstance(message, Exception):
                raise message
            return message

    def rpc(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if "method" not in message and message.get("id") == request_id:
                if "error" in message:
                    detail = message["error"].get("message", "Unknown host error")
                    raise AuditError(f"Codex {method} failed: {detail}")
                return message.get("result", {})
            self.handle(message)

    def handle(self, message):
        method = message.get("method", "")
        params = message.get("params") or {}
        if "id" in message and method:
            self.server_request(message["id"], method, params)
            return
        if params.get("threadId") not in (None, self.run.session_id):
            return
        if method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage":
                self.run.message(item.get("text", ""), phase=item.get("phase"))
            else:
                self.run.emit({"type": "progress", "text": f"Completed {item.get('type', 'tool')}"})
        elif method == "item/started":
            item = params.get("item", {})
            if item.get("type") not in ("agentMessage", "reasoning", "userMessage"):
                self.run.emit({"type": "progress", "text": f"Running {item.get('type', 'tool')}"})
        elif method == "turn/completed":
            turn = params.get("turn", {})
            if not self.turn_id:
                self.pending_completion = message
                return
            if turn.get("id") != self.turn_id:
                return
            status = turn.get("status")
            if status == "failed" or turn.get("error"):
                error = turn.get("error") or {}
                raise AuditError(f"Codex turn failed: {error.get('message', 'Unknown host error')}")
            if status not in ("completed", "interrupted"):
                raise AuditError("Codex returned an unknown completion status.")
            self.finished = status
        elif method == "error" and not params.get("willRetry", False):
            error = params.get("error") or {}
            raise AuditError(f"Codex failed: {error.get('message', 'Unknown host error')}")

    def server_request(self, request_id, method, params):
        approvals = ("item/commandExecution/requestApproval", "item/fileChange/requestApproval")
        supported = (*approvals, "item/permissions/requestApproval", "item/tool/requestUserInput")
        if method not in supported and method != "mcpServer/elicitation/request":
            self.send(
                {"id": request_id, "error": {"code": -32601, "message": "Unsupported request"}}
            )
            return
        is_question = method in ("item/tool/requestUserInput", "mcpServer/elicitation/request")
        answer = self.run.ask(
            {
                **params,
                "kind": "question" if is_question else "approval",
                "request_id": str(request_id),
                "method": method,
            }
        )
        if method in approvals:
            result = {"decision": "accept" if _approved(answer) else "decline"}
        elif method == "item/permissions/requestApproval":
            result = {
                "permissions": params.get("permissions", {}) if _approved(answer) else {},
                "scope": "turn",
            }
        elif method == "mcpServer/elicitation/request":
            # Never derive structured MCP authorization from free text.
            result = {
                "action": "accept" if _approved(answer) else "decline",
                "content": answer.get("content") if _approved(answer) else None,
            }
        else:
            result = {"answers": _question_answers(params, answer, codex=True)}
        self.send({"id": request_id, "result": result})

    def close(self):
        self.closed.set()
        # This process is a fresh session leader, so terminate only its owned tree.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=2)
        # A descendant can outlive the session leader and still hold its pipes.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(self.process.pid, signal.SIGKILL)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            with suppress(OSError):
                stream.close()


def _codex(run):
    client = _Codex(run)
    try:
        client.rpc("initialize", {"clientInfo": {"name": "agentagon", "version": "0.1.3"}})
        client.send({"method": "initialized"})
        account = client.rpc("account/read", {"refreshToken": False})
        if account.get("requiresOpenaiAuth") and not account.get("account"):
            raise AuditError("Sign in with codex login before starting a coding-agent task.")
        model = run.request.get("model")
        if model:
            model = _choose_codex_model(model, _codex_catalog(client))
        params = {
            "cwd": run.request["cwd"],
            "sandbox": run.request["sandbox"],
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
        }
        if model:
            params["model"] = model
        if run.session_id:
            params["threadId"] = run.session_id
        response = client.rpc("thread/resume" if run.session_id else "thread/start", params)
        run.session(response.get("thread", {}).get("id"), response.get("model"))
        response = client.rpc(
            "turn/start",
            {
                "threadId": run.session_id,
                "input": [{"type": "text", "text": run.request["prompt"]}],
            },
        )
        client.turn_id = response.get("turn", {}).get("id")
        if not client.turn_id:
            raise AuditError("Codex did not return a turn id.")
        if client.pending_completion:
            client.handle(client.pending_completion)
        while client.finished is None:
            client.handle(client.receive())
        return run.result(client.finished)
    except _Stopped:
        if run.session_id and client.turn_id:
            with suppress(OSError):
                client.send(
                    {
                        "id": "cancel",
                        "method": "turn/interrupt",
                        "params": {"threadId": run.session_id, "turnId": client.turn_id},
                    }
                )
        raise
    finally:
        client.close()


async def _claude(run):
    try:
        sdk = importlib.import_module("claude_agent_sdk")
    except ImportError:
        raise AuditError("Install agentagon[claude] to use the Claude Agent SDK.") from None
    api_key = run.request.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AuditError("Configure an Anthropic API key. Subscription authentication is not used.")
    run.secrets.add(api_key)

    async def can_use_tool(tool_name, input_data, context):
        question = tool_name == "AskUserQuestion"
        answer = await asyncio.to_thread(
            run.ask,
            {
                **input_data,
                "kind": "question" if question else "approval",
                "request_id": getattr(context, "tool_use_id", None) or str(uuid.uuid4()),
                "tool": tool_name,
            },
        )
        if question and answer.get("decision") != "decline":
            return sdk.PermissionResultAllow(
                updated_input={
                    **input_data,
                    "answers": _question_answers(input_data, answer, codex=False),
                }
            )
        if _approved(answer):
            return sdk.PermissionResultAllow(updated_input=input_data)
        return sdk.PermissionResultDeny(message="The user declined this action.")

    async def keep_input_open(input_data, tool_use_id, context):
        return {"continue_": True}

    options = {
        "cwd": run.request["cwd"],
        "permission_mode": "default",
        "setting_sources": ["user", "project", "local"],
        "system_prompt": {"type": "preset", "preset": "claude_code"},
        "can_use_tool": can_use_tool,
        "hooks": {"PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[keep_input_open])]},
        "env": {
            **run.request.get("env", {}),
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR": "",
            "CLAUDE_CODE_USE_BEDROCK": "0",
            "CLAUDE_CODE_USE_VERTEX": "0",
            "CLAUDE_CODE_USE_FOUNDRY": "0",
        },
        "max_buffer_size": _MAX_LINE,
        "stderr": lambda line: None,
        "sandbox": {"enabled": True, "allowUnsandboxedCommands": False},
    }
    if run.request["sandbox"] == "read-only":
        options["tools"] = _READ_TOOLS
        options["disallowed_tools"] = ["Bash", "Write", "Edit", "NotebookEdit", "Agent", "Task"]
    if run.request.get("model"):
        options["model"] = run.request["model"]
    if run.request.get("executable"):
        options["cli_path"] = run.request["executable"]
    if run.session_id:
        options["resume"] = run.session_id
    result_state = None

    async def receive(client):
        nonlocal result_state
        async for message in client.receive_response():
            run.check()
            kind = type(message).__name__
            if kind == "SystemMessage" and getattr(message, "subtype", None) == "init":
                run.session(message.data.get("session_id"), message.data.get("model"))
            elif kind == "AssistantMessage":
                if getattr(message, "error", None):
                    raise AuditError(f"Claude assistant failed: {message.error}")
                for block in message.content:
                    if type(block).__name__ == "TextBlock":
                        run.message(block.text)
                    elif type(block).__name__ == "ToolUseBlock":
                        run.emit({"type": "progress", "text": f"Running {block.name}"})
            elif kind == "ResultMessage":
                session_id = getattr(message, "session_id", None)
                if session_id and not run.session_id:
                    run.session(session_id)
                if session_id and session_id != run.session_id:
                    raise AuditError("Claude returned a different session id.")
                if getattr(message, "is_error", False):
                    raise AuditError(
                        f"Claude turn failed: {getattr(message, 'result', None) or message.subtype}"
                    )
                if not run.session_id:
                    raise AuditError("Claude did not return a session id.")
                if run.request.get("response_mode") == "raw-final":
                    final = getattr(message, "result", None)
                    if not isinstance(final, str) or not final.strip():
                        raise AuditError("Claude did not provide terminal response text.")
                    if len(final.encode("utf-8")) > _MAX_OUTPUT:
                        raise AuditError("Coding-agent output exceeded the application limit.")
                    run.raw_final = final
                result_state = "completed"

    async def drive():
        async with sdk.ClaudeSDKClient(options=sdk.ClaudeAgentOptions(**options)) as client:
            await client.query(run.request["prompt"])
            receiver = asyncio.create_task(receive(client))
            try:
                while not receiver.done():
                    run.check()
                    await asyncio.wait({receiver}, timeout=0.1)
                await receiver
            except (_Stopped, AuditError, asyncio.CancelledError):
                with suppress(Exception):
                    await asyncio.wait_for(client.interrupt(), timeout=2)
                raise
            finally:
                if not receiver.done():
                    receiver.cancel()
                with suppress(asyncio.CancelledError, _Stopped, AuditError):
                    await receiver

    driver = asyncio.create_task(drive())
    try:
        # Also observe cancellation during SDK connection and query startup.
        while not driver.done():
            run.check()
            await asyncio.wait({driver}, timeout=0.1)
        await driver
    finally:
        if not driver.done():
            driver.cancel()
        with suppress(asyncio.CancelledError, _Stopped, AuditError):
            await driver
    if result_state != "completed":
        raise AuditError("Claude disconnected before the turn completed. Resume explicitly.")
    return run.result(result_state)
