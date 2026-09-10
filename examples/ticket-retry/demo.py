"""Prepare a fresh, seeded application and an ordinary Agentagon evaluation draft."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from agentagon.experiments import preparation
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace

HERE = Path(__file__).resolve().parent
GOAL = "Prevent duplicate tickets after a response timeout while preserving ordinary and distinct requests"
EVALUATOR_FILES = [
    "benchmark.py",
    "model.py",
    "verify.py",
    "ticket_environment.py",
    "test_ticket_retry.py",
    "agentagon_events.py",
]


def initialize(destination, repetitions=2):
    if destination.exists():
        raise ValueError(
            "Choose a new directory; the demo never overwrites an existing application"
        )
    destination.mkdir(parents=True)
    shutil.copyfile(HERE / "application.py", destination / "application.py")
    subprocess.run(["git", "init", "--initial-branch=main", "-q", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "add", "application.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "user.name=Agentagon Demo",
            "-c",
            "user.email=demo@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "test: establish seeded ticket retry application",
        ],
        check=True,
    )
    work = Workspace(destination)
    work.initialize()
    profile = {
        "runner": {"kind": "local"},
        "limits": {
            "max_candidates": 3,
            "max_trials": 4 * repetitions,
            "max_elapsed_seconds": 1800,
            "trial_timeout_seconds": 150,
            "parallel_candidates": 1,
            "parallel_trials": 1,
            "stagnation_rounds": 3,
        },
    }
    Config().update_profile("project", "ticket-demo", profile, work.root)
    budget = {
        "max_trials": 4 * repetitions,
        "max_elapsed_seconds": 1800,
        "trial_timeout_seconds": 150,
    }
    started = preparation.start(work, "ticket-demo", budget, goal=GOAL, author="ticket-demo-author")
    prepared = work.root / started["worktree"]
    for name in EVALUATOR_FILES:
        source = (
            HERE.parents[1] / "skills/eval/helpers" / name
            if name == "agentagon_events.py"
            else HERE / name
        )
        shutil.copyfile(source, prepared / name)
    variants = work.state / "demo"
    variants.mkdir()
    # Ship preparation requires a destination and a captured base. Keep both local
    # for this fixture; no GitHub repository or external publication is involved.
    local_remote = variants / "review-origin.git"
    subprocess.run(
        ["git", "clone", "--bare", "--no-hardlinks", "-q", str(work.root), str(local_remote)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(work.root), "remote", "add", "origin", str(local_remote)], check=True
    )
    subprocess.run(["git", "-C", str(work.root), "fetch", "-q", "origin", "main"], check=True)
    baseline = (HERE / "application.py").read_text()
    fixed = baseline.replace("idempotency_key=None", 'idempotency_key=decision["request_id"]')
    wrong = fixed.replace(
        'idempotency_key=decision["request_id"]', 'idempotency_key="all-requests"'
    )
    for name, content in {
        "baseline.py": baseline,
        "fixed.py": fixed,
        "wrong-key.py": wrong,
        "no-write.py": 'def execute(decision, tickets):\n    return {"id": "claimed-success"}\n',
    }.items():
        (variants / name).write_text(content)
    spec = {
        "goal": GOAL,
        "editable_paths": ["application.py"],
        "evaluation_paths": EVALUATOR_FILES,
        "benchmark": {"argv": [sys.executable, "benchmark.py"]},
        "metrics": {
            "completion": {"direction": "max", "unit": "fraction"},
            "duplicates": {"direction": "min", "unit": "tickets"},
        },
        "task_metrics": {
            name: {"direction": "max", "unit": "pass"} for name in ("ordinary", "timeout")
        },
        "checks": [
            {"id": "ordinary", "argv": [sys.executable, "verify.py", "ordinary"]},
            {
                "id": "timeout",
                "argv": [sys.executable, "verify.py", "timeout"],
                "baseline_expected": "fail",
            },
            {
                "id": "distinct-requests",
                "argv": [
                    sys.executable,
                    "-m",
                    "unittest",
                    "test_ticket_retry.TicketRegression.test_distinct_requests_remain_distinct",
                ],
            },
        ],
        "repetitions": repetitions,
        "seeds": list(range(repetitions)),
    }
    plan = {
        "spec": spec,
        "provenance": "Seeded example with actual Codex model decisions. The owner-defined contract requires exactly one ticket with the requested identity/title and a returned confirmation; distinct requests must remain distinct. Model aliases are recorded, monetary cost is unknown.",
        "coverage": {
            "status": "limited",
            "rationale": "Two synthetic live-model cases and one deterministic distinct-request control; no customer incident, real ticket API, statistical confidence or untouched holdout is claimed.",
            "holdout_paths": [],
        },
        "negative_cases": [
            {
                "id": "false-completion",
                "description": "Claim success without creating any ticket",
                "mutations": [{"path": "application.py", "source": ".agentagon/demo/no-write.py"}],
                "expected_checks": ["ordinary", "timeout", "distinct-requests"],
            }
        ],
        "deliver_paths": ["test_ticket_retry.py", "ticket_environment.py"],
    }
    plan_path = variants / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    return {
        "workspace": str(work.root),
        "evaluation_id": started["evaluation_id"],
        "plan": str(plan_path),
        "profile": "ticket-demo",
        "delivery_base": "main",
        "delivery_destination": "local Git fixture only",
        "seeded_failure": True,
        "state": started["state"],
        "next_action": "Run eval check, then obtain an independent review before freezing. No model calls have run yet.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--repetitions", type=int, choices=range(1, 4), default=2)
    args = parser.parse_args()
    print(
        json.dumps(initialize(args.destination.expanduser().resolve(), args.repetitions), indent=2)
    )
