"""One event contract across helper languages, local execution and remote collection."""

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from support.runners import FakeRemote, command, request

from agentagon.core.records import validate_record
from agentagon.experiments import runners, worker
from agentagon.experiments.evidence import DEFAULT_LIMITS


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    return root


HELPERS = Path(__file__).parents[1] / "skills" / "eval" / "helpers"


@pytest.mark.parametrize("language", ["python", "node"])
def test_helpers_emit_the_same_valid_contract(language, tmp_path):
    output = tmp_path / "events.jsonl"
    env = {**os.environ, "AGENTAGON_EVENTS_PATH": str(output)}
    if language == "python":
        env["PYTHONPATH"] = str(HELPERS)
        args = [
            sys.executable,
            "-c",
            "from agentagon_events import emit; emit('task_start','café',{'input':'x'}); emit('task_end','café',{'ok':True})",
        ]
    else:
        node = shutil.which("node")
        if not node:
            pytest.skip("Node is required for the Node helper conformance check")
        args = [
            node,
            "-e",
            "const {emit}=require(process.argv[1]); emit('task_start','café',{input:'x'}); emit('task_end','café',{ok:true})",
            str(HELPERS / "agentagon_events.cjs"),
        ]
    subprocess.run(args, env=env, check=True, capture_output=True)
    events = [json.loads(line) for line in output.read_text().splitlines()]
    for event in events:
        validate_record("task-event", event)
    assert [event["event"] for event in events] == ["task_start", "task_end"]
    assert events[-1]["data"] == {"ok": True}


def evidence_request(code, **limits):
    return {
        **request([command("bench", "benchmark", code)]),
        "run_id": "run-fixture",
        "candidate_id": "candidate-fixture",
        "evidence_limits": {**DEFAULT_LIMITS, **limits},
    }


@pytest.mark.parametrize("kind", ["local", "ssh", "e2b"])
def test_task_evidence_survives_partial_failure_and_remote_cleanup(
    kind, source, tmp_path, monkeypatch
):
    monkeypatch.setattr(runners, "_transport", FakeRemote)
    shutil.copy(HELPERS / "agentagon_events.py", source)
    code = """import os
from pathlib import Path
from agentagon_events import emit
emit('task_start', 'completed')
emit('output', 'completed', {'text': 'observed'})
emit('task_end', 'completed', {'ok': True})
emit('task_start', 'interrupted')
emit('failure', 'interrupted', {'message': 'dependency unavailable'})
Path(os.environ['AGENTAGON_ARTIFACTS_DIR'], 'report.txt').write_text('retained output')
emit('artifact', 'completed', {'path': 'report.txt'})
with open(os.environ['AGENTAGON_EVENTS_PATH'], 'a') as stream:
    stream.write('{"partial":')
raise SystemExit(2)
"""
    profile = {"runner": {"kind": kind, "host": "test", "remote_root": str(tmp_path / "remote")}}
    result = runners.execute(profile, source, tmp_path / "attempt", evidence_request(code))
    evidence = result["evidence"]
    assert result["results"][0]["exit_code"] == 2
    assert evidence["incomplete"]
    assert evidence["events"][2]["event"] == "task_end"
    assert evidence["identity"]["candidate_id"] == "candidate-fixture"
    assert base64.b64decode(evidence["artifacts"][0]["content_base64"]) == b"retained output"
    cached = json.loads((tmp_path / "attempt" / "collected.json").read_text())
    assert cached["evidence"] == evidence
    assert not result["cleanup_pending"]
    if kind != "local":
        assert not list((tmp_path / "remote").iterdir())


def test_limits_malformed_events_and_unsafe_artifacts_are_diagnostic(source, tmp_path):
    code = """import os,json
from pathlib import Path
events = Path(os.environ['AGENTAGON_EVENTS_PATH'])
events.write_text('bad-json\\n' + json.dumps({'version':1,'event':'output','task_id':'x','at':'now','data':{},'candidate_id':'spoofed'}) + '\\n')
root=Path(os.environ['AGENTAGON_ARTIFACTS_DIR'])
(root/'large').write_bytes(b'x'*100)
(root/'link').symlink_to('/etc/hosts')
os.mkfifo(root/'pipe')
Path(os.environ['AGENTAGON_RESULT_PATH']).write_text('{"metrics":{"score":17}}')
"""
    result = runners.execute(
        {}, source, tmp_path / "attempt", evidence_request(code, max_artifact_bytes=10)
    )
    evidence = result["evidence"]
    assert evidence["malformed_events"] == 2
    assert len(evidence["rejected_artifacts"]) == 3
    assert evidence["truncated"]
    assert not evidence["events"] and not evidence["artifacts"]
    assert json.loads(result["benchmark_output"])["metrics"]["score"] == 17


def test_events_are_bounded_and_secrets_redacted(source, tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_SECRET", "secret-value")
    shutil.copy(HELPERS / "agentagon_events.py", source)
    code = """import os
from pathlib import Path
from agentagon_events import emit
for n in range(100): emit('output',str(n),{'text':os.environ['TOKEN']})
Path(os.environ['AGENTAGON_ARTIFACTS_DIR'],os.environ['TOKEN']+'.txt').write_text(os.environ['TOKEN'])
"""
    result = runners.execute(
        {"env": {"TOKEN": "EVIDENCE_SECRET"}},
        source,
        tmp_path / "attempt",
        evidence_request(code, max_events=2),
    )
    assert len(result["evidence"]["events"]) == 2
    assert result["evidence"]["truncated"]
    assert result["evidence"]["events"][0]["data"]["text"] == "[REDACTED]"
    assert base64.b64decode(result["evidence"]["artifacts"][0]["content_base64"]) == b"[REDACTED]"
    assert result["evidence"]["artifacts"][0]["path"] == "[REDACTED].txt"
    assert "secret-value" not in json.dumps(result["evidence"])


def test_live_progress_is_mirrored_locally_before_completion(source, tmp_path):
    shutil.copy(HELPERS / "agentagon_events.py", source)
    args = evidence_request(
        "from agentagon_events import emit; import time; emit('task_start','waiting'); time.sleep(2)"
    )
    attempt = tmp_path / "attempt"
    result = {}
    thread = threading.Thread(
        target=lambda: result.update(runners.execute({}, source, attempt, args))
    )
    thread.start()
    deadline = time.monotonic() + 5
    seen = None
    while time.monotonic() < deadline:
        path = attempt / "progress.json"
        if path.exists():
            seen = json.loads(path.read_text())
            if seen["events"]:
                break
        time.sleep(0.05)
    assert seen and seen["events"][0]["task_id"] == "waiting"
    assert seen["identity"]["run_id"] == "run-fixture"
    thread.join(timeout=6)
    assert result["evidence"]["incomplete"]


def test_byte_bound_retains_only_complete_events(tmp_path):
    event = (
        json.dumps({"version": 1, "event": "output", "task_id": "task", "at": "now", "data": {}})
        + "\n"
    )
    (tmp_path / "events-0.jsonl").write_text(event * 3)
    retained_size = len(json.dumps({**json.loads(event), "command_id": "bench"}).encode())
    args = evidence_request("pass", max_event_bytes=retained_size + 1)
    evidence = worker.task_evidence(tmp_path, args, [], complete=True)
    assert len(evidence["events"]) == 1
    assert evidence["truncated"] and evidence["incomplete"]
