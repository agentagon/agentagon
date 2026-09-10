import json
from pathlib import Path

import pytest

from agentagon.core.records import AuditError
from agentagon.storage.config import Config, credential


def test_config_location_and_read_have_no_side_effects(tmp_path, monkeypatch):
    path = Config().path
    assert path == tmp_path / "settings" / "config.json"
    assert Config().read() == {"version": 1, "user": {}, "projects": {}}
    assert not path.parent.exists()
    monkeypatch.delenv("AGENTAGON_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert Config().path == tmp_path / "xdg" / "agentagon" / "config.json"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert Config().path == Path.home() / ".config" / "agentagon" / "config.json"


def test_settings_precedence_and_checkout_isolation(workspace, tmp_path):
    config = Config()
    other = tmp_path / "another-checkout"
    defaults = config.effective(workspace.root)
    assert defaults["intelligence"]["api_key_env"] == "AGENTAGON_API_KEY"
    assert defaults["traces"]["state"] == "unset"
    config.update("user", values={"traces.source": "braintrust", "traces.project": "default"})
    config.update("project", workspace.root, {"traces.project": "checkout"})
    config.update("project", other, {"traces.project": "other", "traces.state": "disabled"})
    settings = config.effective(workspace.root, {"traces.project": "invocation"})
    assert settings["traces"]["project"] == "invocation"
    assert config.effective(workspace.root)["traces"]["project"] == "checkout"
    assert config.effective(other)["traces"]["project"] == "other"
    assert config.effective(workspace.root)["traces"]["state"] == "unset"
    config.update("project", workspace.root, unset=("traces.project",))
    assert config.effective(workspace.root)["traces"]["project"] == "default"
    assert config.effective()["traces"]["project"] == "default"
    assert config.read()["projects"][str(workspace.root)] == {}
    assert config.path.stat().st_mode & 0o777 == 0o600


def test_environment_credentials_are_resolved_without_being_saved(workspace, monkeypatch):
    config = Config()
    secret = "very-private-provider-value"
    monkeypatch.setenv("CUSTOM_PROVIDER_KEY", secret)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public-project-identifier")
    summary = config.update(
        "project",
        workspace.root,
        {
            "traces.state": "enabled",
            "traces.source": "langfuse",
            "traces.project": "application",
            "traces.api_key_env": "CUSTOM_PROVIDER_KEY",
            "traces.endpoint": "https://cloud.langfuse.com/",
        },
    )
    assert summary["traces"]["key_configured"]
    assert summary["traces"]["public_key_configured"]
    assert summary["traces"]["provider_configured"]
    assert not summary["traces"]["onboarding_pending"]
    assert summary["settings"]["traces"]["public_key_env"] == "LANGFUSE_PUBLIC_KEY"
    assert credential(config.effective(workspace.root), "traces") == secret
    assert secret not in json.dumps(summary)
    assert secret not in config.path.read_text()
    assert "public-project-identifier" not in config.path.read_text()


def test_onboarding_is_user_wide_for_intelligence_and_per_checkout_for_traces(
    workspace, tmp_path, monkeypatch
):
    config = Config()
    other = tmp_path / "other"
    assert config.summary(workspace.root)["intelligence"]["onboarding_pending"]
    assert config.summary(workspace.root)["traces"]["onboarding_pending"]
    config.update("user", values={"intelligence.access_presented": True})
    config.update("project", workspace.root, {"traces.state": "disabled"})
    assert not config.summary(other)["intelligence"]["onboarding_pending"]
    assert not config.summary(workspace.root)["traces"]["onboarding_pending"]
    assert config.summary(other)["traces"]["onboarding_pending"]
    config.update("project", workspace.root, {"traces.state": "unset"})
    assert config.summary(workspace.root)["traces"]["onboarding_pending"]
    config.update("user", values={"intelligence.access_presented": False})
    config.update("user", values={"intelligence.endpoint": "https://guidance.example"})
    monkeypatch.setenv("AGENTAGON_API_KEY", "private-key")
    assert not config.summary(other)["intelligence"]["onboarding_pending"]
    assert config.summary(other)["intelligence"]["configured"]


@pytest.mark.parametrize(
    ("scope", "values"),
    [
        ("user", {"intelligence.api_key": "raw-secret"}),
        ("user", {"intelligence.api_key_env": "raw-secret"}),
        ("user", {"intelligence.access_presented": "true"}),
        ("user", {"intelligence.access_presented": 1}),
        ("user", {"traces.state": "enabled"}),
        ("project", {"intelligence.access_presented": True}),
        ("project", {"traces.state": "yes"}),
        ("project", {"traces.source": "unknown-provider"}),
        ("project", {"traces.project": " "}),
        ("project", {"traces.public_key_env": "$PUBLIC_KEY"}),
        ("project", {"traces.endpoint": "https://user:password@provider.example"}),
        ("project", {"traces.endpoint": "https://provider.example?key=secret"}),
        ("project", {"traces.endpoint": "https://provider.example\n.invalid"}),
        ("user", {"intelligence.endpoint": "http://provider.example"}),
        ("user", {"intelligence.endpoint": "https://provider.example/path"}),
    ],
)
def test_invalid_settings_fail_without_writing(workspace, scope, values):
    config = Config()
    with pytest.raises(AuditError):
        config.update(scope, workspace.root, values)
    assert not config.path.exists()


def test_invalid_scope_unset_and_overrides_fail(workspace):
    config = Config()
    with pytest.raises(AuditError, match="scope"):
        config.update("checkout", workspace.root)
    with pytest.raises(AuditError, match="checkout root"):
        config.update("project", values={"traces.project": "demo"})
    with pytest.raises(AuditError, match="set and unset"):
        config.update("user", values={"traces.project": "demo"}, unset=("traces.project",))
    with pytest.raises(AuditError, match="unsupported setting"):
        config.update("user", unset=("traces.api_key",))
    with pytest.raises(AuditError, match="environment variable"):
        config.effective(workspace.root, {"intelligence.api_key_env": "sk-secret"})


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"version": 0, "user": {}, "projects": {}},
        {"version": True, "user": {}, "projects": {}},
        {"version": 1, "user": {}, "projects": {}, "api_key": "secret"},
        {"version": 1, "user": {"intelligence": {"api_key": "secret"}}, "projects": {}},
        {"version": 1, "user": {}, "projects": {"relative/path": {}}},
    ],
)
def test_invalid_config_is_not_rewritten(data):
    config = Config()
    config.path.parent.mkdir()
    original = json.dumps(data)
    config.path.write_text(original)
    with pytest.raises(AuditError):
        config.update("user", values={"intelligence.access_presented": True})
    assert config.path.read_text() == original


def test_legacy_workspace_is_rejected_without_migration(workspace):
    metadata = workspace.state / "workspace.json"
    assert metadata.exists()
    legacy = workspace.state / "config.json"
    legacy.write_text('{"contract_version":"1","intelligence_access_presented_at":"old"}')
    for action in (workspace.initialize, workspace.require_initialized):
        with pytest.raises(AuditError, match="unsupported workspace"):
            action()
    assert "intelligence_access_presented_at" in legacy.read_text()
    assert "intelligence" not in metadata.read_text()


def test_config_symlink_is_not_followed(tmp_path):
    config = Config()
    config.path.parent.mkdir()
    target = tmp_path / "target.json"
    target.write_text("private original")
    config.path.symlink_to(target)
    with pytest.raises(AuditError, match="symlink"):
        config.update("user", values={"intelligence.access_presented": True})
    assert target.read_text() == "private original"
