"""Actual model decisions plus controlled, freshly reset tool state for each case."""

import json
import os
from pathlib import Path

from agentagon_events import emit
from application import execute
from model import decide
from ticket_environment import TicketService
from verify import CASES, correct


def main():
    tasks = [
        {
            "request_id": case["request_id"],
            "request": f"Create a support ticket titled '{case['title']}'.",
        }
        for case in CASES.values()
    ]
    actions, model = decide(tasks)
    observations, outcomes = {}, {}
    for (case_id, expected), action in zip(CASES.items(), actions, strict=True):
        emit("task_start", case_id, {"seeded_failure": True, "model": model})
        emit("input", case_id, {"request": expected, "model_decision": action})
        service = TicketService(timeout_after_write=case_id == "timeout")
        result, error = None, None
        try:
            result = execute(action, service)
        except (TimeoutError, ValueError, KeyError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        observation = {"tickets": service.tickets, "returned": result, "error": error}
        passed = correct(observation, expected)
        observations[case_id], outcomes[case_id] = observation, int(passed)
        emit("output", case_id, observation)
        emit("task_end", case_id, {"observed_correct": passed})
    output = {
        "metrics": {
            "completion": sum(outcomes.values()) / len(outcomes),
            "duplicates": sum(max(0, len(o["tickets"]) - 1) for o in observations.values()),
        },
        "tasks": outcomes,
        "observations": observations,
        "model": model,
    }
    Path(os.environ["AGENTAGON_RESULT_PATH"]).write_text(json.dumps(output))
    # Each command receives its own RESULT_PATH. Share only this trial's observations
    # through its fresh execution directory for the subsequent verifier commands.
    Path(".ticket-observations.json").write_text(json.dumps(observations))
    print(json.dumps(output))


if __name__ == "__main__":
    main()
