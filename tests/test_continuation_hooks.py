"""Lifecycle callbacks supply context and bounded nudges, never model dispatch."""

import json

from click.testing import CliRunner

from agentagon.cli.main import main
from agentagon.experiments import engine, hooks


def payload(workspace, event="Stop", session="session-1", **extra):
    return {"cwd": str(workspace.root), "session_id": session, "hook_event_name": event, **extra}


def test_only_bound_session_can_continue_and_unchanged_progress_stops_nudging(
    application, specification
):
    started = engine.start(application, specification, "local")
    assert hooks.handle(payload(application)) == {}
    bound = hooks.bind(application, started["run_id"], "session-1", "codex")
    assert bound["continuation"] == "manual_resume"
    assert hooks.handle(payload(application, session="another-session")) == {}
    first = hooks.handle(payload(application))
    assert first["decision"] == "block"
    assert hooks.handle(payload(application, stop_hook_active=True)) == {}
    # A host restart/rebind must not reset the no-progress guard.
    assert (
        hooks.bind(application, started["run_id"], "session-1", "codex")["continuation"]
        == "observed_native_hook"
    )
    assert hooks.handle(payload(application)) == {}
    engine.run(application, started["run_id"])
    assert hooks.handle(payload(application, stop_hook_active=True))["decision"] == "block"


def test_restoration_supplies_context_without_launch_and_stop_suppresses_hook(
    application, specification
):
    started = engine.start(application, specification, "local")
    hooks.bind(application, started["run_id"], "session-1", "claude-code")
    restored = hooks.handle(payload(application, "SessionStart", source="compact"))
    assert "decision" not in restored
    assert restored["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert (
        hooks.bind(application, started["run_id"], "session-1", "claude-code")["continuation"]
        == "manual_resume"
    )
    assert engine.status(application, started["run_id"])["usage"]["trials"] == 0
    engine.stop(application, started["run_id"])
    assert hooks.handle(payload(application)) == {}


def test_pause_user_input_and_session_end_cannot_restart_work(application, specification):
    started = engine.start(application, specification, "local")
    hooks.bind(application, started["run_id"], "session-1", "codex", pause=True)
    assert hooks.handle(payload(application)) == {}
    hooks.bind(application, started["run_id"], "session-1", "codex")
    first = hooks.handle(payload(application))
    hooks.handle(payload(application, "UserPromptSubmit", prompt=first["reason"]))
    # Native generated continuation does not impersonate a separate user request.
    engine.run(application, started["run_id"])
    assert hooks.handle(payload(application))["decision"] == "block"
    hooks.handle(payload(application, "UserPromptSubmit", prompt="Please wait for my answer"))
    assert hooks.handle(payload(application)) == {}
    hooks.bind(application, started["run_id"], "session-1", "codex")
    hooks.handle(payload(application, "SessionEnd"))
    assert hooks.handle(payload(application)) == {}


def test_hook_cli_fails_quietly_on_malformed_or_unbound_inputs(application):
    runner = CliRunner()
    for content in ("not json", b"\xff", json.dumps(payload(application)), "x" * 1048577):
        result = runner.invoke(main, ["fix", "hook"], input=content)
        assert result.exit_code == 0
        assert json.loads(result.output) == {}
