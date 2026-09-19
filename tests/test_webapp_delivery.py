"""Verified delivery stays local until the user explicitly publishes it."""

import uuid

import pytest
from support.delivery import creates, pushes

from agentagon.core.records import AuditError
from agentagon.workflows.service import Application


@pytest.fixture
def app(tmp_path):
    value = Application(tmp_path / "webapp", execute=lambda *_: {})
    yield value
    value.close()


def test_prepare_download_and_explicit_publish(app, selected, github, tmp_path):
    project = app.register(str(selected["workspace"].root))
    payload = {"kind": "optimize", "source_id": selected["run_id"], "publish": False}
    with pytest.raises(AuditError, match="prepare the local"):
        app.deliver(
            project["id"],
            {**payload, "publish": True, "remote": "origin", "base": selected["base"]},
        )
    prepared = app.deliver(project["id"], payload)
    assert prepared["state"] == "prepared"
    assert not creates(github) and not pushes(github)
    assert app.delivery_artifact(project["id"], prepared["delivery_id"], "diff")
    assert prepared["artifact_urls"]["diff"].startswith(f"/api/projects/{project['id']}/")
    other = tmp_path / "other"
    other.mkdir()
    other_project = app.register(str(other))
    with pytest.raises(AuditError, match="not found"):
        app.delivery_artifact(other_project["id"], prepared["delivery_id"], "diff")
    published = app.deliver(
        project["id"],
        {
            **payload,
            "publish": True,
            "delivery_id": prepared["delivery_id"],
            "remote": "origin",
            "base": selected["base"],
        },
    )
    assert published["state"] == "published"
    assert len(creates(github)) == 1
    app.deliver(
        project["id"],
        {
            **payload,
            "publish": True,
            "delivery_id": prepared["delivery_id"],
            "remote": "origin",
            "base": selected["base"],
        },
    )
    assert len(creates(github)) == 1


def test_run_controls_require_current_revision_and_reject_host_ack(app, selected):
    project = app.register(str(selected["workspace"].root))
    run = app.result(project["id"], "optimize", selected["run_id"])
    request = {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "expected_revision": run["revision"],
        "action": "select",
        "candidate_id": selected["candidate_id"],
    }
    reply = app.control_run(project["id"], selected["run_id"], request)
    assert "revision" in reply
    assert (
        app.control_run(project["id"], selected["run_id"], request)["revision"] == reply["revision"]
    )
    with pytest.raises(AuditError, match="unsupported"):
        app.control_run(project["id"], selected["run_id"], {**request, "action": "ack"})
