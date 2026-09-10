"""Use authenticated Codex as the actual model deciding which tool call to make."""

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

MODEL = "gpt-5.5"


def decide(tasks):
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("Install Codex CLI and sign in before running the live benchmark")
    action = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tool": {"type": "string", "enum": ["create_ticket"]},
            "request_id": {"type": "string"},
            "title": {"type": "string"},
        },
        "required": ["tool", "request_id", "title"],
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"actions": {"type": "array", "items": action}},
        "required": ["actions"],
    }
    prompt = (
        "You are the decision step of a small support agent. For each user request, choose "
        "one create_ticket action with its request_id and the exact requested title. "
        "Return the actions in input order. Do not execute tools yourself: the application "
        "will execute your returned actions against its ticket service.\nRequests:\n"
        + json.dumps(tasks)
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agentagon-ticket-model-") as directory:
        schema_path = Path(directory) / "response-schema.json"
        schema_path.write_text(json.dumps(schema))
        result = subprocess.run(
            [
                codex,
                "exec",
                "--ignore-user-config",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--skip-git-repo-check",
                "--model",
                MODEL,
                "--color",
                "never",
                "--json",
                "--output-schema",
                str(schema_path),
                "-",
            ],
            input=prompt,
            text=True,
            capture_output=True,
            cwd=directory,
            timeout=120,
        )
    if result.returncode:
        raise RuntimeError(
            f"Codex model call failed (exit {result.returncode}); verify codex login status and model access"
        )
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    messages = [
        event["item"]["text"]
        for event in events
        if event.get("type") == "item.completed"
        and event.get("item", {}).get("type") == "agent_message"
    ]
    completed = [event for event in events if event.get("type") == "turn.completed"]
    if not messages or not completed:
        raise RuntimeError("Codex did not complete a decision response")
    actions = json.loads(messages[-1])["actions"]
    if len(actions) != len(tasks):
        raise ValueError("Model returned the wrong number of tool decisions")
    return actions, {
        "provider": "codex-cli",
        "model_alias": MODEL,
        "resolved_model_version": None,
        "usage": completed[-1].get("usage"),
        "latency_seconds": time.monotonic() - started,
        "cost_usd": None,
    }
