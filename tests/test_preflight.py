"""Cheap gates skip expensive work without manufacturing verified measurements."""

import json

import pytest
from support.experiments import baseline, executions, git, propose, verify
from support.runners import command, request

from agentagon.core.records import AuditError
from agentagon.experiments import engine, inspection, runners


def test_failed_candidate_preflight_skips_benchmark_and_cannot_be_selected(
    application, specification
):
    specification["checks"][0]["preflight"] = True
    start = baseline(application, specification)
    initial_executions = executions()
    created, _ = propose(application, start["run_id"], quality=0.1)
    failed = engine.run(application, start["run_id"], created["candidate_id"])
    assert failed["candidate"]["state"] == "failed"
    assert not failed["candidate"]["metrics"]
    assert executions() == initial_executions
    assert created["candidate_id"] not in failed["frontier"]
    trial = failed["candidate"]["trials"][0]
    assert "preflight check failed" in trial["error"]
    observed = application.read_artifact(trial["artifact"])
    assert observed["benchmark_output"] is None
    assert observed["state"] == "preflight_failed"
    assert observed["skipped_commands"] == [
        {"id": "benchmark", "role": "benchmark", "reason": "preflight quality-control failed"}
    ]
    detail = inspection.candidate(application, start["run_id"], created["candidate_id"])
    assert detail["trials"][0]["skipped_commands"] == observed["skipped_commands"]
    with pytest.raises(AuditError):
        engine.select(application, start["run_id"], created["candidate_id"])
    usage = failed["usage"]
    engine.run(application, start["run_id"], created["candidate_id"])
    assert engine.status(application, start["run_id"])["usage"]["trials"] == usage["trials"]
    improved, _ = propose(application, start["run_id"], quality=0.9, variant="valid")
    assert (
        verify(application, start["run_id"], improved["candidate_id"])["candidate"]["state"]
        == "verified"
    )


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_expected_baseline_failure_only_continues_on_deliberate_exit_one(
    application, specification, exit_code
):
    checks = application.root / "checks.py"
    checks.write_text(f"raise SystemExit({exit_code})\n")
    git(application.root, "add", "checks.py")
    git(
        application.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Baseline expectation fixture",
    )
    specification["checks"][0].update(preflight=True, baseline_expected="fail")
    start = engine.start(application, specification, "local")
    measured = engine.run(application, start["run_id"])
    if exit_code == 1:
        assert measured["candidate"]["state"] == "awaiting_review"
        assert len(executions()) == 3
        assert measured["candidate"]["checks"][0]["expected"] == "fail"
    else:
        assert measured["candidate"]["state"] == "failed"
        assert not executions()


def test_runner_retains_skipped_commands_and_replays_without_execution(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    gate = {
        **command("gate", "check", "print('invalid'); raise SystemExit(1)"),
        "gate_exit_code": 0,
    }
    args = request([gate, *request()["commands"], command("post", "check", "print('postflight')")])
    attempt = tmp_path / "attempt"
    result = runners.execute({}, source, attempt, args)
    assert result["state"] == "preflight_failed"
    assert [r["id"] for r in result["results"]] == ["gate"]
    assert [r["role"] for r in result["skipped_commands"]] == ["benchmark", "check"]
    assert "invalid" in result["results"][0]["stdout"]
    assert runners.execute({}, source, attempt, args) == result
    assert len(list((attempt / "job").glob("*.stdout.log"))) == 1


def test_successful_preflight_keeps_postflight_and_measurements(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    args = request(
        [
            {**command("gate", "check", "print('preflight')"), "gate_exit_code": 0},
            *request()["commands"],
            command("post", "check", "raise SystemExit(1)"),
        ]
    )
    result = runners.execute({}, source, tmp_path / "attempt", args)
    assert result["state"] == "completed"
    assert [c["exit_code"] for c in result["results"]] == [0, 0, 1]
    assert json.loads(result["benchmark_output"])["metrics"] == {"score": 17}
    assert "skipped_commands" not in result


@pytest.mark.parametrize(
    "misuse", ["after-benchmark", "benchmark-gate", "boolean-exit", "arbitrary-exit"]
)
def test_invalid_gate_request_is_rejected_before_execution(tmp_path, misuse):
    source = tmp_path / "source"
    source.mkdir()
    gate = {**command("gate", "check", "print('gate')"), "gate_exit_code": 0}
    commands = [gate, *request()["commands"]]
    if misuse == "after-benchmark":
        commands.reverse()
    elif misuse == "benchmark-gate":
        commands = [{**request()["commands"][0], "gate_exit_code": 0}]
    else:
        gate["gate_exit_code"] = True if misuse == "boolean-exit" else 2
    with pytest.raises(AuditError, match="preflight gates"):
        runners.execute({}, source, tmp_path / "attempt", request(commands))
