"""Confirmed application agents, versioned goals and retained measurement suites."""

import copy
import math
import re
import uuid
from pathlib import Path

from agentagon.capabilities import discovery
from agentagon.capabilities.experiments.spec import path as relative_path
from agentagon.capabilities.traces import snapshots
from agentagon.core.records import AuditError, digest, now
from agentagon.storage.state import identifier, private_directory

GOAL_CATEGORIES = {
    "correctness": "Task success and correctness",
    "reliability": "Reliability and tool use",
    "grounding": "Grounding and factuality",
    "safety": "Safety and policy",
    "security": "Security and permissions",
    "latency": "Latency",
    "cost": "Cost and efficiency",
    "interaction": "Interaction and instruction following",
    "custom": "Custom objective",
}
EXCLUDED = {".git", ".agentagon", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
MAX_DISCOVERY_IMPORTS = 50
MAX_DISCOVERY_IMPORT_BYTES = 20_000_000
DISCOVERY_PREFERENCES_ID = "preferences"
DISCOVERY_PREFERENCES = {
    "version": 1,
    "seen": False,
    "coding_review": False,
    "trace_metadata": False,
    "trace_cap": 100,
    "trace_connection_id": None,
}
_GENERIC_AGENT_TERMS = {
    "agent",
    "assistant",
    "graph",
    "main",
    "service",
    "workflow",
}


def _id(prefix):
    return prefix + "_" + uuid.uuid4().hex[:24]


def _metadata_strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _metadata_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _metadata_strings(item)
    elif isinstance(value, (str, int, float, bool)):
        yield str(value)


def _agent_terms(agent):
    values = [agent.get("name", "")]
    for scope in agent.get("code_scopes", []):
        path = Path(scope)
        values.extend((path.stem, path.parent.name))
    return {
        term
        for value in values
        for term in re.findall(r"[a-z0-9]+", value.casefold())
        if len(term) >= 4 and term not in _GENERIC_AGENT_TERMS
    }


def _text(value, label, limit=4000, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise AuditError(f"{label} must be {'0' if empty else '1'}–{limit} characters")
    return value.strip()


def _paths(values, root):
    if not isinstance(values, list) or len(values) > 100:
        raise AuditError("code scopes must be a bounded list of relative paths")
    result = []
    for value in values:
        value = relative_path(value)
        if set(Path(value).parts) & EXCLUDED:
            raise AuditError("private state and dependencies cannot be application-agent scopes")
        target = root / value
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise AuditError("agent scope escapes this project")
        if not target.exists():
            raise AuditError(f"agent scope does not exist: {value}")
        result.append(value)
    return list(dict.fromkeys(result))


def _selector(value):
    if not isinstance(value, dict) or set(value) - {
        "connection_id",
        "project",
        "filters",
        "name",
        "environment",
    }:
        raise AuditError("invalid agent trace selector")
    if len(str(value)) > 8000:
        raise AuditError("agent trace selector is too large")
    for key in ("connection_id", "project", "name", "environment"):
        if key in value:
            _text(value[key], key, 500)
    filters = value.get("filters", {})
    if not isinstance(filters, dict) or len(filters) > 20:
        raise AuditError("trace filters must be a bounded object")
    if any(
        not isinstance(k, str) or not isinstance(v, (str, bool, int, float))
        for k, v in filters.items()
    ):
        raise AuditError("trace filters must contain scalar values")
    return copy.deepcopy(value)


class Catalog:
    def __init__(self, state):
        self.state = state

    def agents(self, project_id):
        root = Path(self.state.project(project_id)["path"])
        eligibility = {}

        def eligible(path):
            if path not in eligibility:
                eligibility[path] = discovery.eligible_suggestion(root, path)
            return eligibility[path]

        return sorted(
            (
                agent
                for agent in self.state.db.list_records(project_id, "application_agents")
                if agent["status"] != "archived"
                and not (
                    agent["status"] == "suggested"
                    and agent.get("discovery_key")
                    and agent["code_scopes"]
                    and not any(eligible(path) for path in agent["code_scopes"])
                )
            ),
            key=lambda a: (a["status"] != "confirmed", a["name"].lower()),
        )

    def excluded_agents(self, project_id):
        """Return discovery identities deliberately kept out of the active inventory."""

        self.state.project(project_id)
        return sorted(
            (
                agent
                for agent in self.state.db.list_records(project_id, "application_agents")
                if agent.get("status") == "archived" and agent.get("discovery_key")
            ),
            key=lambda agent: (agent.get("name", "").casefold(), agent["id"]),
        )

    def discovery_preferences(self, project_id):
        self.state.project(project_id)
        saved = self.state.db.get_record(
            project_id, "discovery_preferences", DISCOVERY_PREFERENCES_ID
        )
        return {**DISCOVERY_PREFERENCES, **(saved or {})}

    def save_discovery_preferences(self, project_id, payload):
        if not isinstance(payload, dict) or set(payload) - {
            "coding_review",
            "trace_metadata",
            "trace_cap",
            "trace_connection_id",
        }:
            raise AuditError("invalid discovery preferences")
        current = self.discovery_preferences(project_id)
        coding_review = payload.get("coding_review", False)
        trace_metadata = payload.get("trace_metadata", False)
        trace_cap = payload.get("trace_cap", 100)
        connection_id = payload.get("trace_connection_id")
        if type(coding_review) is not bool or type(trace_metadata) is not bool:
            raise AuditError("discovery choices must be enabled or disabled")
        if type(trace_cap) is not int or not 1 <= trace_cap <= 100:
            raise AuditError("trace metadata cap must be between 1 and 100")
        if connection_id is not None:
            identifier(connection_id, "connection")
        record = {
            **current,
            "version": 1,
            "seen": True,
            "coding_review": coding_review,
            "trace_metadata": trace_metadata,
            "trace_cap": trace_cap,
            "trace_connection_id": connection_id,
        }
        return self.state.db.put_record(
            project_id,
            "discovery_preferences",
            DISCOVERY_PREFERENCES_ID,
            record,
            expected_revision=current.get("revision", 0),
        )

    def apply_coding_review(self, project_id, reviews, *, task_id=None):
        if not isinstance(reviews, list) or len(reviews) > 100:
            raise AuditError("coding-agent discovery review is invalid")
        suggestions = {
            agent["id"]: agent
            for agent in self.agents(project_id)
            if (
                agent["status"] == "suggested"
                or (agent.get("identity_review") or {}).get("source") == "discovery"
            )
            and len(agent.get("code_scopes", [])) == 1
        }
        updates = []
        seen = set()
        for review in reviews:
            if not isinstance(review, dict) or set(review) != {
                "id",
                "file",
                "name",
                "keep",
                "responsibility",
            }:
                raise AuditError("coding-agent discovery review is invalid")
            candidate_id = review["id"]
            if (
                candidate_id in seen
                or candidate_id not in suggestions
                or type(review["keep"]) is not bool
            ):
                raise AuditError("coding-agent discovery review does not match the local scan")
            record = suggestions[candidate_id]
            if review["file"] != record["code_scopes"][0]:
                raise AuditError("coding-agent discovery review does not match the local scan")
            seen.add(candidate_id)
            name = _text(review["name"], "agent name", 160)
            responsibility = _text(
                review["responsibility"],
                "agent responsibility",
                1000,
                empty=not review["keep"],
            )
            if not review["keep"] and responsibility:
                raise AuditError("excluded coding-agent candidates must not have a responsibility")
            manually_edited = (record.get("responsibility_inference") or {}).get(
                "state"
            ) == "edited" and bool(record.get("description", "").strip())
            active = record["status"] == "confirmed"
            edited_fields = set(record.get("user_edited_fields", []))
            evidence = [
                item
                for item in record.get("evidence", [])
                if not isinstance(item, dict) or item.get("kind") != "coding_agent_review"
            ]
            evidence.append(
                {
                    "kind": "coding_agent_review",
                    "decision": "preserve_manual_edit"
                    if manually_edited
                    else "keep"
                    if review["keep"]
                    else "exclusion_suggested"
                    if active
                    else "exclude",
                    "task_id": task_id,
                }
            )
            updates.append(
                {
                    **record,
                    "name": record["name"] if manually_edited or "name" in edited_fields else name,
                    "description": (
                        record["description"]
                        if manually_edited or "description" in edited_fields or not review["keep"]
                        else responsibility
                    ),
                    "status": "confirmed"
                    if active
                    else "suggested"
                    if manually_edited or review["keep"]
                    else "archived",
                    "evidence": evidence,
                    "responsibility_inference": (
                        record["responsibility_inference"]
                        if manually_edited
                        else {
                            "state": "inferred"
                            if review["keep"]
                            else "limited"
                            if active
                            else "excluded",
                            **(
                                {}
                                if review["keep"]
                                else {
                                    "reason": (
                                        "Managed review classified this code candidate as outside "
                                        "the application-agent inventory."
                                    )
                                }
                            ),
                            "task_id": task_id,
                            "at": now(),
                            "source_discovery_key": record.get("discovery_key"),
                        }
                    ),
                    "identity_review": (
                        record.get("identity_review", {})
                        if manually_edited or active
                        else {
                            "state": "suggested" if review["keep"] else "excluded",
                            **(
                                {}
                                if review["keep"]
                                else {
                                    "reason": (
                                        "Managed review classified this code candidate as outside "
                                        "the application-agent inventory."
                                    )
                                }
                            ),
                            "at": now(),
                            "source": "coding_agent_review",
                            "discovery_key": record.get("discovery_key"),
                        }
                    ),
                }
            )
        if updates:
            with self.state.db.transaction() as tx:
                for record in updates:
                    tx.put_record(
                        project_id,
                        "application_agents",
                        record["id"],
                        record,
                        expected_revision=record["revision"],
                    )
        return {"reviewed": len(updates), "kept": sum(r["status"] != "archived" for r in updates)}

    def apply_trace_metadata(self, project_id, connection, traces):
        if not isinstance(traces, list) or len(traces) > 100:
            raise AuditError("trace metadata sample is invalid")
        available = []
        for trace in traces:
            if not isinstance(trace, dict):
                continue
            text = " ".join(_metadata_strings(trace)).casefold()
            if text:
                available.append((trace, text))
        matches = 0
        updates = []
        used = set()
        for agent in self.agents(project_id):
            if agent["status"] == "archived" or not agent.get("code_scopes"):
                continue
            terms = _agent_terms(agent)
            scored = []
            for index, (trace, text) in enumerate(available):
                if index in used:
                    continue
                hits = [term for term in terms if term in text]
                score = sum(len(term) for term in hits)
                if hits and (len(hits) >= 2 or max(map(len, hits)) >= 6):
                    scored.append((score, index, trace, hits))
            if not scored:
                continue
            _score, index, trace, hits = max(scored, key=lambda item: item[0])
            used.add(index)
            evidence = [
                item
                for item in agent.get("evidence", [])
                if not isinstance(item, dict) or item.get("kind") != "trace_metadata"
            ]
            evidence.append(
                {
                    "kind": "trace_metadata",
                    "connection_id": connection["id"],
                    "provider": connection["provider"],
                    "trace_id": trace.get("trace_id") or trace.get("id"),
                    "matched_on": hits,
                }
            )
            updates.append({**agent, "evidence": evidence})
            matches += 1
        if updates:
            with self.state.db.transaction() as tx:
                for record in updates:
                    tx.put_record(
                        project_id,
                        "application_agents",
                        record["id"],
                        record,
                        expected_revision=record["revision"],
                    )
        return {"sampled": len(traces), "matched": matches}

    def agent(self, project_id, agent_id):
        self.state.project(project_id)
        identifier(agent_id, "agent")
        result = self.state.db.get_record(project_id, "application_agents", agent_id)
        if result is None:
            raise AuditError("application agent not found in this project")
        return result

    def save_agent(self, project_id, payload, agent_id=None, *, suggestion=None):
        root = self.state.workspace(project_id).root
        if set(payload) - {
            "name",
            "description",
            "code_scopes",
            "shared_dependencies",
            "trace_selector",
            "status",
            "expected_revision",
            "role",
        }:
            raise AuditError("unsupported application-agent fields")
        previous = (
            (self.state.db.get_record(project_id, "application_agents", agent_id) or {})
            if agent_id and suggestion
            else self.agent(project_id, agent_id)
            if agent_id
            else {}
        )
        record = {**previous, "id": agent_id or _id("agent"), "project_id": project_id}
        record["name"] = _text(payload.get("name", previous.get("name")), "agent name", 160)
        record["description"] = _text(
            payload.get("description", previous.get("description", "")), "description", empty=True
        )
        record["role"] = payload.get("role", previous.get("role", "unknown"))
        if not isinstance(record["role"], str) or record["role"] not in discovery.AGENT_ROLES:
            raise AuditError(
                "choose serving, background, evaluation, development_utility or unknown"
            )
        if "role" in payload:
            record["role_source"] = "user"
        if previous and suggestion is None:
            record["user_edited_fields"] = sorted(
                set(previous.get("user_edited_fields", []))
                | {
                    key
                    for key in (
                        "name",
                        "description",
                        "code_scopes",
                        "shared_dependencies",
                        "trace_selector",
                        "role",
                    )
                    if key in payload and payload[key] != previous.get(key)
                }
            )
        if (
            previous
            and "description" in payload
            and record["description"] != previous.get("description", "")
        ):
            record["responsibility_inference"] = {
                "state": "edited",
                "at": now(),
                "source_discovery_key": previous.get("discovery_key"),
            }
        record["code_scopes"] = _paths(
            payload.get("code_scopes", previous.get("code_scopes", [])), root
        )
        record["shared_dependencies"] = _paths(
            payload.get("shared_dependencies", previous.get("shared_dependencies", [])), root
        )
        record["trace_selector"] = _selector(
            payload.get("trace_selector", previous.get("trace_selector", {}))
        )
        if not record["code_scopes"] and not record["trace_selector"]:
            raise AuditError("bind the agent to code or a trace selector")
        record["status"] = payload.get("status", previous.get("status", "confirmed"))
        if record["status"] not in {"suggested", "confirmed", "archived"}:
            raise AuditError("unsupported application-agent status")
        if record["status"] == "confirmed" and not record["code_scopes"]:
            record["limitations"] = ["Trace-only agent: bind code before starting a measured fix."]
        else:
            record["limitations"] = []
        if suggestion:
            record.update(suggestion)
        binding = {k: record[k] for k in ("code_scopes", "shared_dependencies", "trace_selector")}
        changed = digest(binding) != previous.get("binding_digest")
        record.update(
            binding_digest=digest(binding),
            binding_version=previous.get("binding_version", 0) + int(changed),
            created_at=previous.get("created_at", now()),
            updated_at=now(),
        )
        with self.state.db.transaction() as tx:
            saved = tx.put_record(
                project_id,
                "application_agents",
                record["id"],
                record,
                expected_revision=payload.get("expected_revision", previous.get("revision", 0)),
            )
        return saved

    def exclude_suggestion(self, project_id, agent_id, reason, expected_revision):
        """Exclude a discovered agent while retaining its stable identity."""

        agent = self.agent(project_id, agent_id)
        if agent["status"] == "archived" or not agent.get("discovery_key"):
            raise AuditError("only an active discovered identity can be excluded")
        reason = _text(reason, "exclusion reason", 500)
        if type(expected_revision) is not int or expected_revision != agent["revision"]:
            raise AuditError("application record changed; reload before updating")
        reviewed_at = now()
        evidence = [*agent.get("evidence", [])]
        evidence.append(
            {"kind": "identity_review", "decision": "exclude", "reason": reason, "at": reviewed_at}
        )
        record = {
            **agent,
            "status": "archived",
            "evidence": evidence,
            "identity_review": {
                "state": "excluded",
                "reason": reason,
                "at": reviewed_at,
                "source": "user",
                "discovery_key": agent["discovery_key"],
            },
        }
        return self.state.db.put_record(
            project_id,
            "application_agents",
            agent_id,
            record,
            expected_revision=expected_revision,
        )

    def restore_suggestion(self, project_id, agent_id, expected_revision):
        """Explicitly restore an excluded identity directly to the usable inventory."""

        agent = self.agent(project_id, agent_id)
        if agent["status"] != "archived" or not agent.get("discovery_key"):
            raise AuditError("only an excluded discovered identity can be restored")
        if type(expected_revision) is not int or expected_revision != agent["revision"]:
            raise AuditError("application record changed; reload before updating")
        root = self.state.workspace(project_id).root
        if (
            agent.get("code_scopes")
            and not any(discovery.eligible_suggestion(root, path) for path in agent["code_scopes"])
            and not agent.get("trace_selector")
        ):
            raise AuditError(
                "the discovered source is no longer available; analyze the project again"
            )
        reviewed_at = now()
        evidence = [*agent.get("evidence", [])]
        evidence.append({"kind": "identity_review", "decision": "restore", "at": reviewed_at})
        record = {
            **agent,
            "status": "confirmed",
            "evidence": evidence,
            "identity_review": {
                "state": "confirmed",
                "at": reviewed_at,
                "source": "user",
                "discovery_key": agent["discovery_key"],
            },
        }
        return self.save_agent(
            project_id,
            {"status": "confirmed", "expected_revision": expected_revision},
            agent_id,
            suggestion={
                "evidence": record["evidence"],
                "identity_review": record["identity_review"],
            },
        )

    def activate_discovered(self, project_id):
        """An explicit detection adopts validated bindings without claiming human review."""
        activated, failures = [], []
        for agent in self.agents(project_id):
            if agent["status"] != "suggested" or not agent.get("discovery_key"):
                continue
            activated_at = now()
            inference = agent.get("responsibility_inference", {})
            if inference.get("state") == "pending":
                inference = {
                    **inference,
                    "state": "not_inferred",
                    "reason": "Agent detected. Responsibility can be described or assessed later.",
                }
            try:
                saved = self.save_agent(
                    project_id,
                    {"status": "confirmed", "expected_revision": agent["revision"]},
                    agent["id"],
                    suggestion={
                        "responsibility_inference": inference,
                        "evidence": [
                            *agent.get("evidence", []),
                            {
                                "kind": "identity_activation",
                                "decision": "automatic_activation",
                                "source": "discovery",
                                "at": activated_at,
                            },
                        ],
                        "identity_review": {
                            "state": "confirmed",
                            "decision": "automatic_activation",
                            "source": "discovery",
                            "at": activated_at,
                            "discovery_key": agent["discovery_key"],
                        },
                    },
                )
                activated.append(saved["id"])
            except AuditError as exc:
                failures.append(
                    {"agent_id": agent["id"], "name": agent["name"], "reason": str(exc)}
                )
        return {
            "activated": len(activated),
            "activated_ids": activated,
            "activation_failures": failures,
        }

    def detect(self, project_id, payload):
        """Run bounded local detection once per explicit user operation, without a model."""
        if not isinstance(payload, dict) or set(payload) - {"operation_id", "snapshot_ids"}:
            raise AuditError("use a detection operation_id and optional retained snapshot_ids")
        try:
            operation = str(uuid.UUID(payload.get("operation_id")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AuditError("operation_id must be a UUID") from exc
        snapshot_ids = payload.get("snapshot_ids", [])
        if not isinstance(snapshot_ids, list) or len(snapshot_ids) > 100:
            raise AuditError("choose at most 100 retained trace snapshots")
        for snapshot_id in snapshot_ids:
            identifier(snapshot_id, "snapshot")
        request_digest = digest({"snapshot_ids": snapshot_ids})
        key = "detection_" + digest({"operation_id": operation})[:24]
        previous = self.state.db.get_record(project_id, "agent_detections", key)
        if previous:
            if previous["request_digest"] != request_digest:
                raise AuditError("operation_id already belongs to another detection request")
            return previous["result"]
        workspace = self.state.workspace(project_id)
        for snapshot_id in snapshot_ids:
            if snapshots.load(workspace, snapshot_id)["kind"] != "traces":
                raise AuditError("agent detection accepts retained trace snapshots")
        result = {**self.discover(project_id, snapshot_ids=snapshot_ids), "operation_id": operation}
        result["count"] = sum(agent["status"] == "confirmed" for agent in result["agents"])
        result["state"] = "ready_with_limits" if result["limitations"] else "ready"
        with self.state.db.transaction() as tx:
            previous = tx.get_record(project_id, "agent_detections", key)
            if previous:
                if previous["request_digest"] != request_digest:
                    raise AuditError("operation_id already belongs to another detection request")
                return previous["result"]
            tx.put_record(
                project_id,
                "agent_detections",
                key,
                {"id": key, "request_digest": request_digest, "result": result},
                expected_revision=0,
            )
        return result

    def _create_discovered(self, project_id, payload, *, suggestion):
        key = suggestion["discovery_key"]
        agent_id = "agent_" + digest({"project_id": project_id, "discovery_key": key})[:24]
        current = self.state.db.get_record(project_id, "application_agents", agent_id)
        if current:
            return current
        try:
            return self.save_agent(
                project_id, {**payload, "expected_revision": 0}, agent_id, suggestion=suggestion
            )
        except AuditError:
            # Another explicit discovery may have won creation while this scan ran.
            current = self.state.db.get_record(project_id, "application_agents", agent_id)
            if current and current.get("discovery_key") == key:
                return current
            raise

    def discover(self, project_id, snapshot_ids=None, *, activate=True):
        workspace = self.state.workspace(project_id)
        existing = {
            a.get("discovery_key"): a
            for a in self.state.db.list_records(project_id, "application_agents")
            if a.get("discovery_key")
        }
        found = {}
        candidates, coverage, limits = discovery.scan(workspace.root)
        for entry in candidates:
            key = digest({"path": entry["path"], "symbol": entry["symbol"]})
            if key not in existing:
                # A manual code binding already represents this exact file and name.
                matches = [
                    a
                    for a in self.state.db.list_records(project_id, "application_agents")
                    if not a.get("discovery_key")
                    and a["name"] == entry["name"]
                    and a["code_scopes"] == [entry["path"]]
                ]
                if (
                    len(matches) == 1
                    and sum(
                        e["name"] == entry["name"] and e["path"] == entry["path"]
                        for e in candidates
                    )
                    == 1
                ):
                    if matches[0]["status"] != "archived":
                        found[matches[0]["id"]] = matches[0]
                    continue
                code_evidence = {"kind": "code", **entry, "captured_at": now()}
                context = discovery.source_context(workspace.root, entry["path"], entry["line"])
                if context:
                    code_evidence["context"] = context
                existing[key] = self._create_discovered(
                    project_id,
                    {
                        "name": entry["name"],
                        "description": "",
                        "code_scopes": [entry["path"]],
                        "status": "suggested",
                    },
                    suggestion={
                        "discovery_key": key,
                        "confidence": "inferred",
                        "role": entry["role"],
                        "role_source": entry["role_source"],
                        "identity_kind": entry.get("identity_kind", "declaration"),
                        "identity_limits": entry.get("identity_limits", []),
                        "dependency_proposals": entry.get("dependency_proposals", []),
                        "dependency_proposals_omitted": entry.get(
                            "dependency_proposals_omitted", 0
                        ),
                        "evidence": [code_evidence],
                        "responsibility_inference": {
                            "state": "not_inferred",
                            "reason": "Detected from source. Responsibility can be described or assessed later.",
                            "source_discovery_key": key,
                        },
                    },
                )
            if existing[key]["status"] != "archived":
                found[existing[key]["id"]] = existing[key]
        trace_items = 0
        trace_snapshots = 0
        import_count, import_bytes = 0, 0
        for path in private_directory(workspace, "imports").glob("snapshot_*.json"):
            if snapshot_ids is not None and path.stem not in snapshot_ids:
                continue
            if (
                import_count >= MAX_DISCOVERY_IMPORTS
                or trace_items >= 5000
                or len(found) >= discovery.MAX_CANDIDATES
            ):
                limits.append("Imported-trace scan limit reached; add remaining agents manually.")
                break
            if path.is_symlink():
                continue
            size = path.stat().st_size
            if import_bytes + size > MAX_DISCOVERY_IMPORT_BYTES:
                limits.append("Imported-trace byte limit reached; add remaining agents manually.")
                break
            import_count += 1
            import_bytes += size
            record = snapshots.load(workspace, path.stem)
            if record["kind"] != "traces":
                continue
            trace_snapshots += 1
            for item in record["items"]:
                if trace_items >= 5000 or len(found) >= discovery.MAX_CANDIDATES:
                    break
                trace_items += 1
                metadata = item.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                from agentagon.capabilities.traces.normalize import normalize

                try:
                    span = normalize(
                        item,
                        record["provenance"]["provider"],
                        record["provenance"].get("project"),
                        "$",
                    )
                    metadata = {**span["resource"], **span["attributes"], **metadata}
                    root_name = (
                        span["name"]
                        if not span["parent_span_ids"] or span["kind"] == "agent"
                        else None
                    )
                except (AuditError, ValueError, TypeError, KeyError):
                    root_name = None
                name = metadata.get("agent_name") or item.get("agent_name") or root_name
                if not isinstance(name, str) or not name.strip():
                    continue
                name = name.strip()[:160]
                selector = {
                    **(
                        {"connection_id": record["connection_id"]}
                        if record.get("connection_id")
                        else {}
                    ),
                    "name": name,
                    "filters": {"agent_name": name}
                    if metadata.get("agent_name") or item.get("agent_name")
                    else {},
                }
                if metadata.get("environment"):
                    selector["environment"] = str(metadata["environment"])
                if record["selection"].get("project"):
                    selector["project"] = record["selection"]["project"]
                key = digest(selector)
                if key in existing:
                    if existing[key]["status"] != "archived":
                        found[existing[key]["id"]] = existing[key]
                    continue
                evidence = {"kind": "traces", "snapshot_id": record["id"]}
                matches = [
                    a
                    for a in self.state.db.list_records(project_id, "application_agents")
                    if a["name"] == name
                    and (
                        a["trace_selector"] == selector
                        or (
                            not a["trace_selector"]
                            and a.get("discovery_key")
                            and metadata.get("code_path") in a["code_scopes"]
                        )
                    )
                ]
                if len(matches) == 1:
                    candidate = matches[0]
                    if candidate["status"] == "suggested" and not candidate["trace_selector"]:
                        candidate = self.save_agent(
                            project_id,
                            {"trace_selector": selector},
                            candidate["id"],
                            suggestion={"evidence": [*candidate["evidence"], evidence]},
                        )
                else:
                    candidate = self._create_discovered(
                        project_id,
                        {
                            "name": name,
                            "description": "",
                            "trace_selector": selector,
                            "status": "suggested",
                        },
                        suggestion={
                            "discovery_key": key,
                            "confidence": "inferred",
                            "evidence": [evidence],
                        },
                    )
                existing[key] = candidate
                if candidate["status"] != "archived":
                    found[candidate["id"]] = candidate
        if trace_items >= 5000 and not any("Imported-trace" in limit for limit in limits):
            limits.append("Imported-trace scan limit reached; add remaining agents manually.")
        activation = (
            self.activate_discovered(project_id)
            if activate
            else {"activated": 0, "activated_ids": [], "activation_failures": []}
        )
        limits.extend(
            f"{item['name']} could not be activated: {item['reason']}"
            for item in activation["activation_failures"]
        )
        return {
            **activation,
            "agents": self.agents(project_id),
            "discovered": len(found),
            "scanned_files": coverage["scanned_files"],
            "coverage": {
                **coverage,
                "import_snapshots": import_count,
                "import_bytes": import_bytes,
                "trace_snapshots": trace_snapshots,
                "trace_items": trace_items,
            },
            "limitations": limits,
        }

    def goals(self, project_id, agent_id):
        self.agent(project_id, agent_id)
        return sorted(
            (
                f
                for f in self.state.db.list_records(project_id, "goals")
                if f["agent_id"] == agent_id
            ),
            key=lambda f: f["created_at"],
            reverse=True,
        )

    def goal_record(self, project_id, agent_id, goal_id):
        self.agent(project_id, agent_id)
        identifier(goal_id, "goal")
        result = self.state.db.get_record(project_id, "goals", goal_id)
        if not result or result["agent_id"] != agent_id:
            raise AuditError("goal_record not found for this application agent")
        return result

    def save_goal(self, project_id, agent_id, payload):
        agent = self.agent(project_id, agent_id)
        if agent["status"] != "confirmed":
            raise AuditError("confirm the application agent before setting a goal_record")
        if set(payload) - {
            "name",
            "category",
            "objective",
            "ideal_behavior",
            "source",
            "operation_id",
        }:
            raise AuditError("unsupported goal_record fields")
        operation = None
        if "operation_id" in payload:
            try:
                operation = str(uuid.UUID(payload["operation_id"]))
            except (ValueError, TypeError, AttributeError) as exc:
                raise AuditError("operation_id must be a UUID") from exc
        request_digest = digest({k: v for k, v in payload.items() if k != "operation_id"})
        operation_key = (
            "goaloperation_" + digest({"agent_id": agent_id, "operation_id": operation})[:24]
        )
        if operation:
            prior = self.state.db.get_record(project_id, "goal_operations", operation_key)
            if prior:
                if prior["request_digest"] != request_digest:
                    raise AuditError("operation_id already belongs to another goal request")
                return self.goal_record(project_id, agent_id, prior["goal_id"])
        category = payload.get("category", "custom")
        if category not in GOAL_CATEGORIES:
            raise AuditError("choose a supported goal_record category")
        goal = _text(payload.get("objective"), "goal_record goal")
        source = copy.deepcopy(payload.get("source", {"kind": "goal"}))
        if (
            not isinstance(source, dict)
            or set(source) - {"kind", "count", "trace_snapshot_id", "issue_id", "audit_id"}
            or source.get("kind") not in {"goal", "issue", "recent_traces"}
        ):
            raise AuditError("unsupported goal_record source")
        if source["kind"] == "recent_traces":
            count = source.get("count", 100)
            if type(count) is not int or not 1 <= count <= 10000:
                raise AuditError("trace count must be between 1 and 10000")
            source["count"] = count
            if source.get("trace_snapshot_id"):
                snapshot = snapshots.load(
                    self.state.workspace(project_id), source["trace_snapshot_id"]
                )
                if snapshot["kind"] != "traces":
                    raise AuditError("select a trace snapshot")
        if source["kind"] == "issue":
            if not source.get("issue_id"):
                raise AuditError("select the saved issue for this goal_record")
            self.validate_evidence_reference(project_id, agent_id, source)
        target = payload.get("ideal_behavior")
        if target is not None:
            target = _text(target, "goal_record target", 1000, empty=True)
        record = {
            "id": (
                "goal_"
                + digest(
                    {"project_id": project_id, "agent_id": agent_id, "operation_id": operation}
                )[:24]
                if operation
                else _id("goal")
            ),
            "project_id": project_id,
            "agent_id": agent_id,
            "name": _text(
                payload.get("name") or GOAL_CATEGORIES[category], "goal_record name", 160
            ),
            "category": category,
            "objective": goal,
            "ideal_behavior": target,
            "source": source,
            "state": "active",
            "version": 1,
            "measurement": None,
            "guardrails": [],
            "created_at": now(),
        }
        with self.state.db.transaction() as tx:
            if operation:
                prior = tx.get_record(project_id, "goal_operations", operation_key)
                if prior:
                    if prior["request_digest"] != request_digest:
                        raise AuditError("operation_id already belongs to another goal request")
                    return tx.get_record(project_id, "goals", prior["goal_id"])
            saved = tx.put_record(project_id, "goals", record["id"], record)
            version = {
                "id": _id("goalversion"),
                "goal_id": record["id"],
                "definition": copy.deepcopy(saved),
                "created_at": now(),
            }
            tx.put_record(project_id, "goal_versions", version["id"], version)
            if operation:
                tx.put_record(
                    project_id,
                    "goal_operations",
                    operation_key,
                    {
                        "id": operation_key,
                        "request_digest": request_digest,
                        "goal_id": record["id"],
                    },
                    expected_revision=0,
                )
        return saved

    def validate_evidence_reference(self, project_id, agent_id, source):
        from agentagon.domain.issues import list_issues

        workspace = self.state.workspace(project_id)
        audit_ids = set()
        if source.get("audit_id"):
            audit_ids.add(workspace.read_audit(source["audit_id"])["audit_id"])
        if source.get("issue_id"):
            identifier(source["issue_id"], "issue")
            issue = next(
                (i for i in list_issues(workspace) if i["issue_id"] == source["issue_id"]), None
            )
            if issue is None:
                raise AuditError("issue not found in this project")
            if issue.get("agent_id") not in (None, agent_id):
                raise AuditError("issue belongs to another agent")
            issue_audits = set(issue["audit_ids"])
            if source.get("audit_id") and source["audit_id"] not in issue_audits:
                raise AuditError("selected issue does not belong to that audit")
            if not source.get("audit_id"):
                audit_ids.update(issue_audits)
        owners = {
            j["application_agent_id"]
            for j in self.state.db.list_records(project_id, "tasks")
            if j.get("application_agent_id")
            and (
                j.get("workflow_ids", {}).get("audit_id") in audit_ids
                or j.get("result", {}).get("audit_id") in audit_ids
            )
        }
        if owners and agent_id not in owners:
            raise AuditError("selected evidence belongs to a different application agent")

    def bind_measurement(
        self, project_id, agent_id, goal_id, payload, *, expected_binding_digest=None
    ):
        from agentagon.capabilities.evaluation.comparisons import _completed
        from agentagon.capabilities.experiments import preparation

        if set(payload) - {
            "evaluation_id",
            "baseline_id",
            "primary_metric",
            "guardrails",
            "expected_revision",
        }:
            raise AuditError("unsupported measurement fields")
        goal_record = self.goal_record(project_id, agent_id, goal_id)
        agent = self.agent(project_id, agent_id)
        if (
            expected_binding_digest is not None
            and agent["binding_digest"] != expected_binding_digest
        ):
            raise AuditError("agent binding changed; review the measurement before attaching it")
        workspace = self.state.workspace(project_id)
        evaluation_id = payload.get("evaluation_id") or (goal_record.get("measurement") or {}).get(
            "evaluation_id"
        )
        identifier(evaluation_id, "eval")
        from agentagon.capabilities.evaluation.datasets import assert_development_evaluator

        assert_development_evaluator(workspace, evaluation_id)
        evaluation = preparation.load(workspace, evaluation_id)
        evaluator_digest = preparation.evaluator_identity(workspace, evaluation_id)
        spec = evaluation["package"]["spec"]
        if not spec.get("scoring"):
            raise AuditError("evaluation requires accepted scoring before binding a measurement")
        previous = goal_record.get("measurement") or {}
        primary = (
            payload.get("primary_metric")
            or previous.get("primary_metric")
            or spec["scoring"].get("primary")
            or spec["scoring"].get("custom_metric")
            or next(iter(spec["metrics"]))
        )
        if primary not in spec["metrics"]:
            raise AuditError("primary metric must be an output of the frozen evaluator")
        scoring_mode = spec["scoring"]["mode"]
        ranked_metric = spec["scoring"].get(
            "primary" if scoring_mode == "primary" else "custom_metric"
        )
        if scoring_mode != "weighted" and primary != ranked_metric:
            raise AuditError(
                "goal_record metric must match the evaluator's accepted scoring objective; prepare a new evaluator to change it"
            )
        guards = payload.get("guardrails")
        if guards is None:
            guards = (
                previous.get("guardrails")
                if previous.get("evaluator_digest") == evaluator_digest
                else None
            )
        if guards is None:
            guards = [
                {
                    "metric": primary,
                    "op": "gte" if spec["metrics"][primary]["direction"] == "max" else "lte",
                    "bound": 0,
                    "reference": "baseline_delta",
                }
            ]
        if not isinstance(guards, list) or not guards or len(guards) > 100:
            raise AuditError("measurement needs at least one bounded regression guardrail")
        for guard in guards:
            if (
                not isinstance(guard, dict)
                or set(guard) != {"metric", "op", "bound", "reference"}
                or guard["metric"] not in spec["metrics"]
                or guard["op"] not in {"gte", "lte"}
                or guard["reference"] not in {"absolute", "baseline_delta", "baseline_ratio"}
            ):
                raise AuditError("invalid measurement guardrail")
            if type(guard["bound"]) not in (int, float) or not math.isfinite(guard["bound"]):
                raise AuditError("guardrail bounds must be finite numbers")
        baseline_id = payload.get("baseline_id")
        if "baseline_id" not in payload and previous.get("evaluator_digest") == evaluator_digest:
            baseline_id = previous.get("baseline_id")
        if baseline_id:
            baseline, _ = _completed(workspace, baseline_id)
            if (
                baseline["evaluation_id"] != evaluation_id
                or baseline["evaluator_digest"] != evaluator_digest
            ):
                raise AuditError("baseline must measure this exact frozen evaluation")
        goal_record["measurement"] = {
            "agent_binding_digest": agent["binding_digest"],
            "evaluation_id": evaluation_id,
            "evaluator_digest": evaluator_digest,
            "baseline_id": baseline_id,
            "primary_metric": primary,
            "scoring_mode": scoring_mode,
            "metrics": spec["metrics"],
            "guardrails": copy.deepcopy(guards),
            "profile_name": evaluation["profile_name"],
        }
        goal_record["guardrails"] = copy.deepcopy(guards)
        goal_record["version"] += 1
        goal_record["updated_at"] = now()
        with self.state.db.transaction() as tx:
            current_agent = tx.get_record(project_id, "application_agents", agent_id)
            if current_agent["binding_digest"] != agent["binding_digest"]:
                raise AuditError(
                    "agent binding changed; review the measurement before attaching it"
                )
            saved = tx.put_record(
                project_id,
                "goals",
                goal_id,
                goal_record,
                expected_revision=payload.get("expected_revision", goal_record["revision"]),
            )
            version = {
                "id": _id("goalversion"),
                "goal_id": goal_id,
                "definition": copy.deepcopy(saved),
                "created_at": now(),
            }
            tx.put_record(project_id, "goal_versions", version["id"], version)
        return saved

    def measurement_status(self, project_id, agent_id, goal_record):
        """Revalidate evidence; a saved identifier alone is not measurement readiness."""
        from agentagon.capabilities.evaluation.comparisons import _completed
        from agentagon.capabilities.experiments import preparation
        from agentagon.workflows.evaluate.designs import Designs

        measurement = goal_record.get("measurement")
        evaluation = {
            "ready": False,
            "reason": "Create or select a reviewed evaluation for this goal_record.",
        }
        baseline = {"ready": False, "reason": "Run a baseline after evaluation preparation."}
        workspace = self.state.workspace(project_id)
        try:
            accepted = Designs(self.state, self).accepted(project_id, agent_id, goal_record["id"])
            selected = (accepted["evaluation"].get("evaluation_id") if accepted else None) or (
                measurement or {}
            ).get("evaluation_id")
            if not selected:
                return {"evaluation": evaluation, "baseline": baseline}
            if accepted:
                Designs.validate_evaluator(workspace, accepted, selected)
            if not measurement or measurement["evaluation_id"] != selected:
                return {
                    "evaluation": {
                        "ready": True,
                        "reason": "Accepted frozen evaluation available.",
                    },
                    "baseline": {
                        "ready": False,
                        "reason": "Run a baseline for the accepted evaluator.",
                    },
                }
            if (
                measurement.get("agent_binding_digest")
                != self.agent(project_id, agent_id)["binding_digest"]
            ):
                raise AuditError(
                    "Agent scope changed. Review and bind this measurement to the current agent."
                )
            identity = preparation.evaluator_identity(workspace, selected)
            if identity != measurement["evaluator_digest"]:
                raise AuditError("Frozen evaluator identity changed. Prepare a new evaluation.")
            evaluation = {"ready": True, "reason": "Frozen evaluation available."}
            if measurement.get("baseline_id"):
                record, _ = _completed(workspace, measurement["baseline_id"])
                if (
                    record["evaluation_id"] != measurement["evaluation_id"]
                    or record["evaluator_digest"] != identity
                ):
                    raise AuditError("Baseline does not measure this frozen evaluator.")
                baseline = {"ready": True, "reason": "Verified baseline available."}
        except (AuditError, OSError, KeyError, ValueError) as exc:
            reason = f"Measurement evidence unavailable: {exc}"
            if not evaluation["ready"]:
                evaluation["reason"] = reason
            baseline["reason"] = reason
        return {"evaluation": evaluation, "baseline": baseline}

    def suite(self, project_id, agent_id, goal_id, *, permitted_paths=None):
        from agentagon.capabilities.experiments.checkouts import under

        active = self.goal_record(project_id, agent_id, goal_id) if goal_id else {"id": None}
        agent = self.agent(project_id, agent_id)
        permitted_paths = permitted_paths or agent["code_scopes"]
        affected = [
            a
            for a in self.agents(project_id)
            if a["status"] == "confirmed"
            and (
                a["id"] == agent_id
                or any(
                    under(path, scope) or under(scope, path)
                    for path in permitted_paths
                    for scope in a["code_scopes"] + a["shared_dependencies"]
                )
            )
        ]
        members, missing = [], []
        for affected_agent in affected:
            goals = [
                f for f in self.goals(project_id, affected_agent["id"]) if f["state"] == "active"
            ]
            if not goals and goal_id:
                missing.append(
                    {
                        "application_agent_id": affected_agent["id"],
                        "name": affected_agent["name"],
                        "reason": "Changes affect this agent. Add a goal_record and establish its baseline, or narrow permitted changes.",
                    }
                )
            for goal_record in goals:
                readiness = self.measurement_status(project_id, affected_agent["id"], goal_record)
                if not readiness["baseline"]["ready"]:
                    missing.append(
                        {
                            "application_agent_id": affected_agent["id"],
                            "goal_id": goal_record["id"],
                            "name": goal_record["name"],
                            "reason": readiness["baseline"]["reason"],
                        }
                    )
                    continue
                members.append(
                    {
                        "application_agent_id": affected_agent["id"],
                        "goal_id": goal_record["id"],
                        "goal_version": goal_record["version"],
                        "name": goal_record["name"],
                        **copy.deepcopy(goal_record["measurement"]),
                    }
                )
        manifest = {
            "version": 1,
            "agent_id": agent_id,
            "goal_id": active["id"],
            "affected_agents": [
                {"agent_id": a["id"], "binding_digest": a["binding_digest"]} for a in affected
            ],
            "members": members,
        }
        return {**manifest, "digest": digest(manifest), "missing": missing}

    def readiness(self, project_id, agent_id):
        agent = self.agent(project_id, agent_id)
        goals = [f for f in self.goals(project_id, agent_id) if f["state"] == "active"]
        statuses = [self.measurement_status(project_id, agent_id, f) for f in goals]
        accepted = [s for s in statuses if s["evaluation"]["ready"]]
        baselined = [s for s in statuses if s["baseline"]["ready"]]
        code = agent["status"] == "confirmed" and bool(agent["code_scopes"])
        ready = (
            code
            and bool(goals)
            and len(baselined) == len(goals)
            and not self.suite(project_id, agent_id, goals[0]["id"])["missing"]
        )
        return {
            "evaluation": {
                "ready": bool(accepted),
                "reason": "Frozen evaluation available."
                if accepted
                else "Create or select a reviewed evaluation for this goal_record.",
            },
            "baseline": {
                "ready": bool(baselined),
                "reason": "Verified baseline available."
                if baselined
                else "Run a baseline after evaluation preparation.",
            },
            "optimize": {
                "ready": ready,
                "reason": "All active goals have evaluations and baselines."
                if ready
                else "Confirm code scope and establish a baseline for every active goal_record.",
            },
        }

    def investigation(self, project_id, agent_id, goal_id, options):
        agent = self.agent(project_id, agent_id)
        goal_record = self.goal_record(project_id, agent_id, goal_id)
        snapshot_id = options.get("trace_snapshot_id") or goal_record["source"].get(
            "trace_snapshot_id"
        )
        trace = None
        if snapshot_id:
            trace = snapshots.load(self.state.workspace(project_id), snapshot_id)
            if trace["kind"] != "traces":
                raise AuditError("audit evidence must be a trace snapshot")
            if agent["trace_selector"] or goal_record["source"].get("count"):
                trace = snapshots.select_traces(
                    self.state.workspace(project_id),
                    project_id,
                    snapshot_id,
                    agent["trace_selector"],
                    goal_record["source"].get("count"),
                )
                snapshot_id = trace["id"]
        if goal_record["source"]["kind"] == "recent_traces" and trace is None:
            raise AuditError("Import and select traces before reviewing recent failures.")
        plan = {
            "version": 1,
            "agent_id": agent_id,
            "binding_version": agent["binding_version"],
            "binding_digest": agent["binding_digest"],
            "goal_id": goal_id,
            "goal_version": goal_record["version"],
            "goal": goal_record["objective"],
            "source": copy.deepcopy(goal_record["source"]),
            "code_scopes": agent["code_scopes"],
            "trace_selector": agent["trace_selector"],
            "trace_snapshot_id": snapshot_id,
            "trace_digest": trace["digest"] if trace else None,
            "trace_cap": goal_record["source"].get("count"),
            "rubric": "audit-v1",
            "limits": [
                "Investigation covers the selected agent and evidence; unrelated issues are not resolved by omission."
            ],
        }
        return {**plan, "digest": digest(plan)}

    def metrics(self, project_id, agent_id):
        from agentagon.capabilities.evaluation.comparisons import _completed

        goals = self.goals(project_id, agent_id)
        goal_ids = {f["id"] for f in goals}
        versions = [
            r["definition"]
            for r in self.state.db.list_records(project_id, "goal_versions")
            if r["goal_id"] in goal_ids
        ]
        workspace = self.state.workspace(project_id)
        series, seen, limits = {}, set(), []

        def rows(goal_id, goal_name, measurement, metrics, run):
            execution = (
                digest(
                    {
                        "profile": run["profile"],
                        "limits": run["limits"],
                        "scoring": run["spec"].get("scoring"),
                    }
                )
                if run
                else None
            )
            for name, definition in metrics.items():
                key = f"{goal_id}:{measurement['evaluator_digest']}:{execution or 'unmeasured'}:{name}"
                yield (
                    name,
                    series.setdefault(
                        key,
                        {
                            "id": key,
                            "name": name,
                            "unit": definition["unit"],
                            "direction": definition["direction"],
                            "goal_id": goal_id,
                            "goal_name": goal_name,
                            "evaluator_id": measurement["evaluation_id"],
                            "evaluator_digest": measurement["evaluator_digest"],
                            "execution_digest": execution,
                            "profile_name": run["profile_name"] if run else None,
                            "measurements": [],
                        },
                    ),
                )

        for goal_record in versions:
            measurement = goal_record.get("measurement")
            if not measurement:
                continue
            baseline_id = measurement.get("baseline_id")
            if baseline_id and (goal_record["id"], baseline_id) in seen:
                continue
            baseline, run = None, None
            if baseline_id:
                seen.add((goal_record["id"], baseline_id))
                try:
                    baseline, run = _completed(workspace, baseline_id)
                except (AuditError, OSError) as exc:
                    limits.append(f"Baseline {baseline_id} is unavailable: {exc}")
            for name, row in rows(
                goal_record["id"], goal_record["name"], measurement, measurement["metrics"], run
            ):
                if baseline is None:
                    continue
                value = baseline["measurement"]["metrics"].get(name)
                row["measurements"].append(
                    {
                        "value": value,
                        "created_at": baseline["created_at"],
                        "source_revision": baseline["source_revision"],
                        "baseline_id": baseline_id,
                        "state": "measured" if type(value) in (int, float) else "missing",
                    }
                )
        # Include retained optimization outcomes without promoting them to reference baselines.
        from agentagon.capabilities.experiments import suites
        from agentagon.capabilities.experiments.store import load_run

        run_ids = {
            j.get("workflow_ids", {}).get("run_id") or j.get("result", {}).get("run_id")
            for j in self.state.db.list_records(project_id, "tasks")
            if j["kind"] == "optimize"
            and any(
                member["goal_id"] in goal_ids
                for member in j["options"].get("suite_manifest", {}).get("members", [])
            )
        } - {None}
        for run_id in sorted(run_ids):
            try:
                state = suites.status(workspace, run_id)
                if not state.get("result"):
                    continue
                outcome = suites.verify_outcome(
                    workspace,
                    run_id,
                    {
                        **state["manifest"],
                        "digest": state["manifest_digest"],
                        "missing": [],
                    },
                )
                for candidate_id, verified in outcome["finalists"].items():
                    finalist = state["finalists"][candidate_id]
                    for member in verified.get("members", []):
                        if member["goal_id"] not in goal_ids:
                            continue
                        child = load_run(workspace, member["executions"]["finalist"])
                        for name, row in rows(
                            member["goal_id"],
                            member["name"],
                            member,
                            child["spec"]["metrics"],
                            child,
                        ):
                            row["measurements"].append(
                                {
                                    "value": member["finalist"].get(name),
                                    "created_at": finalist.get("completed_at", state["created_at"]),
                                    "source_revision": finalist["binding"]["source_revision"],
                                    "run_id": run_id,
                                    "candidate_id": candidate_id,
                                    "state": "verified"
                                    if verified["passed"]
                                    else "guardrail_failed",
                                }
                            )
            except (AuditError, OSError, KeyError) as exc:
                limits.append(f"Fix measurement {run_id} is unavailable: {exc}")
        guards = []
        for goal_record in goals:
            measurement = goal_record.get("measurement")
            if not measurement:
                continue
            for index, guard in enumerate(measurement["guardrails"]):
                guards.append(
                    {
                        "id": f"{goal_record['id']}:{index}",
                        "name": f"{goal_record['name']}: {guard['metric']}",
                        "goal_id": goal_record["id"],
                        "state": "active"
                        if self.measurement_status(project_id, agent_id, goal_record)["baseline"][
                            "ready"
                        ]
                        else "needs_baseline",
                        "threshold": guard["bound"],
                        **guard,
                    }
                )
        return {
            "metrics": list(series.values()),
            "guardrails": guards,
            "limitations": list(dict.fromkeys(limits)),
        }
