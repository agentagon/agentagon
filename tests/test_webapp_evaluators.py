"""Native framework integration and explicit publication without SDK or network calls."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from agentagon.core.records import AuditError
from agentagon.webapp import evaluators, snapshots
from agentagon.webapp.providers import CredentialStore, ProviderClient, ProviderError
from agentagon.webapp.state import AppState


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    state = AppState(tmp_path / "state")
    saved = state.register(str(root))
    workspace = state.workspace(saved["id"])
    workspace.initialize()
    return workspace


def dataset(workspace, items=None):
    return snapshots.save(
        workspace,
        workspace.project_id,
        {
            "kind": "dataset",
            "connection_id": "connection_" + "a" * 24,
            "selection": {"dataset_id": "native"},
            "provenance": {"provider": "braintrust", "version": "frozen-v1"},
            "completeness": {"complete": True},
            "items": items
            or [
                {
                    "id": "one",
                    "input": "question",
                    "expected": "answer",
                    "expected_present": True,
                    "metadata": {"family": "one"},
                }
            ],
        },
    )


def export_module(workspace, record, framework, tmp_path):
    bundle = evaluators.export_bundle(workspace, record["id"], framework)
    directory = tmp_path / framework
    directory.mkdir(exist_ok=True)
    for entry in bundle["files"]:
        (directory / entry["name"]).write_bytes(workspace.read_blob(entry["artifact"]))
    spec = importlib.util.spec_from_file_location("native_eval", directory / "native_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, bundle, directory


def test_discovery_is_bounded_read_only_and_distinguishes_frameworks(project, monkeypatch):
    files = {
        "qa.eval.ts": 'import { Eval } from "braintrust";\nEval("QA", {});',
        "test_deep.py": "from deepeval import assert_test\ndef test_answer(): pass\n",
        "test_plain.py": "def test_plain(): assert True\n",
        "eval_custom.py": "raise RuntimeError('never import discovery targets')\n",
        "instrumentation.py": "import braintrust\nbraintrust.init_logger(project='app')\n",
        "package.json": json.dumps(
            {"scripts": {"eval": "bt eval qa.eval.ts", "start": "node server.js"}}
        ),
    }
    for name, content in files.items():
        (project.root / name).write_text(content)
    outside = project.root.parent / "outside.py"
    outside.write_text("from deepeval import evaluate\n")
    (project.root / "linked_eval.py").symlink_to(outside)
    result = evaluators.discover(project)
    assert {candidate["framework"] for candidate in result["candidates"]} == evaluators.FRAMEWORKS
    script = next(
        candidate for candidate in result["candidates"] if candidate["entrypoint"] == "package.json"
    )
    assert script["command"] == {"argv": ["npm", "run", "eval"], "cwd": "."}
    assert all(item["source_digest"] and item["evidence"] for item in result["candidates"])
    instrumentation = next(
        item for item in result["candidates"] if item["entrypoint"] == "instrumentation.py"
    )
    assert instrumentation["command"] is None and instrumentation["confidence"] == "low"
    assert not any(item["entrypoint"] == "linked_eval.py" for item in result["candidates"])
    monkeypatch.setattr(evaluators, "MAX_FILES", 1)
    assert evaluators.discover(project)["truncated"] is True


def test_plan_preserves_native_command_and_detects_source_or_dataset_drift(project):
    (project.root / "test_eval.py").write_text("from deepeval import assert_test\n")
    record = dataset(project)
    payload = {
        "mode": "reuse",
        "framework": "deepeval",
        "entrypoint": "test_eval.py",
        "command": {
            "argv": ["deepeval", "test", "run", "test_eval.py::test_case", "-n", "2"],
            "cwd": ".",
        },
        "scorer": "Existing AnswerRelevancyMetric threshold 0.8",
        "output_mapping": {"quality.v1-score": "summary.relevancy"},
        "dataset_snapshot_id": record["id"],
    }
    plan = evaluators.prepare_plan(project, payload)
    assert plan["command"] == payload["command"]
    assert plan["state"] == "ready"
    assert plan["dataset_digest"] == record["digest"]
    assert evaluators.validate_plan(project, plan) == plan
    assert evaluators.prepare_plan(project, payload) == plan
    (project.root / "test_eval.py").write_text("changed scorer\n")
    with pytest.raises(AuditError, match="source changed"):
        evaluators.validate_plan(project, plan)
    tampered = {**plan, "scorer": "Always pass"}
    with pytest.raises(AuditError, match="integrity"):
        evaluators.validate_plan(project, tampered)


def test_new_plan_keeps_unresolved_expectations_actionable(project):
    record = dataset(
        project,
        [{"id": "unknown", "input": {"messages": []}, "expected": None, "expected_present": False}],
    )
    plan = evaluators.prepare_plan(
        project, {"mode": "create", "framework": "braintrust", "dataset_snapshot_id": record["id"]}
    )
    assert plan["state"] == "draft"
    assert len(plan["blockers"]) == 4
    assert "Missing references" in plan["blockers"][-1]


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "reuse", "framework": "unknown"},
        {"mode": "create", "framework": "braintrust", "entrypoint": "../secret.py"},
        {"mode": "create", "framework": "braintrust", "command": {"argv": "shell command"}},
        {"mode": "create", "framework": "deepeval", "output_mapping": {"quality": 1}},
    ],
)
def test_invalid_native_plans_fail_before_running_code(project, payload):
    with pytest.raises(AuditError):
        evaluators.prepare_plan(project, payload)


def test_braintrust_helper_keeps_conversation_and_missing_labels(project, tmp_path, monkeypatch):
    conversation = {
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "history"},
            {"role": "user", "content": "next"},
        ]
    }
    record = dataset(
        project,
        [
            {
                "id": "one",
                "input": conversation,
                "observed_output": {"answer": "observed"},
                "expected": None,
                "expected_present": False,
                "metadata": {"agentagon": "original metadata"},
            }
        ],
    )
    module, bundle, _ = export_module(project, record, "braintrust", tmp_path)
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "braintrust",
        SimpleNamespace(Eval=lambda *args, **kwargs: calls.append((args, kwargs))),
    )
    task, scorer = lambda value: value, lambda *args: 1
    module.run(task, [scorer])
    options = calls[0][1]
    assert options["no_send_logs"] is True
    assert options["task"] is task and options["scores"] == [scorer]
    row = options["data"][0]
    assert row["input"] == conversation and "expected" not in row
    assert row["metadata"]["source_metadata"] == {"agentagon": "original metadata"}
    assert row["metadata"]["agentagon"]["observed_output"] == {"answer": "observed"}
    assert bundle["missing_expectations"] == 1 and bundle["state"] == "draft"
    assert evaluators.export_bundle(project, record["id"], "braintrust") == bundle


def test_deepeval_helper_uses_fresh_outputs_and_explicit_conversation_mapping(
    project, tmp_path, monkeypatch
):
    observed = []
    monkeypatch.setitem(
        sys.modules, "deepeval", SimpleNamespace(evaluate=lambda **kwargs: observed.append(kwargs))
    )
    monkeypatch.setitem(sys.modules, "deepeval.dataset", SimpleNamespace(Golden=SimpleNamespace))
    monkeypatch.setitem(
        sys.modules, "deepeval.test_case", SimpleNamespace(LLMTestCase=SimpleNamespace)
    )
    module, _, _ = export_module(project, dataset(project), "deepeval", tmp_path)
    assert module.load_goldens()[0].expected_output == "answer"
    module.run(lambda value: "fresh " + value, ["existing-scorer"])
    assert observed[0]["test_cases"][0].actual_output == "fresh question"
    assert observed[0]["metrics"] == ["existing-scorer"]
    structured = dataset(
        project,
        [
            {
                "id": "conversation",
                "input": {"messages": [{"role": "user", "content": "help"}]},
                "expected_present": False,
            }
        ],
    )
    module, bundle, _ = export_module(project, structured, "deepeval", tmp_path)
    with pytest.raises(ValueError, match="case_factory"):
        module.run(lambda _: pytest.fail("must validate before application call"), [])
    module.run(
        lambda value: {"fresh": value},
        [],
        case_factory=lambda case, actual: {"source": case, "fresh": actual},
    )
    assert observed[-1]["test_cases"][0]["source"]["input"] == structured["items"][0]["input"]
    assert "expected" not in observed[-1]["test_cases"][0]["source"]
    assert "case_factory" in bundle["limitations"][-1]


@pytest.mark.parametrize("metric", ["quality.v1-score", "not a metric", "nonfinite"])
def test_exported_native_command_executes_sdk_and_reviewed_result_mapper(project, tmp_path, metric):
    _, _, directory = export_module(project, dataset(project), "braintrust", tmp_path)
    (directory / "braintrust.py").write_text(
        "def Eval(project, *, data, task, scores, no_send_logs):\n assert no_send_logs\n return {'quality': scores[0](task(data[0]['input']), data[0]['expected'])}\n"
    )
    (directory / "application.py").write_text(
        "def task(value):\n return 'answer'\ndef scorers():\n return [lambda actual, expected: float(actual == expected)]\ndef metrics(result):\n"
        f" return {{{metric!r}: "
        + ("float('nan')" if metric == "nonfinite" else "result['quality']")
        + "}\n"
    )
    output = directory / "result.json"
    result = subprocess.run(
        [
            sys.executable,
            "native_eval.py",
            "--task",
            "application:task",
            "--scorers",
            "application:scorers",
            "--metrics",
            "application:metrics",
        ],
        cwd=directory,
        env={**os.environ, "AGENTAGON_RESULT_PATH": str(output)},
        capture_output=True,
        text=True,
    )
    if metric == "quality.v1-score":
        assert result.returncode == 0, result.stderr
        assert json.loads(output.read_text()) == {"metrics": {metric: 1.0}}
    else:
        assert result.returncode != 0 and "finite numeric metrics" in result.stderr
        assert not output.exists()


class Braintrust:
    def __init__(self):
        self.remote = None
        self.rows = {}
        self.writes = 0
        self.lose_response = False
        self.entered = None
        self.release = None

    def __call__(self, request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer provider-secret"
        if request.url.path == "/v1/dataset":
            if self.remote is None:
                self.remote = {**body, "id": "remote-dataset"}
            return httpx.Response(200, json=self.remote)
        if request.url.path.endswith("/fetch"):
            rows = list(self.rows.values())
            offset = int(body.get("cursor", "0"))
            page = rows[offset : offset + body["limit"]]
            return httpx.Response(
                200,
                json={
                    "events": page,
                    "cursor": str(offset + len(page)) if offset + len(page) < len(rows) else None,
                },
            )
        assert request.url.path.endswith("/insert")
        if self.entered is not None:
            self.entered.set()
            self.release.wait(5)
        self.writes += 1
        self.rows.update({event["id"]: event for event in body["events"]})
        if self.lose_response:
            self.lose_response = False
            raise httpx.ReadError("provider-secret response was lost")
        return httpx.Response(200, json={"row_ids": [event["id"] for event in body["events"]]})


def publisher():
    provider = Braintrust()
    credentials = CredentialStore()
    connection = {
        "id": "connection_" + "a" * 24,
        "provider": "braintrust",
        "project": "remote-project",
        "credentials": {"api_key": credentials.set("provider-secret")},
    }
    return ProviderClient(connection, credentials, httpx.MockTransport(provider)), provider


def test_publication_requires_exact_preview_and_replays_without_network(project):
    client, provider = publisher()
    preview = evaluators.preview_publication(
        project, client.connection, dataset(project)["id"], {"name": "Reviewed cases"}
    )
    assert provider.remote is None
    assert preview["destination"]["name"].startswith("Reviewed cases [agentagon-")
    operation = str(uuid.uuid4())
    with pytest.raises(AuditError, match="authorization"):
        evaluators.publish_dataset(project, client, preview["id"], operation)
    result = evaluators.publish_dataset(project, client, preview["id"], operation, authorized=True)
    assert result["state"] == "completed" and provider.writes == 1
    assert "provider-secret" not in json.dumps(result)
    assert (
        evaluators.publish_dataset(project, client, preview["id"], operation, authorized=True)
        == result
    )
    assert provider.writes == 1
    other = evaluators.preview_publication(
        project, client.connection, dataset(project)["id"], {"name": "Other cases"}
    )
    with pytest.raises(AuditError, match="another preview"):
        evaluators.publish_dataset(project, client, other["id"], operation, authorized=True)


def test_uncertain_insert_reconciles_without_duplicate_writes_after_restart(project):
    client, provider = publisher()
    record = dataset(
        project,
        [{"id": str(index), "input": index, "expected_present": False} for index in range(105)],
    )
    preview = evaluators.preview_publication(
        project, client.connection, record["id"], {"name": "Draft examples"}
    )
    provider.lose_response = True
    operation = str(uuid.uuid4())
    with pytest.raises(ProviderError, match="Unable to reach") as error:
        evaluators.publish_dataset(project, client, preview["id"], operation, authorized=True)
    assert "provider-secret" not in str(error.value)
    saved = project.metadata_store.get_record(
        project.project_id, "dataset_publications", preview["id"]
    )
    assert saved["state"] == "interrupted"
    # Simulate termination after taking a durable claim but before saving an outcome.
    saved["state"] = "publishing"
    project.metadata_store.put_record(
        project.project_id, "dataset_publications", preview["id"], saved
    )
    recovered = evaluators.publish_dataset(
        project, client, preview["id"], operation, authorized=True
    )
    assert recovered["state"] == "completed" and provider.writes == 1
    assert len(provider.rows) == 105 and all(
        "expected" not in row for row in provider.rows.values()
    )


def test_retry_accepts_provider_null_missing_expectations(project):
    client, provider = publisher()
    record = dataset(project, [{"id": "one", "input": "question", "expected_present": False}])
    preview = evaluators.preview_publication(
        project, client.connection, record["id"], {"name": "Cases"}
    )
    provider.lose_response = True
    operation = str(uuid.uuid4())
    with pytest.raises(ProviderError):
        evaluators.publish_dataset(project, client, preview["id"], operation, authorized=True)
    for row in provider.rows.values():
        row["expected"] = None
    assert (
        evaluators.publish_dataset(project, client, preview["id"], operation, authorized=True)[
            "state"
        ]
        == "completed"
    )
    assert provider.writes == 1


@pytest.mark.parametrize("response", [[], {"metadata": []}])
def test_malformed_publication_provider_shapes_are_safe_errors(project, response):
    client, provider = publisher()
    provider.remote = response
    preview = evaluators.preview_publication(
        project, client.connection, dataset(project)["id"], {"name": "Cases"}
    )
    with pytest.raises(ProviderError, match="not owned"):
        evaluators.publish_dataset(
            project, client, preview["id"], str(uuid.uuid4()), authorized=True
        )
    assert provider.writes == 0


def test_publication_rejects_changed_destination_and_foreign_dataset(project):
    client, provider = publisher()
    preview = evaluators.preview_publication(
        project, client.connection, dataset(project)["id"], {"name": "Cases"}
    )
    client.connection["endpoint"] = "https://other.example"
    with pytest.raises(AuditError, match="connection changed"):
        evaluators.publish_dataset(
            project, client, preview["id"], str(uuid.uuid4()), authorized=True
        )
    assert provider.remote is None
    client.connection.pop("endpoint")
    provider.remote = {
        "id": "foreign",
        "name": preview["destination"]["name"],
        "project_id": "remote-project",
        "metadata": {},
    }
    with pytest.raises(ProviderError, match="not owned"):
        evaluators.publish_dataset(
            project, client, preview["id"], str(uuid.uuid4()), authorized=True
        )
    assert provider.writes == 0


def test_concurrent_publication_has_one_remote_writer(project):
    client, provider = publisher()
    preview = evaluators.preview_publication(
        project, client.connection, dataset(project)["id"], {"name": "Cases"}
    )
    provider.entered, provider.release = threading.Event(), threading.Event()
    operation = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            evaluators.publish_dataset, project, client, preview["id"], operation, authorized=True
        )
        assert provider.entered.wait(3)
        try:
            with pytest.raises(AuditError, match="in progress"):
                evaluators.publish_dataset(
                    project, client, preview["id"], operation, authorized=True
                )
        finally:
            provider.release.set()
        assert pending.result()["state"] == "completed"
    assert provider.writes == 1


def test_final_dataset_cannot_be_exported_or_published(project):
    record = dataset(project)
    preview = {
        key: copy.deepcopy(record[key])
        for key in ("kind", "connection_id", "selection", "items", "provenance", "completeness")
    }
    preview["provenance"]["dataset_partition"] = "final_holdout"
    final = snapshots.save(project, project.project_id, preview)
    client, provider = publisher()
    with pytest.raises(AuditError, match="final"):
        evaluators.export_bundle(project, final["id"], "braintrust")
    with pytest.raises(AuditError, match="final"):
        evaluators.preview_publication(project, client.connection, final["id"], {"name": "Final"})
    assert provider.remote is None
