"""Portable, stdlib-only durable experiment worker (also copied to remote runners)."""

import argparse
import base64
import hashlib
import json
import os
import platform
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

IGNORED = {".git", ".agentagon"}
EVENT_TYPES = {"task_start", "progress", "input", "output", "failure", "artifact", "task_end"}


def bounded_file(path, maximum):
    """Reject links and special files without following or blocking on them."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("evidence must be a regular file")
        content = stream.read(maximum + 1)
    return content[:maximum], len(content) > maximum


def task_evidence(job, request, secrets, complete=False):
    limits = request.get("evidence_limits")
    if not limits:
        return None
    evidence = {
        "version": 1,
        "events": [],
        "artifacts": [],
        "malformed_events": 0,
        "rejected_artifacts": [],
        "truncated": False,
        "incomplete": not complete,
    }
    event_bytes = retained_event_bytes = artifact_bytes = visited = 0
    for index, command in enumerate(request["commands"]):
        event_path = job / f"events-{index}.jsonl"
        if event_path.exists() or event_path.is_symlink():
            try:
                raw, truncated = bounded_file(
                    event_path, max(0, limits["max_event_bytes"] - event_bytes)
                )
                event_bytes += len(raw)
                evidence["truncated"] |= truncated
                for line in raw.splitlines(keepends=True):
                    if len(evidence["events"]) >= limits["max_events"]:
                        evidence["truncated"] = True
                        break
                    if not line.endswith(b"\n"):
                        evidence["incomplete"] = True
                        continue
                    try:
                        event = json.loads(line)
                        if (
                            not isinstance(event, dict)
                            or set(event) != {"version", "event", "task_id", "at", "data"}
                            or type(event["version"]) is not int
                            or event["version"] != 1
                            or not isinstance(event["event"], str)
                            or event["event"] not in EVENT_TYPES
                            or not isinstance(event["task_id"], str)
                            or not 1 <= len(event["task_id"]) <= 256
                            or not isinstance(event["at"], str)
                            or not 1 <= len(event["at"]) <= 64
                            or not isinstance(event["data"], dict)
                        ):
                            raise ValueError("malformed task event")
                        # Round-trip also rejects NaN and redacts inside nested payloads.
                        clean = json.dumps(event, allow_nan=False)
                        for secret in secrets:
                            if secret:
                                clean = clean.replace(json.dumps(secret)[1:-1], "[REDACTED]")
                        event = {**json.loads(clean), "command_id": command["id"]}
                        size = len(json.dumps(event).encode())
                        if retained_event_bytes + size > limits["max_event_bytes"]:
                            evidence["truncated"] = True
                            break
                        retained_event_bytes += size
                        evidence["events"].append(event)
                    except (ValueError, TypeError, UnicodeError, RecursionError):
                        evidence["malformed_events"] += 1
            except (OSError, ValueError):
                evidence["malformed_events"] += 1
        root = job / f"artifacts-{index}"
        if root.is_symlink():
            evidence["rejected_artifacts"].append(f"{command['id']}: artifact directory is a link")
            continue
        if root.exists() and not root.is_dir():
            evidence["rejected_artifacts"].append(
                f"{command['id']}: artifact root is not a directory"
            )
            continue
        for directory_index, (directory, dirs, files) in enumerate(
            os.walk(root, followlinks=False)
        ):
            if directory_index >= 1000:
                evidence["truncated"] = True
                break
            links = [name for name in dirs if (Path(directory) / name).is_symlink()]
            dirs[:] = sorted(name for name in dirs if name not in links)
            for name in sorted(files + links):
                visited += 1
                if visited > limits["max_artifacts"]:
                    evidence["truncated"] = True
                    break
                file = Path(directory) / name
                relative = file.relative_to(root).as_posix()
                try:
                    if not file.resolve().is_relative_to(root.resolve()):
                        raise ValueError("artifact escapes directory")
                    remaining = limits["max_total_artifact_bytes"] - artifact_bytes
                    content, truncated = bounded_file(
                        file, min(remaining, limits["max_artifact_bytes"])
                    )
                    if truncated:
                        evidence["truncated"] = True
                        raise ValueError("artifact exceeds retention limit")
                    for secret in secrets:
                        if secret:
                            content = content.replace(secret.encode(), b"[REDACTED]")
                    if len(content) > min(remaining, limits["max_artifact_bytes"]):
                        raise ValueError("redacted artifact exceeds retention limit")
                    artifact_bytes += len(content)
                    evidence["artifacts"].append(
                        {
                            "command_id": command["id"],
                            "path": relative,
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "bytes": len(content),
                            "content_base64": base64.b64encode(content).decode("ascii"),
                        }
                    )
                except (OSError, ValueError):
                    evidence["rejected_artifacts"].append(f"{command['id']}: {relative[:256]}")
            if visited > limits["max_artifacts"]:
                break
    started = {
        (e["command_id"], e["task_id"]) for e in evidence["events"] if e["event"] == "task_start"
    }
    ended = {
        (e["command_id"], e["task_id"]) for e in evidence["events"] if e["event"] == "task_end"
    }
    evidence["incomplete"] |= (
        bool(started - ended) or bool(evidence["malformed_events"]) or evidence["truncated"]
    )
    # Filenames are emitter-controlled evidence too; never retain credentials in metadata.
    for secret in secrets:
        if secret:
            for artifact in evidence["artifacts"]:
                artifact["path"] = artifact["path"].replace(secret, "[REDACTED]")
            evidence["rejected_artifacts"] = [
                name.replace(secret, "[REDACTED]") for name in evidence["rejected_artifacts"]
            ]
    return evidence


def now():
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017 (remote Python 3.10)


def save(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class EscapingSourceLinkError(ValueError):
    """A source link resolves outside the executable snapshot."""


def manifest(source, strict=True):
    """Canonical executable-source hashes and modes, shared with the host."""
    result = {}
    for directory, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED)
        links = [name for name in dirs if (Path(directory) / name).is_symlink()]
        for name in sorted(files + links):
            if name in IGNORED:
                continue
            path = Path(directory) / name
            if path.is_symlink():
                if strict and not path.resolve().is_relative_to(source.resolve()):
                    raise EscapingSourceLinkError("source symlink escapes snapshot")
                digest = hashlib.sha256(os.readlink(path).encode())
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
            else:
                if strict:
                    raise ValueError("source contains an unsupported file type")
                continue
            mode = 0o777 if path.is_symlink() else path.stat().st_mode & 0o777
            result[path.relative_to(source).as_posix()] = f"{digest.hexdigest()}:{mode}"
    return result


def unpack(job):
    source = job / "source"
    source.mkdir(mode=0o700, exist_ok=True)
    links = []
    with tarfile.open(job / "source.tar", "r") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe source archive path")
            path = source.joinpath(*relative.parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as data, path.open("wb") as output:
                    while chunk := data.read(1024 * 1024):
                        output.write(chunk)
                path.chmod(member.mode & 0o777)
            elif member.issym():
                target = (path.parent / member.linkname).resolve()
                if not target.is_relative_to(source.resolve()):
                    raise ValueError("source symlink escapes snapshot")
                links.append((path, member.linkname))
            else:
                raise ValueError("unsupported source archive entry")
    for path, target in links:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)


class Log:
    """Bounded UTF-8 log with redaction across arbitrary pipe chunk boundaries."""

    def __init__(self, path, secrets, limit):
        import codecs

        self.stream = path.open("w", encoding="utf-8")
        self.decode = codecs.getincrementaldecoder("utf-8")("replace")
        self.secrets = sorted({value for value in secrets if value}, key=len, reverse=True)
        self.width = max([len(value) for value in self.secrets] + [1])
        self.pending = ""
        self.limit = limit
        self.count = 0
        self.parts = []
        self.truncated = False

    def write(self, data, final=False):
        self.pending += self.decode.decode(data, final=final)
        output = []
        if not self.secrets:
            output.append(self.pending)
            self.pending = ""
        while self.pending and (final or len(self.pending) >= self.width):
            secret = next((item for item in self.secrets if self.pending.startswith(item)), None)
            if secret:
                output.append("[REDACTED]")
                self.pending = self.pending[len(secret) :]
            else:
                output.append(self.pending[0])
                self.pending = self.pending[1:]
        content = "".join(output)
        remaining = max(0, self.limit - self.count)
        encoded = content.encode("utf-8")
        kept = encoded[:remaining].decode("utf-8", errors="ignore")
        self.truncated |= len(encoded) > remaining
        self.count += len(kept.encode("utf-8"))
        self.parts.append(kept)
        self.stream.write(kept)
        self.stream.flush()
        if final:
            self.stream.close()

    def text(self):
        return "".join(self.parts)


def stop(process, grace=1.0):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        if process.poll() is not None:
            return
        raise
    started = time.monotonic()
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    if grace:
        time.sleep(max(0, grace - (time.monotonic() - started)))
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if process.poll() is None:
            raise
    process.wait()


def deadline_timestamp(request):
    value = datetime.fromisoformat(request["deadline_at"].replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("attempt deadline must include a timezone")
    return value.timestamp()


def runtime_identity():
    return {
        "platform": sys.platform,
        "machine": platform.machine(),
        "os_release": platform.release(),
        "python": sys.executable,
        "python_version": platform.python_version(),
    }


def run(job, token):
    request = json.loads((job / "request.json").read_text())
    if request["owner_token"] != token:
        raise ValueError("attempt ownership mismatch")
    import fcntl

    with (job / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (job / "result.json").exists() or (job / "owner.json").exists():
            return
        source = Path(request["source"])
        started = now()
        save(job / "owner.json", {"token": token, "pid": os.getpid(), "started_at": started})
        duration = request["timeout_seconds"]
        if request.get("deadline_at"):
            duration = min(duration, deadline_timestamp(request) - time.time())
        deadline = time.monotonic() + max(0, duration)
        secrets = [os.environ.get(name, "") for name in request.get("secret_env_names", [])]
        environment = os.environ.copy()
        environment.update(
            {
                "AGENTAGON_SEED": str(request["seed"]),
                "AGENTAGON_INPUTS_DIR": str(source),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        result = {key: request[key] for key in ("attempt_id", "source_digest", "evaluation_digest")}
        result.update(
            state="completed",
            results=[],
            benchmark_output=None,
            started_at=started,
            runtime_identity=runtime_identity(),
            finalized=True,
        )
        result.update(
            {key: request[key] for key in ("inputs_digest", "profile_digest") if key in request}
        )
        limit = request.get("max_output_bytes", 262144)
        next_progress = 0
        try:
            result["source_manifest_before"] = manifest(source)
            for index, command in enumerate(request["commands"]):
                if (job / "cancel").exists():
                    result["state"] = "cancelled"
                    break
                if (job / "deadline").exists() or time.monotonic() >= deadline:
                    result["state"] = "timed_out"
                    break
                command_output = job / f"benchmark-{index}.json"
                environment["AGENTAGON_RESULT_PATH"] = str(command_output)
                if request.get("evidence_limits"):
                    artifact_dir = job / f"artifacts-{index}"
                    artifact_dir.mkdir(mode=0o700, exist_ok=True)
                    environment["AGENTAGON_EVENTS_PATH"] = str(job / f"events-{index}.jsonl")
                    environment["AGENTAGON_ARTIFACTS_DIR"] = str(artifact_dir)
                cwd = (source / command.get("cwd", ".")).resolve()
                if not cwd.is_relative_to(source.resolve()):
                    raise ValueError("command cwd escapes source snapshot")
                logs = {
                    name: Log(job / f"{index}.{name}.log", secrets, limit)
                    for name in ("stdout", "stderr")
                }
                entry = {
                    "id": command["id"],
                    "role": command["role"],
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                }
                process = None
                try:
                    process = subprocess.Popen(
                        command["argv"],
                        cwd=cwd,
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                    save(
                        job / "active.json",
                        {
                            "token": token,
                            "worker_pid": os.getpid(),
                            "pid": process.pid,
                            "command_id": command["id"],
                        },
                    )
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                        while selector.get_map() or process.poll() is None:
                            if request.get("evidence_limits") and time.monotonic() >= next_progress:
                                save(job / "progress.json", task_evidence(job, request, secrets))
                                next_progress = time.monotonic() + 1
                            if (
                                (job / "cancel").exists()
                                or (job / "deadline").exists()
                                or time.monotonic() >= deadline
                            ):
                                result["state"] = (
                                    "cancelled" if (job / "cancel").exists() else "timed_out"
                                )
                                entry["timed_out"] = result["state"] == "timed_out"
                                stop(process)
                                for key in list(selector.get_map().values()):
                                    selector.unregister(key.fileobj)
                                    key.fileobj.close()
                                break
                            if process.poll() is not None:
                                stop(process, 0)
                            for key, _ in selector.select(0.1):
                                chunk = os.read(key.fileobj.fileno(), 65536)
                                if chunk:
                                    logs[key.data].write(chunk)
                                else:
                                    selector.unregister(key.fileobj)
                                    key.fileobj.close()
                    entry["exit_code"] = process.wait()
                except OSError as exc:
                    entry["exit_code"] = 127
                    logs["stderr"].write(str(exc).encode())
                finally:
                    if process is not None:
                        stop(process, 0)
                    for name, log in logs.items():
                        log.write(b"", final=True)
                        entry[name] = log.text()
                        if log.truncated:
                            entry[name + "_truncated"] = True
                    (job / "active.json").unlink(missing_ok=True)
                result["results"].append(entry)
                if command["role"] == "benchmark" and command_output.exists():
                    if command_output.is_symlink() or command_output.stat().st_size > limit:
                        entry["artifact_error"] = (
                            "benchmark result is a symlink or exceeds output limit"
                        )
                    else:
                        output = command_output.read_text(encoding="utf-8", errors="replace")
                        for secret in sorted(secrets, key=len, reverse=True):
                            if secret:
                                output = output.replace(secret, "[REDACTED]")
                        result["benchmark_output"] = output
                        command_output.write_text(output, encoding="utf-8")
                if result["state"] != "completed":
                    break
                if command["role"] == "setup" and entry["exit_code"] != 0:
                    result["state"] = "setup_failed"
                    break
                if "gate_exit_code" in command and entry["exit_code"] != command["gate_exit_code"]:
                    result["state"] = "preflight_failed"
                    result["skipped_commands"] = [
                        {
                            "id": pending["id"],
                            "role": pending["role"],
                            "reason": f"preflight {command['id']} failed",
                        }
                        for pending in request["commands"][index + 1 :]
                    ]
                    break
            result["source_manifest_after"] = manifest(source, strict=False)
        except Exception as exc:
            result["state"] = "interrupted"
            message = str(exc)
            for secret in secrets:
                if secret:
                    message = message.replace(secret, "[REDACTED]")
            result["error"] = message
        result["finalized"] = True
        result["ended_at"] = now()
        if request.get("evidence_limits"):
            result["evidence"] = task_evidence(
                job, request, secrets, complete=result["state"] == "completed"
            )
        save(job / "result.json", result)
        if request.get("evidence_limits"):
            # The retained result is bounded and redacted. Raw emitter files are transient.
            for index in range(len(request["commands"])):
                for root in (job / f"events-{index}.jsonl", job / f"artifacts-{index}"):
                    if root.is_dir() and not root.is_symlink():
                        shutil.rmtree(root)
                    else:
                        root.unlink(missing_ok=True)


def status(job, token):
    """A held worker lock establishes liveness without trusting a recycled process ID."""
    import fcntl

    if (job / "result.json").exists():
        return json.loads((job / "result.json").read_text())
    owner = json.loads((job / "owner.json").read_text()) if (job / "owner.json").exists() else None
    if owner and owner["token"] != token:
        raise ValueError("attempt ownership mismatch")
    with (job / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            progress = job / "progress.json"
            return {
                "state": "running",
                "owner": owner,
                "evidence": json.loads(progress.read_text()) if progress.exists() else None,
            }
    return {"state": "unknown", "owner": owner}


def cancel_job(job, token, reason="cancelled"):
    """Cancel a queued attempt under its launch lock, or ask the live worker to stop."""
    import fcntl

    request = json.loads((job / "request.json").read_text())
    if request["owner_token"] != token:
        raise ValueError("attempt ownership mismatch")
    (job / ("deadline" if reason == "timed_out" else "cancel")).touch(mode=0o600)
    with (job / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if (job / "result.json").exists() or (job / "owner.json").exists():
            return
        result = {
            key: request[key]
            for key in (
                "attempt_id",
                "source_digest",
                "evaluation_digest",
                "inputs_digest",
                "profile_digest",
            )
            if key in request
        }
        result.update(
            state=reason,
            results=[],
            benchmark_output=None,
            started_at=None,
            ended_at=now(),
            runtime_identity=runtime_identity(),
            finalized=True,
        )
        save(job / "result.json", result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "launch", "unpack", "status", "cancel", "expire"))
    parser.add_argument("job")
    parser.add_argument("token", nargs="?")
    args = parser.parse_args()
    job = Path(args.job).resolve()
    if args.action == "unpack":
        unpack(job)
        return
    if args.action == "launch":
        envelope = json.load(sys.stdin)
        environment = os.environ.copy()
        environment.update(envelope.get("env", {}))
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "run", str(job), args.token],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        print(json.dumps({"pid": process.pid}))
        return
    if args.action == "run":
        run(job, args.token)
        return
    request = json.loads((job / "request.json").read_text())
    if request["owner_token"] != args.token:
        raise ValueError("attempt ownership mismatch")
    if args.action in {"cancel", "expire"}:
        cancel_job(job, args.token, "timed_out" if args.action == "expire" else "cancelled")
    print(json.dumps(status(job, args.token)))


if __name__ == "__main__":
    main()
