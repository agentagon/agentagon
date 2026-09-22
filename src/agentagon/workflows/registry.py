"""Explicit built-in workflow catalog; no executable plugin discovery."""

from pathlib import Path

from agentagon.core.records import load_json

FOLDERS = {
    name: name
    for name in ("discover", "fix", "optimize", "design", "baseline", "audit", "assess", "observe")
}
FOLDERS["eval"] = "evaluate"
ROOT = Path(__file__).parent
REGISTRY = {name: load_json(ROOT / folder / "definition.json") for name, folder in FOLDERS.items()}


def instruction_text(workflow):
    return (ROOT / FOLDERS[workflow] / "instructions.md").read_text()


def handler(workflow):
    from importlib import import_module

    return import_module(f"agentagon.workflows.{FOLDERS[workflow]}.handler")
