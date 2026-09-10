"""The same portable fixture on local and explicitly authorized live runners."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from support.experiments import git, passing_review

from agentagon.experiments import (
    cleanup,
    delivery,
    engine,
    inspection,
    orchestration,
    preparation,
    runners,
)
from agentagon.storage.config import Config
from agentagon.storage.workspace import Workspace


@pytest.fixture
def portable_application(tmp_path):
    root = tmp_path / "portable"
    root.mkdir()
    git(root, "init", "-q")
    (root / "app.json").write_text('{"latency":100,"quality":0.8}')
    (root / "benchmark.py").write_text(
        "import json, os\nfrom pathlib import Path\n"
        "assert 'E2B_API_KEY' not in os.environ\n"
        "values = json.loads(Path('app.json').read_text())\n"
        "Path(os.environ['AGENTAGON_RESULT_PATH']).write_text(json.dumps({'metrics': values, 'tasks': {'portable-case': values['quality']}}))\n"
        "if os.environ.get('AGENTAGON_EVENTS_PATH'):\n"
        "    events = [{'version':1,'event':kind,'task_id':'portable-case','at':'fixture','data':data} for kind,data in [('task_start',{'input':'portable fixture'}),('output',values),('task_end',{'ok':values['quality']>=0.7})]]\n"
        "    Path(os.environ['AGENTAGON_EVENTS_PATH']).write_text(''.join(json.dumps(event)+'\\n' for event in events))\n"
        "    Path(os.environ['AGENTAGON_ARTIFACTS_DIR'],'answer.json').write_text(json.dumps(values))\n"
    )
    (root / "check.py").write_text(
        "import json\nfrom pathlib import Path\nassert json.loads(Path('app.json').read_text())['quality'] >= 0.7\n"
    )
    workspace = Workspace(root)
    workspace.initialize()
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@localhost",
        "commit",
        "-qm",
        "Portable fixture",
    )
    return workspace


def settings(kind):
    runner = {"kind": kind}
    if kind == "e2b":
        runner.update(
            template=os.environ.get("AGENTAGON_E2B_TEMPLATE", "base"), sandbox_timeout_seconds=90
        )
    elif kind == "ssh":
        runner.update(
            host=os.environ["AGENTAGON_LIVE_SSH_HOST"], remote_root="/tmp/agentagon-live-tests"
        )
    return {
        "runner": runner,
        "limits": {
            "max_candidates": 1,
            "max_trials": 2,
            "max_elapsed_seconds": 180,
            "parallel_candidates": 1,
            "parallel_trials": 1,
            "trial_timeout_seconds": 30,
        },
    }


@pytest.mark.parametrize("kind", ["local", "ssh", "e2b"])
def test_portable_full_fix_lifecycle(kind, portable_application):
    if kind == "ssh" and not os.environ.get("AGENTAGON_LIVE_SSH_HOST"):
        pytest.skip("SSH fixture requires an authorized configured host alias")
    if kind == "e2b" and os.environ.get("AGENTAGON_LIVE_E2B") != "1":
        pytest.skip("E2B fixture requires explicit live-test authorization")
    workspace = portable_application
    original = git(workspace.root, "rev-parse", "HEAD")
    Config().update_profile("project", kind, settings(kind), workspace.root)
    spec = {
        "goal": "Improve latency with quality controls",
        "editable_paths": ["app.json"],
        "evaluation_paths": ["benchmark.py", "check.py"],
        "benchmark": {"argv": ["python3", "benchmark.py"]},
        "metrics": {
            "latency": {"unit": "ms", "direction": "min"},
            "quality": {"unit": "fraction", "direction": "max"},
        },
        "constraints": [{"metric": "quality", "op": "gte", "bound": 0.7}],
        "checks": [{"id": "quality", "argv": ["python3", "check.py"]}],
        "repetitions": 1,
        "seeds": [71],
    }
    initial = engine.start(workspace, spec, kind)
    run_id = initial["run_id"]
    try:
        measured = engine.run(workspace, run_id)
        assert measured["candidate"]["state"] == "awaiting_review", measured["candidate"]
        engine.run(workspace, run_id, review=passing_review(measured))
        created = engine.new(
            workspace,
            run_id,
            hypothesis="Optimize the measured application",
            author="fixture-author",
        )
        (workspace.root / created["candidate"]["worktree"] / "app.json").write_text(
            '{"latency":80,"quality":0.85}'
        )
        measured = engine.run(workspace, run_id, created["candidate_id"])
        assert measured["candidate"]["state"] == "awaiting_review", measured["candidate"]
        assert measured["candidate"]["metrics"] == {"latency": 80, "quality": 0.85}
        engine.run(workspace, run_id, created["candidate_id"], review=passing_review(measured))
        selected = engine.select(workspace, run_id, created["candidate_id"])
        assert not selected["cleanup_pending"]
        assert selected["usage"]["trials"] == 2
        assert (
            git(workspace.root, "rev-parse", selected["selected_branch"] + "^{tree}")
            == selected["candidate"]["source_digest"]
        )
        assert git(workspace.root, "rev-parse", "HEAD") == original
        assert git(workspace.root, "status", "--porcelain") == ""
    finally:
        engine.stop(workspace, run_id)


@pytest.mark.parametrize("kind", ["local", "ssh", "e2b"])
def test_portable_goal_to_clean_verified_delivery(kind, portable_application, tmp_path):
    if kind == "ssh" and not os.environ.get("AGENTAGON_LIVE_SSH_HOST"):
        pytest.skip("SSH fixture requires an authorized configured host alias")
    if kind == "e2b" and os.environ.get("AGENTAGON_LIVE_E2B") != "1":
        pytest.skip("E2B fixture requires explicit live-test authorization")
    workspace = portable_application
    original = git(workspace.root, "rev-parse", "HEAD")
    profile = settings(kind)
    profile["limits"].update(
        max_candidates=4, max_trials=6, max_elapsed_seconds=600, parallel_candidates=2
    )
    profile["orchestration"] = {
        **orchestration.DEFAULT_SETTINGS,
        "round_width": 2,
        "host_capacity": 2,
        "resource_slots": 2,
    }
    Config().update_profile("project", kind, profile, workspace.root)
    spec = {
        "goal": "Improve latency with quality controls",
        "editable_paths": ["app.json"],
        "evaluation_paths": ["benchmark.py", "check.py"],
        "benchmark": {"argv": ["python3", "benchmark.py"]},
        "metrics": {
            "latency": {"unit": "ms", "direction": "min"},
            "quality": {"unit": "fraction", "direction": "max"},
        },
        "constraints": [{"metric": "quality", "op": "gte", "bound": 0.7}],
        "checks": [{"id": "quality", "argv": ["python3", "check.py"]}],
        "repetitions": 1,
        "seeds": [71],
    }
    spec["task_metrics"] = {"portable-case": {"unit": "fraction", "direction": "max"}}
    draft = preparation.start(
        workspace,
        kind,
        {"max_trials": 3, "max_elapsed_seconds": 600, "trial_timeout_seconds": 30},
        goal=spec["goal"],
        author="benchmark-author",
    )
    negative = workspace.state / "incorrect.json"
    negative.write_text('{"latency":100,"quality":0.1}')
    plan = {
        "spec": spec,
        "provenance": "Synthetic portable fixture with explicit quality ground truth.",
        "coverage": {
            "status": "limited",
            "rationale": "One synthetic case; no independent production holdout is available.",
            "holdout_paths": [],
        },
        "negative_cases": [
            {
                "id": "incorrect",
                "description": "Incorrect response with quality below the known floor",
                "mutations": [
                    {"path": "app.json", "source": str(negative.relative_to(workspace.root))}
                ],
                "expected_checks": ["quality"],
            }
        ],
    }
    checked = preparation.check(workspace, draft["evaluation_id"], plan)
    assert checked["state"] == "awaiting_review", checked
    review = checked["checks"][-1]["review_template"]
    review.update(
        reviewer="independent-benchmark-reviewer",
        verdict="pass",
        rationale="Reviewed the synthetic ground truth, baseline, negative control and stated coverage limitation.",
    )
    review["assessments"] = dict.fromkeys(review["assessments"], True)
    preparation.freeze(workspace, draft["evaluation_id"], review)
    started = engine.start(workspace, preparation.fix_spec(workspace, draft["evaluation_id"]), kind)
    run_id, parent = started["run_id"], started["candidate_id"]
    try:
        measured = engine.run(workspace, run_id)
        engine.run(workspace, run_id, review=passing_review(measured))
        briefs = [
            {
                "parent_id": parent,
                "hypothesis": hypothesis,
                "author": f"author-{index}",
                "editable_paths": ["app.json"],
                "evidence": [f"candidate:{parent}"],
            }
            for index, hypothesis in enumerate(
                ["Improve both latency and quality", "Explore a faster latency tradeoff"]
            )
        ]
        reserved = orchestration.reserve_round(
            workspace,
            run_id,
            {
                "operation_id": "portable-round",
                "host_id": "portable-host",
                "host_capacity": 2,
                "branches": briefs,
            },
        )
        for index, cid in enumerate(reserved["candidate_ids"]):
            orchestration.assign(
                workspace, run_id, cid, "portable-host", f"portable-author-{index}"
            )
            info = engine.status(workspace, run_id, cid)
            (workspace.root / info["candidate"]["worktree"] / "app.json").write_text(
                json.dumps(
                    {"latency": 80 if index == 0 else 75, "quality": 0.85 if index == 0 else 0.8},
                    indent=4,
                )
            )
            measured = engine.run(workspace, run_id, cid)
            assert measured["candidate"]["state"] == "awaiting_review", measured
            engine.run(workspace, run_id, cid, review=passing_review(measured))
            evidence = inspection.candidate(workspace, run_id, cid)
            assert evidence["trials"][0]["evidence"]["events"][-1]["event"] == "task_end"
            assert inspection.artifact(workspace, run_id, cid, evidence["trials"][0]["trial_id"], 0)
        winner = reserved["candidate_ids"][0]
        engine.select(workspace, run_id, winner)
        cleaning = cleanup.start(workspace, run_id, "portable-cleanup", "cleanup-author")
        cid = cleaning["candidate_id"]
        (workspace.root / cleaning["candidate"]["worktree"] / "app.json").write_text(
            '{"latency":80,"quality":0.85}\n'
        )
        measured = engine.run(workspace, run_id, cid)
        engine.run(workspace, run_id, cid, review=passing_review(measured))
        assert cleanup.finish(workspace, run_id, cid)["state"] == "selected_cleanup"
        remote = tmp_path / "delivery.git"
        git(tmp_path, "init", "--bare", "-q", str(remote))
        git(workspace.root, "remote", "add", "origin", str(remote))
        base = git(workspace.root, "branch", "--show-current")
        git(workspace.root, "push", "-q", "origin", f"HEAD:refs/heads/{base}")
        git(
            workspace.root,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            f"refs/remotes/origin/{base}",
        )
        delivered = delivery.ship(workspace, run_id)
        assert delivered["candidate_id"] == cid and delivered["state"] == "prepared"
        assert engine.status(workspace, run_id)["usage"]["trials"] == 4
        assert not engine.status(workspace, run_id)["cleanup_pending"]
        assert git(workspace.root, "rev-parse", "HEAD") == original
        assert git(workspace.root, "status", "--porcelain") == ""
    finally:
        engine.stop(workspace, run_id)


@pytest.mark.skipif(
    os.environ.get("AGENTAGON_LIVE_E2B") != "1", reason="explicit live E2B authorization required"
)
def test_live_e2b_reconnects_same_sandbox_and_cleans_it(
    portable_application, tmp_path, monkeypatch
):
    source = portable_application.root
    profile = settings("e2b")
    request = {
        "attempt_id": "live-reconnect",
        "source_digest": "portable-fixture",
        "evaluation_digest": "v1",
        "timeout_seconds": 30,
        "seed": 71,
        "deadline_at": (datetime.now(UTC) + timedelta(seconds=90)).isoformat(),
        "commands": [
            {
                "id": "benchmark",
                "role": "benchmark",
                "argv": ["python3", "benchmark.py"],
                "cwd": ".",
            }
        ],
    }
    attempt = tmp_path / "reconnect-attempt"
    original_command = runners.E2B.command
    original_create = runners.E2B.create
    created = []

    def track_create(self):
        original_create(self)
        created.append(self.descriptor["sandbox_id"])

    def lose_status(self, action, env=None):
        if action == "status":
            raise ConnectionError("Injected connection loss after actual remote execution launch")
        return original_command(self, action, env)

    monkeypatch.setattr(runners.E2B, "create", track_create)
    monkeypatch.setattr(runners.E2B, "command", lose_status)
    try:
        interrupted = runners.execute(profile, source, attempt, request)
        assert interrupted["state"] == "interrupted"
        assert not interrupted["finalized"]
        first = json.loads((attempt / "executor.json").read_text())
        monkeypatch.setattr(runners.E2B, "command", original_command)
        collected = runners.execute(profile, source, attempt, request)
        assert collected["state"] == "completed"
        assert not collected["cleanup_pending"]
        assert created == [first["sandbox_id"]]
        assert json.loads(collected["benchmark_output"])["metrics"]["latency"] == 100
        assert runners.execute(profile, source, attempt, request) == collected
    finally:
        monkeypatch.setattr(runners.E2B, "command", original_command)
        if (attempt / "executor.json").exists():
            runners.cancel(profile, attempt)
            if (attempt / "collected.json").exists():
                runners.cleanup(profile, attempt)
