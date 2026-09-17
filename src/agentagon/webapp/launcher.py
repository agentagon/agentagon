"""Start one foreground local service, or open a project in the existing service."""

import fcntl
import os
import time
import webbrowser
from pathlib import Path

import click
import httpx

from agentagon.core.records import AuditError, load_json
from agentagon.webapp.server import create_server
from agentagon.webapp.service import Application
from agentagon.webapp.state import AppState, atomic_write


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
                if health.status_code != 200 or health.json().get("application") != "agentagon":
                    raise AuditError("the existing application session could not be verified")
                response = client.post(
                    origin + "/api/projects",
                    headers=headers,
                    json={"path": str(Path(workspace).resolve())},
                )
                if response.status_code != 200:
                    raise AuditError("unable to select this project in the running application")
                project = response.json()
            url = origin + "/?project=" + project["id"]
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
            project = application.register(str(Path(workspace).resolve()))
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
            url = f"http://127.0.0.1:{server.server_port}/?project={project['id']}"
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
