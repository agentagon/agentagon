"""Shared experiments test support."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentagon.experiments import engine
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace

BENCHMARK = """import json, os
from pathlib import Path
application = json.loads(Path('app.json').read_text())
with Path(os.environ['EXECUTION_LOG']).open('a') as stream:
    stream.write(json.dumps({'seed': int(os.environ['AGENTAGON_SEED']), 'variant': application['variant']}) + '\\n')
Path(os.environ['AGENTAGON_RESULT_PATH']).write_text(json.dumps({
    'metrics': {'latency': application['latency'], 'quality': application['quality']},
    'tasks': [{'seed': int(os.environ['AGENTAGON_SEED'])}]
}))
"""


def git(root, *arguments, binary=False):
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=True)
    return result.stdout if binary else result.stdout.decode().strip()


@pytest.fixture
def application(tmp_path, monkeypatch):
    root = tmp_path / "application"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "core.filemode", "true")
    (root / "app.json").write_text(
        json.dumps({"latency": 100, "quality": 0.8, "variant": "baseline"})
    )
    (root / "benchmark.py").write_text(BENCHMARK)
    (root / "checks.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "assert json.loads(Path('app.json').read_text())['quality'] >= 0.4\n"
    )
    (root / "model.bin").write_bytes(b"\x00baseline\xff")
    (root / "tool.sh").write_text("#!/bin/sh\nprintf 'baseline\\n'\n")
    (root / "tool.sh").chmod(0o644)
    workspace = Workspace(root)
    workspace.initialize()
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Agentagon Tests",
        "-c",
        "user.email=tests@localhost",
        "commit",
        "-qm",
        "Baseline fixture",
    )
    monkeypatch.setenv("TEST_EXECUTION_LOG", str(tmp_path / "executions.jsonl"))
    profile = {
        "runner": {"kind": "local"},
        "env": {"EXECUTION_LOG": "TEST_EXECUTION_LOG"},
        "limits": {
            "max_candidates": 8,
            "max_trials": 60,
            "max_elapsed_seconds": 600,
            "parallel_candidates": 1,
            "parallel_trials": 1,
            "trial_timeout_seconds": 10,
            "stagnation_rounds": 5,
        },
    }
    Config().update_profile("project", "local", profile, root)
    return workspace


@pytest.fixture
def specification():
    return {
        "goal": "Improve latency and quality without regressing either hard floor",
        "editable_paths": ["app.json", "model.bin", "tool.sh"],
        "evaluation_paths": ["benchmark.py", "checks.py"],
        "benchmark": {"argv": [sys.executable, "benchmark.py"]},
        "metrics": {
            "latency": {"direction": "min", "unit": "ms"},
            "quality": {"direction": "max", "unit": "fraction"},
        },
        "constraints": [
            {"metric": "quality", "op": "gte", "bound": 0.6, "reference": "absolute"},
            {"metric": "latency", "op": "lte", "bound": 1.05, "reference": "baseline_ratio"},
        ],
        "checks": [{"id": "quality-control", "argv": [sys.executable, "checks.py"]}],
    }


def executions():
    path = Path(os.environ["TEST_EXECUTION_LOG"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def passing_review(result, reviewer="independent-reviewer"):
    review = copy.deepcopy(result["review_template"])
    review.update(
        reviewer=reviewer,
        verdict="pass",
        rationale="Inspected the sealed patch, protected checks, and every retained trial.",
    )
    review["assessments"] = dict.fromkeys(review["assessments"], True)
    return review


def verify(workspace, run_id, candidate_id):
    measured = engine.run(workspace, run_id, candidate_id)
    assert measured["candidate"]["state"] == "awaiting_review", measured["candidate"]
    return engine.run(workspace, run_id, candidate_id, review=passing_review(measured))


def baseline(workspace, spec):
    started = engine.start(workspace, spec, "local")
    verified = verify(workspace, started["run_id"], started["candidate_id"])
    assert verified["candidate"]["state"] == "verified", verified["candidate"]
    return verified


def propose(workspace, run_id, *, parent_id=None, latency=80, quality=0.8, variant="candidate"):
    created = engine.new(
        workspace,
        run_id,
        parent_id=parent_id,
        hypothesis=f"Evaluate {variant} while retaining controls",
        author="candidate-author",
    )
    path = workspace.root / created["candidate"]["worktree"]
    (path / "app.json").write_text(
        json.dumps({"latency": latency, "quality": quality, "variant": variant})
    )
    return created, path
