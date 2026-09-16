"""Loopback-only HTTP and event streaming for the local application."""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from agentagon.core.records import AuditError, encoded
from agentagon.webapp.jobs import public_job

APP_ASSETS = {
    "/": ("webapp.html", "text/html; charset=utf-8"),
    "/webapp.js": ("webapp.js", "text/javascript; charset=utf-8"),
    "/webapp.css": ("webapp.css", "text/css; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/logo-split-crown-96.png": ("logo-split-crown-96.png", "image/png"),
}


def create_server(application, port=0):
    if type(port) is not int or not 0 <= port <= 65535:
        raise AuditError("port must be an integer between 0 and 65535")
    token = secrets.token_urlsafe(32)
    streams = threading.BoundedSemaphore(16)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        @property
        def origin(self):
            return f"http://127.0.0.1:{self.server.server_port}"

        def _headers(self, status, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
            )

        def _respond(self, status, body, content_type):
            self._headers(status, content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, body):
            self._respond(status, encoded(body).encode(), "application/json; charset=utf-8")

        def _host(self):
            return self.headers.get_all("Host", []) == [f"127.0.0.1:{self.server.server_port}"]

        def _token(self):
            tokens = self.headers.get_all("X-Agentagon-Token", [])
            return (
                len(tokens) == 1
                and tokens[0].isascii()
                and secrets.compare_digest(tokens[0], token)
            )

        def _read_allowed(self):
            return self.headers.get_all("Origin", []) in ([], [self.origin]) and (
                self._token()
                or (
                    self.headers.get_all("Sec-Fetch-Site", []) == ["same-origin"]
                    and self.headers.get("Sec-Fetch-Mode") in {"same-origin", "cors", "navigate"}
                )
            )

        def _bootstrap(self):
            if (
                self.headers.get_all("Sec-Fetch-Site", []) != ["same-origin"]
                or self.headers.get_all("Sec-Fetch-Mode", []) != ["same-origin"]
                or self.headers.get_all("X-Agentagon-Bootstrap", []) != ["1"]
                or self.headers.get_all("Origin", []) not in ([], [self.origin])
            ):
                self._json(403, {"error": "Open Agentagon to use this session."})
                return
            self._json(200, {"token": token, "controls_enabled": True})

        def _body(self):
            if self.headers.get_all("Content-Type", []) != ["application/json"]:
                raise AuditError("request content type must be application/json")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                raise AuditError("request requires one bounded content length")
            try:
                length = int(lengths[0])
            except ValueError as exc:
                raise AuditError("invalid content length") from exc
            if not 0 < length <= 131072:
                raise AuditError("request must be between 1 byte and 128 KB")
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise AuditError("incomplete request")
            try:

                def invalid(_value):
                    raise ValueError("non-finite JSON")

                body = json.loads(raw, parse_constant=invalid)
            except (ValueError, UnicodeDecodeError) as exc:
                raise AuditError("request must be valid JSON") from exc
            if not isinstance(body, dict):
                raise AuditError("request must be a JSON object")
            return body

        def do_GET(self):
            if not self._host():
                self._json(403, {"error": "Use the exact local URL printed by Agentagon."})
                return
            path = urlsplit(self.path).path
            try:
                if path in APP_ASSETS:
                    name, content_type = APP_ASSETS[path]
                    self._respond(
                        200,
                        files("agentagon").joinpath("dashboard_assets", name).read_bytes(),
                        content_type,
                    )
                elif path == "/api/session":
                    self._bootstrap()
                elif not self._read_allowed():
                    self._json(403, {"error": "Invalid application session or origin."})
                else:
                    parts = path.strip("/").split("/")
                    if (
                        len(parts) == 4
                        and parts[:2] == ["api", "projects"]
                        and parts[3] == "events"
                    ):
                        self._events(parts[2])
                    elif (
                        len(parts) == 6
                        and parts[:2] == ["api", "projects"]
                        and parts[3] == "deliveries"
                    ):
                        self._respond(
                            200,
                            application.delivery_artifact(parts[2], parts[4], parts[5]),
                            "text/plain; charset=utf-8",
                        )
                    else:
                        self._json(200, self._get(parts))
            except AuditError as exc:
                self._json(400, {"error": str(exc)})
            except (OSError, ValueError, KeyError, TypeError):
                self._json(400, {"error": "Unable to read this application resource."})

        def _get(self, parts):
            if parts == ["api", "health"]:
                return {"application": "agentagon", "version": 1}
            if parts == ["api", "projects"]:
                return application.projects()
            if parts == ["api", "connections"]:
                return application.connections()
            if parts == ["api", "agents"]:
                return application.agents()
            if parts == ["api", "agents", "codex", "models"]:
                return application.codex_models()
            if len(parts) == 4 and parts[:2] == ["api", "connections"] and parts[3] == "datasets":
                connection = application.connection(parts[2])
                return {
                    "datasets": application.provider_factory(
                        connection, application.credentials
                    ).datasets()
                }
            if len(parts) >= 4 and parts[:2] == ["api", "projects"]:
                project_id, resource = parts[2:4]
                application.state.project(project_id)
                if resource == "application-agents":
                    if len(parts) == 4:
                        return application.application_agents(project_id)
                    if len(parts) == 5:
                        return application.catalog.agent(project_id, parts[4])
                    if len(parts) == 6:
                        agent_id, child = parts[4:6]
                        if child == "overview":
                            query = parse_qs(urlsplit(self.path).query)
                            selected = query.get("focus_id", [None])
                            if len(selected) != 1:
                                raise AuditError("select one focus")
                            return application.agent_overview(project_id, agent_id, selected[0])
                        if child == "focuses":
                            return {"focuses": application.catalog.focuses(project_id, agent_id)}
                        if child == "metrics":
                            return application.catalog.metrics(project_id, agent_id)
                if resource == "overview" and len(parts) == 4:
                    return application.overview(project_id)
                if resource == "settings" and len(parts) == 4:
                    return application.settings(project_id)
                if resource == "dataset-splits" and len(parts) == 4:
                    return {
                        "splits": application.state.db.list_records(project_id, "dataset_splits")
                    }
                if resource == "jobs":
                    if len(parts) == 4:
                        return {"jobs": [public_job(j) for j in application.jobs.list(project_id)]}
                    if len(parts) == 5:
                        return application.jobs.get(project_id, parts[4])
                if resource == "results" and len(parts) == 6:
                    return application.result(project_id, parts[4], parts[5])
                if resource == "baselines" and len(parts) == 7 and parts[5] == "compare":
                    from agentagon.webapp.comparisons import compare

                    return compare(application.state.workspace(project_id), parts[4], parts[6])
                if resource == "runs" and len(parts) == 7 and parts[5] == "candidates":
                    from agentagon.experiments import inspection

                    return inspection.candidate(
                        application.state.workspace(project_id), parts[4], parts[6]
                    )
            raise AuditError("resource not found")

        def do_POST(self):
            self._mutate()

        def do_DELETE(self):
            self._mutate()

        def _mutate(self):
            if (
                not self._host()
                or self.headers.get_all("Origin", []) != [self.origin]
                or not self._token()
            ):
                self._json(
                    403, {"error": "Invalid application session or origin. Reload Agentagon."}
                )
                return
            try:
                body = self._body()
                parts = urlsplit(self.path).path.strip("/").split("/")
                result = (
                    self._delete(parts) if self.command == "DELETE" else self._post(parts, body)
                )
                self._json(200, result)
            except AuditError as exc:
                self._json(400, {"error": str(exc)})
            except (OSError, ValueError, TypeError, KeyError):
                self._json(
                    400,
                    {
                        "error": "Unable to apply this operation; check inputs and local permissions."
                    },
                )

        def _delete(self, parts):
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                return application.remove_project(parts[2])
            if len(parts) == 3 and parts[:2] == ["api", "connections"]:
                return application.disconnect(parts[2])
            raise AuditError("operation not found")

        def _post(self, parts, body):
            if parts == ["api", "projects"]:
                return application.register(body.get("path"))
            if parts == ["api", "projects", "clone"]:
                return application.clone_project(body)
            if parts == ["api", "connections"]:
                return application.save_connection(body)
            if parts == ["api", "agents"]:
                return application.save_agents(body)
            if len(parts) == 4 and parts[:2] == ["api", "connections"] and parts[3] == "test":
                return application.test_connection(parts[2])
            if len(parts) >= 4 and parts[:2] == ["api", "projects"]:
                project_id, resource = parts[2:4]
                application.state.project(project_id)
                if resource == "application-agents":
                    if len(parts) == 4:
                        return application.save_application_agent(project_id, body)
                    if len(parts) == 5:
                        if parts[4] == "discover":
                            return application.catalog.discover(project_id)
                        return application.save_application_agent(project_id, body, parts[4])
                    if len(parts) == 6 and parts[5] == "focuses":
                        return application.catalog.save_focus(project_id, parts[4], body)
                    if len(parts) == 8 and parts[5] == "focuses" and parts[7] == "measurement":
                        return application.catalog.bind_measurement(
                            project_id, parts[4], parts[6], body
                        )
                if resource == "jobs":
                    if len(parts) == 4:
                        return application.submit_job(project_id, body)
                    if len(parts) == 6:
                        return application.jobs.control(project_id, parts[4], parts[5], body)
                if resource == "settings" and len(parts) == 4:
                    return application.update_settings(project_id, body)
                if resource == "deliveries" and len(parts) == 4:
                    return application.deliver(project_id, body)
                if resource == "runs" and len(parts) == 6 and parts[5] == "control":
                    return application.control_run(project_id, parts[4], body)
                if resource == "imports":
                    if len(parts) == 4:
                        return application.import_preview(project_id, body)
                    if len(parts) == 5 and parts[4] == "preview":
                        return application.preview(project_id, body)
                if resource == "datasets":
                    if len(parts) == 5 and parts[4] == "derive":
                        return application.derive_dataset(project_id, body)
                    if len(parts) == 6 and parts[5] == "split":
                        from agentagon.webapp import datasets

                        return datasets.split(application.state, project_id, parts[4], body)
            raise AuditError("operation not found")

        def _events(self, project_id):
            application.state.workspace(project_id)
            if not streams.acquire(blocking=False):
                self._json(429, {"error": "Too many open activity streams."})
                return
            try:
                self.connection.settimeout(10)
                self._headers(200, "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                seen = {}
                while not application.jobs.stopping:
                    with application.jobs.condition:
                        jobs = application.jobs.list(project_id)
                        for job in jobs:
                            if seen.get(job["id"]) != job.get("revision"):
                                data = encoded(public_job(job))
                                # encoded() is pretty-printed JSON; every line needs SSE's data prefix.
                                lines = "\n".join("data: " + line for line in data.splitlines())
                                self.wfile.write(
                                    f"event: update\nid: {job['id']}:{job.get('revision', 0)}\n{lines}\n\n".encode()
                                )
                                seen[job["id"]] = job.get("revision")
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        application.jobs.condition.wait(timeout=10)
            except (BrokenPipeError, ConnectionError, OSError, AuditError):
                pass
            finally:
                streams.release()
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.application = application
    server.session_token = token
    return server
