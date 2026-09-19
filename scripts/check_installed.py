"""Check wheel resources from a fresh environment outside the source checkout."""

import json
import subprocess
import sys
import tempfile
import tomllib
from importlib.resources import files
from pathlib import Path

import agentagon
from agentagon.capabilities.experiments.runtime import BudgetTracker, EvalServer, GepaEngine, Task
from agentagon.core.records import catalog, resource_path
from agentagon.dashboard.server import APP_ASSETS
from agentagon.mcp.server import create_mcp
from agentagon.usage import track
from agentagon.workflows.procedures import REFERENCES
from agentagon.workflows.registry import REGISTRY, handler, instruction_text

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
for path in resource_path("contracts/v1").glob("*.json"):
    json.loads(path.read_text())
assert catalog()
assert resource_path("workflows/evaluate/references/authoring.md").is_file()
for suffix in ("py", "cjs"):
    assert resource_path(f"workflows/evaluate/helpers/agentagon_events.{suffix}").read_bytes()
for name, _ in APP_ASSETS.values():
    assert files("agentagon").joinpath("dashboard/assets", name).read_bytes()
for references in REFERENCES.values():
    for reference in references:
        assert resource_path(reference).read_bytes()
assert callable(track)
with tempfile.TemporaryDirectory(prefix="agentagon-gepa-check-") as directory:
    server = EvalServer(
        Task("0", "increase score"),
        lambda candidate, example, **kwargs: (float(candidate), {}),
        BudgetTracker(4),
    )
    engine = GepaEngine(
        directory,
        engine={"parallel": False, "use_cloudpickle": False, "seed": 0},
        reflection={"reflection_lm": lambda prompt: "```\n1\n```"},
    )
    result = engine.run(server.task, server)
    assert result.best_candidate == "1" and result.best_score == 1.0
    assert server.budget.used <= 4

assert {"fix", "discover", "optimize", "assess", "observe"} <= set(REGISTRY)
for workflow in REGISTRY:
    assert instruction_text(workflow)
    assert callable(
        getattr(handler(workflow), "accept", None) or getattr(handler(workflow), "validate", None)
    )
assert create_mcp().name == "Agentagon"
result = subprocess.run(
    [sys.executable, "-m", "agentagon", "--help"], capture_output=True, text=True, check=True
)
assert "serve" in result.stdout and "mcp" in result.stdout
assert "install" not in result.stdout and "_internal" not in result.stdout
print(
    "Installed workflow resources, contracts, event helpers, React assets and MCP load successfully."
)
print("Installed GEPA completes a bounded offline search.")
