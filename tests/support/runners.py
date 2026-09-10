"""Shared runners test support."""

import json
import os
import subprocess
import sys
from pathlib import Path


def command(identifier, role, code):
    return {"id": identifier, "role": role, "argv": [sys.executable, "-c", code], "cwd": "."}


def request(commands=None, timeout=5):
    return {
        "attempt_id": "attempt-1",
        "source_digest": "source-hash",
        "evaluation_digest": "evaluation-hash",
        "seed": 17,
        "timeout_seconds": timeout,
        "commands": commands
        or [
            command(
                "bench",
                "benchmark",
                "import json,os; from pathlib import Path; Path(os.environ['AGENTAGON_RESULT_PATH']).write_text(json.dumps({'metrics':{'score':int(os.environ['AGENTAGON_SEED'])}})); print('observed')",
            )
        ],
    }


class FakeRemote:
    """Real standalone worker execution behind in-memory SSH/E2B transport boundaries."""

    def __init__(self, settings, descriptor):
        self.settings, self.descriptor = settings, descriptor
        self.child = None

    def create(self):
        self.descriptor["sandbox_id"] = "fake-sandbox"

    def put(self, path, data):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def command(self, action, env=None):
        job = Path(self.descriptor["remote_job"])
        if action == "launch":
            self.child = subprocess.Popen(
                [
                    sys.executable,
                    str(job / "worker.py"),
                    "run",
                    str(job),
                    self.descriptor["owner_token"],
                ],
                env={**os.environ, **(env or {})},
            )
            return json.dumps({"pid": self.child.pid}).encode()
        value = subprocess.run(
            [
                sys.executable,
                str(job / "worker.py"),
                action,
                str(job),
                self.descriptor["owner_token"],
            ],
            capture_output=True,
            check=True,
        )
        return value.stdout

    def cleanup(self):
        import shutil

        if self.child:
            self.child.wait(timeout=5)
        shutil.rmtree(self.descriptor["remote_job"])
