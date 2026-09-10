"""Offline mechanics checks; these do not establish live-model demo results."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/ticket-retry"


def command(root, *args, result_name="result.json"):
    return subprocess.run(
        [sys.executable, *args],
        cwd=root,
        env={**os.environ, "AGENTAGON_RESULT_PATH": str(root / result_name)},
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def test_demo_state_assertions_catch_duplicates_preserve_success_and_reject_missing_inputs(
    tmp_path,
):
    for name in (
        "application.py",
        "ticket_environment.py",
        "benchmark.py",
        "verify.py",
        "test_ticket_retry.py",
    ):
        shutil.copyfile(EXAMPLE / name, tmp_path / name)
    shutil.copyfile(
        EXAMPLE.parents[1] / "skills/eval/helpers/agentagon_events.py",
        tmp_path / "agentagon_events.py",
    )
    (tmp_path / "model.py").write_text(
        'from verify import CASES\ndef decide(tasks):\n    return [{"tool":"create_ticket", **case} for case in CASES.values()], {"provider":"offline-test-fixture"}\n'
    )
    missing = command(tmp_path, "verify.py", "ordinary")
    assert missing.returncode == 2
    assert "Verifier input error" in missing.stderr
    baseline = (tmp_path / "application.py").read_text()
    for source, expected_completion, expected_timeout in [
        (baseline, 0.5, 1),
        (
            baseline.replace("idempotency_key=None", 'idempotency_key=decision["request_id"]'),
            1.0,
            0,
        ),
        (baseline, 0.5, 1),
    ]:
        (tmp_path / "application.py").write_text(source)
        result = command(tmp_path, "benchmark.py")
        assert result.returncode == 0, result.stderr
        assert (
            json.loads((tmp_path / "result.json").read_text())["metrics"]["completion"]
            == expected_completion
        )
        assert (
            command(
                tmp_path, "verify.py", "ordinary", result_name="different-command.json"
            ).returncode
            == 0
        )
        assert (
            command(tmp_path, "verify.py", "timeout", result_name="another-command.json").returncode
            == expected_timeout
        )
        tests = command(tmp_path, "-B", "-m", "unittest", "test_ticket_retry")
        assert tests.returncode == expected_timeout, tests.stderr
    # An apparently successful shared-key repair silently merges distinct requests.
    (tmp_path / "application.py").write_text(
        baseline.replace("idempotency_key=None", 'idempotency_key="all-requests"')
    )
    assert (
        command(
            tmp_path,
            "-B",
            "-m",
            "unittest",
            "test_ticket_retry.TicketRegression.test_distinct_requests_remain_distinct",
        ).returncode
        == 1
    )
