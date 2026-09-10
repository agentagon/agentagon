"""Shared delivery test support."""

import json
from pathlib import Path

import pytest

from agentagon.experiments import delivery, engine, store
from support.experiments import baseline, git, propose, verify


@pytest.fixture
def selected(application, specification, tmp_path):
    specification["repetitions"] = 1
    origin = baseline(application, specification)
    created, _ = propose(application, origin["run_id"])
    candidate = verify(application, origin["run_id"], created["candidate_id"])
    engine.select(application, origin["run_id"], created["candidate_id"])
    selection = store.load_run(application, origin["run_id"])["selected"]
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(application.root, "remote", "add", "origin", str(remote))
    base = git(application.root, "branch", "--show-current")
    git(application.root, "push", "-q", "origin", f"HEAD:refs/heads/{base}")
    git(application.root, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{base}")
    return {
        "workspace": application,
        "run_id": candidate["run_id"],
        "candidate_id": candidate["candidate_id"],
        "branch": selection["branch"],
        "revision": candidate["candidate"]["source_revision"],
        "remote": remote,
        "base": base,
    }


@pytest.fixture
def github(selected, monkeypatch):
    original = delivery._command
    calls, prs = [], []

    def command(root, argv, **kwargs):
        calls.append(argv)
        if argv[0] != "gh":
            return original(root, argv, **kwargs)
        if argv[1:3] == ["pr", "list"]:
            return json.dumps(prs)
        assert argv[1:3] == ["pr", "create"]
        assert "--draft" in argv
        assert "--body-file" in argv and "--body" not in argv
        body = Path(argv[argv.index("--body-file") + 1]).read_text()
        prs.append(
            {
                "url": "https://github.com/example/project/pull/7",
                "number": 7,
                "state": "OPEN",
                "isDraft": True,
                "headRefOid": selected["revision"],
                "headRefName": selected["branch"],
                "baseRefName": selected["base"],
                "isCrossRepository": False,
                "body": body,
            }
        )
        return prs[-1]["url"]

    monkeypatch.setattr(delivery, "_command", command)
    monkeypatch.setattr(delivery, "_repository", lambda url: "github.com/example/project")
    monkeypatch.setattr(delivery.shutil, "which", lambda name: "/mock/gh")
    return {"calls": calls, "prs": prs, "command": command}


def pushes(github):
    return [argv for argv in github["calls"] if argv[0] == "git" and "push" in argv]


def creates(github):
    return [argv for argv in github["calls"] if argv[:3] == ["gh", "pr", "create"]]
