"""Prepare a real audit packet and partial report using only a synthetic local app."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(tempfile.mkdtemp(prefix="agentagon-example-"))
    app = root / "app"
    app.mkdir()
    (app / "app.py").write_text(
        'def answer(question):\n    return {"answer": question.strip()}\n', encoding="utf-8"
    )
    environment = {**os.environ, "AGENTAGON_CONFIG": str(root / "config.json")}

    def cli(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "agentagon", "--workspace", str(app), *arguments],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    cli("init")
    audit = cli(
        "audit",
        "start",
        "--mode",
        "code",
        "--host",
        "example",
        "--model",
        "none",
        "--goal",
        "Review input handling in the synthetic application",
    )
    audit_id = audit["audit_id"]
    packet = cli("audit", "prepare", audit_id, "--stage", "evidence")
    report = cli("audit", "report", audit_id)
    status = cli("status", "--audit", audit_id)
    assert status["pending_action"] == "evidence"
    assert Path(packet["packet"]).is_file()
    assert Path(report["report"]).is_file()
    print(
        json.dumps(
            {
                "workspace": str(app),
                "audit_id": audit_id,
                "pending_action": status["pending_action"],
                "packet": packet["packet"],
                "response_template": packet["response_template"],
                "report": report["report"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
