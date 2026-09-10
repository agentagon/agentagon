"""Shared evaluation test support."""

import copy
import json

from agentagon.experiments import preparation

BUDGET = {"max_trials": 12, "max_elapsed_seconds": 600, "trial_timeout_seconds": 10}


def draft(workspace, spec, **kwargs):
    started = preparation.start(
        workspace,
        "local",
        kwargs.pop("budget", BUDGET),
        goal=spec["goal"],
        author="benchmark-author",
        **kwargs,
    )
    negative = workspace.state / "wrong-behavior.json"
    negative.write_text(json.dumps({"latency": 100, "quality": 0.1, "variant": "incorrect"}))
    plan = {
        "spec": copy.deepcopy(spec),
        "provenance": "Synthetic application fixture; quality below 0.4 is known incorrect behavior.",
        "coverage": {
            "status": "limited",
            "rationale": "One synthetic fixture; no independent production ground truth is available.",
            "holdout_paths": [],
        },
        "negative_cases": [
            {
                "id": "low-quality",
                "description": "Deliberately return an unacceptable answer",
                "mutations": [
                    {"path": "app.json", "source": str(negative.relative_to(workspace.root))}
                ],
                "expected_checks": ["quality-control"],
            }
        ],
    }
    return started, plan


def review_for(checked):
    review = copy.deepcopy(checked["checks"][-1]["review_template"])
    review.update(
        reviewer="independent-benchmark-reviewer",
        verdict="pass",
        rationale="Inspected source, fixture provenance, limited coverage, baseline and deliberately incorrect case results.",
    )
    review["assessments"] = dict.fromkeys(review["assessments"], True)
    return review
