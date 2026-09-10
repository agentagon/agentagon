"""Check wheel resources from a fresh environment outside the source checkout."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from importlib.resources import files
from pathlib import Path

import agentagon
from agentagon.core.records import catalog, resource_path
from agentagon.dashboard import ASSETS
from agentagon.installation import SKILLS
from agentagon.usage import track

assert Path(agentagon.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), (
    "Run this check with the wheel installed in a fresh virtual environment."
)
repository = Path(__file__).resolve().parents[1]
project = tomllib.loads((repository / "pyproject.toml").read_text())
for destination, patterns in project["tool"]["setuptools"]["data-files"].items():
    for pattern in patterns:
        for source in repository.glob(pattern):
            installed = Path(sys.prefix) / destination / source.name
            assert installed.read_bytes() == source.read_bytes(), str(installed)
for skill in SKILLS:
    skill_text = resource_path(f"skills/{skill}/SKILL.md").read_text()
    assert f'agentagon telemetry skill_invoked --data \'{{"skill":"{skill}"}}\'' in skill_text
for relative in (
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    "hooks/hooks.json",
):
    json.loads(resource_path(relative).read_text())
for path in resource_path("contracts/v1").glob("*.json"):
    json.loads(path.read_text())
assert catalog()
for suffix in ("py", "cjs"):
    assert resource_path(f"skills/eval/helpers/agentagon_events.{suffix}").read_bytes()
for name, _ in ASSETS.values():
    assert files("agentagon").joinpath("dashboard_assets", name).read_bytes()
assert callable(track)
with tempfile.TemporaryDirectory(prefix="agentagon-telemetry-check-") as directory:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentagon",
            "telemetry",
            "skill_invoked",
            "--data",
            '{"skill":"audit"}',
        ],
        cwd=directory,
        env={
            **os.environ,
            "AGENTAGON_CONFIG": str(Path(directory) / "config.json"),
            "AGENTAGON_TELEMETRY_DISABLED": "1",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"status": "disabled"}
    assert not list(Path(directory).iterdir())
print("Installed package, plugin resources, contracts, helpers and dashboard assets are readable.")
