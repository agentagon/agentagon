"""Native evaluator discovery, pinned authoring plans and explicit dataset exports."""

import copy
import fcntl
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from agentagon.capabilities.evaluation.datasets import assert_development
from agentagon.capabilities.experiments.spec import NAME, command, path
from agentagon.capabilities.traces import snapshots
from agentagon.core.records import AuditError, digest, load_json, now
from agentagon.storage.state import identifier, private_directory

FRAMEWORKS = {"braintrust", "deepeval", "pytest", "custom"}
MAX_FILES = 500
MAX_FILE_BYTES = 200_000
MAX_SCAN_BYTES = 5_000_000
_EXCLUDED = {".git", ".agentagon", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".json", ".toml", ".ini", ".cfg", ".sh"}


def _source(workspace, name):
    relative = path(name)
    target = workspace.root / relative
    if relative == "." or any(part in _EXCLUDED for part in Path(relative).parts):
        raise AuditError("choose evaluator source outside private or generated directories")
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise AuditError("evaluator source cannot use symlinks")
    if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
        raise AuditError("evaluator source must be a regular file no larger than 200 KB")
    with target.open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise AuditError("evaluator source exceeds 200 KB")
    return content


def discover(workspace, code_scopes=None):
    """Inspect bounded source text only; never import code or run a discovered command."""
    if code_scopes is not None and (not isinstance(code_scopes, list) or not code_scopes):
        raise AuditError("discovery scopes must be a nonempty list of relative paths")
    scopes = [path(scope) for scope in (code_scopes or ["."])]
    if len(scopes) > 100:
        raise AuditError("choose at most 100 discovery scopes")
    pending, candidates = [workspace.root], []
    visited = scanned = total = 0
    truncated = False
    while pending and not truncated:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > 10000 or scanned >= MAX_FILES or total >= MAX_SCAN_BYTES:
                    truncated = True
                    break
                if entry.name in _EXCLUDED or entry.is_symlink():
                    continue
                relative = str(Path(entry.path).relative_to(workspace.root))
                if not any(
                    scope == "."
                    or relative == scope
                    or relative.startswith(scope + "/")
                    or scope.startswith(relative + "/")
                    for scope in scopes
                ):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                    continue
                if (
                    not entry.is_file(follow_symlinks=False)
                    or Path(relative).suffix not in _EXTENSIONS
                ):
                    continue
                scanned += 1
                try:
                    content = _source(workspace, relative)
                    total += len(content)
                    text = content.decode("utf-8")
                except (AuditError, OSError, UnicodeError):
                    continue
                framework = None
                pattern = None
                for name, expression in (
                    (
                        "braintrust",
                        r"(?:from\s+braintrust\s+import|import\s+braintrust\b|from\s+['\"]braintrust['\"]|require\(['\"]braintrust['\"]\))",
                    ),
                    ("deepeval", r"(?:from\s+deepeval(?:\.|\s)|import\s+deepeval\b)"),
                    ("pytest", r"(?:import\s+pytest\b|from\s+pytest\s+import|def\s+test_\w+\s*\()"),
                ):
                    pattern = re.search(expression, text)
                    if pattern:
                        framework = name
                        break
                if framework is None and re.search(
                    r"(?:eval|benchmark|test)[_.-]", Path(relative).name, re.I
                ):
                    framework = "custom"
                if framework:
                    native_entrypoint = True
                    if framework == "braintrust":
                        native_entrypoint = bool(re.search(r"\b(?:Eval|asyncEval)\s*\(", text))
                    elif framework == "deepeval":
                        native_entrypoint = bool(re.search(r"\bdef\s+test_\w+\s*\(", text))
                    argv = {
                        "braintrust": ["bt", "eval", relative],
                        "deepeval": ["deepeval", "test", "run", relative],
                        "pytest": ["python", "-m", "pytest", relative],
                        "custom": ["python", relative] if relative.endswith(".py") else [],
                    }[framework]
                    if not native_entrypoint:
                        argv = []
                    evidence = {
                        "path": relative,
                        "line": text.count("\n", 0, pattern.start()) + 1 if pattern else 1,
                        "detail": f"{framework} source signature"
                        if pattern
                        else "Evaluation-like filename; inspect its actual entrypoint",
                    }
                    item = {
                        "framework": framework,
                        "entrypoint": relative,
                        "command": {"argv": argv, "cwd": "."} if argv else None,
                        "evidence": [evidence],
                        "confidence": "medium" if pattern and native_entrypoint else "low",
                        "source_digest": hashlib.sha256(content).hexdigest(),
                    }
                    if not native_entrypoint:
                        evidence["detail"] += "; choose its actual evaluation command"
                    item["id"] = "discovery_" + digest(item)[:24]
                    candidates.append(item)
                if Path(relative).name == "package.json":
                    try:
                        scripts = json.loads(text).get("scripts", {})
                    except (ValueError, AttributeError):
                        continue
                    if not isinstance(scripts, dict):
                        continue
                    for name, native in scripts.items():
                        if not isinstance(native, str) or not re.search(
                            r"eval|test|bench", name, re.I
                        ):
                            continue
                        item = {
                            "framework": "braintrust" if "bt eval" in native else "custom",
                            "entrypoint": relative,
                            "command": {
                                "argv": ["npm", "run", name],
                                "cwd": str(Path(relative).parent),
                            },
                            "evidence": [
                                {
                                    "path": relative,
                                    "line": 1,
                                    "detail": f"package.json script {name}",
                                }
                            ],
                            "confidence": "high",
                            "source_digest": hashlib.sha256(content).hexdigest(),
                        }
                        item["id"] = "discovery_" + digest(item)[:24]
                        candidates.append(item)
    return {
        "candidates": sorted(candidates, key=lambda item: (item["entrypoint"], item["id"])),
        "scanned_files": scanned,
        "truncated": truncated,
        "limitations": [
            "Signatures suggest entrypoints; they do not establish evaluator quality or permission to execute."
        ],
    }


def _dataset(workspace, snapshot_id):
    record = snapshots.load(workspace, snapshot_id)
    if record["kind"] != "dataset":
        raise AuditError("select a dataset snapshot")
    assert_development(workspace, record)
    return record


def prepare_plan(workspace, payload):
    """Pin the user's native execution choices for later host authoring and review."""
    allowed = {
        "mode",
        "framework",
        "entrypoint",
        "command",
        "dataset_snapshot_id",
        "scorer",
        "output_mapping",
    }
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise AuditError("unsupported evaluator plan fields")
    if payload.get("mode") not in {"reuse", "create"} or payload.get("framework") not in FRAMEWORKS:
        raise AuditError("choose reuse/create and a supported evaluator framework")
    body = {
        "version": 1,
        "project_id": workspace.project_id,
        "origin": str(workspace.root),
        **copy.deepcopy(payload),
    }
    blockers, sources = [], []
    entrypoint = payload.get("entrypoint")
    if entrypoint:
        content = _source(workspace, entrypoint)
        sources.append({"path": path(entrypoint), "digest": hashlib.sha256(content).hexdigest()})
    elif payload["mode"] == "reuse":
        blockers.append("Choose the existing evaluator entrypoint.")
    native = payload.get("command")
    if native:
        body["command"] = command(native)
    else:
        blockers.append("Agree on the native evaluation command.")
    scorer = payload.get("scorer", "")
    if not isinstance(scorer, str) or len(scorer) > 4000:
        raise AuditError("scorer must be a description or source reference up to 4000 characters")
    if not scorer.strip():
        blockers.append("Identify the scorer and accepted correctness rule.")
    mapping = payload.get("output_mapping", {})
    if (
        not isinstance(mapping, dict)
        or len(mapping) > 100
        or any(
            not isinstance(key, str)
            or not NAME.fullmatch(key)
            or not isinstance(value, str)
            or not value.strip()
            or len(value) > 300
            for key, value in mapping.items()
        )
    ):
        raise AuditError("output mapping must map metric names to bounded native result paths")
    if not mapping:
        blockers.append("Map native results to named metrics before measured execution.")
    if payload.get("dataset_snapshot_id"):
        record = _dataset(workspace, payload["dataset_snapshot_id"])
        body["dataset_digest"] = record["digest"]
        if snapshots.summary(record)["missing_expectations"]:
            blockers.append(
                "Missing references need accepted expectations or a reviewed reference-free correctness rule."
            )
    body.update(source_files=sources, blockers=blockers, state="draft" if blockers else "ready")
    checksum = digest(body)
    result = {**body, "id": "evalplan_" + checksum[:24], "digest": checksum}
    workspace.initialize()
    target = private_directory(workspace, "evaluator-plans") / f"{result['id']}.json"
    if target.exists() and load_json(target) != result:
        raise AuditError("saved evaluator plan changed")
    workspace.write(target, result)
    return result


def validate_plan(workspace, plan):
    content = {key: value for key, value in plan.items() if key not in {"id", "digest"}}
    if (
        digest(content) != plan.get("digest")
        or plan.get("project_id") != workspace.project_id
        or plan.get("origin") != str(workspace.root)
    ):
        raise AuditError("evaluator plan integrity or project binding changed")
    for source in plan["source_files"]:
        if hashlib.sha256(_source(workspace, source["path"])).hexdigest() != source["digest"]:
            raise AuditError("native evaluator source changed; review a new plan")
    if (
        plan.get("dataset_snapshot_id")
        and _dataset(workspace, plan["dataset_snapshot_id"])["digest"] != plan["dataset_digest"]
    ):
        raise AuditError("native evaluator dataset changed")
    return plan


_LOADER = '''"""Exported native evaluation helper. Supply reviewed application/scorer callables."""
import json
from pathlib import Path

def load_cases():
    return [json.loads(line) for line in Path(__file__).with_name("dataset.jsonl").read_text().splitlines() if line]

'''
_BRAINTRUST = """def run(task, scores, project="Agentagon local evaluation"):
    from braintrust import Eval
    return Eval(project, data=load_cases(), task=task, scores=scores, no_send_logs=True)
"""
_DEEPEVAL = """def load_goldens():
    from deepeval.dataset import Golden
    goldens = []
    for case in load_cases():
        if not isinstance(case["input"], str) or ("expected" in case and not isinstance(case["expected"], str)):
            raise ValueError("Structured or conversation data needs an explicit case_factory; load_cases preserves its full shape.")
        values = {"input": case["input"], "additional_metadata": case["metadata"]}
        if "expected" in case:
            values["expected_output"] = case["expected"]
        goldens.append(Golden(**values))
    return goldens

def run(task, metrics, case_factory=None):
    from deepeval import evaluate
    from deepeval.test_case import LLMTestCase
    source = load_cases()
    if case_factory is None and any(not isinstance(case["input"], str) or ("expected" in case and not isinstance(case["expected"], str)) for case in source):
        raise ValueError("Provide a reviewed case_factory for structured or multi-turn evaluation; no conversation is flattened.")
    cases = []
    for case in source:
        actual = task(case["input"])
        if case_factory is not None:
            cases.append(case_factory(case, actual))
        else:
            values = {"input": case["input"], "actual_output": actual, "additional_metadata": case["metadata"]}
            if "expected" in case:
                values["expected_output"] = case["expected"]
            cases.append(LLMTestCase(**values))
    return evaluate(test_cases=cases, metrics=metrics)
"""
_MAIN = """
if __name__ == "__main__":
    import argparse
    import importlib
    import math
    import os
    import re

    parser = argparse.ArgumentParser(description="Run reviewed native callables with frozen exported data")
    parser.add_argument("--task", required=True, help="module:callable for the current application")
    parser.add_argument("--scorers", required=True, help="module:factory returning native scorers")
    parser.add_argument("--metrics", required=True, help="module:callable mapping native results to named numeric metrics")
    args = parser.parse_args()
    def resolve(value):
        module, name = value.split(":", 1)
        return getattr(importlib.import_module(module), name)
    result = run(resolve(args.task), resolve(args.scorers)())
    metrics = resolve(args.metrics)(result)
    if not isinstance(metrics, dict) or not metrics or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", name) or type(value) not in (int, float) or not math.isfinite(value) for name, value in metrics.items()):
        raise ValueError("The reviewed result mapping must return named finite numeric metrics")
    Path(os.environ["AGENTAGON_RESULT_PATH"]).write_text(json.dumps({"metrics": metrics}, allow_nan=False))
"""


def _events(record):
    result = []
    for index, item in enumerate(record["items"]):
        metadata = {"source_metadata": copy.deepcopy(item.get("metadata") or {})}
        metadata["agentagon"] = {
            "snapshot_id": record["id"],
            "snapshot_digest": record["digest"],
            "example_id": item["id"],
            "source": item.get("source", {}),
            "provenance": record["provenance"],
        }
        if "observed_output" in item:
            metadata["agentagon"]["observed_output"] = item["observed_output"]
        event = {
            "id": "agentagon_" + digest([record["digest"], index, item["id"]])[:32],
            "input": item.get("input"),
            "metadata": metadata,
        }
        if (
            item.get("expected_present", item.get("expected") is not None)
            and item.get("expected") is not None
        ):
            event["expected"] = item["expected"]
        result.append(event)
    if len(json.dumps(result, allow_nan=False).encode()) > snapshots.MAX_IMPORT_BYTES:
        raise AuditError("export payload exceeds 20 MB; select a smaller snapshot")
    return result


def export_bundle(workspace, snapshot_id, framework):
    if framework not in {"braintrust", "deepeval"}:
        raise AuditError("native dataset export supports Braintrust or DeepEval")
    record = _dataset(workspace, snapshot_id)
    events = _events(record)
    body = {
        "framework": framework,
        "snapshot_id": snapshot_id,
        "snapshot_digest": record["digest"],
        "count": len(events),
        "missing_expectations": snapshots.summary(record)["missing_expectations"],
        "state": "draft",
        "limitations": [
            "Review task, scorer, output mapping and dependencies before running. Missing expectations stay missing."
        ],
    }
    if framework == "deepeval" and any(not isinstance(item["input"], str) for item in events):
        body["limitations"].append(
            "Structured and multi-turn inputs require an explicit DeepEval case_factory; the export does not infer conversation semantics."
        )
    generated = {
        "dataset.jsonl": "".join(
            json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n" for item in events
        ),
        "native_eval.py": _LOADER
        + (_BRAINTRUST if framework == "braintrust" else _DEEPEVAL)
        + _MAIN,
    }
    files = [
        {
            "name": name,
            "artifact": workspace.blob(value.encode(), ".txt"),
            "digest": hashlib.sha256(value.encode()).hexdigest(),
        }
        for name, value in generated.items()
    ]
    body["files"] = files
    checksum = digest(body)
    receipt = {**body, "id": "export_" + checksum[:24], "digest": checksum}
    workspace.write(private_directory(workspace, "exports") / f"{receipt['id']}.json", receipt)
    return receipt


def _connection(connection):
    from agentagon.capabilities.traces.providers import DEFAULT_ENDPOINTS
    from agentagon.storage.config import validate_endpoint

    if connection.get("provider") != "braintrust":
        raise AuditError("dataset publication currently supports Braintrust only")
    return {
        "id": connection["id"],
        "project_id": connection["project_id"],
        "provider": "braintrust",
        "project": connection.get("project"),
        "endpoint": validate_endpoint(
            connection.get("endpoint") or DEFAULT_ENDPOINTS["braintrust"]
        ),
    }


def preview_publication(workspace, connection, snapshot_id, selection):
    if not isinstance(selection, dict) or set(selection) - {"project", "name"}:
        raise AuditError("publication requires a destination project and dataset name")
    if connection.get("project_id") != workspace.project_id:
        raise AuditError("connection not found in this project")
    if "project" in selection and selection["project"] != connection.get("project"):
        raise AuditError("use the connected provider project")
    from agentagon.capabilities.traces.providers import _text

    record = _dataset(workspace, snapshot_id)
    if not record["items"] or len(record["items"]) > 1000:
        raise AuditError("publish between 1 and 1000 dataset examples")
    destination = {
        "project": _text(
            selection.get("project") or connection.get("project"), "destination project"
        ),
        "name": _text(selection.get("name"), "dataset name", maximum=150),
    }
    bound_connection = _connection(connection)
    body = {
        "project_id": workspace.project_id,
        "origin": str(workspace.root),
        "connection": bound_connection,
        "destination": destination,
        "snapshot_id": snapshot_id,
        "snapshot_digest": record["digest"],
        "events": _events(record),
        "missing_expectations": snapshots.summary(record)["missing_expectations"],
    }
    destination["name"] += " [agentagon-" + digest(body)[:16] + "]"
    checksum = digest(body)
    preview_id = "publication_" + checksum[:24]
    payload = {
        **body,
        "preview_id": preview_id,
        "digest": checksum,
        "count": len(record["items"]),
        "state": "preview",
        "warning": "Uploads these exact examples to Braintrust. Missing expectations remain absent; observed outputs are metadata, not labels.",
    }
    database = workspace.metadata_store
    with database.transaction() as transaction:
        existing = transaction.get_record(workspace.project_id, "dataset_publications", preview_id)
        if existing is not None:
            return existing
        payload["artifact"] = workspace.artifact(body)
        return transaction.put_record(
            workspace.project_id, "dataset_publications", preview_id, payload
        )


def publish_dataset(workspace, client, preview_id, operation_id, *, authorized=False):
    """Only an explicit publication action may enter this write path; retries reconcile IDs."""
    if authorized is not True:
        raise AuditError("explicit dataset publication authorization is required")
    identifier(preview_id, "publication")
    lock = private_directory(workspace, "publication-locks") / (preview_id + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AuditError("publication is in progress; wait for its result") from None
        return _publish_dataset(workspace, client, preview_id, operation_id)
    finally:
        os.close(descriptor)


def _publish_dataset(workspace, client, preview_id, operation_id):
    try:
        operation = str(uuid.UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        raise AuditError("publication operation_id must be a UUID") from None
    database, project = workspace.metadata_store, workspace.project_id
    operation_key = "publicationop_" + digest(operation)[:24]
    with database.transaction() as transaction:
        saved = transaction.get_record(project, "dataset_publications", preview_id)
        if saved is None:
            raise AuditError("publication preview not found in this project")
        body = workspace.read_artifact(saved["artifact"])
        if (
            digest(body) != saved["digest"]
            or body["origin"] != str(workspace.root)
            or body["project_id"] != project
        ):
            raise AuditError("publication preview integrity changed")
        if body["connection"] != _connection(client.connection):
            raise AuditError("publication destination connection changed; review a new preview")
        _dataset(workspace, body["snapshot_id"])
        receipt = transaction.get_record(project, "publication_operations", operation_key)
        if receipt and receipt["preview_id"] != preview_id:
            raise AuditError("publication operation_id already belongs to another preview")
        if not receipt:
            transaction.put_record(
                project, "publication_operations", operation_key, {"preview_id": preview_id}
            )
        if saved["state"] == "completed":
            return saved
        saved.update(state="publishing", started_at=now())
        saved = transaction.put_record(project, "dataset_publications", preview_id, saved)
    try:
        result = client.publish_dataset(body["destination"], body["events"], saved["digest"])
    except Exception:
        saved.update(
            state="interrupted",
            next_action="Retry this exact preview and operation to reconcile provider records.",
        )
        database.put_record(project, "dataset_publications", preview_id, saved)
        raise
    saved.update(state="completed", completed_at=now(), receipt=result, next_action=None)
    return database.put_record(project, "dataset_publications", preview_id, saved)
