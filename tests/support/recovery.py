"""Shared recovery test support."""

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from agentagon.experiments import engine
from support.experiments import git


def install_gate(workspace, condition):
    """Block a selected real benchmark after it records execution, before its metric result."""
    path = workspace.root / "benchmark.py"
    gate = f"""import time
if {condition}:
    gate_path = Path(os.environ['EXECUTION_LOG'] + '.gate')
    release_path = Path(os.environ['EXECUTION_LOG'] + '.release')
    gate_path.write_text(json.dumps({{'seed': int(os.environ['AGENTAGON_SEED']), 'variant': application['variant']}}))
    while not release_path.exists():
        time.sleep(.01)
"""
    original = path.read_text()
    path.write_text(
        original.replace(
            "Path(os.environ['AGENTAGON_RESULT_PATH'])",
            gate + "Path(os.environ['AGENTAGON_RESULT_PATH'])",
        )
    )
    git(workspace.root, "add", "benchmark.py")
    git(
        workspace.root,
        "-c",
        "user.name=Recovery Tests",
        "-c",
        "user.email=tests@localhost",
        "commit",
        "-qm",
        "Add controlled benchmark gate",
    )


def gate_path(suffix):
    return Path(os.environ["TEST_EXECUTION_LOG"] + suffix)


def wait_for_gate():
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if gate_path(".gate").exists():
            try:
                return json.loads(gate_path(".gate").read_text())
            except json.JSONDecodeError:
                pass
        time.sleep(0.01)
    raise AssertionError("benchmark did not reach its gate")


@contextmanager
def invocation(workspace, run_id, candidate_id):
    outcome = {}

    def drive():
        try:
            outcome["result"] = engine.run(workspace, run_id, candidate_id)
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=drive)
    thread.start()
    try:
        yield thread, outcome
    finally:
        gate_path(".release").touch()
        if thread.is_alive():
            engine.stop(workspace, run_id)
        thread.join(timeout=10)
        assert not thread.is_alive(), "engine driver did not release its owned execution"
