"""Durable local, OpenSSH, and E2B evaluation execution."""

import fcntl
import io
import json
import math
import os
import shlex
import subprocess
import sys
import tarfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from agentagon.core.records import AuditError
from agentagon.experiments import worker

WORKER = Path(worker.__file__).resolve()
OUTPUT_LIMIT = 262144
MAX_REMOTE_RESPONSE_BYTES = 8 * 1024 * 1024


def _save(path, value):
    worker.save(path, value)


def _env(profile, required=True):
    result = {}
    for destination, reference in profile.get("env", {}).items():
        if not isinstance(destination, str) or not isinstance(reference, str):
            raise AuditError("runner environment must map names to environment variable references")
        if reference not in os.environ and required:
            raise AuditError(f"missing runner credential environment variable: {reference}")
        if reference in os.environ:
            result[destination] = os.environ[reference]
    return result


def _redact(value, env):
    for secret in sorted({value for value in env.values() if value}, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def _error(request, message, **extra):
    return {
        "state": "interrupted",
        "finalized": False,
        "attempt_id": request["attempt_id"],
        "source_digest": request["source_digest"],
        "evaluation_digest": request["evaluation_digest"],
        **{key: request[key] for key in ("inputs_digest", "profile_digest") if key in request},
        "results": [],
        "benchmark_output": None,
        "started_at": None,
        "ended_at": datetime.now(UTC).isoformat(),
        "error": message,
        **extra,
    }


def _archive(source, limit):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name in worker.manifest(source):
            path = source / name
            if not path.is_symlink() and output.tell() + path.stat().st_size + 2048 > limit:
                raise AuditError("source snapshot exceeds remote transfer limit")
            info = archive.gettarinfo(str(path), arcname=name)
            if info.issym():
                archive.addfile(info)
            else:
                info.type, info.linkname = tarfile.REGTYPE, ""
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
    if output.tell() > limit:
        raise AuditError("source snapshot exceeds remote transfer limit")
    return output.getvalue()


class SSH:
    def __init__(self, settings, descriptor):
        self.settings = settings
        self.descriptor = descriptor
        host = settings.get("host")
        if (
            not isinstance(host, str)
            or not host
            or host.startswith("-")
            or any(c.isspace() for c in host)
        ):
            raise AuditError("SSH runner requires a configured host alias")
        self.prefix = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            host,
        ]

    def call(self, argv, data=None, timeout=30):
        result = subprocess.run(
            [*self.prefix, shlex.join(argv)],
            input=data,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("SSH operation failed (exit " + str(result.returncode) + ")")
        if len(result.stdout) > MAX_REMOTE_RESPONSE_BYTES:
            raise RuntimeError("remote response exceeds limit")
        return result.stdout

    def put(self, path, data):
        script = "import os,pathlib,sys; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True,mode=0o700); f=os.fdopen(os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'wb'); f.write(sys.stdin.buffer.read()); f.close()"
        self.call([self.settings.get("python", "python3"), "-c", script, path], data)

    def command(self, action, env=None):
        job = self.descriptor["remote_job"]
        argv = [
            self.settings.get("python", "python3"),
            job + "/worker.py",
            action,
            job,
            self.descriptor["owner_token"],
        ]
        data = json.dumps({"env": env or {}}).encode() if action == "launch" else None
        return self.call(argv, data)

    def cleanup(self):
        job = self.descriptor["remote_job"]
        script = "import json,pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); p.exists() or sys.exit(0); assert json.loads((p/'request.json').read_text())['owner_token']==sys.argv[2]; shutil.rmtree(p)"
        self.call(
            [
                self.settings.get("python", "python3"),
                "-c",
                script,
                job,
                self.descriptor["owner_token"],
            ]
        )


class E2B:
    def __init__(self, settings, descriptor):
        try:
            from e2b import Sandbox
        except ImportError as exc:
            raise AuditError("E2B runner requires the agentagon[e2b] optional dependency") from exc
        reference = settings.get("api_key_env", "E2B_API_KEY")
        if not os.environ.get(reference):
            raise AuditError(f"missing E2B credential environment variable: {reference}")
        self.api = Sandbox
        self.api_key = os.environ[reference]
        self.settings = settings
        self.descriptor = descriptor
        self.sandbox = None

    def connect(self):
        if self.sandbox is None:
            try:
                expires = datetime.fromisoformat(self.descriptor["sandbox_expires_at"])
                if expires.tzinfo is None:
                    raise ValueError("sandbox expiry requires a timezone")
                remaining = math.floor(expires.timestamp() - time.time())
            except (KeyError, TypeError, ValueError) as exc:
                raise AuditError("E2B attempt has no valid original sandbox expiry") from exc
            if remaining <= 0:
                raise AuditError("E2B sandbox lifetime expired; reconnect cannot extend it")
            self.sandbox = self.api.connect(
                self.descriptor["sandbox_id"],
                api_key=self.api_key,
                timeout=remaining,
                request_timeout=20,
            )
        return self.sandbox

    def create(self):
        self.descriptor.setdefault(
            "sandbox_expires_at",
            datetime.fromtimestamp(
                time.time() + self.descriptor["sandbox_timeout_seconds"], UTC
            ).isoformat(),
        )
        self.sandbox = self.api.create(
            template=self.settings.get("template"),
            timeout=self.descriptor["sandbox_timeout_seconds"],
            metadata={
                "agentagon_attempt": self.descriptor["attempt_id"],
                "agentagon_owner": self.descriptor["owner_token"],
            },
            secure=True,
            lifecycle={"on_timeout": "kill", "auto_resume": False},
            api_key=self.api_key,
            request_timeout=30,
        )
        self.descriptor["sandbox_id"] = self.sandbox.sandbox_id

    def put(self, path, data):
        self.connect().files.make_dir(str(Path(path).parent), request_timeout=20)
        self.connect().files.write(path, data, request_timeout=30)

    def command(self, action, env=None):
        job = self.descriptor["remote_job"]
        argv = [
            self.settings.get("python", "python3"),
            job + "/worker.py",
            "run" if action == "launch" else action,
            job,
            self.descriptor["owner_token"],
        ]
        if action == "launch":
            handle = self.connect().commands.run(
                shlex.join(argv), background=True, envs=env or {}, timeout=20
            )
            return json.dumps({"pid": handle.pid}).encode()
        value = self.connect().commands.run(shlex.join(argv), timeout=20)
        if len(value.stdout) > MAX_REMOTE_RESPONSE_BYTES:
            raise RuntimeError("remote response exceeds limit")
        return value.stdout.encode()

    def cleanup(self):
        self.api.kill(self.descriptor["sandbox_id"], api_key=self.api_key, request_timeout=20)


def _transport(settings, descriptor):
    return SSH(settings, descriptor) if settings["kind"] == "ssh" else E2B(settings, descriptor)


def _local_status(job, token):
    return worker.status(job, token)


def _validate(request):
    if "evidence_limits" in request:
        from agentagon.experiments.evidence import validate_limits

        validate_limits(request["evidence_limits"])
    if request.get("deadline_at"):
        try:
            worker.deadline_timestamp(request)
        except (TypeError, ValueError, AttributeError) as exc:
            raise AuditError("attempt deadline must be an ISO timestamp with a timezone") from exc
    if (
        not isinstance(request.get("timeout_seconds"), (float, int))
        or not math.isfinite(request["timeout_seconds"])
        or request["timeout_seconds"] <= 0
    ):
        raise AuditError("runner timeout must be positive")
    commands = request.get("commands", [])
    if not commands or sum(command.get("role") == "benchmark" for command in commands) != 1:
        raise AuditError("runner requires exactly one benchmark command")
    ids = set()
    benchmark_seen = False
    for command in commands:
        if command.get("id") in ids or not isinstance(command.get("id"), str):
            raise AuditError("runner command IDs must be unique strings")
        ids.add(command["id"])
        if command.get("role") not in {"setup", "benchmark", "check"}:
            raise AuditError("invalid runner command role")
        if "gate_exit_code" in command and (
            command["role"] != "check"
            or benchmark_seen
            or type(command["gate_exit_code"]) is not int
            or command["gate_exit_code"] not in (0, 1)
        ):
            raise AuditError("preflight gates must precede the benchmark and expect exit 0 or 1")
        benchmark_seen = benchmark_seen or command["role"] == "benchmark"
        if (
            not isinstance(command.get("argv"), list)
            or not command["argv"]
            or any(not isinstance(arg, str) or "\x00" in arg for arg in command["argv"])
        ):
            raise AuditError("runner command must contain a nonempty argv array")
        cwd = Path(command.get("cwd", "."))
        if cwd.is_absolute() or ".." in cwd.parts:
            raise AuditError("runner command cwd must be inside source snapshot")


def _cancel_before_dispatch(attempt_dir, request, settings, descriptor=None):
    """Finalize observed non-dispatch while holding executor.lock, without remote access."""
    descriptor = descriptor or {
        "version": 1,
        "attempt_id": request["attempt_id"],
        "owner_token": uuid.uuid4().hex,
        "runner": settings,
        "request": request,
    }
    descriptor["state"] = "cancelled_before_dispatch"
    _save(attempt_dir / "executor.json", descriptor)
    result = _error(
        request,
        "cancellation recorded before dispatch; no evaluation process was launched",
        state="cancelled",
        finalized=True,
        cleanup_pending=False,
    )
    _save(attempt_dir / "collected.json", result)
    return result


def execute(profile: dict, source: Path, attempt_dir: Path, request: dict) -> dict:
    """Execute once; subsequent calls collect the same durable attempt without relaunching."""
    _validate(request)
    source, attempt_dir = Path(source).resolve(), Path(attempt_dir).resolve()
    attempt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = dict(profile.get("runner", {"kind": "local"}))
    kind = settings.setdefault("kind", "local")
    if kind not in {"local", "ssh", "e2b"}:
        raise AuditError("unsupported experiment runner")
    env = _env(profile, required=False)
    descriptor_path = attempt_dir / "executor.json"
    transport = None
    child = None
    with (attempt_dir / "executor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if descriptor_path.exists():
            descriptor = json.loads(descriptor_path.read_text())
            if descriptor["request"] != request or descriptor["runner"] != settings:
                raise AuditError("attempt already belongs to another request or runner")
            if (cached := _collected(attempt_dir)) is not None:
                return cached
            if descriptor["state"] == "cancelled_before_dispatch":
                return _cancel_before_dispatch(attempt_dir, request, settings, descriptor)
        else:
            if (attempt_dir / "cancel").exists():
                return _cancel_before_dispatch(attempt_dir, request, settings)
            env = _env(profile)
            token = uuid.uuid4().hex
            remote_root = settings.get("remote_root", "/tmp/agentagon")
            if (
                not isinstance(remote_root, str)
                or not remote_root.startswith("/")
                or ".." in Path(remote_root).parts
            ):
                raise AuditError("remote_root must be an absolute directory")
            descriptor = {
                "version": 1,
                "attempt_id": request["attempt_id"],
                "owner_token": token,
                "runner": settings,
                "request": request,
                "state": "dispatching",
                "remote_job": remote_root.rstrip("/") + "/" + token,
                "sandbox_timeout_seconds": int(
                    settings.get("sandbox_timeout_seconds", request["timeout_seconds"] + 120)
                ),
            }
            if (
                kind == "e2b"
                and descriptor["sandbox_timeout_seconds"] < request["timeout_seconds"] + 30
            ):
                raise AuditError(
                    "E2B sandbox timeout must exceed attempt timeout by at least 30 seconds"
                )
            if request.get("deadline_at") and worker.deadline_timestamp(request) <= time.time():
                descriptor["state"] = "expired"
                _save(descriptor_path, descriptor)
                result = _error(
                    request,
                    "absolute execution deadline expired before dispatch",
                    state="timed_out",
                    finalized=True,
                    cleanup_pending=False,
                )
                _save(attempt_dir / "collected.json", result)
                return result
            source_archive = None
            if kind != "local":
                transport = _transport(settings, descriptor)
                source_archive = _archive(
                    source, int(profile.get("limits", {}).get("max_source_bytes", 67108864))
                )
            _save(descriptor_path, descriptor)
            job = attempt_dir / "job"
            job.mkdir(mode=0o700, exist_ok=True)
            maximum = min(
                int(profile.get("limits", {}).get("max_output_bytes", OUTPUT_LIMIT)), 1048576
            )
            worker_request = {
                **request,
                "owner_token": token,
                "source": str(source) if kind == "local" else descriptor["remote_job"] + "/source",
                "secret_env_names": list(env),
                "max_output_bytes": maximum,
            }
            _save(job / "request.json", worker_request)
            if (attempt_dir / "cancel").exists():
                return _cancel_before_dispatch(attempt_dir, request, settings, descriptor)
            try:
                if kind == "local":
                    baseline = {
                        name: os.environ[name]
                        for name in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT")
                        if name in os.environ
                    }
                    baseline.update(env)
                    child = subprocess.Popen(
                        [
                            settings.get("python", sys.executable),
                            str(WORKER),
                            "run",
                            str(job),
                            token,
                        ],
                        env=baseline,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                    )
                    descriptor["pid"] = child.pid
                else:
                    if kind == "e2b":
                        descriptor["sandbox_expires_at"] = datetime.fromtimestamp(
                            time.time() + descriptor["sandbox_timeout_seconds"], UTC
                        ).isoformat()
                        _save(descriptor_path, descriptor)
                        transport.create()
                        _save(descriptor_path, descriptor)
                    remote_job = descriptor["remote_job"]
                    transport.put(remote_job + "/worker.py", WORKER.read_bytes())
                    transport.put(remote_job + "/request.json", json.dumps(worker_request).encode())
                    transport.put(remote_job + "/source.tar", source_archive)
                    transport.command("unpack")
                    launched = json.loads(transport.command("launch", env))
                    descriptor["pid"] = launched["pid"]
                descriptor["state"] = "running"
                _save(descriptor_path, descriptor)
            except Exception as exc:
                descriptor["state"] = "uncertain"
                descriptor["error"] = _redact(str(exc), env)
                _save(descriptor_path, descriptor)
    if kind != "local" and transport is None:
        try:
            transport = _transport(settings, descriptor)
        except Exception as exc:
            return _error(request, _redact(str(exc), env), cleanup_pending=True)
    # A short startup grace covers the interval between process creation and owner-file creation.
    grace = time.monotonic() + 3
    consecutive_errors = 0
    while True:
        if (cached := _collected(attempt_dir)) is not None:
            return cached
        try:
            if request.get("deadline_at") and worker.deadline_timestamp(request) <= time.time():
                if kind == "local":
                    worker.cancel_job(attempt_dir / "job", descriptor["owner_token"], "timed_out")
                else:
                    transport.command("expire")
            if (attempt_dir / "cancel").exists():
                if kind == "local":
                    (attempt_dir / "job" / "cancel").touch(mode=0o600)
                else:
                    transport.command("cancel")
            status = (
                _local_status(attempt_dir / "job", descriptor["owner_token"])
                if kind == "local"
                else json.loads(transport.command("status"))
            )
            consecutive_errors = 0
        except Exception as exc:
            if (cached := _collected(attempt_dir)) is not None:
                return cached
            consecutive_errors += 1
            if consecutive_errors >= 3:
                return _error(
                    request,
                    _redact(str(exc), env),
                    cleanup_pending=kind != "local",
                    remote_state="unknown",
                )
            time.sleep(0.2 * consecutive_errors)
            continue
        if status["state"] not in {"running", "unknown"}:
            if any(
                status.get(key) != request[key]
                for key in ("attempt_id", "source_digest", "evaluation_digest")
            ):
                return _error(
                    request, "runner result identity mismatch", cleanup_pending=kind != "local"
                )
            if child is not None:
                child.wait(timeout=5)
            _bind_evidence(status, request)
            return _collect(attempt_dir, status, transport)

        if status.get("evidence"):
            _bind_evidence(status, request)
            _save(attempt_dir / "progress.json", status["evidence"])
        if status["state"] == "unknown" and time.monotonic() >= grace:
            return _error(
                request,
                "attempt launch or process status is unknown; it was not relaunched",
                cleanup_pending=kind != "local",
            )
        time.sleep(0.1 if kind == "local" else 0.5)


def _bind_evidence(result, request):
    if result.get("evidence") is not None:
        result["evidence"]["identity"] = {
            key: request[key]
            for key in (
                "run_id",
                "evaluation_id",
                "candidate_id",
                "attempt_id",
                "source_digest",
                "evaluation_digest",
            )
            if key in request
        }


def _collected(attempt_dir):
    path = attempt_dir / "collected.json"
    if not path.exists():
        return None
    with (attempt_dir / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return json.loads(path.read_text())


def _collect(attempt_dir, result, transport):
    with (attempt_dir / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        collected = attempt_dir / "collected.json"
        if collected.exists():
            return json.loads(collected.read_text())
        result["cleanup_pending"] = transport is not None
        _save(collected, result)
        if transport is not None:
            try:
                transport.cleanup()
                result["cleanup_pending"] = False
            except Exception:
                result["cleanup_pending"] = True
            _save(collected, result)
        return result


def cancel(profile: dict, attempt_dir: Path, *, request: dict | None = None) -> dict:
    """Request cooperative process-group cancellation without racing the executor lock."""
    attempt_dir = Path(attempt_dir).resolve()
    attempt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = attempt_dir / "executor.json"
    if request is not None:
        _validate(request)
        if path.exists() and json.loads(path.read_text())["request"] != request:
            raise AuditError("attempt already belongs to another cancellation request")
    (attempt_dir / "cancel").touch(mode=0o600)
    if not path.exists() and request is not None:
        with (attempt_dir / "executor.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # The dispatcher will observe the marker before launch, or cancellation
                # can reconnect to its recorded ownership on the next invocation.
                return _error(request, "cancellation requested while dispatch is being recorded")
            if not path.exists():
                settings = dict(profile.get("runner", {"kind": "local"}))
                settings.setdefault("kind", "local")
                return _cancel_before_dispatch(attempt_dir, request, settings)
    if not path.exists():
        return {"state": "interrupted", "error": "attempt has no runner descriptor"}
    descriptor = json.loads(path.read_text())
    if request is not None and descriptor["request"] != request:
        raise AuditError("attempt already belongs to another cancellation request")
    settings = descriptor["runner"]
    if (cached := _collected(attempt_dir)) is not None:
        return cached
    if descriptor["state"] == "cancelled_before_dispatch":
        with (attempt_dir / "executor.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if (cached := _collected(attempt_dir)) is not None:
                return cached
            return _cancel_before_dispatch(attempt_dir, descriptor["request"], settings, descriptor)
    transport = None
    if settings["kind"] == "local":
        (attempt_dir / "job").mkdir(mode=0o700, exist_ok=True)
        (attempt_dir / "job" / "cancel").touch(mode=0o600)
        if (attempt_dir / "job" / "request.json").exists():
            worker.cancel_job(attempt_dir / "job", descriptor["owner_token"])

        def getter():
            return _local_status(attempt_dir / "job", descriptor["owner_token"])
    else:
        try:
            transport = _transport(settings, descriptor)
            transport.command("cancel")

            def getter():
                return json.loads(transport.command("status"))
        except Exception:
            if (cached := _collected(attempt_dir)) is not None:
                return cached
            return _error(
                descriptor["request"], "remote cancellation is unconfirmed", cleanup_pending=True
            )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if (cached := _collected(attempt_dir)) is not None:
            return cached
        try:
            status = getter()
        except Exception:
            if (cached := _collected(attempt_dir)) is not None:
                return cached
            return _error(
                descriptor["request"], "cancellation is unconfirmed", cleanup_pending=True
            )
        if status["state"] not in {"running", "unknown"}:
            _bind_evidence(status, descriptor["request"])
            return _collect(attempt_dir, status, transport)
        time.sleep(0.1)
    return _error(
        descriptor["request"],
        "cancellation is requested but not confirmed",
        cleanup_pending=settings["kind"] != "local",
    )


def cleanup(profile: dict, attempt_dir: Path) -> dict:
    """Retry remote cleanup only after a terminal result was collected locally."""
    attempt_dir = Path(attempt_dir).resolve()
    descriptor = json.loads((attempt_dir / "executor.json").read_text())
    collected = attempt_dir / "collected.json"
    if not collected.exists():
        raise AuditError("collect or cancel the attempt before cleanup")
    result = json.loads(collected.read_text())
    try:
        if descriptor["runner"]["kind"] != "local" and descriptor.get("state") not in {
            "expired",
            "cancelled_before_dispatch",
        }:
            _transport(descriptor["runner"], descriptor).cleanup()
        result["cleanup_pending"] = False
        result.pop("cleanup_error", None)
    except Exception:
        result["cleanup_pending"] = True
        result["cleanup_error"] = (
            "remote cleanup did not complete; retry cleanup when connectivity returns"
        )
    _save(collected, result)
    return result
