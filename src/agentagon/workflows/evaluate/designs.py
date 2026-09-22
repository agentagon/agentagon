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
    """Validate measurement context as bounded observations."""
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

    def attached_cases(self, project_id, agent_id, goal_id):
        key = self._identity(project_id, agent_id, goal_id)
        return self.state.db.get_record(project_id, "measurement_cases", key)

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
        cases = self.attached_cases(project_id, agent_id, goal_id)
        record = self._prepare(project_id, agent_id, goal_id, value, cases)
        with self.state.db.transaction() as tx:
            current = tx.get_record(project_id, "measurement_cases", key)
            if (current or {}).get("revision", 0) != (cases or {}).get("revision", 0):
                raise AuditError("Attached cases changed. Reload before saving the design.")
            return tx.put_record(
                project_id, "measurement_designs", key, record, expected_revision=revision
            )

    def _prepare(self, project_id, agent_id, goal_id, value, cases):
        key = self._identity(project_id, agent_id, goal_id)
        value = copy.deepcopy(value)
        workspace = self.state.workspace(project_id)
        evaluation = value["evaluation"]
        if cases and not evaluation.get("evaluation_id"):
            evaluation.setdefault("dataset_snapshot_id", cases["dataset_snapshot_id"])
            self._contains_cases(workspace, evaluation["dataset_snapshot_id"], cases)
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
            if cases:
                self._frozen_contains_cases(workspace, record, cases["dataset_snapshot_id"])
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
            "required_frozen_evaluations": (cases or {}).get("required_frozen_evaluations", []),
            "required_case_snapshot_id": (cases or {}).get("dataset_snapshot_id"),
        }
        if cases and evaluation.get("evaluation_id"):
            self.validate_evaluator(workspace, record, evaluation["evaluation_id"])
        return record

    @staticmethod
    def _contains_cases(workspace, snapshot_id, cases):
        from agentagon.capabilities.evaluation.datasets import assert_development

        required = snapshots.load(workspace, cases["dataset_snapshot_id"])
        assert_development(workspace, required)
        selected = snapshots.load(workspace, snapshot_id)
        rows = {item["id"]: digest(item) for item in selected["items"]}
        if any(rows.get(item["id"]) != digest(item) for item in required["items"]):
            raise AuditError(
                "The selected dataset drops or changes attached cases. Use the attached "
                "dataset or a combined dataset that retains every reviewed case."
            )

    @staticmethod
    def _frozen_contains_cases(workspace, record, snapshot_id):
        """Reuse is possible only when a frozen private input already contains the cases."""
        from agentagon.capabilities.evaluation.datasets import assert_development

        required = snapshots.load(workspace, snapshot_id)
        assert_development(workspace, required)
        required_rows = {item["id"]: digest(item) for item in required["items"]}
        package = record["package"]
        for entry in package["files"]:
            if (
                entry["kind"] != "inputs"
                or entry.get("deleted")
                or not re.fullmatch(r"agentagon-private/snapshot_[a-f0-9]{24}\.json", entry["path"])
                or {"path": entry["path"], "source": entry["artifact"]}
                not in package["spec"]["inputs"]
            ):
                continue
            content = workspace.read_blob(entry["artifact"])
            if hashlib.sha256(content).hexdigest() != entry["digest"]:
                raise AuditError("Frozen dataset input checksum changed.")
            selected = snapshots.load(workspace, entry["path"].rsplit("/", 1)[1][:-5])
            assert_development(workspace, selected)
            try:
                payload = json.loads(content)
            except (ValueError, UnicodeError):
                raise AuditError("Frozen dataset input is not a reviewed snapshot.") from None
            if digest(payload) != digest(snapshots.input_payload(selected)):
                raise AuditError("Frozen dataset input differs from its reviewed snapshot.")
            selected_rows = {item["id"]: digest(item) for item in selected["items"]}
            if all(selected_rows.get(key) == value for key, value in required_rows.items()):
                return
        raise AuditError(
            "This frozen evaluator does not contain every attached case. Choose Create "
            "evaluation, or select a frozen evaluator that retains the reviewed cases."
        )

    @staticmethod
    def _combine_cases(workspace, records):
        from agentagon.capabilities.evaluation.datasets import assert_development

        sources, rows = {}, {}
        for record in records:
            assert_development(workspace, record)
            sources[record["id"]] = {"id": record["id"], "digest": record["digest"]}
            for item in record["items"]:
                if item["id"] in rows and digest(rows[item["id"]]) != digest(item):
                    raise AuditError("Datasets contain conflicting case IDs; review them first.")
                rows[item["id"]] = item
        if len(sources) == 1:
            return records[0]
        return snapshots.save(
            workspace,
            workspace.project_id,
            {
                "kind": "dataset",
                "connection_id": None,
                "selection": {"dataset_id": "Evaluation cases"},
                "items": [rows[key] for key in sorted(rows)],
                "provenance": {
                    "derivation": "attached_evaluation_cases",
                    "source_datasets": [sources[key] for key in sorted(sources)],
                    "dataset_partition": "development",
                    "observed_outputs_are_expectations": False,
                },
                "completeness": {"complete": True, "limitations": []},
            },
        )

    def attach_cases(self, project_id, agent_id, goal_id, payload):
        """Retain reviewed cases atomically without accepting or running an evaluation."""
        from agentagon.capabilities.evaluation.datasets import assert_development_evaluator
        from agentagon.capabilities.experiments import preparation
        from agentagon.workflows.runtime import operation_id

        if not isinstance(payload, dict) or set(payload) != {
            "dataset_snapshot_id",
            "operation_id",
            "expected_revision",
            "expected_cases_revision",
        }:
            raise AuditError("Provide the case dataset, operation ID and both current revisions.")
        for field in ("expected_revision", "expected_cases_revision"):
            if type(payload[field]) is not int or payload[field] < 0:
                raise AuditError("Provide nonnegative design and attached-case revisions.")
        key = self._identity(project_id, agent_id, goal_id)
        op = operation_id(payload["operation_id"])
        receipt_id = "caseattachment_" + digest({"operation_id": op})[:24]
        request_digest = digest({"agent_id": agent_id, "goal_id": goal_id, **payload})
        prior = self.state.db.get_record(project_id, "case_attachments", receipt_id)
        if prior:
            if prior["request_digest"] != request_digest:
                raise AuditError("operation_id already belongs to another case attachment")
            return prior["result"]
        workspace = self.state.workspace(project_id)
        source = snapshots.load(workspace, payload["dataset_snapshot_id"])
        if (
            source["kind"] != "dataset"
            or source["provenance"].get("expectation_status") != "reviewed"
            or not source["items"]
            or snapshots.summary(source)["missing_expectations"]
        ):
            raise AuditError("Select a saved case with a reviewed expected behavior.")
        draft = self.get(project_id, agent_id, goal_id)
        cases = self.attached_cases(project_id, agent_id, goal_id)
        if (draft or {}).get("revision", 0) != payload["expected_revision"] or (cases or {}).get(
            "revision", 0
        ) != payload["expected_cases_revision"]:
            raise AuditError("Evaluation or attached cases changed. Reload before attaching.")
        records = [source]
        if cases:
            records.append(snapshots.load(workspace, cases["dataset_snapshot_id"]))
        parents = copy.deepcopy((cases or {}).get("required_frozen_evaluations", []))
        limitations = []
        value = {field: copy.deepcopy(draft[field]) for field in FIELDS} if draft else None
        if value:
            evaluation = value["evaluation"]
            if evaluation.get("dataset_snapshot_id"):
                records.append(snapshots.load(workspace, evaluation["dataset_snapshot_id"]))
            if evaluation.get("evaluation_id"):
                evaluation_id = evaluation["evaluation_id"]
                assert_development_evaluator(workspace, evaluation_id)
                frozen = preparation.load(workspace, evaluation_id)
                if frozen["state"] != "frozen":
                    raise AuditError("The source evaluator is no longer frozen.")
                parent = {
                    "evaluation_id": evaluation_id,
                    "evaluator_digest": preparation.evaluator_identity(workspace, evaluation_id),
                }
                if parent not in parents:
                    parents.append(parent)
                # Unknown native input formats cannot be safely rewritten. Preserve every
                # frozen input in the next evaluator and supply reviewed cases separately.
                value["evaluation"] = {"mode": "create", "framework": "custom"}
                limitations.append(
                    "Create a new evaluator that retains all inputs from "
                    + evaluation_id
                    + " and includes the attached cases. Review its command and scorer."
                )
        combined = self._combine_cases(workspace, records)
        seed = {
            "id": key,
            "agent_id": agent_id,
            "goal_id": goal_id,
            "dataset_snapshot_id": combined["id"],
            "dataset_digest": combined["digest"],
            "case_count": len(combined["items"]),
            "required_frozen_evaluations": parents,
            "limitations": list(dict.fromkeys((cases or {}).get("limitations", []) + limitations)),
        }
        updated = None
        if value:
            value["evaluation"]["dataset_snapshot_id"] = combined["id"]
            value["limitations"] = list(dict.fromkeys(value["limitations"] + limitations))
            updated = self._prepare(project_id, agent_id, goal_id, validate(value), seed)
            updated["previous_design_revision"] = draft["revision"]
        with self.state.db.transaction() as tx:
            receipt = tx.get_record(project_id, "case_attachments", receipt_id)
            if receipt:
                if receipt["request_digest"] != request_digest:
                    raise AuditError("operation_id already belongs to another case attachment")
                return receipt["result"]
            current = tx.get_record(project_id, "measurement_designs", key)
            if (current or {}).get("revision", 0) != payload["expected_revision"]:
                raise AuditError("Evaluation changed. Reload before attaching cases.")
            saved = tx.put_record(
                project_id,
                "measurement_cases",
                key,
                seed,
                expected_revision=payload["expected_cases_revision"],
            )
            if updated:
                updated = tx.put_record(
                    project_id,
                    "measurement_designs",
                    key,
                    updated,
                    expected_revision=payload["expected_revision"],
                )
            result = {
                **saved,
                "design_revision": (updated or {}).get("revision", 0),
                "state": "draft_updated" if updated else "cases_saved",
                "operation_id": op,
            }
            tx.put_record(
                project_id,
                "case_attachments",
                receipt_id,
                {"id": receipt_id, "request_digest": request_digest, "result": result},
                expected_revision=0,
            )
            return result

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
        if accepted.get("required_case_snapshot_id"):
            Designs._frozen_contains_cases(workspace, record, accepted["required_case_snapshot_id"])
        expected = score_definition(accepted)
        if any(spec.get("scoring", {}).get(key) != value for key, value in expected.items()):
            raise AuditError("Evaluator scoring differs from the accepted measurement design.")
        for parent in accepted.get("required_frozen_evaluations", []):
            from agentagon.capabilities.evaluation.datasets import assert_development_evaluator

            assert_development_evaluator(workspace, parent["evaluation_id"])
            if (
                preparation.evaluator_identity(workspace, parent["evaluation_id"])
                != parent["evaluator_digest"]
            ):
                raise AuditError("The source frozen evaluator identity changed.")
            original = preparation.load(workspace, parent["evaluation_id"])
            inputs = {
                item["path"]: item
                for item in package["files"]
                if item["kind"] == "inputs" and not item.get("deleted")
            }
            for item in original["package"]["files"]:
                if item["kind"] != "inputs" or item.get("deleted"):
                    continue
                retained = inputs.get(item["path"])
                if (
                    not retained
                    or retained["digest"] != item["digest"]
                    or {"path": item["path"], "source": retained["artifact"]} not in spec["inputs"]
                    or hashlib.sha256(workspace.read_blob(retained["artifact"])).hexdigest()
                    != item["digest"]
                ):
                    raise AuditError(
                        "The new evaluator must retain every original frozen input: " + item["path"]
                    )
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
                "required_frozen_evaluations": accepted.get("required_frozen_evaluations", []),
                "context": "\n\n".join(accepted["background"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
