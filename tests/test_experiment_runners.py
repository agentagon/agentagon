"""Observable durable runner behavior; live remote checks require explicit opt-in."""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from support.runners import FakeRemote, command, request

from agentagon.core.records import AuditError
from agentagon.experiments import checkouts, runners


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "input.txt").write_text("unchanged")
    return root


def test_local_result_is_durable_and_execute_is_idempotent(source, tmp_path):
    attempt = tmp_path / "attempt"
    args = {**request(), "inputs_digest": "inputs-hash", "profile_digest": "profile-hash"}
    result = runners.execute({}, source, attempt, args)
    assert result["inputs_digest"] == "inputs-hash"
    assert result["profile_digest"] == "profile-hash"
    assert result["state"] == "completed"
    assert result["runtime_identity"]["python"] == sys.executable
    assert result["runtime_identity"]["python_version"]
    assert json.loads(result["benchmark_output"])["metrics"] == {"score": 17}
    assert result["results"][0]["stdout"] == "observed\n"
    assert result["source_manifest_before"] == result["source_manifest_after"]
    assert runners.execute({}, source, attempt, args) == result
    assert len(list((attempt / "job").glob("*.stdout.log"))) == 1
    changed = {**request(), "seed": 18}
    with pytest.raises(AuditError, match="another request"):
        runners.execute({}, source, attempt, changed)


def test_setup_failure_prevents_benchmark_and_all_checks_run(source, tmp_path):
    commands = [command("setup", "setup", "raise SystemExit(4)"), *request()["commands"]]
    failed = runners.execute({}, source, tmp_path / "failed", request(commands))
    assert failed["state"] == "setup_failed"
    assert [entry["id"] for entry in failed["results"]] == ["setup"]
    commands = [
        *request()["commands"],
        command("check1", "check", "raise SystemExit(2)"),
        command("check2", "check", "print('last check')"),
    ]
    checked = runners.execute({}, source, tmp_path / "checked", request(commands))
    assert [entry["exit_code"] for entry in checked["results"]] == [0, 2, 0]


def test_deadline_kills_closed_pipe_process_and_its_child(source, tmp_path):
    child = "import signal,time,sys; from pathlib import Path; signal.signal(signal.SIGTERM,lambda *_:(Path('child.stopped').write_text('yes'),sys.exit(0))); Path('child.ready').touch(); time.sleep(30)"
    code = (
        "import os,subprocess,time; from pathlib import Path; p=subprocess.Popen(["
        + repr(sys.executable)
        + ",'-c',"
        + repr(child)
        + "]); "
        + "exec(\"while not Path('child.ready').exists(): time.sleep(.01)\"); os.close(1); os.close(2); time.sleep(30)"
    )
    started = time.monotonic()
    result = runners.execute(
        {},
        source,
        tmp_path / "attempt",
        request([command("bench", "benchmark", code)], timeout=0.4),
    )
    assert result["state"] == "timed_out"
    assert time.monotonic() - started < 4
    assert (source / "child.stopped").read_text() == "yes"


def test_cancel_running_worker(source, tmp_path):
    attempt = tmp_path / "attempt"
    outcome = {}
    args = request(
        [command("bench", "benchmark", "import time; print('started',flush=True); time.sleep(30)")],
        timeout=35,
    )
    thread = threading.Thread(
        target=lambda: outcome.update(runners.execute({}, source, attempt, args))
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not (attempt / "job" / "active.json").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    result = runners.cancel({}, attempt)
    thread.join(timeout=6)
    assert not thread.is_alive()
    assert result["state"] == "cancelled"
    assert outcome["state"] == "cancelled"


def test_redacts_split_secrets_and_bounds_logs(source, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEST_SECRET", "super-secret-value")
    code = "import os,time; secret=os.environ['TOKEN']; os.write(1,secret[:7].encode()); time.sleep(.1); os.write(1,secret[7:].encode()); print('x'*10000)"
    profile = {"env": {"TOKEN": "RUNNER_TEST_SECRET"}, "limits": {"max_output_bytes": 128}}
    result = runners.execute(
        profile, source, tmp_path / "attempt", request([command("bench", "benchmark", code)])
    )
    output = result["results"][0]["stdout"]
    assert "[REDACTED]" in output
    assert "super-secret-value" not in output
    assert len(output.encode()) <= 128
    assert result["results"][0]["stdout_truncated"]
    for path in (tmp_path / "attempt").rglob("*"):
        if path.is_file():
            assert b"super-secret-value" not in path.read_bytes()


def test_reconnect_after_controller_exits_does_not_repeat_command(source, tmp_path):
    attempt = tmp_path / "attempt"
    code = "import time; from pathlib import Path; p=Path('count'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); time.sleep(.6)"
    args = request([command("bench", "benchmark", code)])
    script = (
        "import json; from pathlib import Path; from agentagon.experiments.runners import execute; execute({},Path("
        + repr(str(source))
        + "),Path("
        + repr(str(attempt))
        + "),json.loads("
        + repr(json.dumps(args))
        + "))"
    )
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    controller = subprocess.Popen([sys.executable, "-c", script], env=environment)
    deadline = time.monotonic() + 5
    while not (source / "count").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    controller.terminate()
    controller.wait(timeout=5)
    result = runners.execute({}, source, attempt, args)
    assert result["state"] == "completed"
    assert (source / "count").read_text() == "1"


def test_manifest_detects_edits_and_preserves_binary_modes_symlinks(source, tmp_path):
    binary = source / "tool"
    binary.write_bytes(b"\x00\xff")
    binary.chmod(0o755)
    (source / "linked").symlink_to("tool")
    result = runners.execute(
        {},
        source,
        tmp_path / "attempt",
        request(
            [
                command(
                    "bench",
                    "benchmark",
                    "from pathlib import Path; Path('input.txt').write_text('changed')",
                )
            ]
        ),
    )
    assert result["source_manifest_before"]["linked"].startswith(
        hashlib.sha256(b"tool").hexdigest() + ":"
    )
    assert result["source_manifest_before"]["tool"].endswith(":493")
    assert (
        result["source_manifest_before"]["input.txt"]
        != result["source_manifest_after"]["input.txt"]
    )
    job = tmp_path / "unpacked"
    job.mkdir()
    (job / "source.tar").write_bytes(runners._archive(source, 1000000))
    runners.worker.unpack(job)
    assert (job / "source" / "tool").read_bytes() == binary.read_bytes()
    assert (job / "source" / "linked").is_symlink()
    assert (job / "source" / "tool").stat().st_mode & 0o111 == 0o111


@pytest.mark.parametrize("kind", ["ssh", "e2b"])
def test_remote_transport_roundtrip_and_cleanup(kind, source, tmp_path, monkeypatch):
    monkeypatch.setattr(runners, "_transport", FakeRemote)
    profile = {"runner": {"kind": kind, "host": "test", "remote_root": str(tmp_path / "remote")}}
    result = runners.execute(profile, source, tmp_path / "attempt", request())
    assert result["state"] == "completed"
    assert result["source_manifest_before"] == result["source_manifest_after"]
    assert json.loads(result["benchmark_output"])["metrics"]["score"] == 17
    assert not list((tmp_path / "remote").iterdir())
    assert not result["cleanup_pending"]


def test_unknown_remote_status_never_relaunches(source, tmp_path, monkeypatch):
    launches = []

    class Uncertain(FakeRemote):
        def command(self, action, env=None):
            if action == "launch":
                launches.append(action)
                raise ConnectionError("lost launch acknowledgement")
            if action == "status":
                raise ConnectionError("network unavailable")
            return super().command(action, env)

    monkeypatch.setattr(runners, "_transport", Uncertain)
    profile = {"runner": {"kind": "ssh", "host": "test", "remote_root": str(tmp_path / "remote")}}
    for _ in range(2):
        result = runners.execute(profile, source, tmp_path / "attempt", request())
        assert result["state"] == "interrupted"
    assert launches == ["launch"]


@pytest.mark.skipif(
    not os.environ.get("AGENTAGON_LIVE_SSH_HOST"),
    reason="set AGENTAGON_LIVE_SSH_HOST for an authorized SSH smoke test",
)
def test_live_ssh_smoke(source, tmp_path):
    profile = {"runner": {"kind": "ssh", "host": os.environ["AGENTAGON_LIVE_SSH_HOST"]}}
    args = request()
    args["commands"][0]["argv"][0] = "python3"
    result = runners.execute(profile, source, tmp_path / "attempt", args)
    assert result["state"] == "completed"
    assert json.loads(result["benchmark_output"])["metrics"]["score"] == 17
    assert not result["cleanup_pending"]


@pytest.mark.skipif(
    os.environ.get("AGENTAGON_LIVE_E2B") != "1",
    reason="set AGENTAGON_LIVE_E2B=1 to authorize a billable E2B smoke test",
)
def test_live_e2b_smoke(source, tmp_path):
    profile = {"runner": {"kind": "e2b"}}
    args = request(timeout=30)
    args["commands"][0]["argv"][0] = "python3"
    result = runners.execute(profile, source, tmp_path / "attempt", args)
    assert result["state"] == "completed"
    assert json.loads(result["benchmark_output"])["metrics"]["score"] == 17
    assert not result["cleanup_pending"]


def test_completed_result_does_not_require_expired_injected_credential(
    source, tmp_path, monkeypatch
):
    monkeypatch.setenv("TEMP_RUNNER_KEY", "ephemeral-token")
    profile = {"env": {"TOKEN": "TEMP_RUNNER_KEY"}}
    result = runners.execute(profile, source, tmp_path / "attempt", request())
    monkeypatch.delenv("TEMP_RUNNER_KEY")
    assert runners.execute(profile, source, tmp_path / "attempt", request()) == result


def test_missing_credential_fails_before_creating_descriptor(source, tmp_path, monkeypatch):
    monkeypatch.delenv("UNCONFIGURED_RUNNER_KEY", raising=False)
    profile = {"env": {"TOKEN": "UNCONFIGURED_RUNNER_KEY"}}
    with pytest.raises(AuditError, match="missing runner credential"):
        runners.execute(profile, source, tmp_path / "attempt", request())
    assert not (tmp_path / "attempt" / "executor.json").exists()


def test_prelaunch_cancellation_prevents_commands(source, tmp_path):
    attempt = tmp_path / "attempt"
    runners.cancel({}, attempt)
    result = runners.execute({}, source, attempt, request())
    assert result["state"] == "cancelled"
    assert result["results"] == []


def test_ssh_preserves_structured_arguments_and_secrets_use_stdin(monkeypatch):
    seen = []

    def call(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=b'{"pid":1}', stderr=b"")

    monkeypatch.setattr(runners.subprocess, "run", call)
    transport = runners.SSH(
        {"host": "configured-host"},
        {"remote_job": "/tmp/space and $(literal)", "owner_token": "owner"},
    )
    transport.command("launch", {"TOKEN": "secret-value"})
    argv, kwargs = seen[0]
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "secret-value" not in " ".join(argv)
    assert json.loads(kwargs["input"])["env"] == {"TOKEN": "secret-value"}
    assert runners.shlex.split(argv[-1])[-2:] == ["/tmp/space and $(literal)", "owner"]


def test_e2b_api_calls_keep_control_key_out_of_sandbox_env(monkeypatch):
    from types import SimpleNamespace

    seen = []
    sandbox = SimpleNamespace(sandbox_id="sandbox-1")
    sandbox.files = SimpleNamespace(
        make_dir=lambda *args, **kwargs: seen.append(("mkdir", args, kwargs)),
        write=lambda *args, **kwargs: seen.append(("write", args, kwargs)),
    )
    sandbox.commands = SimpleNamespace(
        run=lambda *args, **kwargs: (
            seen.append(("run", args, kwargs))
            or SimpleNamespace(pid=42, stdout='{"state":"running"}')
        )
    )
    api = SimpleNamespace(
        create=lambda **kwargs: seen.append(("create", (), kwargs)) or sandbox,
        connect=lambda *args, **kwargs: seen.append(("connect", args, kwargs)) or sandbox,
        kill=lambda *args, **kwargs: seen.append(("kill", args, kwargs)),
    )
    monkeypatch.setitem(sys.modules, "e2b", SimpleNamespace(Sandbox=api))
    monkeypatch.setenv("TEST_E2B_CONTROL_KEY", "control-secret")
    descriptor = {
        "remote_job": "/tmp/job",
        "owner_token": "owner",
        "attempt_id": "attempt",
        "sandbox_timeout_seconds": 120,
    }
    settings = {"api_key_env": "TEST_E2B_CONTROL_KEY"}
    transport = runners.E2B(settings, descriptor)
    transport.create()
    transport.put("/tmp/job/input", b"content")
    assert json.loads(transport.command("launch", {"TOKEN": "task-secret"}))["pid"] == 42
    transport = runners.E2B(settings, descriptor)
    transport.command("status")
    transport.cleanup()
    created = next(entry for entry in seen if entry[0] == "create")
    assert created[2]["secure"] is True
    assert created[2]["lifecycle"] == {"on_timeout": "kill", "auto_resume": False}
    launched = next(entry for entry in seen if entry[0] == "run" and entry[2].get("background"))
    assert launched[2]["envs"] == {"TOKEN": "task-secret"}
    assert "control-secret" not in str(launched)
    assert any(entry[0] == "connect" for entry in seen)
    assert any(entry[0] == "kill" for entry in seen)


def test_remote_cancel_collects_result_and_cleans_owned_directory(source, tmp_path, monkeypatch):
    monkeypatch.setattr(runners, "_transport", FakeRemote)
    profile = {"runner": {"kind": "ssh", "host": "test", "remote_root": str(tmp_path / "remote")}}
    attempt = tmp_path / "attempt"
    args = request([command("bench", "benchmark", "import time; time.sleep(30)")], timeout=35)
    outcome = {}
    thread = threading.Thread(
        target=lambda: outcome.update(runners.execute(profile, source, attempt, args))
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not list((tmp_path / "remote").glob("*/active.json")) and time.monotonic() < deadline:
        time.sleep(0.02)
    result = runners.cancel(profile, attempt)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result["state"] == "cancelled"
    assert not result["cleanup_pending"]
    assert not list((tmp_path / "remote").iterdir())
    assert json.loads((attempt / "collected.json").read_text())["state"] == "cancelled"


def test_failed_remote_cleanup_is_recoverable(source, tmp_path, monkeypatch):
    calls = []

    class FailingCleanup(FakeRemote):
        def cleanup(self):
            calls.append("cleanup")
            if len(calls) == 1:
                raise ConnectionError("network unavailable")
            super().cleanup()

    monkeypatch.setattr(runners, "_transport", FailingCleanup)
    profile = {"runner": {"kind": "ssh", "host": "test", "remote_root": str(tmp_path / "remote")}}
    result = runners.execute(profile, source, tmp_path / "attempt", request())
    assert result["state"] == "completed"
    assert result["cleanup_pending"]
    cleaned = runners.cleanup(profile, tmp_path / "attempt")
    assert not cleaned["cleanup_pending"]
    assert not list((tmp_path / "remote").iterdir())


def test_rejects_escaping_symlink_before_remote_transfer(source, tmp_path, monkeypatch):
    (source / "outside").symlink_to(tmp_path / "private.txt")
    with pytest.raises(ValueError, match="symlink escapes"):
        runners._archive(source, 1000000)


@pytest.mark.parametrize("manifest", [runners.worker.manifest, checkouts.source_manifest])
def test_source_manifest_records_files_modes_and_links(source, manifest):
    binary = b"\x00\xff\x80" * 400000
    (source / "input.txt").chmod(0o644)
    (source / "tool").write_bytes(binary)
    (source / "tool").chmod(0o755)
    (source / "nested").mkdir()
    (source / "nested" / "empty").touch(mode=0o600)
    (source / "link").symlink_to("input.txt")
    (source / "directory-link").symlink_to("nested", target_is_directory=True)
    (source / "dangling-link").symlink_to("missing")
    (source / ".git").mkdir()
    (source / ".git" / "ignored").write_text("private Git state")
    (source / "nested" / ".agentagon").write_text("private Agentagon state")

    assert manifest(source) == {
        "input.txt": hashlib.sha256(b"unchanged").hexdigest() + ":420",
        "tool": hashlib.sha256(binary).hexdigest() + ":493",
        "nested/empty": hashlib.sha256(b"").hexdigest() + ":384",
        "link": hashlib.sha256(b"input.txt").hexdigest() + ":511",
        "directory-link": hashlib.sha256(b"nested").hexdigest() + ":511",
        "dangling-link": hashlib.sha256(b"missing").hexdigest() + ":511",
    }


def test_host_source_manifest_preserves_escaping_link_error(source, tmp_path):
    (source / "outside").symlink_to(tmp_path / "private.txt")
    with pytest.raises(AuditError, match="execution snapshot contains an escaping symlink"):
        checkouts.source_manifest(source)


def test_source_archive_limit_rejects_large_file(source):
    target = source / "large"
    with target.open("wb") as stream:
        stream.truncate(2 * 1024 * 1024)
    with pytest.raises(AuditError, match="source snapshot exceeds"):
        runners._archive(source, 1024 * 1024)


def test_stale_pid_does_not_establish_worker_liveness(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "owner.json").write_text(json.dumps({"token": "owner", "pid": os.getpid()}))
    assert runners.worker.status(job, "owner")["state"] == "unknown"


def test_terminal_worker_error_is_distinct_from_recoverable_transport_loss(
    source, tmp_path, monkeypatch
):
    job = tmp_path / "job"
    job.mkdir()
    args = {**request(), "owner_token": "owner", "source": str(source)}
    (job / "request.json").write_text(json.dumps(args))

    def inaccessible(_):
        raise OSError("input source became unavailable")

    monkeypatch.setattr(runners.worker, "manifest", inaccessible)
    runners.worker.run(job, "owner")
    result = json.loads((job / "result.json").read_text())
    assert result["state"] == "interrupted"
    assert result["finalized"] is True
    assert "source became unavailable" in result["error"]


def test_cleanup_transport_failure_returns_pending_instead_of_raising(
    source, tmp_path, monkeypatch
):
    class BrokenCleanup(FakeRemote):
        def cleanup(self):
            raise RuntimeError("remote transport exception")

    monkeypatch.setattr(runners, "_transport", BrokenCleanup)
    profile = {"runner": {"kind": "ssh", "host": "test", "remote_root": str(tmp_path / "remote")}}
    runners.execute(profile, source, tmp_path / "attempt", request())
    result = runners.cleanup(profile, tmp_path / "attempt")
    assert result["cleanup_pending"]
    assert result["state"] == "completed"


def test_symlink_manifest_mode_is_portable(source):
    (source / "link").symlink_to("input.txt")
    assert runners.worker.manifest(source)["link"].endswith(":511")


def deadline_after(seconds):
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def test_absolute_deadline_bounds_local_execution(source, tmp_path):
    args = request([command("bench", "benchmark", "import time; time.sleep(30)")], timeout=30)
    args["deadline_at"] = deadline_after(0.3)
    started = time.monotonic()
    result = runners.execute({}, source, tmp_path / "attempt", args)
    assert result["state"] == "timed_out"
    assert result["finalized"]
    assert time.monotonic() - started < 3


def test_expired_absolute_deadline_prevents_remote_creation(source, tmp_path, monkeypatch):
    def unexpected(*args):
        raise AssertionError("expired attempt must not create a remote runner")

    monkeypatch.setattr(runners, "_transport", unexpected)
    args = {**request(), "deadline_at": deadline_after(-1)}
    result = runners.execute({"runner": {"kind": "e2b"}}, source, tmp_path / "attempt", args)
    assert result["state"] == "timed_out"
    assert result["results"] == []
    assert not result["cleanup_pending"]


def test_remote_staging_delay_cannot_reset_absolute_deadline(source, tmp_path, monkeypatch):
    class SlowStage(FakeRemote):
        def put(self, path, data):
            if path.endswith("worker.py"):
                time.sleep(0.3)
            super().put(path, data)

    monkeypatch.setattr(runners, "_transport", SlowStage)
    profile = {"runner": {"kind": "ssh", "host": "test", "remote_root": str(tmp_path / "remote")}}
    args = {**request(), "deadline_at": deadline_after(0.15)}
    result = runners.execute(profile, source, tmp_path / "attempt", args)
    assert result["state"] == "timed_out"
    assert result["results"] == []


def test_runtime_named_tracked_files_are_transported(source, tmp_path):
    for directory in ("build", "dist", "node_modules", ".venv", "__pycache__"):
        target = source / directory / "required.bin"
        target.parent.mkdir()
        target.write_bytes(b"\x00\xff")
    job = tmp_path / "job"
    job.mkdir()
    (job / "source.tar").write_bytes(runners._archive(source, 1000000))
    runners.worker.unpack(job)
    assert runners.worker.manifest(source) == runners.worker.manifest(job / "source")
    assert len(runners.worker.manifest(job / "source")) == 6


def test_setup_created_environment_symlinks_do_not_hide_original_file_mutations(source, tmp_path):
    args = request(
        [
            *request()["commands"],
            command(
                "runtime",
                "check",
                "import os,sys; from pathlib import Path; Path('.venv/bin').mkdir(parents=True); os.symlink(sys.executable,'.venv/bin/python'); Path('input.txt').write_text('changed')",
            ),
        ]
    )
    result = runners.execute({}, source, tmp_path / "attempt", args)
    assert result["state"] == "completed"
    assert (
        result["source_manifest_before"]["input.txt"]
        != result["source_manifest_after"]["input.txt"]
    )
    assert ".venv/bin/python" in result["source_manifest_after"]


def test_e2b_reconnect_preserves_original_sandbox_lifetime(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    clock = [1700000000.25]
    monkeypatch.setattr(runners.time, "time", lambda: clock[0])
    monkeypatch.setenv("TEST_E2B_CONTROL_KEY", "synthetic-control-key")
    descriptor = {"sandbox_timeout_seconds": 120, "attempt_id": "attempt", "owner_token": "owner"}
    seen = []
    sandbox = SimpleNamespace(sandbox_id="sandbox-with-fixed-expiry")

    def create(**kwargs):
        assert (
            datetime.fromisoformat(descriptor["sandbox_expires_at"]).timestamp() == clock[0] + 120
        )
        seen.append(("create", kwargs["timeout"]))
        return sandbox

    api = SimpleNamespace(
        create=create,
        connect=lambda *args, **kwargs: seen.append(("connect", kwargs["timeout"])) or sandbox,
        kill=lambda *args, **kwargs: seen.append(("kill", args[0])),
    )
    monkeypatch.setitem(sys.modules, "e2b", SimpleNamespace(Sandbox=api))
    settings = {"api_key_env": "TEST_E2B_CONTROL_KEY"}
    runners.E2B(settings, descriptor).create()
    expiry = descriptor["sandbox_expires_at"]
    clock[0] += 30.1
    runners.E2B(settings, descriptor).connect()
    clock[0] += 60
    runners.E2B(settings, descriptor).connect()
    assert seen[:3] == [("create", 120), ("connect", 89), ("connect", 29)]
    assert descriptor["sandbox_expires_at"] == expiry
    clock[0] += 30
    expired = runners.E2B(settings, descriptor)
    with pytest.raises(AuditError, match="lifetime expired"):
        expired.connect()
    expired.cleanup()
    assert seen[-1] == ("kill", "sandbox-with-fixed-expiry")
    assert sum(action == "connect" for action, _ in seen) == 2
