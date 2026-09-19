"""Inspectable task evidence stays local, bounded, and bound to its candidate."""

import json

import pytest
from support.evaluation import draft
from support.experiments import git
from test_webapp import running

from agentagon.capabilities.experiments import engine, inspection, preparation, runners
from agentagon.core.records import AuditError
from agentagon.workflows.service import Application


def instrument(workspace):
    benchmark = workspace.root / "benchmark.py"
    with benchmark.open("a") as stream:
        stream.write(
            "\nevent = {'version':1,'event':'failure','task_id':'case-17','at':'now','data':{'message':'<script>task failed</script>'}}\nPath(os.environ['AGENTAGON_EVENTS_PATH']).write_text(json.dumps(event)+'\\n')\nPath(os.environ['AGENTAGON_ARTIFACTS_DIR'],'case.txt').write_text('retained case output')\n"
        )
    git(workspace.root, "add", "benchmark.py")
    git(
        workspace.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-qm",
        "Instrument fixture",
    )


def test_inspection_retrieves_bound_logs_tasks_and_artifacts_without_runner_calls(
    application, specification, monkeypatch
):
    instrument(application)
    started = engine.start(application, specification, "local")
    measured = engine.run(application, started["run_id"])
    monkeypatch.setattr(runners, "execute", lambda *_: pytest.fail("inspection must never execute"))
    detail = inspection.candidate(application, started["run_id"], started["candidate_id"])
    trial = detail["trials"][0]
    assert trial["evidence"]["events"][0]["task_id"] == "case-17"
    assert "content_base64" not in json.dumps(detail)
    assert (
        inspection.artifact(
            application, started["run_id"], started["candidate_id"], trial["trial_id"], 0
        )
        == b"retained case output"
    )
    assert detail["review_template"] == measured["review_template"]
    with pytest.raises(AuditError, match="not retained"):
        inspection.artifact(
            application, started["run_id"], started["candidate_id"], "another-trial", 0
        )


def test_dashboard_evidence_downloads_and_evaluation_projection_are_read_only(
    application, specification, monkeypatch
):
    instrument(application)
    started = engine.start(application, specification, "local")
    engine.run(application, started["run_id"])
    evaluated, plan = draft(application, specification)
    preparation.check(application, evaluated["evaluation_id"], plan)
    before = {
        file: file.read_bytes()
        for file in application.state.rglob("*")
        if file.is_file() and not file.is_symlink()
    }
    monkeypatch.setattr(runners, "execute", lambda *_: pytest.fail("dashboard must never execute"))
    detail = inspection.candidate(application, started["run_id"], started["candidate_id"])
    trial = detail["trials"][0]
    assert (
        inspection.artifact(
            application, started["run_id"], started["candidate_id"], trial["trial_id"], 0
        )
        == b"retained case output"
    )
    with pytest.raises(AuditError):
        inspection.artifact(application, started["run_id"], started["candidate_id"], "foreign", 0)
    app = Application(application.root.parent / "inspection-service")
    project = app.register(str(application.root))["id"]
    with running(app) as (client, _):
        path = f"/api/projects/{project}/runs/{started['run_id']}/candidates/{started['candidate_id']}/trials/{trial['trial_id']}/artifacts/0"
        response = client.get(path)
        assert response.status_code == 200
        assert response.content == b"retained case output"
        assert client.get(path.replace(trial["trial_id"], "foreign")).status_code == 400
    assert {file: file.read_bytes() for file in before} == before
