"""Regression checks from independent review of the local application boundary."""

import pytest
from support.evaluation import draft
from support.experiments import propose, verify

from agentagon.core.records import AuditError
from agentagon.experiments import delivery, engine, preparation
from agentagon.webapp import snapshots
from agentagon.webapp.service import Application


def test_publication_rejects_a_changed_selection_after_package_review(
    selected, tmp_path, monkeypatch
):
    app = Application(tmp_path / "review-app", execute=lambda *_: {})
    try:
        workspace = selected["workspace"]
        project = app.register(str(workspace.root))
        payload = {"kind": "fix", "source_id": selected["run_id"]}
        prepared = app.deliver(project["id"], {**payload, "publish": False})
        other, _ = propose(
            workspace, selected["run_id"], latency=85, quality=0.9, variant="different-selection"
        )
        verify(workspace, selected["run_id"], other["candidate_id"])
        engine.select(workspace, selected["run_id"], other["candidate_id"])
        published = []

        def capture_publish(_workspace, record, _url, **_kwargs):
            published.append(record["candidate_id"])

        monkeypatch.setattr(delivery, "_publish", capture_publish)
        with pytest.raises(AuditError, match="prepared delivery changed"):
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
        assert published == []
        assert prepared["candidate_id"] == selected["candidate_id"]
    finally:
        app.close()


def test_materialized_private_dataset_cannot_be_reclassified_as_delivered_source(
    application, specification
):
    record = snapshots.save(
        application,
        "project_" + "a" * 24,
        {
            "kind": "dataset",
            "connection_id": "connection_" + "1" * 24,
            "selection": {"dataset_id": "private-cases"},
            "items": [{"id": "one", "input": "private example", "expected": "private reference"}],
            "provenance": {"provider": "braintrust"},
            "completeness": {"complete": True},
        },
    )
    evaluation, plan = draft(application, specification)
    materialized = snapshots.materialize(application, record["id"], evaluation["evaluation_id"])
    plan["spec"]["inputs"] = []
    plan["spec"]["overlays"] = []
    plan["spec"]["evaluation_paths"].append(materialized["path"])
    plan["deliver_paths"] = [materialized["path"]]
    with pytest.raises(AuditError, match="private|materialized|imported"):
        preparation._snapshot(
            application, preparation.load(application, evaluation["evaluation_id"]), plan
        )
    plan["spec"]["inputs"] = [materialized["input"]]
    plan["deliver_paths"] = ["checks.py"]
    _, files = preparation._snapshot(
        application, preparation.load(application, evaluation["evaluation_id"]), plan
    )
    private = next(entry for entry in files if entry["path"] == materialized["path"])
    assert private["kind"] == "inputs" and private["deliver"] is False
