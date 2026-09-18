"""Reviewed measurement proposals are immutable inputs, never implicit executions."""

import copy
import json
import uuid

import pytest
from test_baselines import frozen
from test_scoring import definition
from test_webapp import project, running
from test_webapp_catalog import agent, focus
from test_webapp_jobs import wait_for

from agentagon.core.records import AuditError
from agentagon.webapp.designs import Designs, score_definition, validate
from agentagon.webapp.service import Application


@pytest.fixture
def app(tmp_path):
    result = Application(tmp_path / "state", execute=lambda *_: {})
    yield result
    result.close()


def proposal(**values):
    score = definition()
    return {
        "expected_revision": 0,
        "metrics": score["metrics"],
        "behaviors": score["behaviors"],
        "scoring": {"mode": "weighted"},
        "evaluation": {"mode": "create", "framework": "custom"},
        "evidence": ["app.py:1"],
        "background": ["Preserve answer quality while reducing unnecessary calls."],
        "limitations": ["Production traces are not connected."],
        **values,
    }


def test_background_is_observation_list_with_legacy_text_compatibility():
    canonical = proposal()
    canonical.pop("expected_revision")
    assert validate(canonical)["background"] == canonical["background"]

    legacy = copy.deepcopy(canonical)
    legacy["background"] = "Preserve the existing behavior."
    assert validate(legacy)["background"] == ["Preserve the existing behavior."]

    optimization = json.loads(
        Designs.background(
            {
                **canonical,
                "digest": "design-digest",
                "background": ["First observation.", "Second observation."],
            },
            "Improve efficiency.",
        )
    )
    assert optimization["context"] == "First observation.\n\nSecond observation."

    invalid = copy.deepcopy(canonical)
    invalid["background"] = {"observation": "not a list"}
    with pytest.raises(AuditError, match="background must be a bounded list"):
        validate(invalid)

    oversized = copy.deepcopy(canonical)
    oversized["background"] = ["x" * 4000 for _ in range(17)]
    with pytest.raises(AuditError, match="background must be within 64 KB"):
        validate(oversized)


def setup(app, tmp_path):
    saved = project(app, tmp_path)
    application = agent(app, saved)
    goal = focus(app, saved, application)
    return saved["id"], application["id"], goal["id"]


def test_acceptance_is_explicit_versioned_and_keeps_original_score_and_gates(app, tmp_path):
    ids = setup(app, tmp_path)
    draft = app.designs.save(*ids, proposal())
    assert app.designs.accepted(*ids) is None
    assert draft["native_plan"]["state"] == "draft"
    accepted = app.designs.accept(*ids, {"expected_revision": draft["revision"]})
    assert accepted["state"] == "accepted"
    assert score_definition(accepted) == {
        key: value for key, value in definition().items() if key not in {"version", "source_paths"}
    }
    changed = proposal(expected_revision=app.designs.get(*ids)["revision"])
    changed["metrics"]["latency"]["weight"] = 4
    draft2 = app.designs.save(*ids, changed)
    assert app.designs.accepted(*ids) == accepted
    next_version = app.designs.accept(*ids, {"expected_revision": draft2["revision"]})
    assert next_version["digest"] != accepted["digest"]
    assert app.state.db.get_record(ids[0], "accepted_designs", accepted["id"]) == accepted
    assert len(app.state.db.list_records(ids[0], "accepted_designs")) == 2


def test_stale_writes_scope_and_source_changes_cannot_be_accepted(app, tmp_path):
    ids = setup(app, tmp_path)
    root = app.state.workspace(ids[0]).root
    (root / "eval.py").write_text("def evaluate(): pass\n")
    value = proposal(evaluation={"mode": "reuse", "framework": "custom", "entrypoint": "eval.py"})
    draft = app.designs.save(*ids, value)
    with pytest.raises(AuditError, match="changed|revision"):
        app.designs.save(*ids, value)
    (root / "eval.py").write_text("def evaluate(): return 1\n")
    with pytest.raises(AuditError, match="source changed"):
        app.designs.accept(*ids, {"expected_revision": draft["revision"]})
    value["expected_revision"] = draft["revision"]
    draft = app.designs.save(*ids, value)
    app.save_application_agent(ids[0], {"code_scopes": ["eval.py"]}, ids[1])
    with pytest.raises(AuditError, match="scope changed"):
        app.designs.accept(*ids, {"expected_revision": draft["revision"]})


def test_design_identity_is_project_and_agent_scoped(app, tmp_path):
    ids = setup(app, tmp_path)
    draft = app.designs.save(*ids, proposal())
    second = project(app, tmp_path, "second")
    other = agent(app, {"id": ids[0]}, name="Research")
    for values in ((second["id"], ids[1], ids[2]), (ids[0], other["id"], ids[2])):
        with pytest.raises(AuditError, match="not found"):
            app.designs.accept(*values, {"expected_revision": draft["revision"]})


def test_existing_frozen_evaluation_cannot_silently_change_scoring(app, application, specification):
    evaluation = frozen(application, specification)
    saved = app.register(str(application.root))
    item = agent(app, saved, code_scopes=["app.json"])
    goal = focus(app, saved, item)
    ids = saved["id"], item["id"], goal["id"]
    value = proposal(evaluation={"mode": "reuse", "evaluation_id": evaluation["evaluation_id"]})
    draft = app.designs.save(*ids, value)
    accepted = app.designs.accept(*ids, {"expected_revision": draft["revision"]})
    app.designs.validate_evaluator(application, accepted, evaluation["evaluation_id"])
    value["expected_revision"] = app.designs.get(*ids)["revision"]
    value["metrics"]["quality"]["weight"] = 10
    with pytest.raises(AuditError, match="new evaluator"):
        app.designs.save(*ids, value)
    changed = copy.deepcopy(accepted)
    changed["behaviors"][0]["bound"] = 0.9
    with pytest.raises(AuditError, match="differs"):
        app.designs.validate_evaluator(application, changed, evaluation["evaluation_id"])


def test_design_job_proposes_once_without_acceptance_or_execution_profile(app, tmp_path):
    ids = setup(app, tmp_path)
    calls = []

    def host(request, emit, ask, cancelled):
        calls.append(request)
        assert request["sandbox"] == "read-only"
        emit({"type": "session", "session_id": "proposal-session", "model": "fixture"})
        value = proposal()
        value.pop("expected_revision")
        return {"state": "completed", "text": json.dumps({"measurement_design": value})}

    app.jobs.execute = host
    payload = {
        "operation_id": str(uuid.uuid4()),
        "kind": "design",
        "application_agent_id": ids[1],
        "focus_id": ids[2],
    }
    submitted = app.submit_job(ids[0], payload)
    result = wait_for(app.jobs, ids[0], submitted["id"])
    assert result["state"] == "completed", result
    assert result["result"]["design_id"] == app.designs.get(*ids)["id"]
    assert app.designs.accepted(*ids) is None
    assert app.submit_job(ids[0], payload)["id"] == submitted["id"]
    assert len(calls) == 1
    with pytest.raises(AuditError, match="prepared by the application"):
        app.submit_job(
            ids[0],
            {**payload, "operation_id": str(uuid.uuid4()), "options": {"design_context": {}}},
        )


def test_design_http_reads_are_passive_and_accept_requires_current_revision(app, tmp_path):
    ids = setup(app, tmp_path)
    root = app.state.workspace(ids[0]).root
    (root / "test_eval.py").write_text("def test_response(): pass\n")
    path = f"/api/projects/{ids[0]}/application-agents/{ids[1]}/focuses/{ids[2]}/design"
    with running(app) as (client, server):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["draft"] is None
        assert any(e["entrypoint"] == "test_eval.py" for e in response.json()["evaluators"])
        assert app.jobs.list(ids[0]) == []
        saved = client.post(path, json=proposal())
        assert saved.status_code == 200, saved.text
        accepted = client.post(
            path + "/accept", json={"expected_revision": saved.json()["revision"]}
        )
        assert accepted.status_code == 200, accepted.text
        assert client.post(path + "/accept", json={"expected_revision": 0}).status_code == 400
        assert (
            client.post(path, json=proposal(), headers={"X-Agentagon-Token": "wrong"}).status_code
            == 403
        )


def _native_design_ids(app, application):
    saved = app.register(str(application.root))
    item = agent(app, saved, code_scopes=["app.json"])
    goal = focus(app, saved, item)
    return saved["id"], item["id"], goal["id"]


def test_frozen_reuse_rejects_mutable_native_choices_and_wrong_evaluator(
    app, application, specification
):
    first = frozen(application, specification)
    second = frozen(application, specification)
    ids = _native_design_ids(app, application)
    for choice in (
        {"mode": "create"},
        {"mode": "reuse", "command": {"argv": ["python", "other.py"], "cwd": "."}},
        {"mode": "reuse", "scorer": "a different scoring implementation"},
        {"mode": "reuse", "output_mapping": {"quality": "different.result"}},
    ):
        with pytest.raises(AuditError, match="Frozen reuse"):
            app.designs.save(
                *ids, proposal(evaluation={**choice, "evaluation_id": first["evaluation_id"]})
            )
    draft = app.designs.save(
        *ids, proposal(evaluation={"mode": "reuse", "evaluation_id": first["evaluation_id"]})
    )
    accepted = app.designs.accept(*ids, {"expected_revision": draft["revision"]})
    with pytest.raises(AuditError, match="Frozen evaluator differs"):
        app.designs.validate_evaluator(application, accepted, second["evaluation_id"])
    changed = copy.deepcopy(accepted)
    changed["native_plan"]["evaluator_digest"] = "f" * 64
    with pytest.raises(AuditError, match="Frozen evaluator differs"):
        app.designs.validate_evaluator(application, changed, first["evaluation_id"])


def test_unscored_frozen_reuse_gives_actionable_error(app, application, specification):
    from support.evaluation import draft, review_for

    from agentagon.experiments import preparation

    started, plan = draft(application, specification)
    checked = preparation.check(application, started["evaluation_id"], plan)
    evaluation = preparation.freeze(application, started["evaluation_id"], review_for(checked))
    ids = _native_design_ids(app, application)
    with pytest.raises(AuditError, match="Scoring changes require a new evaluator"):
        app.designs.save(
            *ids,
            proposal(evaluation={"mode": "reuse", "evaluation_id": evaluation["evaluation_id"]}),
        )


@pytest.mark.parametrize("input_state", ["missing", "altered", "exact"])
def test_created_evaluator_requires_exact_reviewed_private_dataset(
    app, application, specification, input_state
):
    from support.evaluation import draft, review_for
    from test_webapp_evaluators import dataset

    from agentagon.experiments import preparation
    from agentagon.webapp import snapshots

    ids = _native_design_ids(app, application)
    workspace = app.state.workspace(ids[0])
    imported = dataset(workspace)
    value = proposal(
        evaluation={"mode": "create", "framework": "custom", "dataset_snapshot_id": imported["id"]}
    )
    saved = app.designs.save(*ids, value)
    accepted = app.designs.accept(*ids, {"expected_revision": saved["revision"]})
    specification["scoring"] = definition()
    started, plan = draft(workspace, specification)
    if input_state != "missing":
        materialized = snapshots.materialize(workspace, imported["id"], started["evaluation_id"])
        plan["spec"].setdefault("inputs", []).append(materialized["input"])
        if input_state == "altered":
            content = snapshots.input_payload(imported)
            content = {**content, "items": [{**content["items"][0], "expected": "invented label"}]}
            workspace.write(workspace.root / materialized["input"]["source"], content)
    checked = preparation.check(workspace, started["evaluation_id"], plan)
    evaluation = preparation.freeze(workspace, started["evaluation_id"], review_for(checked))
    if input_state == "exact":
        app.designs.validate_evaluator(workspace, accepted, evaluation["evaluation_id"])
    else:
        with pytest.raises(AuditError, match="accepted dataset|reviewed snapshot"):
            app.designs.validate_evaluator(workspace, accepted, evaluation["evaluation_id"])
    with pytest.raises(AuditError, match="Frozen reuse"):
        app.designs.save(
            *ids,
            proposal(
                expected_revision=app.designs.get(*ids)["revision"],
                evaluation={
                    "mode": "reuse",
                    "evaluation_id": evaluation["evaluation_id"],
                    "dataset_snapshot_id": imported["id"],
                },
            ),
        )


def test_accepting_new_scoring_removes_old_measurement_from_ready_suite(
    app, application, specification
):
    from test_baselines import complete

    from agentagon.experiments import baselines

    specification["repetitions"] = 1
    evaluation = frozen(application, specification)
    baseline = complete(application, baselines.start(application, evaluation["evaluation_id"]))
    ids = _native_design_ids(app, application)
    draft = app.designs.save(
        *ids, proposal(evaluation={"mode": "reuse", "evaluation_id": evaluation["evaluation_id"]})
    )
    app.designs.accept(*ids, {"expected_revision": draft["revision"]})
    app.catalog.bind_measurement(
        *ids, {"evaluation_id": evaluation["evaluation_id"], "baseline_id": baseline["baseline_id"]}
    )
    assert app.catalog.measurement_status(ids[0], ids[1], app.catalog.focus(*ids))["baseline"][
        "ready"
    ]
    changed = proposal(expected_revision=app.designs.get(*ids)["revision"])
    changed["metrics"]["quality"]["weight"] = 5
    draft = app.designs.save(*ids, changed)
    app.designs.accept(*ids, {"expected_revision": draft["revision"]})
    current = app.catalog.focus(*ids)
    assert current["measurement"]["baseline_id"] == baseline["baseline_id"]
    readiness = app.catalog.measurement_status(ids[0], ids[1], current)
    assert not readiness["evaluation"]["ready"] and not readiness["baseline"]["ready"]
    assert "accepted measurement design" in readiness["evaluation"]["reason"]
    suite = app.catalog.suite(*ids)
    assert suite["members"] == [] and suite["missing"][0]["focus_id"] == ids[2]


def test_accepting_frozen_b_runs_its_baseline_and_keeps_a_history(app, application, specification):
    from support.experiments import passing_review
    from test_baselines import complete
    from test_webapp_jobs import manifest

    from agentagon.experiments import baselines
    from agentagon.experiments.host_bridge import HostBridge

    specification["repetitions"] = 1
    first = frozen(application, specification)
    second = frozen(application, specification)
    baseline_a = complete(application, baselines.start(application, first["evaluation_id"]))
    ids = _native_design_ids(app, application)
    draft = app.designs.save(
        *ids, proposal(evaluation={"mode": "reuse", "evaluation_id": first["evaluation_id"]})
    )
    app.designs.accept(*ids, {"expected_revision": draft["revision"]})
    initially_ready = app.catalog.measurement_status(ids[0], ids[1], app.catalog.focus(*ids))
    assert initially_ready["evaluation"]["ready"] and not initially_ready["baseline"]["ready"]
    app.catalog.bind_measurement(
        *ids, {"evaluation_id": first["evaluation_id"], "baseline_id": baseline_a["baseline_id"]}
    )
    draft = app.designs.save(
        *ids,
        proposal(
            expected_revision=app.designs.get(*ids)["revision"],
            evaluation={"mode": "reuse", "evaluation_id": second["evaluation_id"]},
        ),
    )
    app.designs.accept(*ids, {"expected_revision": draft["revision"]})
    current = app.catalog.focus(*ids)
    assert current["measurement"]["evaluation_id"] == first["evaluation_id"]
    ready = app.catalog.measurement_status(ids[0], ids[1], current)
    assert ready["evaluation"]["ready"] and not ready["baseline"]["ready"]
    request = {
        "operation_id": str(uuid.uuid4()),
        "kind": "baseline",
        "application_agent_id": ids[1],
        "focus_id": ids[2],
        "options": {"evaluation_id": second["evaluation_id"]},
    }
    with pytest.raises(AuditError, match="baseline.*accepted evaluator"):
        app.submit_job(ids[0], {**request, "kind": "fix"})
    with pytest.raises(AuditError, match="baseline does not measure"):
        app.submit_job(
            ids[0],
            {
                **request,
                "options": {**request["options"], "baseline_id": baseline_a["baseline_id"]},
            },
        )

    def host(options, emit, ask, cancelled):
        reviewing = options["sandbox"] == "read-only"
        session = "baseline-b-reviewer" if reviewing else "baseline-b-author"
        emit({"type": "session", "session_id": session, "model": "fixture"})
        job = manifest(options)
        if reviewing:
            task = job["review_tasks"][job["active_review_id"]]
            output = {
                "review": passing_review({"review_template": task["template"]}, reviewer=session)
            }
        else:
            baseline_id = job["workflow_ids"]["baseline_id"]
            result = baselines.advance(application, baseline_id)
            output = {"baseline_id": baseline_id}
            if result["state"] == "host_pending":
                owner = result["budget_id"]
                pending = HostBridge(application, owner).pending()[0]
                output["needs_review"] = {"owner_id": owner, "request_id": pending["request_id"]}
        return {"state": "completed", "session_id": session, "text": json.dumps(output)}

    app.jobs.execute = host
    submitted = app.submit_job(ids[0], request)
    finished = wait_for(app.jobs, ids[0], submitted["id"], timeout=20)
    assert finished["state"] == "completed", finished
    measurement = app.catalog.focus(*ids)["measurement"]
    assert measurement["evaluation_id"] == second["evaluation_id"]
    assert app.catalog.measurement_status(ids[0], ids[1], app.catalog.focus(*ids))["baseline"][
        "ready"
    ]
    assert (
        baselines.status(application, baseline_a["baseline_id"])["evaluation_id"]
        == first["evaluation_id"]
    )
    assert any(
        record["definition"].get("measurement", {}).get("baseline_id") == baseline_a["baseline_id"]
        for record in app.state.db.list_records(ids[0], "focus_versions")
    )


@pytest.mark.parametrize("mode", ["create", "reuse"])
def test_new_native_plan_cannot_reuse_a_predating_evaluator_with_identical_scoring(
    app, application, specification, monkeypatch, mode
):
    from test_baselines import complete

    from agentagon.experiments import baselines

    specification["repetitions"] = 1
    previous = frozen(application, specification)
    baseline = complete(application, baselines.start(application, previous["evaluation_id"]))
    ids = _native_design_ids(app, application)
    app.catalog.bind_measurement(
        *ids, {"evaluation_id": previous["evaluation_id"], "baseline_id": baseline["baseline_id"]}
    )
    saved = app.designs.save(*ids, proposal(evaluation={"mode": mode, "framework": "custom"}))
    accepted = app.designs.accept(*ids, {"expected_revision": saved["revision"]})
    with pytest.raises(AuditError, match="prepared after its acceptance"):
        app.designs.validate_evaluator(application, accepted, previous["evaluation_id"])
    status = app.catalog.measurement_status(ids[0], ids[1], app.catalog.focus(*ids))
    assert not status["evaluation"]["ready"] and not status["baseline"]["ready"]
    for kind in ("baseline", "fix"):
        with pytest.raises(AuditError, match="prepared after its acceptance"):
            app.submit_job(
                ids[0],
                {
                    "operation_id": str(uuid.uuid4()),
                    "kind": kind,
                    "application_agent_id": ids[1],
                    "focus_id": ids[2],
                },
            )
    monkeypatch.setattr(app.jobs, "_dispatch", lambda: None)
    task = app.submit_job(
        ids[0],
        {
            "operation_id": str(uuid.uuid4()),
            "kind": "eval",
            "application_agent_id": ids[1],
            "focus_id": ids[2],
            "options": {"profile": "local"},
        },
    )
    assert task["state"] == "queued" and not task["options"].get("evaluation_id")
    fresh = frozen(application, specification)
    app.designs.validate_evaluator(application, accepted, fresh["evaluation_id"])
