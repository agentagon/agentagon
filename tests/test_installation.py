"""Native-manager contracts and ownership boundaries for plugin installation."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentagon import installation
from agentagon.core.records import AuditError


@pytest.fixture
def managers(monkeypatch):
    calls = []
    markets = {"codex": [], "claude": []}
    installed = {"codex": [], "claude": []}

    def run(executable, arguments, environment):
        host = Path(executable).name
        calls.append((host, arguments))
        assert Path(environment["CODEX_HOME"]).is_dir()
        assert Path(environment["CLAUDE_CONFIG_DIR"]).is_dir()
        if arguments[:3] == ["plugin", "marketplace", "list"]:
            return json.dumps({"marketplaces": markets[host]} if host == "codex" else markets[host])
        if arguments[:3] == ["plugin", "marketplace", "add"]:
            root = arguments[3]
            markets[host] = [
                {
                    "name": installation.MARKETPLACE,
                    "marketplaceSource": {
                        "sourceType": "local",
                        "source": root,
                    },
                }
                if host == "codex"
                else {"name": installation.MARKETPLACE, "source": "directory", "path": root}
            ]
            return "{}"
        if arguments[:3] == ["plugin", "marketplace", "update"]:
            return "{}"
        if arguments[:2] == ["plugin", "list"]:
            return json.dumps(
                {"installed": installed[host]} if host == "codex" else installed[host]
            )
        if arguments[1] in {"add", "install", "update"}:
            marketplace = markets[host][0]
            root = Path(
                marketplace["marketplaceSource"]["source"]
                if host == "codex"
                else marketplace["path"]
            )
            manifest = json.loads((root / "plugins/ag/.codex-plugin/plugin.json").read_text())
            installed[host] = [
                {
                    "pluginId" if host == "codex" else "id": installation.PLUGIN_ID,
                    "version": manifest["version"],
                    "enabled": True,
                    "scope": "user",
                }
            ]
            return "{}"
        if arguments[1] == "enable":
            installed[host][0]["enabled"] = True
            return "{}"
        raise AssertionError(arguments)

    monkeypatch.setattr(installation, "_run", run)
    monkeypatch.setattr(installation.shutil, "which", lambda name: f"/opt/{name}")
    return calls, markets, installed


def test_installs_and_updates_native_plugins_without_touching_unrelated_files(tmp_path, managers):
    unrelated = tmp_path / ".codex/config.toml"
    unrelated.parent.mkdir()
    unrelated.write_text('model = "user-selected-model"\n')
    result = installation.install_plugins(home=tmp_path)
    assert result["deferred"] == []
    assert result["skills"] == [
        "ag:audit",
        "ag:review",
        "ag:setup",
        "ag:dashboard",
        "ag:fix",
        "ag:ship",
        "ag:eval",
    ]
    assert [item["host"] for item in result["hosts"]] == ["codex", "claude-code"]
    plugin = Path(result["plugin_root"])
    assert all((plugin / "skills" / skill / "SKILL.md").is_file() for skill in installation.SKILLS)
    assert (plugin / "skills/audit/references/braintrust.md").is_file()
    assert (plugin / "skills/review/SKILL.md").read_bytes() == installation.resource_path(
        "skills/review/SKILL.md"
    ).read_bytes()
    assert (plugin / "skills/ship/SKILL.md").read_bytes() == installation.resource_path(
        "skills/ship/SKILL.md"
    ).read_bytes()
    (plugin / "personal-note.txt").write_text("Keep this")
    again = installation.install_plugins(home=tmp_path)
    assert result["hosts"] == again["hosts"]
    assert (plugin / "personal-note.txt").read_text() == "Keep this"
    assert unrelated.read_text() == 'model = "user-selected-model"\n'
    calls, _, _ = managers
    assert ("claude", ["plugin", "update", installation.PLUGIN_ID, "--scope", "user"]) in calls
    assert ("claude", ["plugin", "enable", installation.PLUGIN_ID, "--scope", "user"]) not in calls


def test_claude_enables_a_disabled_plugin_after_update(tmp_path, managers, monkeypatch):
    calls, _, installed = managers
    installation.install_plugins(["claude-code"], home=tmp_path)
    original_run = installation._run

    def keep_disabled_after_update(executable, arguments, environment):
        result = original_run(executable, arguments, environment)
        if arguments[:2] == ["plugin", "update"]:
            installed["claude"][0]["enabled"] = False
        return result

    monkeypatch.setattr(installation, "_run", keep_disabled_after_update)
    result = installation.install_plugins(["claude-code"], home=tmp_path)
    assert result["hosts"][0]["enabled"] is True
    assert ("claude", ["plugin", "enable", installation.PLUGIN_ID, "--scope", "user"]) in calls


def test_host_selection_and_unavailable_cli(tmp_path, managers, monkeypatch):
    result = installation.install_plugins(["codex", "codex"], home=tmp_path)
    assert [entry["host"] for entry in result["hosts"]] == ["codex"]
    assert all(host == "codex" for host, _ in managers[0])
    monkeypatch.setattr(installation.shutil, "which", lambda name: None)
    with pytest.raises(AuditError, match="no supported host CLI"):
        installation.install_plugins(home=tmp_path)
    with pytest.raises(AuditError, match="claude executable not found"):
        installation.install_plugins(["claude-code"], home=tmp_path)


def test_conflicting_native_marketplace_is_preserved_before_copy(tmp_path, managers):
    calls, markets, _ = managers
    markets["codex"] = [
        {
            "name": installation.MARKETPLACE,
            "marketplaceSource": {"sourceType": "local", "source": "/somewhere-else"},
        }
    ]
    with pytest.raises(AuditError, match="unrelated.*marketplace"):
        installation.install_plugins(["codex"], home=tmp_path)
    assert not (tmp_path / ".local/share/agentagon").exists()
    assert calls == [("codex", ["plugin", "marketplace", "list", "--json"])]


def test_unmanaged_destination_and_escaping_symlink_are_preserved(tmp_path):
    root = tmp_path / "marketplace"
    root.mkdir()
    existing = root / "keep.txt"
    existing.write_text("Keep")
    with pytest.raises(AuditError, match="unmanaged"):
        installation._sync_bundle(root)
    existing.unlink()
    installation._sync_bundle(root)
    target = tmp_path / "outside.md"
    target.write_text("Do not overwrite")
    skill = root / "plugins/ag/skills/audit/SKILL.md"
    skill.unlink()
    skill.symlink_to(target)
    with pytest.raises(AuditError, match="escapes"):
        installation._sync_bundle(root)
    assert target.read_text() == "Do not overwrite"


def test_native_cache_version_tracks_source_changes(tmp_path, monkeypatch):
    source = tmp_path / "resources"
    for folder in (".codex-plugin", ".claude-plugin"):
        destination = source / folder
        destination.mkdir(parents=True)
        (destination / "plugin.json").write_text(json.dumps({"name": "ag", "version": "0.1.0"}))
    for name in installation.SKILLS:
        destination = source / "skills" / name
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text(f"{name} instructions")
    monkeypatch.setattr(installation, "resource_path", lambda name: source / name)
    root = tmp_path / "managed"
    initial = installation._sync_bundle(root)
    generated = (
        "eval/helpers/__pycache__/agentagon_events.cpython-313.pyc",
        "eval/helpers/__pycache__/metadata.json",
        "eval/helpers/agentagon_events.pyc",
        "eval/helpers/agentagon_events.pyo",
    )
    for relative in generated:
        path = source / "skills" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"machine-specific generated content")
    assert installation._sync_bundle(root) == initial
    assert all(not (root / "plugins/ag/skills" / relative).exists() for relative in generated)

    (source / "skills/audit/SKILL.md").write_text("New audit instructions")
    updated = installation._sync_bundle(root)
    assert updated != initial
    assert (root / "plugins/ag/skills/audit/SKILL.md").read_text() == "New audit instructions"


def test_interrupted_first_copy_can_be_resumed(tmp_path, monkeypatch):
    replace = installation._replace_bytes

    def interrupted(path, content):
        if path.name == "SKILL.md":
            raise OSError("interrupted copy")
        replace(path, content)

    monkeypatch.setattr(installation, "_replace_bytes", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        installation._sync_bundle(tmp_path)
    monkeypatch.setattr(installation, "_replace_bytes", replace)
    installation._sync_bundle(tmp_path)
    assert all(
        (tmp_path / "plugins/ag/skills" / name / "SKILL.md").is_file()
        for name in installation.SKILLS
    )


def test_native_error_is_not_reported_as_success_or_echoed(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "private diagnostic"),
    )
    with pytest.raises(AuditError, match="exit 1") as error:
        installation._run("codex", ["plugin", "list", "--json"], {})
    assert "private diagnostic" not in str(error.value)


def test_bootstrap_retries_interrupted_venv_and_preserves_unmanaged_prefix(tmp_path):
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1] == '-c':\n"
        "    raise SystemExit(0)\n"
        "assert sys.argv[1:3] == ['-m', 'venv']\n"
        "prefix = Path(sys.argv[3])\n"
        "(prefix / 'bin').mkdir(parents=True, exist_ok=True)\n"
        "interrupted = prefix / 'first-attempt'\n"
        "if not interrupted.exists():\n"
        "    interrupted.touch()\n"
        "    raise SystemExit(12)\n"
        "for name in ('python', 'agentagon'):\n"
        "    executable = prefix / 'bin' / name\n"
        "    executable.write_text('#!/bin/sh\\nexit 0\\n')\n"
        "    executable.chmod(0o755)\n"
    )
    fake_python.chmod(0o755)
    prefix = tmp_path / "runtime"
    bin_dir = tmp_path / "bin"
    environment = {
        **os.environ,
        "AGENTAGON_INSTALL_PYTHON": str(fake_python),
        "AGENTAGON_INSTALL_PREFIX": str(prefix),
        "AGENTAGON_INSTALL_BIN_DIR": str(bin_dir),
    }
    script = Path(__file__).resolve().parents[1] / "scripts/install.sh"
    command = ["sh", str(script), "--host", "codex"]
    first = subprocess.run(command, env=environment, capture_output=True, text=True)
    assert first.returncode == 12
    assert (prefix / "first-attempt").exists()
    assert (prefix / ".agentagon-runtime").is_file()
    assert not (bin_dir / "agentagon").exists()
    retry = subprocess.run(command, env=environment, capture_output=True, text=True)
    assert retry.returncode == 0, retry.stderr
    assert (bin_dir / "agentagon").resolve() == prefix / "bin/agentagon"

    unmanaged = tmp_path / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / "keep.txt").write_text("Preserve me")
    environment["AGENTAGON_INSTALL_PREFIX"] = str(unmanaged)
    rejected = subprocess.run(command, env=environment, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "Preserved unmanaged install destination" in rejected.stderr
    assert list(unmanaged.iterdir()) == [unmanaged / "keep.txt"]
    assert (unmanaged / "keep.txt").read_text() == "Preserve me"


@pytest.mark.skipif(
    os.environ.get("AGENTAGON_TEST_NATIVE_CODEX") != "1" or not shutil.which("codex"),
    reason="opt in to native Codex installation in an isolated configuration",
)
def test_real_codex_registers_and_enables_packaged_skills(tmp_path):
    result = installation.install_plugins(["codex"], home=tmp_path)
    assert result["hosts"][0]["enabled"] is True
    cache = tmp_path / ".codex/plugins/cache/agentagon-local/ag"
    skills = next(cache.iterdir()) / "skills"
    assert sorted(path.name for path in skills.iterdir()) == sorted(installation.SKILLS)
    assert (skills.parent / "hooks/hooks.json").read_bytes() == installation.resource_path(
        "hooks/hooks.json"
    ).read_bytes()
    for relative in (
        "review/SKILL.md",
        "fix/SKILL.md",
        "fix/references/contract.md",
        "ship/SKILL.md",
        "eval/helpers/agentagon_events.py",
        "eval/helpers/agentagon_events.cjs",
        "fix/references/roles.md",
    ):
        assert (skills / relative).read_bytes() == installation.resource_path(
            f"skills/{relative}"
        ).read_bytes()
    assert installation.install_plugins(["codex"], home=tmp_path)["hosts"] == result["hosts"]


@pytest.mark.skipif(
    os.environ.get("AGENTAGON_TEST_NATIVE_CLAUDE") != "1" or not shutil.which("claude"),
    reason="opt in to native Claude Code installation in an isolated configuration",
)
def test_real_claude_registers_and_enables_packaged_skills(tmp_path):
    result = installation.install_plugins(["claude-code"], home=tmp_path)
    assert result["hosts"][0]["enabled"] is True
    cache = tmp_path / ".claude/plugins/cache/agentagon-local/ag"
    skills = next(cache.iterdir()) / "skills"
    assert sorted(path.name for path in skills.iterdir()) == sorted(installation.SKILLS)
    assert (skills.parent / "hooks/hooks.json").read_bytes() == installation.resource_path(
        "hooks/hooks.json"
    ).read_bytes()
    for relative in (
        "review/SKILL.md",
        "fix/SKILL.md",
        "fix/references/contract.md",
        "ship/SKILL.md",
        "eval/helpers/agentagon_events.py",
        "eval/helpers/agentagon_events.cjs",
        "fix/references/roles.md",
    ):
        assert (skills / relative).read_bytes() == installation.resource_path(
            f"skills/{relative}"
        ).read_bytes()
    assert installation.install_plugins(["claude-code"], home=tmp_path)["hosts"] == result["hosts"]
