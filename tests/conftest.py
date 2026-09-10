import json
import subprocess
from pathlib import Path

import pytest
from support.dashboard import fix_run as fix_run
from support.delivery import github as github
from support.delivery import selected as selected
from support.experiments import application as application
from support.experiments import specification as specification

from agentagon.operations import import_traces, start
from agentagon.storage.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    from agentagon import usage

    monkeypatch.setenv("AGENTAGON_CONFIG", str(tmp_path / "settings" / "config.json"))
    monkeypatch.delenv("AGENTAGON_API_KEY", raising=False)
    monkeypatch.delenv("AGENTAGON_TELEMETRY_DISABLED", raising=False)
    monkeypatch.delenv("AGENTAGON_POSTHOG_PROJECT_TOKEN", raising=False)
    monkeypatch.setattr(usage, "POSTHOG_PROJECT_TOKEN", "")


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "customer"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "app.py").write_text(
        'def weather(city):\n    raise TimeoutError("upstream unavailable")\n', encoding="utf-8"
    )
    work = Workspace(root)
    work.initialize()
    subprocess.run(["git", "-C", str(root), "add", "app.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "test: establish application baseline",
        ],
        check=True,
    )
    return work


@pytest.fixture
def fixtures():
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def imported(workspace, fixtures, tmp_path):
    audit_id = start(
        workspace,
        mode="traces",
        source="braintrust",
        project="demo",
        start_time="2026-08-10T00:00:00Z",
        end_time="2026-08-11T00:00:00Z",
        limit="all",
        scopes=[],
        host="test",
        model="fixture",
    )["audit_id"]
    acquisition = tmp_path / "acquisition.json"
    acquisition.write_text(
        json.dumps(
            {
                "source": "braintrust",
                "project": "demo",
                "method": "fixture",
                "tool_version": "fixture-v1",
                "fetched_at": "2026-08-12T00:00:00Z",
                "completeness": "complete",
                "pagination_complete": True,
                "selected_trace_ids": ["root"],
                "failed_trace_ids": [],
            }
        ),
        encoding="utf-8",
    )
    import_traces(workspace, audit_id, fixtures / "braintrust.json", acquisition)
    return audit_id
