"""Local app integration: safe routing, imports, settings and launcher compatibility."""

import json
import os
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
from agentagon.webapp.providers import CredentialStore, ProviderError
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
def app(tmp_path, monkeypatch):
    application = Application(
        tmp_path / "app-state", execute=lambda *_: {}, provider_factory=Provider
    )

    def unavailable_keyring():
        raise ProviderError("OS credential storage unavailable")

    monkeypatch.setattr(application.credentials, "_keyring", unavailable_keyring)
    yield application
    application.close()


def project(app, tmp_path, name="project"):
    root = tmp_path / name
    root.mkdir()
    (root / "app.py").write_text("def answer():\n    return 42\n")
    return app.register(str(root))


def connection(app, project_id, **changes):
    discovered = app.discover_connection(
        project_id,
        {
            "provider": "braintrust",
            "credentials": {"api_key": "private-test-value"},
            **changes,
        },
    )
    return app.save_connection(
        project_id,
        {
            "discovery_id": discovered["discovery_id"],
            "project": discovered["projects"][0]["selection_id"],
        },
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


def test_connection_secrets_never_persist_and_one_project_owns_connection(app, tmp_path):
    first, second = project(app, tmp_path), project(app, tmp_path, "other")
    saved = connection(app, first["id"])
    assert "credentials" not in saved
    assert b"private-test-value" not in app.state.path.read_bytes()
    assert "private-test-value" not in json.dumps(app.connections(first["id"]))
    assert saved["project_id"] == first["id"]
    assert "project_ids" not in saved
    assert app.connections(second["id"])["connections"] == []
    assert app.test_connection(first["id"], saved["id"])["status"] == "connected"
    with pytest.raises(AuditError, match="not found in this project"):
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
    app.disconnect(first["id"], source["id"])
    assert app.overview(first["id"])["datasets"][0]["id"] == saved["id"]


def test_import_uses_provider_cleaned_selection_without_echoing_credentials(app, tmp_path):
    from agentagon.webapp.providers import ProviderClient

    saved_project = project(app, tmp_path)
    secret = "private-filter-test-value"

    def provider_response(request):
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(403)
        return httpx.Response(
            200,
            json=[{"id": "remote", "name": "Remote"}] if request.url.path == "/sessions" else [],
        )

    transport = httpx.MockTransport(provider_response)
    app.provider_factory = lambda connection, credentials: ProviderClient(
        connection, credentials, transport=transport
    )
    source = connection(
        app, saved_project["id"], provider="langsmith", credentials={"api_key": secret}
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
        pending = executor.submit(app.test_connection, saved_project["id"], source["id"])
        try:
            assert entered.wait(5), "connection test did not start"
            if change == "disconnect":
                app.disconnect(saved_project["id"], source["id"])
            else:

                class NewProvider(Provider):
                    def test(self):
                        return {
                            "status": "connected",
                            "projects": [{"id": "new-project", "name": "New project"}],
                        }

                app.provider_factory = NewProvider
                connection(
                    app,
                    saved_project["id"],
                    id=source["id"],
                    credentials={"api_key": "new-private-test-value"},
                )
        finally:
            release.set()
        with pytest.raises(AuditError, match="not found|changed"):
            pending.result(timeout=5)

    if change == "disconnect":
        assert app.connections(saved_project["id"])["connections"] == []
    else:
        current = app.connection(source["id"], saved_project["id"])
        assert current["project"] == "new-project"
        assert current["status"] == "connected"
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
    assert app.connections(saved["id"])["connections"] == []
    assert Config().effective(workspace.root)["traces"]["source"] == "braintrust"


def test_settings_preserve_scope_direct_intelligence_key_and_reject_stale_revision(
    app, tmp_path
):
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
    updated = app.update_settings(
        first["id"],
        {
            "scope": "project",
            "values": {"intelligence.mode": "ask"},
            "intelligence_api_key": "private-intelligence-key",
        },
    )
    key_env = updated["settings"]["intelligence"]["api_key_env"]
    assert key_env.startswith("AGENTAGON_INTELLIGENCE_")
    assert updated["intelligence_key_configured"] is True
    assert os.environ[key_env] == "private-intelligence-key"
    assert "private-intelligence-key" not in json.dumps(updated)
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


def test_discovery_is_ephemeral_binds_workspace_and_replays_save(app, tmp_path):
    first, second = project(app, tmp_path), project(app, tmp_path, "other")

    class Workspaces(Provider):
        def test(self):
            return {
                "status": "connected",
                "projects": [
                    {
                        "id": "same-id",
                        "name": "Production",
                        "workspace_id": "one",
                        "workspace_name": "One",
                    },
                    {
                        "id": "same-id",
                        "name": "Staging",
                        "workspace_id": "two",
                        "workspace_name": "Two",
                    },
                ],
            }

    app.provider_factory = Workspaces
    discovered = app.discover_connection(
        first["id"],
        {
            "provider": "langsmith",
            "endpoint": "https://example.test",
            "credentials": {"api_key": "ephemeral-secret"},
        },
    )
    assert len({row["selection_id"] for row in discovered["projects"]}) == 2
    assert app.state.read()["connections"] == {}
    assert b"ephemeral-secret" not in app.state.path.read_bytes()
    assert "ephemeral-secret" not in json.dumps(discovered)
    temporary = next(iter(app.credentials._values))
    with pytest.raises(AuditError, match="choose a project"):
        app.save_connection(
            first["id"], {"discovery_id": discovered["discovery_id"], "project": "invented"}
        )
    command = {
        "discovery_id": discovered["discovery_id"],
        "project": discovered["projects"][1]["selection_id"],
    }
    with pytest.raises(AuditError, match="not found in this project"):
        app.save_connection(second["id"], command)
    saved = app.save_connection(first["id"], command)
    assert (
        saved["project_id"],
        saved["project"],
        saved["workspace_id"],
        saved["project_name"],
    ) == (first["id"], "same-id", "two", "Staging")
    assert saved["name"] == "LangSmith · Staging"
    assert saved["endpoint"] == "https://example.test"
    assert temporary not in app.credentials._values
    assert app.save_connection(first["id"], command) == saved
    assert len(app.state.read()["connections"]) == 1
    with pytest.raises(AuditError, match="changed after saving"):
        app.save_connection(
            first["id"], {**command, "project": discovered["projects"][0]["selection_id"]}
        )


def test_connection_edit_keeps_blank_credentials_and_cleans_failed_commit(
    app, tmp_path, monkeypatch
):
    saved_project = project(app, tmp_path)
    saved = connection(app, saved_project["id"], endpoint="https://provider.example")
    previous = app.connection(saved["id"], saved_project["id"])
    edited = connection(app, saved_project["id"], id=saved["id"], credentials={"api_key": ""})
    assert edited["endpoint"] == "https://provider.example"
    assert (
        app.connection(saved["id"], saved_project["id"])["credentials"] == previous["credentials"]
    )
    discovered = app.discover_connection(
        saved_project["id"],
        {
            "id": saved["id"],
            "provider": "braintrust",
            "credentials": {"api_key": "replacement-secret"},
        },
    )

    @contextmanager
    def failed_commit():
        yield app.state.read()
        raise OSError("commit failed")

    monkeypatch.setattr(app.state, "locked", failed_commit)
    with pytest.raises(OSError, match="commit failed"):
        app.save_connection(
            saved_project["id"],
            {
                "discovery_id": discovered["discovery_id"],
                "project": discovered["projects"][0]["selection_id"],
            },
        )
    assert app.credentials._values == {previous["credentials"]["api_key"]: "private-test-value"}
    assert not app.connection_discoveries
    assert (
        app.connection(saved["id"], saved_project["id"])["credentials"] == previous["credentials"]
    )


def test_discovery_replacement_failure_and_expiry_clear_temporary_credentials(
    app, tmp_path, monkeypatch
):
    saved = project(app, tmp_path)
    command = {"provider": "braintrust", "credentials": {"api_key": "temporary-secret"}}
    initial = app.discover_connection(saved["id"], command)
    reference = next(iter(app.credentials._values))
    app.discover_connection(saved["id"], command)
    assert initial["discovery_id"] not in app.connection_discoveries
    assert reference not in app.credentials._values

    class Broken(Provider):
        def test(self):
            raise AuditError("provider authentication failed")

    app.provider_factory = Broken
    with pytest.raises(AuditError, match="authentication failed"):
        app.discover_connection(saved["id"], command)
    assert not app.connection_discoveries
    assert not app.credentials._values
    app.provider_factory = Provider
    expired = threading.Event()
    discard = app._discard_discovery

    def observe_expiry(discovery_id):
        discard(discovery_id)
        expired.set()

    monkeypatch.setattr(app, "_discard_discovery", observe_expiry)
    monkeypatch.setattr("agentagon.webapp.service.DISCOVERY_TTL_SECONDS", 0.05)
    result = app.discover_connection(saved["id"], command)
    assert expired.wait(2), "discovery timer did not clear expired credentials"
    assert not app.connection_discoveries
    assert not app.credentials._values
    with pytest.raises(AuditError, match="discover again"):
        app.save_connection(
            saved["id"],
            {
                "discovery_id": result["discovery_id"],
                "project": result["projects"][0]["selection_id"],
            },
        )


def test_remove_project_deletes_only_its_connections_and_credentials(app, tmp_path):
    first, second = project(app, tmp_path), project(app, tmp_path, "other")
    owned, retained = connection(app, first["id"]), connection(app, second["id"])
    owned_reference = app.connection(owned["id"], first["id"])["credentials"]["api_key"]
    retained_reference = app.connection(retained["id"], second["id"])["credentials"]["api_key"]
    app.discover_connection(
        first["id"], {"provider": "braintrust", "credentials": {"api_key": "pending-secret"}}
    )
    removed = app.remove_project(first["id"])
    assert removed["evidence_preserved"]
    assert app.state.read()["connections"].keys() == {retained["id"]}
    assert all(
        draft["connection"]["project_id"] == second["id"]
        for draft in app.connection_discoveries.values()
    )
    assert owned_reference not in app.credentials._values
    assert app.credentials._values == {retained_reference: "private-test-value"}


def test_connections_reject_remote_project_overrides_and_cross_project_actions(app, tmp_path):
    first, second = project(app, tmp_path), project(app, tmp_path, "other")
    saved = connection(app, first["id"])
    for operation in (
        lambda: app.test_connection(second["id"], saved["id"]),
        lambda: app.connection_datasets(second["id"], saved["id"]),
        lambda: app.disconnect(second["id"], saved["id"]),
        lambda: app.discover_connection(
            second["id"], {"id": saved["id"], "provider": "braintrust"}
        ),
        lambda: app.save_application_agent(
            second["id"], {"trace_selector": {"connection_id": saved["id"]}}
        ),
        lambda: app.preview_dataset_publication(
            second["id"], "unused", {"connection_id": saved["id"], "name": "Cases"}
        ),
        lambda: app.publish_dataset(
            second["id"],
            "unused",
            {
                "connection_id": saved["id"],
                "preview_id": "unused",
                "operation_id": str(uuid.uuid4()),
            },
        ),
    ):
        with pytest.raises(AuditError, match="not found in this project"):
            operation()
    for operation in (
        lambda: app.preview(
            first["id"],
            {
                "connection_id": saved["id"],
                "kind": "traces",
                "selection": {"project": "another-remote"},
            },
        ),
        lambda: app.preview_dataset_publication(
            first["id"],
            "unused",
            {"connection_id": saved["id"], "project": "another-remote", "name": "Cases"},
        ),
        lambda: app.save_application_agent(
            first["id"],
            {"trace_selector": {"connection_id": saved["id"], "project": "another-remote"}},
        ),
    ):
        with pytest.raises(AuditError, match="connected provider project"):
            operation()


def test_http_connections_discover_save_and_remain_project_scoped(app, tmp_path):
    first, second = project(app, tmp_path), project(app, tmp_path, "other")
    path = f"/api/projects/{first['id']}/connections"
    other = f"/api/projects/{second['id']}/connections"
    with running(app) as (client, _):
        assert client.get("/api/connections").status_code == 400
        assert client.post("/api/connections", json={}).status_code == 400
        discovery = client.post(
            path + "/discover",
            json={"provider": "braintrust", "credentials": {"api_key": "secret"}},
        )
        assert discovery.status_code == 200
        discovered = discovery.json()
        assert client.get(path).json() == {"connections": []}
        response = client.post(
            path,
            json={
                "discovery_id": discovered["discovery_id"],
                "project": discovered["projects"][0]["selection_id"],
            },
        )
        assert response.status_code == 200
        saved = response.json()
        assert client.get(path).json()["connections"][0]["id"] == saved["id"]
        assert client.get(other).json() == {"connections": []}
        for destination, expected in ((path, 200), (other, 400)):
            assert (
                client.post(destination + f"/{saved['id']}/test", json={}).status_code == expected
            )
            assert client.get(destination + f"/{saved['id']}/datasets").status_code == expected
        assert client.request("DELETE", other + f"/{saved['id']}", json={}).status_code == 400
        assert client.request("DELETE", path + f"/{saved['id']}", json={}).status_code == 200
        assert client.get(path).json() == {"connections": []}


def test_discovery_cache_bounds_and_close_remove_only_temporary_credentials(
    app, tmp_path, monkeypatch
):
    monkeypatch.setattr("agentagon.webapp.service.MAX_CONNECTION_DISCOVERIES", 2)
    projects = [project(app, tmp_path, f"project-{index}") for index in range(3)]
    discoveries = []
    for saved in projects:
        discoveries.append(
            app.discover_connection(
                saved["id"],
                {"provider": "braintrust", "credentials": {"api_key": "pending-secret"}},
            )
        )
    assert discoveries[0]["discovery_id"] not in app.connection_discoveries
    assert len(app.connection_discoveries) == len(app.credentials._values) == 2
    app.close()
    assert not app.connection_discoveries
    assert not app.credentials._values


def test_connection_reports_session_storage_when_one_key_is_temporary(app, monkeypatch):
    monkeypatch.setattr(app.credentials, "resolve", lambda reference: "resolved")
    value = app.connection_projection(
        {
            "id": "connection_" + "a" * 24,
            "credentials": {
                "public_key": "keyring:" + "a" * 32,
                "secret_key": "session:" + "b" * 32,
            },
        }
    )
    assert value["credential_mode"] == "session"
    assert "credentials" not in value
