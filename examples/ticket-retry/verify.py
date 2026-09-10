"""Assert stored tool state and returned identity, rather than a completion claim."""

import json
import sys
from pathlib import Path

CASES = {
    "ordinary": {"request_id": "normal-1", "title": "Printer offline"},
    "timeout": {"request_id": "timeout-1", "title": "Cannot sign in"},
}


def correct(observation, expected):
    tickets = observation["tickets"]
    return (
        observation["error"] is None
        and len(tickets) == 1
        and observation["returned"] == tickets[0]
        and tickets[0]["request_id"] == expected["request_id"]
        and tickets[0]["title"] == expected["title"]
    )


if __name__ == "__main__":
    case = sys.argv[1]
    try:
        outcome = json.loads(Path(".ticket-observations.json").read_text())[case]
        passed = correct(outcome, CASES[case])
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        print(f"Verifier input error: {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"case": case, "expected": CASES[case], "observed": outcome}))
    sys.exit(0 if passed else 1)
