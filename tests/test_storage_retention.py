"""Storage reclamation preserves replay, evidence identity and user-owned edits."""

import base64
import errno
import fcntl
import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from support.dashboard import request as http_request
from support.dashboard import running
from support.experiments import baseline, git, passing_review, propose
from support.runners import FakeRemote, command, request

from agentagon.cli.main import main
from agentagon.core.records import AuditError
from agentagon.experiments import engine, evidence, inspection, runners, store, worker


def artifact_request():
    code = """import os
from pathlib import Path
Path(os.environ['AGENTAGON_ARTIFACTS_DIR'], 'output.bin').write_bytes(b'payload' * 1000)
Path(os.environ['AGENTAGON_RESULT_PATH']).write_text('{"metrics":{"score":17}}')
print('retained log')
"""
    return {
        **request([command("bench", "benchmark", code)]),
        "evidence_limits": dict(evidence.DEFAULT_LIMITS),
    }


@pytest.mark.parametrize("kind", ["local", "ssh", "e2b"])
def test_retained_results_share_bytes_and_replay_without_execution(workspace, kind, monkeypatch):
    monkeypatch.setattr(runners, "_transport", FakeRemote)
    profile = {
        "runner": {"kind": kind, "host": "test", "remote_root": str(workspace.root / "remote")}
    }
    attempts = [workspace.state / "attempts" / str(index) for index in range(2)]
    refs = []
    for index, attempt in enumerate(attempts):
        args = {**artifact_request(), "attempt_id": f"attempt-{index}"}
        result = runners.execute(profile, workspace.root, attempt, args)
        progress = attempt / "progress.json"
        progress.write_text(json.dumps(result["evidence"]))
        relative = evidence.retain_result(workspace, attempt, result)
        retained = workspace.read_artifact(relative)
        refs.append(retained["evidence"]["artifacts"][0]["content_path"])
        assert "content_base64" not in json.dumps(retained)
        assert (
            evidence.artifact_bytes(workspace, retained["evidence"]["artifacts"][0])
            == b"payload" * 1000
        )
        canonical = workspace.root / relative
        assert canonical.samefile(attempt / "collected.json")
        if kind == "local":
            assert canonical.samefile(attempt / "job" / "result.json")
            assert not list((attempt / "job").glob("*.log"))
            assert not list((attempt / "job").glob("benchmark-*.json"))
            worker.run(
                attempt / "job", json.loads((attempt / "executor.json").read_text())["owner_token"]
            )
        assert not progress.exists()
        assert runners.execute(profile, workspace.root, attempt, args) == retained
        assert evidence.retain_result(workspace, attempt, retained) == relative
        # Cleanup replaces its cache; it must never mutate immutable linked bytes.
        before = canonical.read_bytes()
        runners.cleanup(profile, attempt)
        assert canonical.read_bytes() == before
        assert workspace.read_artifact(relative) == retained
        assert canonical.samefile(attempt / "collected.json")
    assert refs[0] == refs[1]
    assert len(list((workspace.state / "evidence").glob("*.bin"))) == 1


def test_failed_retention_leaves_recoverable_payloads(workspace, monkeypatch):
    attempt = workspace.state / "attempts" / "one"
    args = artifact_request()
    result = runners.execute({}, workspace.root, attempt, args)
    original = (attempt / "collected.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as context:
        context.setattr(workspace, "artifact", fail)
        with pytest.raises(OSError, match="disk full"):
            evidence.retain_result(workspace, attempt, result)
    assert (attempt / "collected.json").read_bytes() == original
    assert (attempt / "job" / "result.json").exists()
    assert runners.execute({}, workspace.root, attempt, args) == result
    evidence.retain_result(workspace, attempt, result)
    assert not list((attempt / "job").glob("*.log"))


def test_filesystem_without_hard_links_still_retains_and_replays(workspace, monkeypatch):
    attempt = workspace.state / "attempts" / "one"
    args = artifact_request()
    result = runners.execute({}, workspace.root, attempt, args)

    def unsupported(*args, **kwargs):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr("agentagon.storage.workspace.os.link", unsupported)
    relative = evidence.retain_result(workspace, attempt, result)
    retained = evidence.read_result(workspace, relative)
    assert "content_base64" not in json.dumps(retained)
    assert runners.execute({}, workspace.root, attempt, args) == retained
    assert (attempt / "job" / "result.json").read_bytes() == (
        workspace.root / relative
    ).read_bytes()


def test_running_worker_prevents_transient_cleanup(workspace):
    attempt = workspace.state / "attempts" / "one"
    result = runners.execute({}, workspace.root, attempt, artifact_request())
    with (attempt / "job" / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        relative = evidence.retain_result(workspace, attempt, result)
        assert list((attempt / "job").glob("*.log"))
        assert not (workspace.root / relative).samefile(attempt / "job" / "result.json")
    evidence.retain_result(workspace, attempt, result)
    assert not list((attempt / "job").glob("*.log"))


def test_binary_artifacts_validate_content_and_keep_legacy_read_support(workspace):
    content = b"retained bytes"
    entry = {
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode(),
    }
    assert evidence.artifact_bytes(workspace, entry) == content
    compact = {k: v for k, v in entry.items() if k != "content_base64"}
    compact["content_path"] = workspace.blob(content, ".bin")
    assert evidence.artifact_bytes(workspace, compact) == content
    with pytest.raises(AuditError, match="path"):
        evidence.artifact_bytes(workspace, {**compact, "content_path": "app.py"})
    (workspace.root / compact["content_path"]).write_bytes(b"tampered")
    with pytest.raises(AuditError, match="checksum"):
        evidence.artifact_bytes(workspace, compact)


def test_verified_candidates_release_worktrees_and_remain_inspectable(application, specification):
    started = baseline(application, specification)
    base = started["candidate"]
    assert base["worktree_released"]
    assert not (application.root / base["worktree"]).exists()
    created, checkout = propose(application, started["run_id"])
    measured = engine.run(application, started["run_id"], created["candidate_id"])
    assert checkout.exists()  # The independent reviewer still needs this tree.
    reviewed = engine.run(
        application, started["run_id"], created["candidate_id"], review=passing_review(measured)
    )
    assert reviewed["candidate"]["worktree_released"]
    assert not checkout.exists()
    details = inspection.candidate(application, started["run_id"], created["candidate_id"])
    assert "app.json" in details["diff"]["text"]
    assert len(details["trials"]) == specification.get("repetitions", 3)
    engine.select(application, started["run_id"], created["candidate_id"])
    child, path = propose(application, started["run_id"], parent_id=created["candidate_id"])
    assert path.exists() and child["candidate"]["parent_id"] == created["candidate_id"]


@pytest.mark.parametrize(
    "change", ["unstaged", "staged", "untracked", "ignored", "branch", "commit"]
)
def test_finished_worktree_preserves_changes_after_sealing(application, specification, change):
    started = baseline(application, specification)
    created, checkout = propose(application, started["run_id"])
    measured = engine.run(application, started["run_id"], created["candidate_id"])
    if change in {"staged", "unstaged"}:
        (checkout / "app.json").write_text("new user work")
        if change == "staged":
            git(checkout, "add", "app.json")
    elif change == "branch":
        git(checkout, "checkout", "-b", "user-branch")
    elif change == "commit":
        git(
            checkout,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@localhost",
            "commit",
            "--allow-empty",
            "-qm",
            "User commit after sealing",
        )
    else:
        (checkout / "notes.txt").write_text("new user work")
        if change == "ignored":
            exclude = Path(git(application.root, "rev-parse", "--git-path", "info/exclude"))
            if not exclude.is_absolute():
                exclude = application.root / exclude
            with exclude.open("a") as stream:
                stream.write("\nnotes.txt\n")
    reviewed = engine.run(
        application, started["run_id"], created["candidate_id"], review=passing_review(measured)
    )
    assert reviewed["candidate"]["state"] == "verified"
    assert not reviewed["candidate"].get("worktree_released")
    assert checkout.exists()


def test_dashboard_downloads_retained_binary_and_legacy_artifacts(application, specification):
    argv = artifact_request()["commands"][0]["argv"]
    argv[-1] = argv[-1].replace('"score":17', '"latency":100,"quality":0.8')
    specification["benchmark"] = {"argv": argv}
    started = baseline(application, specification)
    candidate = started["candidate"]
    trial = candidate["trials"][0]
    prefix = f"/api/runs/{started['run_id']}/candidates/{candidate['candidate_id']}"
    url = f"{prefix}/trials/{trial['trial_id']}/artifacts/0"
    retained = application.read_artifact(trial["artifact"])
    entry = retained["evidence"]["artifacts"][0]
    with running(application) as server:
        status, _, body = http_request(server, prefix)
        assert status == 200 and b"content_path" not in body and b"content_base64" not in body
        status, _, content = http_request(server, url)
        assert status == 200 and content == b"payload" * 1000
    binary = application.root / entry["content_path"]
    binary.write_bytes(b"tampered")
    with pytest.raises(AuditError, match="checksum"):
        engine.select(application, started["run_id"], candidate["candidate_id"])
    binary.write_bytes(content)
    # Exercise a historical inline result without rewriting the original evidence.
    entry["content_base64"] = base64.b64encode(content).decode()
    entry.pop("content_path")
    legacy = application.artifact(retained)
    data = store.load_run(application, started["run_id"])
    data["candidates"][candidate["candidate_id"]]["trials"][0]["artifact"] = legacy
    application.write(store.run_dir(application, started["run_id"]) / "state.json", data)
    with running(application) as server:
        assert http_request(server, url)[2] == content


def test_reports_are_explicit_snapshots_and_reads_do_not_generate_them(application, specification):
    started = engine.start(application, specification, "local")
    root = store.run_dir(application, started["run_id"])
    state = (root / "state.json").read_bytes()
    assert not (root / "report.md").exists()
    assert not (root / "report.json").exists()
    engine.status(application, started["run_id"])
    assert not (root / "report.json").exists()
    exported = CliRunner().invoke(
        main, ["--workspace", str(application.root), "fix", "report", started["run_id"]]
    )
    assert exported.exit_code == 0, exported.output
    paths = json.loads(exported.output)
    assert Path(paths["markdown"]).is_file()
    report = Path(paths["json"]).read_bytes()
    assert json.loads(report)["revision"] == paths["revision"]
    assert (root / "state.json").read_bytes() == state
    measured = engine.run(application, started["run_id"])
    assert Path(paths["json"]).read_bytes() == report
    fresh = store.export_report(application, started["run_id"])
    assert fresh["revision"] == measured["revision"] > paths["revision"]
