"""The app retains exact suite review identities across a measured Fix."""

import copy
import json

import pytest
from test_suites import _host, _suite
from test_webapp_jobs import validation_job

from agentagon.core.records import AuditError
from agentagon.experiments import optimize_run, suites
from agentagon.experiments.host_bridge import HostBridge
from agentagon.experiments.store import load_run
from agentagon.webapp import workflows
from agentagon.webapp.service import Application


@pytest.mark.parametrize("quality,expected", [(0.9, "completed"), (0.7, "completed_with_limits")])
def test_suite_completion_requires_each_managed_child_reviewer(
    application, specification, quality, expected, tmp_path
):
    job = validation_job("fix", engine="gepa")
    run_id, manifest = _suite(application, specification)
    job.update(agent="codex", session_id="author", actual_model="fake")
    job["options"]["suite_manifest"] = manifest
    job["workflow_ids"]["run_id"] = run_id
    optimize_run.configure(
        application, run_id, host="codex", model="fake", optimizer="gepa", max_trials=10
    )
    # Pause with an unclaimed native child review, exactly as the managed author does.
    for _ in range(12):
        progress = optimize_run.advance(application, run_id)
        pending = HostBridge(application, run_id).pending()
        request = next((r for r in pending if r["payload"].get("execution_run_id")), None)
        if request:
            break
        assert pending, progress
        for request in pending:
            HostBridge(application, run_id).fulfill(request, _host(quality))
    else:
        pytest.fail("suite never reached its child review")
    handoff = {
        "run_id": run_id,
        "needs_review": {"owner_id": run_id, "request_id": request["request_id"]},
    }
    task = workflows.review_handoff(application, job, json.dumps(handoff))
    assert task["run_id"] == request["payload"]["execution_run_id"]
    assert task["run_id"] != run_id
    assert task["workflow_run_id"] == run_id
    assert task["template"]["run_id"] == task["run_id"]
    result = optimize_run.advance(application, run_id, host_handler=_host(quality))
    assert result["state"] == "completed"
    children = [
        entry["execution_run_id"]
        for entry in suites.status(application, run_id)["measurements"].values()
    ]
    job["review_tasks"] = {}
    for reviewed_run in [run_id, *children]:
        record = load_run(application, reviewed_run)
        for candidate in record["candidates"].values():
            if candidate.get("review"):
                job["review_tasks"][reviewed_run + candidate["candidate_id"]] = {
                    "state": "completed",
                    "run_id": reviewed_run,
                    "session_id": candidate["review"]["reviewer"],
                }
    output = {"text": json.dumps({"run_id": run_id})}
    state, saved, action = workflows.validate_result(application, job, output)
    assert state == expected
    assert saved["suite"]["passed"] is (quality == 0.9)
    assert saved["comparison"] == result["selection"]
    assert action is None
    missing = copy.deepcopy(job)
    missing["review_tasks"] = {
        key: value
        for key, value in missing["review_tasks"].items()
        if value["run_id"] != children[-1]
    }
    with pytest.raises(AuditError, match="application-managed reviewer"):
        workflows.validate_result(application, missing, output)

    app = Application(tmp_path / "metrics-metadata", execute=lambda *_: {})
    try:
        project = app.register(str(application.root))
        agent = app.save_application_agent(
            project["id"], {"name": "Measured agent", "code_scopes": ["app.json"]}
        )
        with app.state.db.transaction() as tx:
            for member in manifest["members"]:
                focus = {
                    "id": member["focus_id"],
                    "agent_id": agent["id"],
                    "name": member["name"],
                    "created_at": job["created_at"],
                    "state": "active",
                    "measurement": {
                        **member,
                        "agent_binding_digest": agent["binding_digest"],
                    },
                }
                tx.put_record(project["id"], "focuses", focus["id"], focus)
                tx.put_record(
                    project["id"],
                    "focus_versions",
                    "version_" + focus["id"],
                    {"focus_id": focus["id"], "definition": focus},
                )
            tx.put_record(project["id"], "jobs", "job_" + "a" * 24, job)
        metrics = app.catalog.metrics(project["id"], agent["id"])
        assert metrics["limitations"] == []
        assert {row["evaluator_digest"] for row in metrics["metrics"]} == {
            member["evaluator_digest"] for member in manifest["members"]
        }
        expected_outcome = "verified" if quality == 0.9 else "guardrail_failed"
        outcomes = [
            point
            for row in metrics["metrics"]
            for point in row["measurements"]
            if point.get("run_id") == run_id
        ]
        assert len(outcomes) == 4
        assert {point["state"] for point in outcomes} == {expected_outcome}
        for row in metrics["metrics"]:
            assert row["execution_digest"]
            assert {point.get("baseline_id") for point in row["measurements"]} - {None}
        for member in manifest["members"]:
            retained = app.catalog.focus(project["id"], agent["id"], member["focus_id"])
            assert retained["measurement"]["baseline_id"] == member["baseline_id"]
    finally:
        app.close()


def test_browser_delivery_rejects_missing_required_suite(selected, tmp_path, monkeypatch):
    app = Application(tmp_path / "metadata", execute=lambda *_: {})
    try:
        project = app.register(str(selected["workspace"].root))
        job = {
            "kind": "fix",
            "state": "running",
            "options": {"suite_manifest": {"digest": "required-suite"}},
            "workflow_ids": {"run_id": selected["run_id"]},
        }
        monkeypatch.setattr(app.jobs, "list", lambda _: [job])
        request = {"kind": "fix", "source_id": selected["run_id"], "publish": False}
        with pytest.raises(AuditError, match="finish this task"):
            app.deliver(project["id"], request)
        job["state"] = "completed"
        with pytest.raises(AuditError, match="no bound measurement suite"):
            app.deliver(project["id"], request)
        with pytest.raises(AuditError, match="no bound measurement suite"):
            app.control_run(
                project["id"],
                selected["run_id"],
                {"action": "select", "candidate_id": selected["candidate_id"]},
            )
    finally:
        app.close()
