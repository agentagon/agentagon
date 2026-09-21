"""Start one foreground local service, or open a project in the existing service."""

import fcntl
import os
import time
import webbrowser
from pathlib import Path

import click
import httpx

from agentagon.core.records import AuditError, load_json
from agentagon.dashboard.server import create_server
from agentagon.storage.state import AppState, atomic_write
from agentagon.workflows.service import Application, service_identity


def _verify_service(health):
    expected = service_identity()
    if health.get("application") != "agentagon":
        raise AuditError("unrecognized local service")
    mismatched = [key for key, value in expected.items() if health.get(key) != value]
    if mismatched:
        fields = ", ".join(key.replace("_", " ") for key in mismatched)
        raise AuditError(
            f"The running Agentagon service uses a different {fields}. "
            "Stop that service and retry with this installation."
        )


def _reuse(state, workspace, open_browser):
    instance_path = state.directory / "instance.json"
    for _ in range(20):
        try:
            if instance_path.is_symlink():
                raise AuditError("application instance metadata cannot be a symlink")
            instance = load_json(instance_path)
            port = instance["port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise AuditError("invalid application instance port")
            origin = f"http://127.0.0.1:{port}"
            headers = {"X-Agentagon-Token": instance["token"], "Origin": origin}
            with httpx.Client(timeout=2, trust_env=False) as client:
                health = client.get(origin + "/api/health", headers=headers)
                if health.status_code != 200:
                    raise AuditError("the existing application session could not be verified")
                _verify_service(health.json())
                project = None
                if workspace is not None:
                    response = client.post(
                        origin + "/api/projects",
                        headers=headers,
                        json={"path": str(Path(workspace).resolve())},
                    )
                    if response.status_code != 200:
                        raise AuditError("unable to select this project in the running application")
                    project = response.json()
            url = origin + ("/?project=" + project["id"] if project else "/")
            click.echo(url)
            if open_browser:
                webbrowser.open(url)
            return
        except (FileNotFoundError, httpx.TransportError, KeyError, ValueError):
            time.sleep(0.1)
    raise AuditError(
        "Agentagon is starting or unavailable; retry, or stop its original terminal process"
    )


def launch(workspace, *, port=0, open_browser=True):
    state = AppState()
    state.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        state.directory / "service.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _reuse(state, workspace, open_browser)
        application = Application(state.directory)
        server = None
        instance = state.directory / "instance.json"
        try:
            project = (
                application.register(str(Path(workspace).resolve()))
                if workspace is not None
                else None
            )
            server = create_server(application, port)
            atomic_write(
                instance,
                {
                    "version": 1,
                    "port": server.server_port,
                    "token": server.session_token,
                    "pid": os.getpid(),
                },
            )
            url = f"http://127.0.0.1:{server.server_port}/"
            if project:
                url += "?project=" + project["id"]
            click.echo(url)
            click.echo(
                "Agentagon is running locally. Keep this terminal open; Ctrl+C stops the service."
            )
            if open_browser:
                webbrowser.open(url)
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            click.echo("\nAgentagon stopped. Interrupted tasks can be resumed next time.")
        finally:
            application.close()
            if server is not None:
                server.server_close()
            instance.unlink(missing_ok=True)


class ServiceClient:
    """Authenticated loopback client. Closing it never owns service lifetime."""

    def __init__(self, instance):
        port = instance.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise AuditError("invalid local service port")
        self.origin = f"http://127.0.0.1:{port}"
        self.headers = {"X-Agentagon-Token": instance["token"], "Origin": self.origin}

    def request(self, method, path, body=None, *, query=None):
        if not path.startswith("/api/") or ".." in path or "?" in path:
            raise AuditError("invalid local service resource")
        if query is not None:
            if not isinstance(query, dict) or any(
                not isinstance(key, str)
                or not key
                or not isinstance(value, (str, int))
                or isinstance(value, bool)
                for key, value in query.items()
            ):
                raise AuditError("invalid local service query")
        with httpx.Client(timeout=30, trust_env=False) as client:
            response = client.request(
                method,
                self.origin + path,
                headers=self.headers,
                json=body,
                params=query,
            )
        result = response.json()
        if response.status_code != 200:
            raise AuditError(result.get("error", "local service request failed"))
        return result


def ensure_service():
    """Reuse or start the service without opening a browser or using client stdout."""
    import subprocess
    import sys

    state = AppState()
    instance_path = state.directory / "instance.json"

    def connect():
        if instance_path.is_symlink():
            raise AuditError("application instance metadata cannot be a symlink")
        instance = load_json(instance_path)
        client = ServiceClient(instance)
        health = client.request("GET", "/api/health")
        _verify_service(health)
        return client

    try:
        return connect()
    except (FileNotFoundError, httpx.TransportError):
        pass
    state.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    log = state.directory / "service.log"
    descriptor = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "ab") as stream:
        subprocess.Popen(
            [sys.executable, "-m", "agentagon", "serve", "--without-project"],
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
    for _ in range(100):
        try:
            return connect()
        except (FileNotFoundError, httpx.TransportError):
            time.sleep(0.1)
    raise AuditError(f"local service did not start; inspect {log}")


def open_dashboard(workspace):
    client = ensure_service()
    project = client.request("POST", "/api/projects", {"path": str(Path(workspace).resolve())})
    url = client.origin + "/projects/" + project["id"] + "/home"
    click.echo(url)
    webbrowser.open(url)
