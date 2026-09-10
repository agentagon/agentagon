import copy
import json

import pytest

from agentagon.core.records import AuditError
from agentagon.storage.config import Config


@pytest.fixture
def execution_profile():
    return {
        "runner": {"kind": "local"},
        "env": {"MODEL_KEY": "CUSTOM_MODEL_KEY"},
        "setup": [],
        "limits": {
            "max_candidates": 4,
            "max_trials": 36,
            "max_elapsed_seconds": 600,
            "parallel_candidates": 1,
            "parallel_trials": 1,
            "trial_timeout_seconds": 30,
        },
    }


def test_profile_overrides_replace_whole_profile_and_keep_other_settings(
    workspace, tmp_path, execution_profile
):
    config = Config()
    config.update("user", values={"traces.source": "braintrust"})
    config.update_profile("user", "evaluation", execution_profile)
    config.update_profile("user", "other", execution_profile)
    project = copy.deepcopy(execution_profile)
    project["env"] = {}
    project["limits"]["max_candidates"] = 2
    config.update_profile("project", "evaluation", project, workspace.root)

    assert config.profile(workspace.root, "evaluation")["env"] == {}
    assert config.profile(workspace.root, "evaluation")["limits"]["max_candidates"] == 2
    assert config.profile(tmp_path / "other-checkout", "evaluation")["env"] == {
        "MODEL_KEY": "CUSTOM_MODEL_KEY"
    }
    assert config.profile(workspace.root, "other")["limits"]["max_candidates"] == 4
    assert config.effective(workspace.root)["traces"]["source"] == "braintrust"
    selected = config.profile(workspace.root, "evaluation")
    selected["limits"]["max_candidates"] = 99
    assert config.profile(workspace.root, "evaluation")["limits"]["max_candidates"] == 2


def test_profile_stores_environment_references_without_resolving_secrets(
    workspace, execution_profile, monkeypatch
):
    monkeypatch.setenv("CUSTOM_MODEL_KEY", "private-value-never-store")
    config = Config()
    result = config.update_profile("project", "local", execution_profile, workspace.root)
    assert "CUSTOM_MODEL_KEY" in config.path.read_text()
    assert "private-value-never-store" not in config.path.read_text()
    assert "private-value-never-store" not in json.dumps(result)
    assert config.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("name", ["", "../escape", "UpperCase", "has space", "a" * 65])
def test_invalid_profile_name_does_not_create_config(execution_profile, name):
    config = Config()
    with pytest.raises(AuditError, match="profile name"):
        config.update_profile("user", name, execution_profile)
    assert not config.path.exists()


def test_missing_required_limit_preserves_existing_profile(execution_profile):
    config = Config()
    config.update_profile("user", "local", execution_profile)
    before = config.path.read_bytes()
    del execution_profile["limits"]["max_trials"]
    with pytest.raises(AuditError):
        config.update_profile("user", "local", execution_profile)
    assert config.path.read_bytes() == before


def test_unknown_profile_and_invalid_scope_do_not_write(execution_profile):
    config = Config()
    with pytest.raises(AuditError, match="profile not found"):
        config.profile(None, "unknown")
    with pytest.raises(AuditError, match="checkout root"):
        config.update_profile("project", "local", execution_profile)
    assert not config.path.exists()
