"""Issue projections keep triage, verification, delivery, and production separate."""

import uuid

import pytest

from agentagon.core.records import AuditError, identifier, timestamp_ns
from agentagon.domain.improvements import deploy
from agentagon.domain.issues import lifecycle_projection, record_issue
from agentagon.workflows.service import Application


@pytest.fixture
def app(tmp_path):
    application = Application(tmp_path / "state", execute=lambda *_: {})
    yield application
    application.close()


def registered(app, root):
    project = app.register(str(root))
    scope = "app.json" if (root / "app.json").exists() else "agent.py"
    if not (root / scope).exists():
        (root / scope).write_text("def answer():\n    return 42\n")
    agent = app.save_application_agent(project["id"], {"name": "Support", "code_scopes": [scope]})
    return project["id"], agent


def test_verified_revision_and_later_predeployment_trace_do_not_imply_production_outcome(
    app, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    project, agent = registered(app, root)
    workspace = app.state.workspace(project)
    original = record_issue(
        workspace,
        key="duplicate-reply",
        title="Duplicate reply",
        summary="One request creates two replies",
        agent_id=agent["id"],
        occurrences=[
            {
                "id": "occurrence_" + "a" * 24,
                "source_id": "snapshot_" + "a" * 24,
                "trace_ids": ["trace-one"],
                "observed_ns": timestamp_ns("2026-09-01T00:00:00+00:00"),
                "basis": "trace",
            }
        ],
    )
    verification = workspace.artifact(
        {
            "provenance": "agentagon-engine",
            "run_id": "run_" + "b" * 24,
            "candidate_id": "candidate_" + "c" * 24,
            "source_revision": "tested-revision",
        }
    )
    with app.state.db.transaction() as tx:
        issue = tx.get_record(project, "issues", original["issue_id"])
        issue.update(
            status="resolved_verified",
            verification=verification,
            tested_revision="tested-revision",
            production_recovery_verified=False,
        )
        issue["history"].append(
            {
                "event_id": "event_" + "d" * 24,
                "at": "2026-09-02T00:00:00+00:00",
                "status": "resolved_verified",
                "reason": "Regression passed on the tested revision",
                "verification": verification,
            }
        )
        tx.put_record(project, "issues", issue["issue_id"], issue)

    projected = lifecycle_projection(app, project, original["issue_id"])
    assert projected["facets"]["triage"]["state"] == "resolved"
    assert projected["facets"]["test_verification"] == {
        "state": "verified",
        "tested_revision": "tested-revision",
        "verified_at": "2026-09-02T00:00:00+00:00",
        "verification": verification,
        "changes": [
            {
                "kind": "issue_verification",
                "verified_at": "2026-09-02T00:00:00+00:00",
                "tested_revision": "tested-revision",
                "verification": verification,
                "run_id": "run_" + "b" * 24,
                "candidate_id": "candidate_" + "c" * 24,
                "task_id": None,
            }
        ],
        "evidence_available": True,
    }
    assert projected["facets"]["production"]["state"] == "not_observed"

    record_issue(
        workspace,
        key="duplicate-reply",
        title="Duplicate reply",
        summary="One request creates two replies",
        agent_id=agent["id"],
        occurrences=[
            {
                "id": "occurrence_" + "e" * 24,
                "source_id": "snapshot_" + "e" * 24,
                "trace_ids": ["trace-two"],
                "observed_ns": timestamp_ns("2026-09-03T00:00:00+00:00"),
                "basis": "trace",
            }
        ],
    )
    unchanged = lifecycle_projection(app, project, original["issue_id"])
    assert unchanged["status"] == "resolved_verified"
    assert unchanged["facets"]["triage"]["disposition"] == "resolved_verified"
    assert unchanged["facets"]["test_verification"]["state"] == "verified"
    assert unchanged["facets"]["production"]["state"] == "not_observed"
    assert unchanged["facets"]["production"]["later_occurrences"] == []
    assert unchanged["next_actions"] == ["review_verified_change"]


def test_unowned_issue_keeps_its_identity_when_code_ownership_is_later_known(app, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    project, agent = registered(app, root)
    workspace = app.state.workspace(project)
    unowned = record_issue(
        workspace,
        key="wrong-recipient",
        title="Wrong recipient",
        summary="The reply is routed to another user",
        occurrences=[],
    )
    attributed = record_issue(
        workspace,
        key=" Wrong-Recipient ",
        title="Wrong recipient",
        summary="The reply is routed to another user",
        agent_id=agent["id"],
        occurrences=[],
    )

    assert attributed["issue_id"] == unowned["issue_id"]
    assert attributed["agent_id"] == agent["id"]
    assert len(app.state.db.list_records(project, "issues")) == 1


def test_user_triage_assignment_and_expectation_append_revisioned_history(app, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    project, agent = registered(app, root)
    issue = record_issue(
        app.state.workspace(project),
        key="unsupported-answer",
        title="Unsupported answer",
        summary="The answer asserts a fact that is absent from the supplied context.",
        occurrences=[],
    )

    assigned = app.update_issue(
        project,
        issue["issue_id"],
        {
            "action": "assign",
            "agent_id": agent["id"],
            "expected_revision": issue["revision"],
            "reason": "This agent owns the response path.",
        },
    )
    expected = app.update_issue(
        project,
        issue["issue_id"],
        {
            "action": "set_expectation",
            "expected_revision": assigned["revision"],
            "expected_behavior": "Answer only from supplied context, otherwise state the limitation.",
            "reason": "Reviewed against the product contract.",
        },
    )
    dismissed = app.update_issue(
        project,
        issue["issue_id"],
        {
            "action": "dismiss",
            "expected_revision": expected["revision"],
            "reason": "The retained trace was generated by an obsolete fixture.",
        },
    )
    assert dismissed["status"] == "dismissed"
    assert dismissed["expected_behavior"].startswith("Answer only")
    assert [event["action"] for event in dismissed["history"][-3:]] == [
        "assign",
        "set_expectation",
        "dismiss",
    ]
    with pytest.raises(AuditError, match="changed; reload"):
        app.update_issue(
            project,
            issue["issue_id"],
            {
                "action": "reopen",
                "expected_revision": expected["revision"],
                "reason": "Stale decision",
            },
        )
    reopened = app.update_issue(
        project,
        issue["issue_id"],
        {
            "action": "reopen",
            "expected_revision": dismissed["revision"],
            "reason": "A current production trace supports the issue.",
        },
    )
    assert reopened["status"] == "reopened"
    detail = app.issue(project, issue["issue_id"])
    assert detail["expected_behavior"] == expected["expected_behavior"]
    assert detail["facets"]["triage"]["provenance"] == "recurrence"


def test_issue_http_update_preserves_revision_conflicts_and_read_projection(app, tmp_path):
    from test_webapp import running

    root = tmp_path / "project"
    root.mkdir()
    project, agent = registered(app, root)
    issue = record_issue(
        app.state.workspace(project),
        key="missing-citation",
        title="Missing citation",
        summary="The response does not cite the supplied evidence.",
        occurrences=[],
    )

    with running(app) as (client, _server):
        response = client.post(
            f"/api/projects/{project}/issues/{issue['issue_id']}",
            json={
                "action": "assign",
                "agent_id": agent["id"],
                "expected_revision": issue["revision"],
            },
        )
        assert response.status_code == 200
        assigned = response.json()
        assert assigned["agent_id"] == agent["id"]
        assert assigned["revision"] == issue["revision"] + 1

        conflict = client.post(
            f"/api/projects/{project}/issues/{issue['issue_id']}",
            json={
                "action": "dismiss",
                "reason": "This decision is stale.",
                "expected_revision": issue["revision"],
            },
        )
        assert conflict.status_code == 400
        assert "reload" in conflict.json()["error"]

        detail = client.get(f"/api/projects/{project}/issues/{issue['issue_id']}")
        assert detail.status_code == 200
        assert detail.json()["history"][-1]["action"] == "assign"
        assert detail.json()["revision"] == assigned["revision"]


def test_selection_delivery_deployment_and_observation_come_from_separate_evidence(app, selected):
    project, agent = registered(app, selected["workspace"].root)
    workspace = app.state.workspace(project)
    issue = record_issue(
        workspace,
        key="quality-loss",
        title="Quality loss",
        summary="Answers omit required details",
        agent_id=agent["id"],
        occurrences=[],
    )
    improvement_id = identifier(
        "improvement", project, selected["run_id"], selected["candidate_id"]
    )
    app.state.db.put_record(
        project,
        "improvements",
        improvement_id,
        {
            "id": improvement_id,
            "workflow": "optimize",
            "agent_id": agent["id"],
            "task_id": "task_" + "1" * 24,
            "goal_id": None,
            "issue_id": issue["issue_id"],
            "run_id": selected["run_id"],
            "candidate_id": selected["candidate_id"],
            "tested_revision": selected["revision"],
            "evaluation_state": "verified",
            "verification": None,
            "summary": "Verified candidate",
            "lessons": [],
            "created_at": "2026-09-01T00:00:00+00:00",
        },
    )
    with app.state.db.transaction() as tx:
        saved_issue = tx.get_record(project, "issues", issue["issue_id"])
        saved_issue["status"] = "resolved_verified"
        tx.put_record(project, "issues", issue["issue_id"], saved_issue)

    before = lifecycle_projection(app, project, issue["issue_id"])
    assert before["facets"]["test_verification"]["state"] == "verified"
    assert before["facets"]["delivery"]["state"] == "not_selected"
    assert before["facets"]["production"]["state"] == "not_observed"
    recommendation = next(
        item for item in app.production.recommendations(project) if item["id"] == issue["issue_id"]
    )
    assert recommendation["action"] == "review_verified_change"

    app.decide_result(
        project,
        "optimize",
        selected["run_id"],
        {
            "operation_id": str(uuid.uuid4()),
            "expected_revision": 0,
            "decision": "select_candidate",
            "candidate_id": selected["candidate_id"],
        },
    )
    chosen = lifecycle_projection(app, project, issue["issue_id"])
    assert chosen["facets"]["delivery"]["state"] == "selected"
    recommendation = next(
        item for item in app.production.recommendations(project) if item["id"] == issue["issue_id"]
    )
    assert recommendation["action"] == "prepare_local_delivery"

    app.deliver(
        project,
        {"kind": "optimize", "source_id": selected["run_id"], "publish": False},
    )
    prepared = lifecycle_projection(app, project, issue["issue_id"])
    assert prepared["facets"]["delivery"]["state"] == "prepared_locally"
    assert prepared["facets"]["production"]["state"] == "not_observed"
    recommendation = next(
        item for item in app.production.recommendations(project) if item["id"] == issue["issue_id"]
    )
    assert recommendation["action"] == "record_deployment"

    record_issue(
        workspace,
        key="quality-loss",
        title="Quality loss",
        summary="Answers omit required details",
        agent_id=agent["id"],
        occurrences=[
            {
                "id": "occurrence_" + "6" * 24,
                "source_id": "snapshot_" + "6" * 24,
                "trace_ids": ["before-deployment"],
                "observed_ns": timestamp_ns("2026-09-05T00:00:00+00:00"),
                "environment": "production",
                "basis": "trace",
            }
        ],
    )

    deployment = deploy(
        app,
        project,
        {
            "operation_id": str(uuid.uuid4()),
            "improvement_id": improvement_id,
            "release": "2026.09.1",
            "environment": "production",
            "revision": selected["revision"],
            "deployed_at": "2026-09-10T00:00:00+00:00",
        },
    )
    recommendation = next(
        item for item in app.production.recommendations(project) if item["id"] == issue["issue_id"]
    )
    assert recommendation["action"] == "observe_production"
    observation_id = "observation_" + "2" * 24
    app.state.db.put_record(
        project,
        "observations",
        observation_id,
        {
            "id": observation_id,
            "monitor_id": "monitor_" + "3" * 24,
            "agent_id": agent["id"],
            "series": 1,
            "window": {
                "start": "2026-09-10T00:00:00+00:00",
                "end": "2026-09-12T00:00:00+00:00",
            },
            "task_id": "task_" + "4" * 24,
            "evidence": ".agentagon/evidence/production.json",
            "coverage": {"complete": True},
            "limitations": [],
            "issue_ids": [],
            "metrics": [
                {
                    "name": "Issue recurrence",
                    "definition": {
                        "metric": "issue_recurrence",
                        "issue_id": issue["issue_id"],
                    },
                    "status": "improved",
                    "current": 0,
                    "deployment_id": deployment["id"],
                    "causal": False,
                }
            ],
        },
    )
    observed = lifecycle_projection(app, project, issue["issue_id"])
    assert observed["facets"]["production"]["state"] == "recovery_supported"
    assert observed["facets"]["production"]["observational"] is True
    assert observed["facets"]["production"]["deployment_id"] == deployment["id"]
    assert observed["facets"]["test_verification"]["tested_revision"] == selected["revision"]
    assert not any(
        item["id"] == issue["issue_id"] for item in app.production.recommendations(project)
    )

    record_issue(
        workspace,
        key="quality-loss",
        title="Quality loss",
        summary="Answers omit required details",
        agent_id=agent["id"],
        occurrences=[
            {
                "id": "occurrence_" + "7" * 24,
                "source_id": "snapshot_" + "7" * 24,
                "trace_ids": ["other-environment"],
                "observed_ns": timestamp_ns("2026-09-13T00:00:00+00:00"),
                "environment": "staging",
                "basis": "trace",
            }
        ],
    )
    still_recovered = lifecycle_projection(app, project, issue["issue_id"])
    assert still_recovered["facets"]["production"]["state"] == "recovery_supported"

    record_issue(
        workspace,
        key="quality-loss",
        title="Quality loss",
        summary="Answers omit required details",
        agent_id=agent["id"],
        occurrences=[
            {
                "id": "occurrence_" + "8" * 24,
                "source_id": "snapshot_" + "8" * 24,
                "trace_ids": ["post-observation"],
                "observed_ns": timestamp_ns("2026-09-13T00:00:00+00:00"),
                "environment": "production",
                "basis": "trace",
            }
        ],
    )
    recurrent = lifecycle_projection(app, project, issue["issue_id"])
    assert recurrent["status"] == "resolved_verified"
    assert recurrent["facets"]["production"]["state"] == "recurring"
    assert recurrent["facets"]["production"]["later_occurrences"][0]["trace_ids"] == [
        "post-observation"
    ]
    recommendation = next(
        item for item in app.production.recommendations(project) if item["id"] == issue["issue_id"]
    )
    assert recommendation["action"] == "investigate_recurrence"


def test_occurrence_identity_keeps_one_failure_and_all_evidence_revisions(app, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    project, agent = registered(app, root)
    workspace = app.state.workspace(project)
    occurrence_id = "occurrence_" + "5" * 24
    for source, evidence in (("snapshot_" + "6" * 24, "first"), ("snapshot_" + "7" * 24, "second")):
        record_issue(
            workspace,
            key="same-failure",
            title="Same failure",
            summary="Updated trace evidence for one runtime occurrence",
            agent_id=agent["id"],
            occurrences=[
                {
                    "id": occurrence_id,
                    "source_id": source,
                    "trace_ids": ["stable-provider-trace"],
                    "observed_ns": timestamp_ns("2026-09-01T00:00:00+00:00"),
                    "basis": "trace",
                    "evidence": evidence,
                }
            ],
        )
    projection = lifecycle_projection(
        app,
        project,
        record_issue(
            workspace,
            key="same-failure",
            title="Same failure",
            summary="Updated trace evidence for one runtime occurrence",
            agent_id=agent["id"],
            occurrences=[],
        )["issue_id"],
    )
    assert projection["historical_affected_traces"] == 1
    assert len(projection["occurrences"]) == 1
    assert len(projection["occurrences"][0]["source_ids"]) == 2
    assert projection["occurrences"][0]["evidence_versions"] == ["first", "second"]
