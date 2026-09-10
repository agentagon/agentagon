"""Shared controls test support."""

import uuid

from agentagon.experiments import controls
from agentagon.experiments.store import load_run


def request(workspace, run_id, action, **fields):
    return {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "expected_revision": load_run(workspace, run_id).get("revision", 0),
        "action": action,
        **fields,
    }


def submit(workspace, run_id, action, **fields):
    return controls.submit(workspace, run_id, request(workspace, run_id, action, **fields))
