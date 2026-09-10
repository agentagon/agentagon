import copy
import math

import pytest
from jsonschema import Draft202012Validator

from agentagon.core.records import AuditError, load_json, resource_path, validate_record
from agentagon.experiments.spec import finite, validate_limits, validate_profile, validate_spec


def profile():
    return {
        "runner": {"kind": "local"},
        "limits": {
            "max_candidates": 4,
            "max_trials": 24,
            "max_elapsed_seconds": 600,
            "parallel_candidates": 1,
            "parallel_trials": 1,
            "trial_timeout_seconds": 30,
        },
    }


def specification():
    return {
        "goal": "Reduce latency",
        "editable_paths": ["app.py"],
        "evaluation_paths": ["benchmark.py", "tests"],
        "benchmark": {"argv": ["python3", "benchmark.py"]},
        "metrics": {"latency": {"direction": "min", "unit": "ms"}},
    }


def review():
    return {
        "version": 1,
        "run_id": "run_fixture",
        "candidate_id": "candidate_fixture",
        "source_digest": "source-fixture",
        "evaluation_digest": "evaluation-fixture",
        "inputs_digest": "inputs-fixture",
        "trial_ids": ["trial_fixture"],
        "reviewer": "independent-reviewer",
        "verdict": "pass",
        "rationale": "The source change preserves the protected behavior and benchmark.",
        "evidence": ["trial_fixture", "candidate_fixture"],
        "assessments": {
            "patch": True,
            "evaluator_integrity": True,
            "issue_relevance": True,
            "measurements": True,
        },
    }


@pytest.mark.parametrize("name", ["fix-spec", "fix-profile", "fix-review"])
def test_bundled_fix_schemas_are_valid(name):
    Draft202012Validator.check_schema(load_json(resource_path(f"contracts/v1/{name}.json")))


def test_profiles_and_specifications_normalize_without_mutating_input():
    raw_profile, raw_spec = profile(), specification()
    before_profile, before_spec = copy.deepcopy(raw_profile), copy.deepcopy(raw_spec)
    normalized_profile = validate_profile(raw_profile)
    normalized_spec = validate_spec(raw_spec)
    assert (raw_profile, raw_spec) == (before_profile, before_spec)
    assert normalized_profile["limits"]["stagnation_rounds"] == 3
    assert normalized_profile["env"] == {}
    assert normalized_spec["benchmark"]["cwd"] == "."
    assert normalized_spec["seeds"] == [0, 1, 2]
    validate_record("fix-profile", normalized_profile)
    validate_record("fix-spec", normalized_spec)


@pytest.mark.parametrize(
    "updates",
    [
        {"runner": {"kind": "e2b", "template": "fixture", "api_key_env": 42}},
        {"runner": {"kind": "e2b", "template": "fixture", "api_key_env": False}},
        {"runner": {"kind": ["local"]}},
        {"runner": {"kind": "local", "password": "private-value"}},
        {"runner": {"kind": "ssh", "host": "target", "remote_root": False}},
        {"env": {"KEY": []}},
        {"env": {7: "KEY"}},
        {"env": {"AGENTAGON_RESULT_PATH": "RESULT_OVERRIDE"}},
        {"env": {"KEY": "raw-secret-value"}},
        {"setup": ["python3 benchmark.py"]},
    ],
)
def test_profile_types_and_secret_references_fail_as_audit_errors(updates):
    value = {**profile(), **updates}
    for validate in (lambda data: validate_record("fix-profile", data), validate_profile):
        with pytest.raises(AuditError) as error:
            validate(value)
        assert "private-value" not in str(error.value)
        assert "raw-secret-value" not in str(error.value)


@pytest.mark.parametrize(
    "updates",
    [
        {"metrics": {7: {"direction": "min", "unit": "ms"}}},
        {"metrics": {"latency": None}},
        {"constraints": [{"metric": [], "op": "lte", "bound": 1}]},
        {"checks": [None]},
        {"checks": [{"id": "gate", "argv": ["python3", "checks.py"], "preflight": "yes"}]},
        {"benchmark": {"argv": ["python3", {"secret": "private"}]}},
        {"evaluation_paths": []},
        {"inputs": [{"source": "../private", "path": "data.json"}]},
        {"overlays": [{"source": "check.py", "path": ".git/config"}]},
        {"inputs": [{"source": ".agentagon/private.json", "path": ".agentagon/private.json"}]},
        {"inputs": [{"source": ".agentagon/private.json", "path": ".AGENTAGON/private.json"}]},
        {"overlays": [{"source": "check.py", "path": "."}]},
        {"editable_paths": ["C:\\private"]},
        {"seeds": [1, True, 3]},
        {"goal": "   "},
        {"promotion": "accepted"},
    ],
)
def test_invalid_specification_structure_is_rejected_by_schema_and_manual_validator(updates):
    value = {**specification(), **updates}
    for validate in (lambda data: validate_record("fix-spec", data), validate_spec):
        with pytest.raises(AuditError):
            validate(value)


def test_cross_field_constraints_remain_enforced_after_structural_validation():
    value = specification()
    value["constraints"] = [{"metric": "unknown", "op": "lte", "bound": 1}]
    validate_record("fix-spec", value)
    with pytest.raises(AuditError, match="constraint"):
        validate_spec(value)
    value = specification()
    value["issue_ids"] = ["issue_fixture"]
    validate_record("fix-spec", value)
    with pytest.raises(AuditError, match="acceptance check"):
        validate_spec(value)
    value = specification()
    value["seeds"] = [1]
    with pytest.raises(AuditError, match="one integer seed"):
        validate_spec(value)


def test_shared_host_capacity_and_e2b_collection_deadline_are_explicit():
    value = profile()
    value["limits"]["parallel_trials"] = 2
    with pytest.raises(AuditError, match="independent_capacity"):
        validate_profile(value)
    value["runner"]["independent_capacity"] = True
    assert validate_profile(value)["limits"]["parallel_trials"] == 2
    value = profile()
    value["runner"] = {"kind": "e2b", "template": "fixture", "sandbox_timeout_seconds": 59}
    with pytest.raises(AuditError, match="30 seconds"):
        validate_profile(value)
    value["runner"]["sandbox_timeout_seconds"] = 60
    normalized = validate_profile(value)
    assert normalized["runner"]["api_key_env"] == "E2B_API_KEY"


def test_trial_limits_cannot_be_missing_or_exceed_total_budget():
    limits = profile()["limits"]
    limits["parallel_candidates"] = limits["max_candidates"] + 1
    with pytest.raises(AuditError, match="total candidate"):
        validate_limits(limits)
    del limits["max_trials"]
    with pytest.raises(AuditError):
        validate_limits(limits)


@pytest.mark.parametrize("number", [True, None, math.nan, math.inf, -math.inf, 10**1000])
def test_nonfinite_metrics_and_unrepresentable_bounds_fail_closed(number):
    with pytest.raises(AuditError, match="finite numbers"):
        finite(number)
    spec = specification()
    spec["constraints"] = [{"metric": "latency", "op": "lte", "bound": number}]
    with pytest.raises(AuditError):
        validate_spec(spec)


@pytest.mark.parametrize("field", ["metrics", "outcomes", "promotion"])
def test_review_cannot_submit_results_or_promotion(field):
    value = review()
    value[field] = {"claimed": "success"}
    with pytest.raises(AuditError):
        validate_record("fix-review", value)


def test_review_requires_finished_verdict_and_complete_boolean_assessments():
    value = review()
    validate_record("fix-review", value)
    value["verdict"] = "pending"
    with pytest.raises(AuditError):
        validate_record("fix-review", value)
    value = review()
    value["assessments"]["measurements"] = 1
    with pytest.raises(AuditError):
        validate_record("fix-review", value)
    value = review()
    del value["inputs_digest"]
    with pytest.raises(AuditError):
        validate_record("fix-review", value)
    value = review()
    value["verdict"] = "reject"
    value["assessments"]["patch"] = False
    validate_record("fix-review", value)
