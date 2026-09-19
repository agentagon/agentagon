"""Immutable dataset preparation, family leakage prevention and final-use claims."""

import copy
import uuid

import pytest
from click.testing import CliRunner

from agentagon.capabilities.evaluation import datasets
from agentagon.capabilities.traces import snapshots
from agentagon.cli.internal import main
from agentagon.core.records import AuditError, digest
from agentagon.storage.state import AppState
from agentagon.workflows.runtime import TaskRuntime


@pytest.fixture
def registered(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    state = AppState(tmp_path / "state")
    project = state.register(str(root))
    workspace = state.workspace(project["id"])
    workspace.initialize()
    return state, project["id"], workspace


def imported(workspace, project, items, kind="dataset"):
    return snapshots.save(
        workspace,
        project,
        {
            "kind": kind,
            "connection_id": "connection_" + "a" * 24,
            "selection": {"project": "remote"},
            "provenance": {"provider": "braintrust"},
            "completeness": {"complete": True, "count": len(items)},
            "items": items,
        },
    )


def example(name, family, input=None, expected="accepted"):
    return {
        "id": name,
        "input": input or {"task": name},
        "expected": expected,
        "expected_present": expected is not None,
        "metadata": {"task_family": family},
    }


def test_derive_preserves_structured_inputs_but_never_promotes_observed_answers(registered):
    state, project, workspace = registered
    turns = {
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "context"},
            {"role": "user", "content": "follow-up"},
        ]
    }
    root = {
        "span_id": "root",
        "root_span_id": "root",
        "span_parents": [],
        "metrics": {"start": 100, "end": 101},
        "metadata": {"session_id": "conversation-one"},
        "input": turns,
        "output": {"answer": "observed, not reviewed"},
    }
    child = {**root, "span_id": "child", "span_parents": ["root"], "input": "tool input"}
    source = imported(workspace, project, [root, child], "traces")
    result = datasets.derive(workspace, project, source["id"], {})
    saved = snapshots.load(workspace, result["id"])
    assert saved["state"] == "draft" and result["missing_expectations"] == 1
    assert saved["items"][0]["input"] == turns
    assert saved["items"][0]["observed_output"] == root["output"]
    assert saved["items"][0]["expected"] is None
    assert saved["items"][0]["expected_present"] is False
    assert saved["items"][0]["metadata"]["conversation_id"] == "conversation-one"
    assert datasets.derive(workspace, project, source["id"], {}) == result
    assert result in snapshots.list_snapshots(workspace)
    assert snapshots.load(workspace, source["id"])["items"] == [root, child]


def test_split_deduplicates_and_merges_families_before_assigning_partitions(registered):
    state, project, workspace = registered
    rows = [
        example("a", "family-a"),
        example("shared-a", "family-a", {"query": "same"}),
        example("shared-b", "family-b", {"query": "same"}),
        example("b", "family-b"),
        example("c", "family-c"),
    ]
    source = imported(workspace, project, rows)
    result = datasets.split(state, project, source["id"], {"holdout_fraction": 0.5})
    assert result["counts"] == {
        "source": 5,
        "development": result["counts"]["development"],
        "holdout": result["counts"]["holdout"],
        "families": 2,
        "development_families": 1,
        "holdout_families": 1,
        "duplicates_removed": 1,
        "missing_expectations": 0,
        "conflicting_expectations": 0,
    }
    development = snapshots.load(workspace, result["development_snapshot_id"])["items"]
    final = snapshots.load(workspace, result["holdout_snapshot_id"])["items"]
    assert not {digest(row["input"]) for row in development} & {
        digest(row["input"]) for row in final
    }
    assert sum(len(items) for items in [development, final]) == 4
    same_partition = next(
        items for items in [development, final] if any(row["id"] == "a" for row in items)
    )
    assert any(row["id"] == "b" for row in same_partition)
    assert snapshots.load(workspace, source["id"])["items"] == rows
    assert datasets.split(state, project, source["id"], {"holdout_fraction": 0.5}) == result
    with pytest.raises(AuditError, match="already has a frozen split"):
        datasets.split(state, project, source["id"], {"holdout_fraction": 0.2})


def test_missing_and_conflicting_labels_remain_draft(registered):
    state, project, workspace = registered
    source = imported(
        workspace,
        project,
        [
            example("a", "a", {"task": "duplicate"}, "one"),
            example("b", "b", {"task": "duplicate"}, "two"),
            example("c", "c", expected=None),
        ],
    )
    result = datasets.split(state, project, source["id"], {})
    assert result["state"] == "draft"
    assert result["counts"]["missing_expectations"] == 2
    assert result["counts"]["conflicting_expectations"] == 1
    for key in ("development_snapshot_id", "holdout_snapshot_id"):
        assert all(
            not row["expected_present"] for row in snapshots.load(workspace, result[key])["items"]
        )


def test_split_requires_two_known_independent_families(registered):
    state, project, workspace = registered
    source = imported(
        workspace, project, [example("a", "a", {"same": True}), example("b", "b", {"same": True})]
    )
    with pytest.raises(AuditError, match="at least two independent families"):
        datasets.split(state, project, source["id"], {})
    rows = [example("a", "a"), example("b", "b")]
    rows[1]["metadata"] = {}
    source = imported(workspace, project, rows)
    with pytest.raises(AuditError, match="label every example"):
        datasets.split(state, project, source["id"], {})
    assert state.db.list_records(project, "dataset_splits") == []


def test_conversation_aliases_and_custom_family_key_do_not_leak(registered):
    state, project, workspace = registered
    rows = [example("a", "a"), example("b", "b"), example("c", "c")]
    rows[0]["metadata"] = {"conversation_id": "shared", "scenario": "a"}
    rows[1]["metadata"] = {"thread_id": "shared", "scenario": "b"}
    rows[2]["metadata"] = {"conversation_id": "separate", "scenario": "c"}
    source = imported(workspace, project, rows)
    result = datasets.split(state, project, source["id"], {"group_key": "scenario"})
    assert result["counts"]["families"] == 2
    for key in ("development_snapshot_id", "holdout_snapshot_id"):
        ids = {row["id"] for row in snapshots.load(workspace, result[key])["items"]}
        assert ("a" in ids) == ("b" in ids)


def test_final_partition_rejected_by_development_tools_and_other_project(registered, tmp_path):
    state, project, workspace = registered
    source = imported(workspace, project, [example("a", "a"), example("b", "b")])
    result = datasets.split(state, project, source["id"], {})
    final = result["holdout_snapshot_id"]
    with pytest.raises(AuditError, match="explicitly bound final"):
        snapshots.materialize(workspace, final, "eval_" + "c" * 24)
    cli = CliRunner().invoke(
        main, ["--workspace", str(workspace.root), "dataset", "inspect", final]
    )
    assert cli.exit_code != 0 and "reserved" in cli.output
    manager = TaskRuntime(state, None, execute=lambda *_: pytest.fail("host should not run"))
    try:
        with pytest.raises(AuditError, match="final verification"):
            manager.submit(
                project,
                {
                    "operation_id": str(uuid.uuid4()),
                    "kind": "audit",
                    "goal": "No exposure",
                    "options": {"dataset_snapshot_id": final},
                },
            )
        assert manager.list(project) == []
    finally:
        manager.close()
    other = tmp_path / "other"
    other.mkdir()
    second = state.register(str(other))
    assert state.db.list_records(second["id"], "dataset_splits") == []
    with pytest.raises(AuditError, match="not found"):
        datasets._load_split(state, second["id"], result["id"])
    assert result["exposure_state"] == "unclaimed"
    assert any("not a sealed" in line for line in result["limitations"])
    from agentagon.storage.workspace import Workspace

    with pytest.raises(AuditError, match="select its development snapshot"):
        snapshots.materialize(Workspace(workspace.root), source["id"], "eval_" + "c" * 24)
    full_parent = CliRunner().invoke(
        main, ["--workspace", str(workspace.root), "dataset", "inspect", source["id"]]
    )
    assert full_parent.exit_code != 0 and "development snapshot" in full_parent.output


def test_final_claim_requires_actual_suite_binding_and_reviewed_rule_then_one_destination(
    application, specification, tmp_path, monkeypatch
):
    from test_baselines import frozen

    from agentagon.capabilities.experiments import engine, preparation, suites
    from agentagon.capabilities.experiments.store import load_run

    evaluation = frozen(application, specification)
    state = AppState(tmp_path / "app")
    project = state.register(str(application.root))["id"]
    workspace = state.workspace(project)
    source = imported(
        workspace, project, [example("a", "a", expected=None), example("b", "b", expected=None)]
    )
    partition = datasets.split(state, project, source["id"], {})
    run_id = engine.start(
        application,
        preparation.fix_spec(application, evaluation["evaluation_id"], reuse=True),
        "local",
    )["run_id"]
    run = load_run(application, run_id)
    candidate = run["candidates"][run["baseline_id"]]
    binding = {
        "run_id": run_id,
        "candidate_id": candidate["candidate_id"],
        "manifest_digest": "a" * 64,
        "source_revision": candidate["source_revision"],
        "source_digest": candidate["source_digest"],
    }
    rule = {"correctness_evaluation_id": evaluation["evaluation_id"]}
    with pytest.raises(AuditError, match="no bound measurement suite"):
        datasets.claim_final(state, project, partition["id"], binding, **rule)
    assert state.db.get_record(project, "dataset_splits", partition["id"])["final_claim"] is None
    retained_binding = copy.deepcopy(binding)
    monkeypatch.setattr(suites, "status", lambda *_: {"binding": retained_binding})
    bad = {**binding, "manifest_digest": "b" * 64}
    with pytest.raises(AuditError, match="does not match the retained suite"):
        datasets.claim_final(state, project, partition["id"], bad, **rule)
    claimed = datasets.claim_final(state, project, partition["id"], binding, **rule)
    assert claimed["state"] == "draft"  # The accepted evaluator does not invent labels.
    assert claimed["exposure_state"] == "claimed"
    assert claimed["final_claim"]["binding"][
        "correctness_evaluator_digest"
    ] == preparation.evaluator_identity(workspace, evaluation["evaluation_id"])
    assert datasets.claim_final(state, project, partition["id"], binding, **rule) == claimed
    retained_binding.update(manifest_digest="b" * 64)
    with pytest.raises(AuditError, match="already exposed"):
        datasets.claim_final(state, project, partition["id"], bad, **rule)
    retained_binding.update(binding)
    draft = preparation.start(
        application,
        "local",
        {"max_trials": 10, "max_elapsed_seconds": 60, "trial_timeout_seconds": 10},
        goal="Separate final verification evaluator",
        author="author",
    )
    copied = datasets.materialize_final(
        state, project, partition["id"], binding, draft["evaluation_id"], **rule
    )
    assert copied["final_verification"] and copied["private"]
    assert (workspace.root / copied["input"]["source"]).is_file()
    with pytest.raises(AuditError, match="cannot be used in development search"):
        datasets.assert_development_evaluator(workspace, draft["evaluation_id"])
    assert (
        datasets.materialize_final(
            state, project, partition["id"], binding, draft["evaluation_id"], **rule
        )["path"]
        == copied["path"]
    )
    other_draft = preparation.start(
        application,
        "local",
        {"max_trials": 10, "max_elapsed_seconds": 60, "trial_timeout_seconds": 10},
        goal="Do not expose again",
        author="other",
    )
    with pytest.raises(AuditError, match="another verification evaluator"):
        datasets.materialize_final(
            state, project, partition["id"], binding, other_draft["evaluation_id"], **rule
        )


def test_http_derivation_and_split_require_session_and_hide_reserved_payloads(registered):
    from test_webapp import running

    from agentagon.workflows.service import Application

    state, project, workspace = registered
    (workspace.root / "app.py").write_text("def agent(): return 'test'\n")
    app = Application(
        state.directory, execute=lambda *_: pytest.fail("HTTP reads must not start a host")
    )
    agent = app.save_application_agent(project, {"name": "Support", "code_scopes": ["app.py"]})
    rows = [
        {
            "span_id": f"root-{index}",
            "root_span_id": f"root-{index}",
            "span_parents": [],
            "metrics": {"start": 100 + index, "end": 101 + index},
            "metadata": {"session_id": f"conversation-{index}"},
            "input": {"question": f"task-{index}"},
            "output": "observed",
        }
        for index in range(3)
    ]
    source = imported(workspace, project, rows, "traces")
    base = f"/api/projects/{project}"
    payload = {
        "trace_snapshot_id": source["id"],
        "application_agent_id": agent["id"],
        "selection": {},
    }
    with running(app) as (client, _):
        token = client.headers.pop("X-Agentagon-Token")
        assert client.post(base + "/datasets/derive", json=payload).status_code == 403
        assert not any(s["kind"] == "dataset" for s in snapshots.list_snapshots(workspace))
        client.headers["X-Agentagon-Token"] = token
        response = client.post(base + "/datasets/derive", json=payload)
        assert response.status_code == 200, response.text
        parent = response.json()["id"]
        initial = client.get(base + f"/results/dataset/{parent}").json()
        assert len(initial["items"]) == 3
        assert all(not item["expected_present"] for item in initial["items"])
        response = client.post(base + f"/datasets/{parent}/split", json={})
        assert response.status_code == 200, response.text
        split = response.json()
        for snapshot_id in (parent, split["holdout_snapshot_id"]):
            result = client.get(base + f"/results/dataset/{snapshot_id}")
            assert result.status_code == 200, result.text
            assert "items" not in result.json()
        development = client.get(
            base + f"/results/dataset/{split['development_snapshot_id']}"
        ).json()
        assert development["items"]
        assert client.post(base + f"/datasets/{parent}/split", json={}).json()["id"] == split["id"]
        assert (
            client.post(
                base + f"/datasets/{parent}/split", json={"holdout_fraction": 0.8}
            ).status_code
            == 400
        )
        assert (
            client.get(base + "/dataset-splits").json()["splits"][0]["exposure_state"]
            == "unclaimed"
        )
