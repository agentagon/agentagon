"""Host lessons preserve engine provenance and never replace fresh verification."""

import copy
from datetime import datetime, timedelta

import pytest
from support.controls import submit
from support.experiments import baseline, executions, propose, verify

from agentagon.core.records import AuditError, digest, encoded
from agentagon.experiments import engine, learning
from agentagon.experiments.store import load_run
from agentagon.storage.config import Config


@pytest.fixture
def scanned_run(application, specification):
    profile = Config().profile(application.root, "local")
    profile["scans"] = {"max_scans": 2, "max_input_bytes": 8192, "scan_timeout_seconds": 60}
    Config().update_profile("project", "local", profile, application.root)
    specification["repetitions"] = 1
    initial = baseline(application, specification)
    candidate, _ = propose(application, initial["run_id"], quality=0.5, variant="quality-failure")
    verified = verify(application, initial["run_id"], candidate["candidate_id"])
    assert verified["candidate"]["state"] == "rejected"
    return application, initial["run_id"], specification


def packet_and_response(workspace, run_id):
    prepared = submit(workspace, run_id, "scan")["result"]
    packet = workspace.read_artifact(prepared["packet"])
    assert digest(packet) == prepared["packet_digest"]
    candidate = packet["candidates"][0]
    response = {
        **prepared["response_template"],
        "author": "scan-host",
        "insights": [
            {
                "kind": "failure_pattern",
                "summary": "Lower latency coincides with the failed quality floor",
                "rationale": "The retained candidate violates the frozen quality constraint",
                "uncertainty": "One candidate is insufficient to establish a common root cause",
                "candidate_ids": [candidate["candidate_id"]],
                "evidence": candidate["evidence"],
                "code_paths": [packet["code_paths"][0]],
            }
        ],
    }
    return prepared, packet, response


def test_scan_preparation_is_bounded_and_never_executes_another_trial(scanned_run):
    workspace, run_id, _ = scanned_run
    before = len(executions())
    prepared, packet, response = packet_and_response(workspace, run_id)
    assert prepared["input_bytes"] <= 8192
    assert packet["candidates"][0]["state"] == "rejected"
    assert learning.pending(load_run(workspace, run_id))["scan_id"] == prepared["scan_id"]
    status = engine.status(workspace, run_id)
    assert status["scan_pending"]["packet"] == prepared["packet"]
    assert status["scan_pending"]["response_template"] == prepared["response_template"]
    accepted = submit(workspace, run_id, "insights", response=response)
    assert accepted["result"]["state"] == "completed"
    assert accepted["result"]["insights"][0]["verification"] == "advisory"
    assert len(executions()) == before
    assert learning.pending(load_run(workspace, run_id))["pending"] is False


def test_without_saved_limits_scans_stay_disabled_and_cannot_be_enabled_midrun(
    application, specification
):
    specification["repetitions"] = 1
    initial = baseline(application, specification)
    run_id = initial["run_id"]
    assert learning.pending(load_run(application, run_id))["enabled"] is False
    profile = Config().profile(application.root, "local")
    profile["scans"] = {"max_scans": 2, "max_input_bytes": 4096, "scan_timeout_seconds": 60}
    Config().update_profile("project", "local", profile, application.root)
    with pytest.raises(AuditError, match="explicit scan limits"):
        submit(application, run_id, "scan")


def test_scan_rejects_foreign_evidence_new_metrics_and_changed_packets(scanned_run):
    workspace, run_id, _ = scanned_run
    _, _, response = packet_and_response(workspace, run_id)
    invalid = copy.deepcopy(response)
    invalid["insights"][0]["metrics"] = {"quality": 1}
    with pytest.raises(AuditError, match="evidence-linked judgments"):
        submit(workspace, run_id, "insights", response=invalid)
    invalid = copy.deepcopy(response)
    invalid["insights"][0]["evidence"] = ["candidate:foreign"]
    with pytest.raises(AuditError, match="evidence"):
        submit(workspace, run_id, "insights", response=invalid)
    with pytest.raises(AuditError, match="different evidence packet"):
        submit(workspace, run_id, "insights", response={**response, "packet_digest": "stale"})
    completed = submit(workspace, run_id, "insights", response=response)["result"]
    assert submit(workspace, run_id, "insights", response=response)["result"] == completed
    invalid = copy.deepcopy(response)
    invalid["insights"][0]["summary"] = "Changed opinion"
    with pytest.raises(AuditError, match="immutable"):
        submit(workspace, run_id, "insights", response=invalid)


def test_expired_scan_keeps_evidence_and_failure_consumes_its_budget(scanned_run, monkeypatch):
    workspace, run_id, _ = scanned_run
    prepared, _, response = packet_and_response(workspace, run_id)
    expired = (datetime.fromisoformat(prepared["deadline_at"]) + timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(learning, "now", lambda: expired)
    with pytest.raises(AuditError, match="deadline"):
        submit(workspace, run_id, "insights", response=response)
    failed = submit(
        workspace, run_id, "scan_fail", scan_id=prepared["scan_id"], reason="Host timed out"
    )
    assert failed["result"]["state"] == "failed"
    assert workspace.read_artifact(failed["result"]["packet"])
    assert learning.pending(load_run(workspace, run_id))["used"] == 1


def test_lessons_are_retrieved_across_runs_as_advice_only(scanned_run):
    workspace, run_id, spec = scanned_run
    _, _, response = packet_and_response(workspace, run_id)
    submit(workspace, run_id, "insights", response=response)
    next_spec = {**copy.deepcopy(spec), "goal": "A new goal with a new frozen evaluation identity"}
    next_run = engine.start(workspace, next_spec, "local")
    context = learning.context(workspace, load_run(workspace, next_run["run_id"]))
    assert len(context["lessons"]) == 1
    lesson = context["lessons"][0]
    assert lesson["run_id"] == run_id
    assert lesson["verification"] == "advisory"
    assert lesson["same_evaluation"] is False
    assert lesson["same_source"] is True
    assert "metrics" not in lesson
    assert next_run["candidate"]["state"] == "sealed"
    assert next_run["candidate"]["metrics"] == {}
    assert next_run["lesson_context"] == context


def test_context_diagnostics_cannot_exceed_saved_input_bound(scanned_run, monkeypatch):
    workspace, run_id, _ = scanned_run
    _, _, response = packet_and_response(workspace, run_id)
    submit(workspace, run_id, "insights", response=response)
    data = load_run(workspace, run_id)
    corrupted = copy.deepcopy(data)
    corrupted["scans"][0]["packet_digest"] = "changed"
    # The byte cap covers both lessons and integrity diagnostics.
    monkeypatch.setattr(learning, "list_runs", lambda _: [corrupted] * 30 + [data])
    current = copy.deepcopy(data)
    current["profile"]["scans"]["max_input_bytes"] = 1024
    context = learning.context(workspace, current)
    assert len(encoded(context).encode()) <= 1024
    assert context["limits"].count("A prior scan failed integrity checks and was excluded") == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("summary", "Unbound invented lesson"),
        ("metrics", {"quality": 1}),
        ("author", "another-host"),
        ("run_id", "foreign-run"),
        ("evaluation_digest", "foreign-evaluation"),
    ],
)
def test_mutated_prior_insight_is_excluded_from_later_context(scanned_run, field, value):
    workspace, run_id, spec = scanned_run
    _, _, response = packet_and_response(workspace, run_id)
    submit(workspace, run_id, "insights", response=response)
    data = load_run(workspace, run_id)
    data["scans"][0]["insights"][0][field] = value
    # Deliberately corrupt only a disposable fixture's state; the packet stays immutable.
    workspace.write(workspace.state / "runs" / run_id / "state.json", data)
    next_run = engine.start(workspace, spec, "local")
    context = learning.context(workspace, load_run(workspace, next_run["run_id"]))
    assert context["lessons"] == []
    assert context["limits"]


def test_whole_checkout_scope_retrieves_lessons_for_a_specific_file(scanned_run):
    workspace, run_id, spec = scanned_run
    _, _, response = packet_and_response(workspace, run_id)
    submit(workspace, run_id, "insights", response=response)
    next_spec = {**copy.deepcopy(spec), "editable_paths": ["."]}
    next_run = engine.start(workspace, next_spec, "local")
    context = learning.context(workspace, load_run(workspace, next_run["run_id"]))
    assert len(context["lessons"]) == 1


def test_specific_scope_retrieves_lessons_from_whole_checkout(application, specification):
    profile = Config().profile(application.root, "local")
    profile["scans"] = {"max_scans": 1, "max_input_bytes": 8192, "scan_timeout_seconds": 60}
    Config().update_profile("project", "local", profile, application.root)
    original = {**copy.deepcopy(specification), "editable_paths": ["."], "repetitions": 1}
    initial = baseline(application, original)
    candidate, _ = propose(application, initial["run_id"])
    verify(application, initial["run_id"], candidate["candidate_id"])
    _, _, response = packet_and_response(application, initial["run_id"])
    submit(application, initial["run_id"], "insights", response=response)
    next_run = engine.start(application, specification, "local")
    context = learning.context(application, load_run(application, next_run["run_id"]))
    assert len(context["lessons"]) == 1


def test_preparing_another_scan_requires_completed_round_and_preserves_count(scanned_run):
    workspace, run_id, _ = scanned_run
    prepared, _, response = packet_and_response(workspace, run_id)
    with pytest.raises(AuditError, match="pending scan"):
        submit(workspace, run_id, "scan")
    submit(workspace, run_id, "insights", response=response)
    with pytest.raises(AuditError, match="no unscanned"):
        submit(workspace, run_id, "scan")
    candidate, _ = propose(workspace, run_id, latency=75, variant="new-idea")
    verify(workspace, run_id, candidate["candidate_id"])
    second = submit(workspace, run_id, "scan")["result"]
    assert second["scan_id"] != prepared["scan_id"]
    submit(
        workspace,
        run_id,
        "scan_fail",
        scan_id=second["scan_id"],
        reason="No additional conclusions",
    )
    with pytest.raises(AuditError, match="count limit"):
        submit(workspace, run_id, "scan")


def test_stopping_a_run_prevents_new_scan_work(scanned_run):
    workspace, run_id, _ = scanned_run
    engine.stop(workspace, run_id)
    with pytest.raises(AuditError, match="stopped"):
        submit(workspace, run_id, "scan")


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"max_scans": 1},
        {"max_scans": True, "max_input_bytes": 4096, "scan_timeout_seconds": 10},
        {"max_scans": 1, "max_input_bytes": 1, "scan_timeout_seconds": 10},
        {"max_scans": 1, "max_input_bytes": 1048577, "scan_timeout_seconds": 10},
    ],
)
def test_scan_limits_are_explicit_and_bounded(settings):
    with pytest.raises(AuditError):
        learning.validate_limits(settings)
