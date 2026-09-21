"""Editable measurement proposals and explicitly accepted immutable definitions."""

import copy
import hashlib
import json
import re

from agentagon.capabilities.experiments import scoring
from agentagon.capabilities.traces import snapshots
from agentagon.core.records import AuditError, digest, now

FIELDS = {"behaviors", "metrics", "scoring", "evaluation", "evidence", "background", "limitations"}
METRIC_FIELDS = {"direction", "aggregation", "missing", "unit", "weight", "scale"}
BEHAVIOR_FIELDS = {"id", "description", "required", "check", "metric", "op", "bound", "rubric"}


def _strings(value, label):
    if (
        not isinstance(value, list)
        or len(value) > 100
        or any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in value)
    ):
        raise AuditError(f"{label} must be a bounded list of nonempty text")
    return value


def _background(value):
    """Normalize legacy text while storing new measurement context as observations."""
    if isinstance(value, str):
        observations = [value] if value.strip() else []
    else:
        observations = _strings(value, "background")
    if len(json.dumps(observations, ensure_ascii=False).encode()) > 64_000:
        raise AuditError("background must be within 64 KB")
    return observations


def score_definition(design):
    """Compare semantic scoring independently from where the evaluator implements it."""
    return {
        **design["scoring"],
        "metrics": {
            name: {k: v for k, v in metric.items() if k in METRIC_FIELDS}
            for name, metric in design["metrics"].items()
        },
        "behaviors": [
            {k: v for k, v in behavior.items() if k in BEHAVIOR_FIELDS}
            for behavior in design["behaviors"]
        ],
    }


def validate(payload):
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise AuditError("unsupported measurement design fields")
    if len(json.dumps(payload).encode()) > 100_000:
        raise AuditError("measurement design exceeds 100 KB")
    value = copy.deepcopy(payload)
    for key in ("evidence", "limitations"):
        value[key] = _strings(value.get(key, []), key)
    value["background"] = _background(value.get("background", []))
    metrics = value.get("metrics")
    behaviors = value.get("behaviors")
    if not isinstance(metrics, dict) or not 1 <= len(metrics) <= 30:
        raise AuditError("define between one and thirty metrics")
    if not isinstance(behaviors, list) or len(behaviors) > 100:
        raise AuditError("behaviors must be a bounded list")
    for name, metric in metrics.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", name):
            raise AuditError("metric names must be identifiers")
        if not isinstance(metric, dict) or set(metric) - METRIC_FIELDS - {
            "description",
            "evidence",
            "prerequisites",
        }:
            raise AuditError("unsupported metric definition")
        if "description" in metric and (
            not isinstance(metric["description"], str) or len(metric["description"]) > 4000
        ):
            raise AuditError("metric description must be bounded text")
        for key in ("evidence", "prerequisites"):
            if key in metric:
                _strings(metric[key], key)
    for behavior in behaviors:
        if not isinstance(behavior, dict) or set(behavior) - BEHAVIOR_FIELDS - {
            "evidence",
            "prerequisites",
        }:
            raise AuditError("unsupported behavior definition")
        for key in ("evidence", "prerequisites"):
            if key in behavior:
                _strings(behavior[key], key)
    if not isinstance(value.get("scoring"), dict) or set(value["scoring"]) - {
        "mode",
        "primary",
        "custom_metric",
        "target",
    }:
        raise AuditError("choose primary, weighted or custom scoring")
    # The draft describes scoring, not an executable evaluator. Its actual protected
    # source paths are established and independently reviewed during preparation.
    scoring.validate(
        {"version": 1, "source_paths": ["measurement.json"], **score_definition(value)}
    )
    evaluation = value.get("evaluation")
    if (
        not isinstance(evaluation, dict)
        or set(evaluation)
        - {
            "mode",
            "evaluation_id",
            "candidate_id",
            "framework",
            "command",
            "entrypoint",
            "dataset_snapshot_id",
            "scorer",
            "output_mapping",
        }
        or evaluation.get("mode") not in {"reuse", "create"}
    ):
        raise AuditError("choose an existing evaluation or create one")
    return value


class Designs:
    def __init__(self, state, catalog):
        self.state, self.catalog = state, catalog

    def _identity(self, project_id, agent_id, goal_id):
        self.catalog.goal_record(project_id, agent_id, goal_id)
        return "design_" + hashlib.sha256(f"{agent_id}:{goal_id}".encode()).hexdigest()[:24]

    def get(self, project_id, agent_id, goal_id):
        key = self._identity(project_id, agent_id, goal_id)
        return self.state.db.get_record(project_id, "measurement_designs", key)

    def accepted(self, project_id, agent_id, goal_id):
        goal_record = self.catalog.goal_record(project_id, agent_id, goal_id)
        key = goal_record.get("accepted_design_id")
        if not key:
            return None
        record = self.state.db.get_record(project_id, "accepted_designs", key)
        if not record or record["agent_id"] != agent_id or record["goal_id"] != goal_id:
            raise AuditError("accepted measurement design is unavailable")
        agent = self.catalog.agent(project_id, agent_id)
        if record["binding_digest"] != agent["binding_digest"]:
            raise AuditError("Agent scope changed. Review and accept its measurement design again.")
        return record

    def save(self, project_id, agent_id, goal_id, payload):
        key = self._identity(project_id, agent_id, goal_id)
        revision = payload.get("expected_revision")
        if type(revision) is not int or revision < 0:
            raise AuditError("provide the measurement design revision")
        value = validate({k: v for k, v in payload.items() if k != "expected_revision"})
        workspace = self.state.workspace(project_id)
        evaluation = value["evaluation"]
        if evaluation.get("dataset_snapshot_id"):
            from agentagon.capabilities.evaluation.datasets import assert_development

            snapshot = snapshots.load(workspace, evaluation["dataset_snapshot_id"])
            if snapshot["kind"] != "dataset":
                raise AuditError("select a dataset snapshot")
            assert_development(workspace, snapshot)
        native = {k: v for k, v in evaluation.items() if k not in {"evaluation_id", "candidate_id"}}
        if evaluation.get("evaluation_id"):
            from agentagon.capabilities.experiments import preparation

            if evaluation["mode"] != "reuse" or set(evaluation) - {"mode", "evaluation_id"}:
                raise AuditError(
                    "Frozen reuse takes only mode and evaluation_id. Changing its dataset, "
                    "command or scorer requires Create evaluation."
                )
            record = preparation.load(workspace, evaluation["evaluation_id"])
            if record["state"] != "frozen":
                raise AuditError("select a frozen evaluator or create an evaluation")
            spec = record["package"]["spec"]
            expected = {k: spec.get("scoring", {}).get(k) for k in score_definition(value)}
            if expected != score_definition(value):
                raise AuditError(
                    "Scoring changes require a new evaluator. Choose Create evaluation."
                )
            native_plan = {
                "evaluation_id": record["evaluation_id"],
                "evaluator_digest": preparation.evaluator_identity(
                    workspace, record["evaluation_id"]
                ),
            }
        else:
            from agentagon.capabilities.evaluation import native as evaluators

            native_plan = evaluators.prepare_plan(workspace, native)
        agent = self.catalog.agent(project_id, agent_id)
        record = {
            **value,
            "id": key,
            "agent_id": agent_id,
            "goal_id": goal_id,
            "state": "draft",
            "binding_digest": agent["binding_digest"],
            "native_plan": native_plan,
        }
        return self.state.db.put_record(
            project_id, "measurement_designs", key, record, expected_revision=revision
        )

    def accept(self, project_id, agent_id, goal_id, payload):
        if (
            set(payload) != {"expected_revision"}
            or type(payload.get("expected_revision")) is not int
            or payload["expected_revision"] < 1
        ):
            raise AuditError("accept the current measurement design revision")
        draft = self.get(project_id, agent_id, goal_id)
        if draft and draft["native_plan"].get("id"):
            from agentagon.capabilities.evaluation import native as evaluators

            evaluators.validate_plan(self.state.workspace(project_id), draft["native_plan"])
        with self.state.db.transaction() as tx:
            goal_record = tx.get_record(project_id, "goals", goal_id)
            if not goal_record or goal_record["agent_id"] != agent_id:
                raise AuditError("goal_record not found for this application agent")
            key = "design_" + hashlib.sha256(f"{agent_id}:{goal_id}".encode()).hexdigest()[:24]
            draft = tx.get_record(project_id, "measurement_designs", key)
            if not draft or draft["revision"] != payload["expected_revision"]:
                raise AuditError(
                    "Measurement proposal changed. Reload and review it before accepting."
                )
            agent = tx.get_record(project_id, "application_agents", agent_id)
            if draft["binding_digest"] != agent["binding_digest"]:
                raise AuditError("Agent scope changed. Save the reviewed proposal again.")
            definition = {k: draft[k] for k in FIELDS}
            version_id = (
                "designversion_" + digest({"draft": draft, "binding": agent["binding_digest"]})[:24]
            )
            if draft["state"] == "accepted":
                return tx.get_record(
                    project_id, "accepted_designs", goal_record["accepted_design_id"]
                )
            accepted = {
                **draft,
                "id": version_id,
                "state": "accepted",
                "digest": digest(definition),
                "accepted_at": now(),
            }
            accepted = tx.put_record(
                project_id, "accepted_designs", version_id, accepted, expected_revision=0
            )
            goal_record.update(accepted_design_id=version_id, version=goal_record["version"] + 1)
            saved = tx.put_record(
                project_id, "goals", goal_id, goal_record, expected_revision=goal_record["revision"]
            )
            tx.put_record(
                project_id,
                "goal_versions",
                version_id,
                {"id": version_id, "goal_id": goal_id, "definition": saved, "created_at": now()},
            )
            tx.put_record(
                project_id,
                "measurement_designs",
                key,
                {**draft, "state": "accepted"},
                expected_revision=draft["revision"],
            )
            return accepted

    @staticmethod
    def validate_evaluator(workspace, accepted, evaluation_id):
        from agentagon.capabilities.experiments import preparation

        record = preparation.load(workspace, evaluation_id)
        if record["state"] != "frozen":
            raise AuditError("The accepted measurement requires a reviewed frozen evaluator.")
        package = record["package"]
        spec = package["spec"]
        expected = score_definition(accepted)
        if any(spec.get("scoring", {}).get(key) != value for key, value in expected.items()):
            raise AuditError("Evaluator scoring differs from the accepted measurement design.")
        plan = accepted["native_plan"]
        if not plan.get("evaluation_id") and record["created_at"] < accepted["accepted_at"]:
            raise AuditError(
                "This measurement plan requires a new evaluator prepared after its acceptance."
            )
        if plan.get("evaluation_id") and (
            evaluation_id != plan["evaluation_id"]
            or preparation.evaluator_identity(workspace, evaluation_id) != plan["evaluator_digest"]
        ):
            raise AuditError("Frozen evaluator differs from the accepted measurement design.")
        snapshot_id = accepted["evaluation"].get("dataset_snapshot_id")
        if snapshot_id:
            from agentagon.capabilities.evaluation.datasets import assert_development

            snapshot = snapshots.load(workspace, snapshot_id)
            assert_development(workspace, snapshot)
            if snapshot["digest"] != plan.get("dataset_digest"):
                raise AuditError("Dataset differs from the accepted measurement design.")
            relative = f"agentagon-private/{snapshot_id}.json"
            entries = [
                entry
                for entry in package["files"]
                if entry["path"] == relative
                and entry["kind"] == "inputs"
                and not entry.get("deleted")
            ]
            if (
                len(entries) != 1
                or {"path": relative, "source": entries[0]["artifact"]} not in spec["inputs"]
            ):
                raise AuditError(
                    "Frozen evaluator must retain the accepted dataset as a private input."
                )
            content = workspace.read_blob(entries[0]["artifact"])
            if hashlib.sha256(content).hexdigest() != entries[0]["digest"]:
                raise AuditError("Frozen dataset input checksum changed.")
            try:
                payload = json.loads(content)
            except (ValueError, UnicodeError):
                raise AuditError("Frozen dataset input is not the reviewed snapshot.") from None
            if digest(payload) != digest(snapshots.input_payload(snapshot)):
                raise AuditError("Frozen dataset input is not the reviewed snapshot.")

    @staticmethod
    def background(accepted, goal):
        return json.dumps(
            {
                "goal": goal,
                "measurement_design": accepted["digest"],
                "scoring": score_definition(accepted),
                "evidence": accepted["evidence"],
                "limitations": accepted["limitations"],
                "context": "\n\n".join(accepted["background"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
