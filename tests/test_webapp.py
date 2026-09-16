"""Local app integration: safe routing, imports, settings and launcher compatibility."""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest
from click.testing import CliRunner

from agentagon.cli.main import main
from agentagon.core.records import AuditError
from agentagon.storage.config import Config
from agentagon.webapp import snapshots
from agentagon.webapp.providers import CredentialStore
from agentagon.webapp.server import create_server
from agentagon.webapp.service import Application


class Provider:
    calls = []

    def __init__(self, connection, credentials):
        self.connection = connection
        self.credentials = credentials

    def test(self):
        self.credentials.resolve(self.connection["credentials"]["api_key"])
        return {"status": "connected", "projects": [{"id": "remote", "name": "Remote"}]}

    def datasets(self):
        return [{"id": "dataset-one", "name": "Tasks"}]

    def preview(self, kind, selection):
        self.calls.append((kind, selection))
        return {
            "items": [
                {
                    "id": "item-one",
                    "input": {"messages": [{"role": "user", "content": "hello"}]},
                    "expected": None,
                    "expected_present": False,
                }
            ],
            "provenance": {"provider": "braintrust", "version": "pinned-version"},
            "completeness": {"complete": True, "count": 1},
        }


@pytest.fixture
def app(tmp_path):
    application = Application(
        tmp_path / "app-state", execute=lambda *_: {}, provider_factory=Provider
    )
    yield application
    application.close()


def project(app, tmp_path, name="project"):
    root = tmp_path / name
    root.mkdir()
    (root / "app.py").write_text("def answer():\n    return 42\n")
    return app.register(str(root))


def connection(app, project_id):
    return app.save_connection(
        {
            "name": "Production",
            "provider": "braintrust",
            "project": "remote",
            "project_ids": [project_id],
            "credentials": {"api_key": "private-test-value"},
            "credential_mode": "session",
        }
    )


@pytest.mark.parametrize("mode", ["keyring", "session", "env"])
@pytest.mark.parametrize("commit_succeeds", [True, False])
def test_claude_credential_rotation_follows_settings_commit(
    app, monkeypatch, mode, commit_succeeds
):
    class Keyring:
        def __init__(self):
            self.values = {}

        def set_password(self, service, name, value):
            self.values[name] = value

        def get_password(self, service, name):
            return self.values.get(name)

        def delete_password(self, service, name):
            assert app.state.read()["agents"]["claude_api_key_ref"] != name
            del self.values[name]

    keyring = Keyring()
    app.credentials = CredentialStore(keyring)
    monkeypatch.setattr("agentagon.webapp.service.detect_agents", lambda: [])
    app.save_agents({"claude_api_key": "old-secret", "credential_mode": "keyring"})
    previous = app.state.read()["agents"]["claude_api_key_ref"]
    monkeypatch.setenv("REPLACEMENT_CLAUDE_KEY", "new-secret")
    payload = {
        "claude_api_key": "REPLACEMENT_CLAUDE_KEY" if mode == "env" else "new-secret",
        "credential_mode": mode,
    }
    if commit_succeeds:
        app.save_agents(payload)
        current = app.state.read()["agents"]["claude_api_key_ref"]
        assert current != previous
        assert app.credentials.resolve(current) == "new-secret"
        assert previous not in keyring.values
        with pytest.raises(AuditError, match="unavailable"):
            app.credentials.resolve(previous)
        # Changing other settings keeps the current credential usable.
        app.save_agents({"concurrency": 2})
        assert app.state.read()["agents"]["claude_api_key_ref"] == current
        assert app.credentials.resolve(current) == "new-secret"
    else:

        @contextmanager
        def failed_commit():
            yield app.state.read()
            raise OSError("settings commit failed")

        monkeypatch.setattr(app.state, "locked", failed_commit)
        with pytest.raises(OSError, match="settings commit failed"):
            app.save_agents(payload)
        assert app.state.read()["agents"]["claude_api_key_ref"] == previous
        assert app.credentials.resolve(previous) == "old-secret"
        assert keyring.values == {previous: "old-secret"}
        assert not app.credentials._values
    assert app.credentials.resolve("env:REPLACEMENT_CLAUDE_KEY") == "new-secret"


def test_claude_credential_rotation_waits_for_starting_session(app, tmp_path, monkeypatch):
    resolving = threading.Event()
    release = threading.Event()
    rotating = threading.Event()
    observed = []
    monkeypatch.setattr("agentagon.webapp.service.detect_agents", lambda: [])
    app.save_agents({"claude_api_key": "old-secret", "credential_mode": "session"})
    previous = app.state.read()["agents"]["claude_api_key_ref"]
    resolve = app.credentials.resolve

    def paused_resolve(reference):
        if reference == previous:
            resolving.set()
            assert release.wait(5), "starting session was not released"
        return resolve(reference)

    class ObservedCondition(threading.Condition):
        def __enter__(self):
            if threading.current_thread().name.startswith("credential-rotation"):
                rotating.set()
            return super().__enter__()

    def host(request, *_):
        observed.append(request["api_key"])
        return {"state": "interrupted", "session_id": "saved-session"}

    monkeypatch.setattr(app.credentials, "resolve", paused_resolve)
    app.jobs.condition = ObservedCondition(threading.RLock())
    app.jobs.execute = host
    saved = project(app, tmp_path)
    job = app.jobs.submit(
        saved["id"],
        {
            "operation_id": str(uuid.uuid4()),
            "kind": "audit",
            "goal": "Review this application",
            "agent": "claude",
        },
    )
    try:
        assert resolving.wait(5), "session did not read its credential reference"
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="credential-rotation") as pool:
            rotation = pool.submit(
                app.save_agents,
                {"claude_api_key": "new-secret", "credential_mode": "session"},
            )
            try:
                # Let rotation reach the manager lock while resolution is paused.
                assert rotating.wait(5), "rotation did not reach the session lock"
            finally:
                release.set()
            rotation.result(timeout=5)
        for worker in list(app.jobs.threads):
            worker.join(timeout=5)
        result = app.jobs.get(saved["id"], job["id"])
        assert result["state"] == "interrupted", result["next_action"]
        assert result["session_id"] == "saved-session"
        assert observed == ["old-secret"]
        current = app.state.read()["agents"]["claude_api_key_ref"]
        assert resolve(current) == "new-secret"
        with pytest.raises(AuditError, match="unavailable"):
            resolve(previous)
    finally:
        release.set()


@contextmanager
def running(app):
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(
            base_url=origin,
            trust_env=False,
            headers={"Origin": origin, "X-Agentagon-Token": server.session_token},
            timeout=5,
        ) as client:
            yield client, server
    finally:
        app.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_register_and_read_never_initializes_or_executes(app, tmp_path):
    saved = project(app, tmp_path)
    assert app.register(saved["path"])["id"] == saved["id"]
    assert not (tmp_path / "project" / ".agentagon").exists()
    assert app.overview(saved["id"])["audits"] == []
    assert app.overview(saved["id"])["jobs"] == []
    assert not (tmp_path / "project" / ".agentagon").exists()


def test_canonical_aliases_share_registration(app, tmp_path):
    saved = project(app, tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(saved["path"])
    assert app.register(str(alias))["id"] == saved["id"]


def test_missing_root_visible_and_removal_preserves_directory(app, tmp_path):
    saved = project(app, tmp_path)
    (tmp_path / "project").rename(tmp_path / "moved")
    assert app.projects()["projects"][0]["available"] is False
    with pytest.raises(AuditError):
        app.overview(saved["id"])
    # Registration can be removed even after the path disappears.
    app.state.remove(saved["id"])
    assert (tmp_path / "moved" / "app.py").exists()


def test_connection_secrets_never_persist_and_assignments_are_explicit(app, tmp_path):
    first, second = project(app, tmp_path), project(app, tmp_path, "other")
    saved = connection(app, first["id"])
    assert "credentials" not in saved
    assert b"private-test-value" not in app.state.path.read_bytes()
    assert "private-test-value" not in json.dumps(app.connections())
    assert app.test_connection(saved["id"])["status"] == "connected"
    with pytest.raises(AuditError, match="assign"):
        app.preview(
            second["id"],
            {"connection_id": saved["id"], "kind": "dataset", "selection": {"dataset_id": "one"}},
        )


def test_snapshot_import_replay_and_project_isolation(app, tmp_path):
    first, second = project(app, tmp_path), project(app, tmp_path, "other")
    source = connection(app, first["id"])
    preview = app.preview(
        first["id"],
        {
            "connection_id": source["id"],
            "kind": "dataset",
            "selection": {"dataset_id": "one", "cap": 10},
        },
    )
    payload = {"preview_id": preview["preview_id"], "operation_id": str(uuid.uuid4())}
    with pytest.raises(AuditError, match="another project"):
        app.import_preview(second["id"], payload)
    saved = app.import_preview(first["id"], payload)
    assert saved["state"] == "draft" and saved["missing_expectations"] == 1
    assert app.import_preview(first["id"], payload)["id"] == saved["id"]
    assert app.overview(first["id"])["datasets"][0]["id"] == saved["id"]
    with pytest.raises(AuditError, match="not found"):
        app.result(second["id"], "dataset", saved["id"])
    app.disconnect(source["id"])
    assert app.overview(first["id"])["datasets"][0]["id"] == saved["id"]


def test_import_uses_provider_cleaned_selection_without_echoing_credentials(app, tmp_path):
    from agentagon.webapp.providers import ProviderClient

    saved_project = project(app, tmp_path)
    secret = "private-filter-test-value"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    app.provider_factory = lambda connection, credentials: ProviderClient(
        connection, credentials, transport=transport
    )
    source = app.save_connection(
        {
            "name": "Dataset source",
            "provider": "langsmith",
            "project_ids": [saved_project["id"]],
            "credentials": {"api_key": secret},
        }
    )
    preview = app.preview(
        saved_project["id"],
        {
            "connection_id": source["id"],
            "kind": "dataset",
            "selection": {"dataset_id": "one", "filters": {"metadata": {"note": secret}}},
        },
    )
    assert secret not in json.dumps(preview)
    assert preview["selection"] == preview["provenance"]["selection"]
    assert preview["selection"]["cap"] == 50
    saved = app.import_preview(
        saved_project["id"],
        {"preview_id": preview["preview_id"], "operation_id": str(uuid.uuid4())},
    )
    workspace = app.state.workspace(saved_project["id"])
    path = workspace.state / "webapp" / "imports" / f"{saved['id']}.json"
    assert secret not in path.read_text()
    assert secret not in json.dumps(app.result(saved_project["id"], "dataset", saved["id"]))


@pytest.mark.parametrize("kind", ["dataset", "traces"])
def test_copied_snapshot_retains_its_original_project_binding(app, tmp_path, kind):
    first, second = project(app, tmp_path), project(app, tmp_path, "other")
    source = connection(app, first["id"])
    preview = app.preview(
        first["id"],
        {"connection_id": source["id"], "kind": kind, "selection": {"dataset_id": "one"}},
    )
    saved = app.import_preview(
        first["id"],
        {"preview_id": preview["preview_id"], "operation_id": str(uuid.uuid4())},
    )
    original = app.state.workspace(first["id"])
    target = app.state.workspace(second["id"])
    target.initialize()
    relative = f"webapp/imports/{saved['id']}.json"
    target.write_bytes(target.state / relative, (original.state / relative).read_bytes())

    with pytest.raises(AuditError, match="belongs to another project"):
        snapshots.load(target, saved["id"])
    with pytest.raises(AuditError, match="belongs to another project"):
        app.result(second["id"], kind, saved["id"])
    with pytest.raises(AuditError, match="belongs to another project"):
        app.overview(second["id"])
    assert app.result(first["id"], kind, saved["id"])["project_id"] == first["id"]


@pytest.mark.parametrize("change", ["disconnect", "replace"])
def test_delayed_connection_test_preserves_new_configuration(app, tmp_path, change):
    saved_project = project(app, tmp_path)
    source = connection(app, saved_project["id"])
    entered, release = threading.Event(), threading.Event()

    class DelayedProvider(Provider):
        def test(self):
            self.credentials.resolve(self.connection["credentials"]["api_key"])
            entered.set()
            assert release.wait(5), "connection test was not released"
            return {"status": "connected", "projects": [{"id": "old-project"}]}

    app.provider_factory = DelayedProvider
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(app.test_connection, source["id"])
        try:
            assert entered.wait(5), "connection test did not start"
            if change == "disconnect":
                app.disconnect(source["id"])
            else:
                app.save_connection(
                    {
                        "id": source["id"],
                        "name": "New connection settings",
                        "provider": "braintrust",
                        "project": "new-project",
                        "project_ids": [saved_project["id"]],
                        "credentials": {"api_key": "new-private-test-value"},
                    }
                )
        finally:
            release.set()
        with pytest.raises(AuditError, match="not found|changed"):
            pending.result(timeout=5)

    if change == "disconnect":
        assert app.connections()["connections"] == []
    else:
        current = app.connection(source["id"])
        assert current["project"] == "new-project"
        assert current["status"] == "not_tested"
        assert "projects" not in current
        assert (
            app.credentials.resolve(current["credentials"]["api_key"]) == "new-private-test-value"
        )


def test_snapshot_tampering_is_detected(app, tmp_path):
    saved_project = project(app, tmp_path)
    source = connection(app, saved_project["id"])
    preview = app.preview(
        saved_project["id"],
        {"connection_id": source["id"], "kind": "dataset", "selection": {"dataset_id": "one"}},
    )
    saved = app.import_preview(
        saved_project["id"],
        {"preview_id": preview["preview_id"], "operation_id": str(uuid.uuid4())},
    )
    workspace = app.state.workspace(saved_project["id"])
    path = workspace.state / "webapp" / "imports" / f"{saved['id']}.json"
    content = json.loads(path.read_text())
    content["items"][0]["expected"] = "invented answer"
    path.write_text(json.dumps(content))
    with pytest.raises(AuditError, match="integrity"):
        snapshots.load(workspace, saved["id"])


def test_cli_trace_settings_do_not_create_app_connections(app, tmp_path):
    saved = project(app, tmp_path)
    workspace = app.state.workspace(saved["id"])
    Config().update(
        "project",
        workspace.root,
        {
            "traces.source": "braintrust",
            "traces.project": "remote",
            "traces.api_key_env": "TEST_PROVIDER_KEY",
        },
    )
    assert app.connections()["connections"] == []
    assert Config().effective(workspace.root)["traces"]["source"] == "braintrust"


def test_settings_preserve_scope_and_reject_stale_revision(app, tmp_path):
    first, second = project(app, tmp_path), project(app, tmp_path, "other")
    original = app.settings(first["id"])
    app.update_settings(
        first["id"],
        {
            "scope": "project",
            "values": {"traces.state": "disabled"},
            "expected_revision": original["revision"],
        },
    )
    assert app.settings(second["id"])["settings"]["traces"]["state"] == "unset"
    with pytest.raises(AuditError, match="changed"):
        app.update_settings(
            first["id"],
            {"values": {"traces.state": "enabled"}, "expected_revision": original["revision"]},
        )


def test_http_bootstrap_origin_host_and_project_routing(app, tmp_path):
    saved = project(app, tmp_path)
    with running(app) as (client, server):
        root = client.get("/")
        assert root.status_code == 200 and "Agentagon" in root.text
        assert "frame-ancestors 'none'" in root.headers["content-security-policy"]
        assert client.get("/api/session").status_code == 403
        boot = client.get(
            "/api/session",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "same-origin",
                "X-Agentagon-Bootstrap": "1",
            },
        )
        assert boot.json()["token"] == server.session_token
        assert client.get("/api/projects", headers={"Host": "malicious.example"}).status_code == 403
        assert (
            client.get("/api/projects", headers={"Origin": "https://malicious.example"}).status_code
            == 403
        )
        assert (
            client.post(
                "/api/projects",
                headers={"Origin": "https://malicious.example"},
                json={"path": str(tmp_path)},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/projects",
                headers={"X-Agentagon-Token": "wrong"},
                json={"path": str(tmp_path)},
            ).status_code
            == 403
        )
        assert (
            client.get(f"/api/projects/{saved['id']}/overview").json()["project"]["id"]
            == saved["id"]
        )
        assert client.get("/api/projects/project_" + "0" * 24 + "/overview").status_code == 400
        assert (
            client.post(
                "/api/projects",
                content='{"path": NaN}',
                headers={"Content-Type": "application/json"},
            ).status_code
            == 400
        )


def test_bare_cli_opens_app_but_subcommands_stay_independent(monkeypatch, tmp_path):
    from agentagon.webapp import launcher

    calls = []
    monkeypatch.setattr(launcher, "launch", lambda path, **kwargs: calls.append((path, kwargs)))
    runner = CliRunner()
    assert runner.invoke(main, ["--workspace", str(tmp_path)]).exit_code == 0
    assert calls[0][0] == tmp_path
    assert runner.invoke(main, ["app", "--no-open"]).exit_code == 0
    assert calls[-1][1]["open_browser"] is False
    count = len(calls)
    assert runner.invoke(main, ["--help"]).exit_code == 0
    assert runner.invoke(main, ["--version"]).exit_code == 0
    assert runner.invoke(main, ["--workspace", str(tmp_path), "setup"]).exit_code == 0
    assert len(calls) == count


def test_private_dataset_materializes_only_in_preparation_worktree(app, application, specification):
    from support.evaluation import draft

    from agentagon.experiments import checkouts
    from agentagon.experiments.engine import _freeze

    saved_project = app.register(str(application.root))
    source = connection(app, saved_project["id"])
    preview = app.preview(
        saved_project["id"],
        {"connection_id": source["id"], "kind": "dataset", "selection": {"dataset_id": "one"}},
    )
    saved = app.import_preview(
        saved_project["id"],
        {"preview_id": preview["preview_id"], "operation_id": str(uuid.uuid4())},
    )
    evaluation, _ = draft(application, specification)
    materialized = snapshots.materialize(application, saved["id"], evaluation["evaluation_id"])
    from pathlib import Path

    assert (Path(materialized["worktree"]) / materialized["path"]).exists()
    assert not (application.root / materialized["path"]).exists()
    assert materialized["private"]
    frozen = _freeze(application, {"inputs": [materialized["input"]], "overlays": []})
    assert len(frozen) == 1 and frozen[0]["kind"] == "inputs"
    assert frozen[0]["deliver"] is False
    runner = application.state / "private-input-runner"
    checkouts.copy_frozen(application.root, runner, frozen)
    copied = json.loads((runner / materialized["path"]).read_text())
    assert copied["snapshot_id"] == saved["id"]
    assert copied["items"] == preview["items"]


def test_launcher_reuses_verified_service(app, tmp_path, capsys):
    from agentagon.webapp.launcher import _reuse
    from agentagon.webapp.state import atomic_write

    saved = project(app, tmp_path)
    with running(app) as (_, server):
        atomic_write(
            app.state.directory / "instance.json",
            {"port": server.server_port, "token": server.session_token},
        )
        _reuse(app.state, saved["path"], False)
        assert f"/?project={saved['id']}" in capsys.readouterr().out
        assert len(app.projects()["projects"]) == 1
