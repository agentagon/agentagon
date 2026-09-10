"""Checkout-local viewer with explicitly enabled, same-origin fix controls."""

import json
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import quote, urlsplit

from agentagon.core.records import AuditError, encoded, timestamp_ns
from agentagon.operations import progress
from agentagon.reporting import build_fix_report, build_report
from agentagon.storage.workspace import Workspace

CONTROL_READ_TIMEOUT_SECONDS = 5

ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/workflow.js": ("workflow.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/logo-split-crown-96.png": ("logo-split-crown-96.png", "image/png"),
    "/tidal-causeway-light-sun.webp": ("tidal-causeway-light-sun.webp", "image/webp"),
    "/tidal-causeway-dark-moon.webp": ("tidal-causeway-dark-moon.webp", "image/webp"),
}


def _audits(workspace: Workspace) -> list[dict]:
    if not workspace.state.exists():
        return []
    return sorted(
        workspace.audits(),
        key=lambda audit: (timestamp_ns(audit["created_at"]), audit["audit_id"]),
        reverse=True,
    )


def _summary(audit: dict) -> dict:
    return {
        **{key: audit[key] for key in ("audit_id", "created_at", "mode", "goal")},
        **{
            key: value
            for key, value in progress(audit).items()
            if key in {"state", "pending_action", "coverage", "workflow", "code_scope", "revision"}
        },
    }


def _change_metadata(change: dict) -> dict:
    fields = {
        "path",
        "old_path",
        "new_path",
        "change_type",
        "revision",
        "old_digest",
        "new_digest",
        "old_mode",
        "new_mode",
        "old_start",
        "old_count",
        "new_start",
        "new_count",
    }
    result = {key: value for key, value in change.items() if key in fields}
    if "hunks" in change:
        result["hunks"] = [_change_metadata(hunk) for hunk in change["hunks"]]
    return result


def _display_finding(finding: dict) -> dict:
    return {
        **finding,
        "source_references": {
            key: {
                **{field: value for field, value in source.items() if field != "change"},
                **({"change": _change_metadata(source["change"])} if source.get("change") else {}),
            }
            for key, source in finding.get("source_references", {}).items()
        },
    }


def _detail(workspace: Workspace, audit_id: str) -> dict:
    # Build a deliberate display projection: never expose raw trace payloads,
    # credential configuration, provider receipts, or intelligence requests.
    report = build_report(workspace, audit_id)
    issue_keys = {
        "issue_id",
        "title",
        "summary",
        "rationale",
        "status",
        "severity",
        "confidence",
        "affected_trace_ids",
        "reviewed_trace_denominator",
        "reviewed_sample_rate",
        "findings",
    }
    issues = []
    for issue in report["issues"]:
        displayed = {key: value for key, value in issue.items() if key in issue_keys}
        displayed["findings"] = [_display_finding(finding) for finding in issue["findings"]]
        displayed["history"] = [
            {key: event[key] for key in ("at", "status", "reason")} for event in issue["history"]
        ]
        issues.append(displayed)
    alignment = report.get("trace_alignment")
    if alignment:
        alignment = {
            **{key: alignment.get(key) for key in ("status", "revision", "scope", "warning")},
            "mismatched_traces": len(alignment.get("mismatched_trace_ids", [])),
        }
    return {
        **{
            key: report[key]
            for key in (
                "audit_id",
                "created_at",
                "mode",
                "goal",
                "state",
                "pending_action",
                "coverage",
                "workflow",
                "revision",
            )
        },
        "host": report["host"],
        "model": report["model"],
        "window": report["window"],
        "code_scope": report["code_scope"].get("code_scope"),
        "code_scopes": report["code_scope"]["scopes"],
        "changes": [_change_metadata(change) for change in report["code_scope"].get("changes", [])],
        "skipped_code_files": len(report["code_scope"]["skipped"]),
        "skipped_code": [
            {"path": item["path"], "reason": item["reason"]}
            for item in report["code_scope"]["skipped"]
        ],
        "trace_alignment": alignment,
        "issues": issues,
        "ungrouped_findings": [
            _display_finding(finding) for finding in report["ungrouped_findings"]
        ],
        "limits": report["limits"],
    }


def _runs(workspace: Workspace) -> list[dict]:
    if not (workspace.state / "runs").exists():
        return []
    from agentagon.experiments.store import list_runs

    return sorted(
        list_runs(workspace),
        key=lambda run: (run.get("created_at") or "", run["run_id"]),
        reverse=True,
    )


def _run_summary(run: dict) -> dict:
    return {key: run.get(key) for key in ("run_id", "created_at", "updated_at", "goal", "state")}


def _run_detail(workspace: Workspace, run_id: str) -> dict:
    from agentagon.experiments import orchestration
    from agentagon.experiments.store import load_run

    data = load_run(workspace, run_id)
    result = build_fix_report(workspace, data)
    if "orchestration" in data.get("profile", {}):
        packet = orchestration.next_packet(workspace, run_id)
        result.update(
            work=packet["work"],
            work_reason=packet["reason"],
            lesson_context=packet["lesson_context"],
        )
    return result


def create_server(
    workspace: Workspace,
    audit_id: str | None = None,
    port: int = 0,
    *,
    run_id: str | None = None,
    controls: bool = False,
) -> ThreadingHTTPServer:
    """Construct a server without starting it; caller owns shutdown/server_close."""
    audits = _audits(workspace)
    if audit_id is not None and not any(audit["audit_id"] == audit_id for audit in audits):
        raise AuditError("audit not found in this checkout")
    if audit_id is not None and run_id is not None:
        raise AuditError("select an audit or a fix run, not both")
    if run_id is not None and not any(run["run_id"] == run_id for run in _runs(workspace)):
        raise AuditError("fix run not found in this checkout")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise AuditError("dashboard port must be between 0 and 65535")
    if not isinstance(controls, bool):
        raise AuditError("dashboard controls must be explicitly enabled or disabled")
    session_token = secrets.token_urlsafe(32) if controls else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Request paths can contain customer text; do not echo them to the CLI.
            pass

        def _respond(
            self, status: int, body: bytes, content_type: str, *, attachment: bool = False
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if attachment:
                self.send_header(
                    "Content-Disposition", 'attachment; filename="agentagon-artifact.bin"'
                )
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            )
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._respond(status, encoded(payload).encode(), "application/json; charset=utf-8")

        def _valid_host(self) -> bool:
            # Only the exact origin printed at startup is accepted. In particular,
            # a public hostname resolving to loopback cannot read local audits.
            return self.headers.get_all("Host", []) == [f"127.0.0.1:{self.server.server_port}"]

        def _origin(self) -> str:
            return f"http://127.0.0.1:{self.server.server_port}"

        def _bootstrap(self) -> None:
            # Fetch metadata is browser-controlled. A navigation or cross-origin page
            # cannot obtain the session token, even when it guesses this server's port.
            if (
                self.headers.get_all("Sec-Fetch-Site", []) != ["same-origin"]
                or self.headers.get_all("Sec-Fetch-Mode", []) != ["same-origin"]
                or self.headers.get_all("X-Agentagon-Bootstrap", []) != ["1"]
                or self.headers.get_all("Origin", []) not in ([], [self._origin()])
            ):
                self._json(403, {"error": "Open the dashboard page to use this session."})
                return
            self._json(200, {"controls_enabled": controls, "token": session_token})

        def do_GET(self):
            if not self._valid_host():
                self._json(403, {"error": "Use the local dashboard URL printed by Agentagon."})
                return
            path = urlsplit(self.path).path
            try:
                if path in ASSETS:
                    name, content_type = ASSETS[path]
                    self._respond(
                        200,
                        files("agentagon").joinpath("dashboard_assets", name).read_bytes(),
                        content_type,
                    )
                elif path == "/api/session":
                    self._bootstrap()
                elif path == "/api/evaluations" or path.startswith("/api/evaluations/"):
                    self._serve_evaluations(path)
                elif path == "/api/audits" or path.startswith("/api/audits/"):
                    self._serve_audits(path)
                elif path == "/api/runs" or path.startswith("/api/runs/"):
                    self._serve_runs(path)
                else:
                    self._json(404, {"error": "Page not found."})
            except (AuditError, OSError, ValueError, KeyError, TypeError):
                self._json(500, {"error": "Unable to read local state. Check agentagon status."})

        do_HEAD = do_GET

        def _serve_evaluations(self, path: str) -> None:
            from agentagon.experiments.inspection import evaluations

            if path == "/api/evaluations":
                self._json(
                    200,
                    {"workspace": str(workspace.root), "evaluations": evaluations(workspace)},
                )
                return
            requested_id = path.removeprefix("/api/evaluations/")
            selected = next(
                (
                    evaluation
                    for evaluation in evaluations(workspace)
                    if evaluation["evaluation_id"] == requested_id
                ),
                None,
            )
            self._json(
                200 if selected else 404,
                selected or {"error": "Evaluation not found in this checkout."},
            )

        def _serve_audits(self, path: str) -> None:
            if path == "/api/audits":
                audits = _audits(workspace)
                self._json(
                    200,
                    {
                        "workspace": str(workspace.root),
                        "audits": [_summary(audit) for audit in audits],
                        "selected_audit_id": audit_id
                        or (audits[0]["audit_id"] if audits else None),
                        "selected_view": "fix" if run_id is not None else "audit",
                    },
                )
                return
            requested_id = path.removeprefix("/api/audits/")
            if not any(audit["audit_id"] == requested_id for audit in _audits(workspace)):
                self._json(404, {"error": "Audit not found in this checkout."})
                return
            self._json(200, _detail(workspace, requested_id))

        def _serve_runs(self, path: str) -> None:
            if path == "/api/runs":
                runs = _runs(workspace)
                self._json(
                    200,
                    {
                        "workspace": str(workspace.root),
                        "runs": [_run_summary(run) for run in runs],
                        "selected_run_id": run_id or (runs[0]["run_id"] if runs else None),
                        "selected_view": "fix" if run_id is not None else "audit",
                    },
                )
                return
            from agentagon.experiments import inspection

            parts = path.split("/")
            requested_id = parts[3]
            if not any(run["run_id"] == requested_id for run in _runs(workspace)):
                self._json(404, {"error": "Fix run not found in this checkout."})
                return
            if len(parts) == 4:
                self._json(200, _run_detail(workspace, requested_id))
            elif len(parts) == 6 and parts[4] == "candidates":
                self._json(200, inspection.candidate(workspace, requested_id, parts[5]))
            elif (
                len(parts) == 10
                and parts[4] == "candidates"
                and parts[6] == "trials"
                and parts[8] == "artifacts"
                and parts[9].isdecimal()
            ):
                self._respond(
                    200,
                    inspection.artifact(workspace, requested_id, parts[5], parts[7], int(parts[9])),
                    "application/octet-stream",
                    attachment=True,
                )
            else:
                self._json(404, {"error": "Evidence endpoint not found."})

        def do_POST(self):
            if not controls:
                self._json(405, {"error": "The dashboard is read-only."})
                return
            if (
                not self._valid_host()
                or self.headers.get_all("Origin", []) != [self._origin()]
                or len(self.headers.get_all("X-Agentagon-Token", [])) != 1
                or not self.headers.get("X-Agentagon-Token", "").isascii()
                or not secrets.compare_digest(
                    self.headers.get("X-Agentagon-Token", ""), session_token
                )
            ):
                self._json(403, {"error": "Invalid dashboard session or origin. Reload this page."})
                return
            parts = urlsplit(self.path).path.split("/")
            if len(parts) != 5 or parts[1:3] != ["api", "runs"] or parts[4] != "control":
                self._json(404, {"error": "Control endpoint not found."})
                return
            if self.headers.get_all("Content-Type", []) != ["application/json"]:
                self._json(415, {"error": "Controls require application/json."})
                return
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                self._json(400, {"error": "Controls require one bounded JSON request body."})
                return
            try:
                size = int(lengths[0])
            except ValueError:
                size = 0
            from agentagon.experiments import controls as control_api

            if not 0 < size <= control_api.MAX_CONTROL_REQUEST_BYTES:
                self._json(
                    413,
                    {
                        "error": "Control request must be between 1 and "
                        f"{control_api.MAX_CONTROL_REQUEST_BYTES} bytes."
                    },
                )
                return
            try:
                self.connection.settimeout(CONTROL_READ_TIMEOUT_SECONDS)
                payload = json.loads(self.rfile.read(size))
                allowed = {
                    "policy",
                    "directive",
                    "expand",
                    "stop",
                    "continue",
                    "select",
                    "invalidate",
                    "exhaust",
                    "cancel",
                }
                if (
                    not isinstance(payload, dict)
                    or not isinstance(payload.get("action"), str)
                    or payload["action"] not in allowed
                ):
                    self._json(400, {"error": "Unsupported dashboard control action."})
                    return
                if not any(run["run_id"] == parts[3] for run in _runs(workspace)):
                    self._json(404, {"error": "Fix run not found in this checkout."})
                    return
                from agentagon.experiments.store import load_run

                control_api.submit(workspace, parts[3], payload)
                # The execution response can contain host packets. Serve only the
                # deliberate display projection, also used by the read endpoint.
                display = control_api.projection(load_run(workspace, parts[3]))
                self._json(200, display)
            except AuditError as exc:
                self._json(getattr(exc, "status_code", 400), {"error": str(exc)})
            except (ValueError, UnicodeError):
                self._json(400, {"error": "Control request must contain valid UTF-8 JSON."})
            except OSError:
                self._json(500, {"error": "Unable to save the control. Refresh before retrying."})

        def _unsupported_method(self):
            self._json(405, {"error": "Only POST is supported for enabled fix controls."})

        do_PUT = _unsupported_method
        do_PATCH = _unsupported_method
        do_DELETE = _unsupported_method
        do_OPTIONS = _unsupported_method

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def serve(
    workspace: Workspace,
    audit_id: str | None = None,
    port: int = 0,
    open_browser: bool = True,
    *,
    run_id: str | None = None,
    controls: bool = False,
) -> None:
    """Print the URL, optionally open it, and serve until interrupted."""
    with create_server(workspace, audit_id, port, run_id=run_id, controls=controls) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        if audit_id is not None:
            url += "?audit=" + quote(audit_id, safe="")
        elif run_id is not None:
            url += "?run=" + quote(run_id, safe="")
        print(
            json.dumps({"state": "serving", "url": url, "workspace": str(workspace.root)}),
            flush=True,
        )
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
