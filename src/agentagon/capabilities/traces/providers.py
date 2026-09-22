"""Bounded provider reads and a separate explicitly invoked dataset publication path."""

import base64
import importlib
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import quote

import httpx

from agentagon.capabilities.traces.normalize import redact
from agentagon.core.records import AuditError, digest, now, timestamp_ns
from agentagon.storage.config import validate_endpoint

DEFAULT_ENDPOINTS = {
    "braintrust": "https://api.braintrust.dev",
    "langsmith": "https://api.smith.langchain.com",
    "langfuse": "https://cloud.langfuse.com",
}
MAX_ITEMS = 1000
MAX_TRACES = 100
MAX_PAGES = 30
MAX_WORKSPACES = 100
MAX_SPANS = 10000
MAX_RESPONSE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 20_000_000
MAX_REQUESTS = 150
MAX_SECONDS = 90
PAGE_SIZE = 100
_SERVICE = "agentagon.webapp"
_REFERENCE = re.compile(r"^(session|keyring):[0-9a-f]{32}$")
_TRACE_METADATA_KEY = re.compile(
    r"agent|service|workflow|environment|deployment|project|session|name|tag", re.I
)
_TRACE_CONTENT_KEY = re.compile(r"input|output|prompt|content|message|error", re.I)
_LANGSMITH_TRACE_FIELDS = [
    "id",
    "trace_id",
    "parent_run_id",
    "parent_run_ids",
    "run_type",
    "name",
    "start_time",
    "end_time",
    "inputs",
    "outputs",
    "error",
    "status",
    "extra",
    "events",
    "tags",
    "session_id",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cost",
    "completion_cost",
    "total_cost",
    "feedback_stats",
]


class ProviderError(AuditError):
    """A provider failure safe to display without its response body or credentials."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class _LimitReached(Exception):
    pass


def _invalid_constant(value):
    raise ValueError("invalid JSON numeric value")


def _text(value, label, *, maximum=2048):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 for char in value)
    ):
        raise ProviderError(f"{label} must be a nonempty, bounded string")
    return value.strip()


def _trace_metadata_record(row, trace_id):
    result = {"trace_id": trace_id}
    for key in ("id", "name", "run_type", "type", "environment", "start_time", "startTime"):
        value = row.get(key)
        if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 500:
            result[key] = value
    tags = row.get("tags")
    if isinstance(tags, list):
        result["tags"] = [str(value)[:200] for value in tags[:30] if isinstance(value, str)]
    metadata = {}

    def collect(value, prefix="", depth=0):
        if depth > 3 or len(metadata) >= 50 or not isinstance(value, dict):
            return
        for key, item in value.items():
            if not isinstance(key, str) or _TRACE_CONTENT_KEY.search(key):
                continue
            name = f"{prefix}.{key}" if prefix else key
            if _TRACE_METADATA_KEY.search(key):
                if isinstance(item, (str, int, float, bool)) and len(str(item)) <= 500:
                    metadata[name] = item
                elif isinstance(item, list) and all(
                    isinstance(entry, (str, int, float, bool)) for entry in item[:30]
                ):
                    metadata[name] = [str(entry)[:200] for entry in item[:30]]
            if isinstance(item, dict):
                collect(item, name, depth + 1)

    for key in ("metadata", "extra", "trace_context", "span_attributes"):
        collect(row.get(key), key)
    if metadata:
        result["metadata"] = metadata
    return result


class CredentialStore:
    """Keep session secrets in memory; optionally use the OS keyring, never a file."""

    def __init__(self, keyring_backend=None):
        self._values = {}
        self._lock = threading.RLock()
        self._backend = keyring_backend

    def _keyring(self):
        if self._backend is not None:
            return self._backend
        try:
            backend = importlib.import_module("keyring").get_keyring()
            # Fail closed for plaintext third-party stores and unavailable backends.
            if not backend.__class__.__module__.startswith(
                (
                    "keyring.backends.macOS",
                    "keyring.backends.SecretService",
                    "keyring.backends.Windows",
                )
            ):
                raise ProviderError("OS credential storage unavailable; use session credentials")
            self._backend = backend
            return backend
        except ProviderError:
            raise
        except Exception:
            raise ProviderError(
                "OS credential storage unavailable; use session credentials"
            ) from None

    def set(self, value, persistence="session"):
        value = _text(value, "credential", maximum=16384)
        if persistence not in {"session", "keyring"}:
            raise ProviderError("credential persistence must be session or keyring")
        reference = f"{persistence}:{uuid.uuid4().hex}"
        with self._lock:
            if persistence == "session":
                self._values[reference] = value
            else:
                try:
                    self._keyring().set_password(_SERVICE, reference, value)
                except ProviderError:
                    raise
                except Exception:
                    raise ProviderError(
                        "Unable to save credential in the OS credential store"
                    ) from None
        return reference

    def set_auto(self, value):
        """Prefer an OS credential store, retaining only memory when it is unavailable."""
        value = _text(value, "credential", maximum=16384)
        try:
            return self.set(value, "keyring")
        except ProviderError:
            return self.set(value, "session")

    def resolve(self, reference):
        if isinstance(reference, str) and reference.startswith("env:"):
            name = reference[4:]
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
                raise ProviderError("Invalid environment credential reference")
            value = os.environ.get(name, "").strip()
        elif isinstance(reference, str) and _REFERENCE.fullmatch(reference):
            with self._lock:
                if reference.startswith("session:"):
                    value = self._values.get(reference, "")
                else:
                    try:
                        value = self._keyring().get_password(_SERVICE, reference) or ""
                    except ProviderError:
                        raise
                    except Exception:
                        raise ProviderError("Unable to read the OS credential store") from None
        else:
            raise ProviderError("Invalid credential reference")
        if not value:
            raise ProviderError(
                "Credential unavailable; reconnect or provide the environment variable"
            )
        return _text(value, "credential", maximum=16384)

    def delete(self, reference):
        if isinstance(reference, str) and re.fullmatch(r"env:[A-Z_][A-Z0-9_]*", reference):
            return  # Environment variables belong to the launching process, not this store.
        if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
            raise ProviderError("Invalid credential reference")
        with self._lock:
            if reference.startswith("session:"):
                self._values.pop(reference, None)
            else:
                try:
                    backend = self._keyring()
                    if backend.get_password(_SERVICE, reference) is not None:
                        backend.delete_password(_SERVICE, reference)
                except ProviderError:
                    raise
                except Exception:
                    raise ProviderError(
                        "Unable to remove credential from OS credential store"
                    ) from None


class ProviderClient:
    def __init__(self, connection: dict, credentials: CredentialStore, transport=None):
        if not isinstance(connection, dict) or connection.get("provider") not in DEFAULT_ENDPOINTS:
            raise ProviderError("Choose Braintrust, LangSmith or Langfuse")
        self.connection = dict(connection)
        self.provider = connection["provider"]
        endpoint = _text(connection.get("endpoint") or DEFAULT_ENDPOINTS[self.provider], "endpoint")
        self.endpoint = validate_endpoint(endpoint)
        self.credentials = credentials
        self.transport = transport
        self._secrets = []
        self._requests = 0
        self._bytes = 0
        self._started = time.monotonic()

    def _begin(self):
        self._requests = 0
        self._bytes = 0
        self._started = time.monotonic()

    def _headers(self):
        references = self.connection.get("credentials", {})
        if not isinstance(references, dict):
            raise ProviderError("Connection credentials must be references")

        def secret(name):
            value = self.credentials.resolve(references.get(name))
            if value not in self._secrets:
                self._secrets.append(value)
            return value

        headers = {"Accept": "application/json"}
        if self.provider == "braintrust":
            headers["Authorization"] = f"Bearer {secret('api_key')}"
        elif self.provider == "langsmith":
            headers["x-api-key"] = secret("api_key")
            if self.connection.get("workspace_id"):
                headers["X-Tenant-Id"] = _text(self.connection["workspace_id"], "workspace ID")
        else:
            pair = f"{secret('public_key')}:{secret('secret_key')}"
            encoded = base64.b64encode(pair.encode()).decode()
            self._secrets.append(encoded)
            headers["Authorization"] = f"Basic {encoded}"
        return headers

    def _clean(self, value):
        if isinstance(value, dict):
            return {self._clean(k): self._clean(v) for k, v in redact(value).items()}
        if isinstance(value, list):
            return [self._clean(v) for v in value]
        if isinstance(value, str):
            for secret in sorted(set(self._secrets), key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
            return redact(value)
        return value

    def _request(self, method, path, *, workspace_id=None, **kwargs):
        if self._requests >= MAX_REQUESTS or time.monotonic() - self._started > MAX_SECONDS:
            raise _LimitReached
        self._requests += 1
        headers = self._headers()
        if workspace_id is not None:
            headers["X-Tenant-Id"] = _text(workspace_id, "workspace ID")
        try:
            with httpx.Client(
                transport=self.transport, timeout=20, follow_redirects=False, trust_env=False
            ) as client:
                with client.stream(
                    method, self.endpoint + path, headers=headers, **kwargs
                ) as response:
                    if response.status_code >= 300:
                        labels = {
                            401: "Authentication failed; check the connection credentials",
                            403: "Provider denied access to the requested resource",
                            404: "Provider resource or API version unavailable",
                            429: "Provider rate limit reached; retry later",
                        }
                        raise ProviderError(
                            labels.get(response.status_code, "Provider request failed"),
                            status_code=response.status_code,
                        )
                    content = bytearray()
                    for part in response.iter_bytes():
                        if time.monotonic() - self._started > MAX_SECONDS:
                            raise _LimitReached
                        content.extend(part)
                        self._bytes += len(part)
                        if self._bytes > MAX_TOTAL_BYTES:
                            raise ProviderError("Provider import exceeds the total size limit")
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise ProviderError("Provider response exceeds the import size limit")
            value = json.loads(content, parse_constant=_invalid_constant)
        except ProviderError:
            raise
        except (httpx.HTTPError, OSError):
            raise ProviderError(
                "Unable to reach provider; check the endpoint and connection"
            ) from None
        except (ValueError, UnicodeError):
            raise ProviderError("Provider returned an invalid JSON response") from None
        if not isinstance(value, (list, dict)):
            raise ProviderError("Provider returned an unsupported response shape")
        return value

    @staticmethod
    def _rows(value, key=None):
        rows = value.get(key) if key and isinstance(value, dict) else value
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ProviderError("Provider returned an invalid record list")
        return rows

    def _catalog(self, resource, *, workspace_id=None):
        items = []
        for page in range(MAX_PAGES):
            params = {"limit": PAGE_SIZE}
            if self.provider == "braintrust":
                path, key = f"/v1/{resource}", "objects"
                if items:
                    params["starting_after"] = _text(items[-1].get("id"), "provider record ID")
                if resource == "dataset" and self.connection.get("project"):
                    params["project_id"] = self.connection["project"]
            elif self.provider == "langsmith":
                path, key = ("/sessions" if resource == "project" else "/datasets"), None
                params["offset"] = len(items)
            else:
                path = (
                    "/api/public/projects" if resource == "project" else "/api/public/v2/datasets"
                )
                key = "data"
                params["page"] = page + 1
            result = self._request("GET", path, params=params, workspace_id=workspace_id)
            rows = self._rows(result, key)
            items.extend(rows)
            if len(items) > MAX_ITEMS:
                raise ProviderError("Provider catalog is too large; narrow the connected project")
            if len(rows) < PAGE_SIZE:
                return self._clean(items)
        raise ProviderError("Provider catalog exceeds the pagination limit")

    def _langsmith_projects(self):
        # This API lists workspaces visible to the key without requiring a tenant ID.
        path = "/workspaces" if self.endpoint.endswith("/api/v1") else "/api/v1/workspaces"
        try:
            workspaces = self._rows(self._request("GET", path, params={"include_deleted": False}))
        except ProviderError as error:
            if error.status_code != 403:
                raise
            # A restricted single-workspace key can read projects without org discovery.
            return self._catalog("project")
        if len(workspaces) > MAX_WORKSPACES:
            raise ProviderError("Too many LangSmith workspaces; use a workspace-scoped key")
        projects, seen = [], set()
        for workspace in workspaces:
            if workspace.get("is_deleted"):
                continue
            workspace_id = _text(workspace.get("id"), "workspace ID")
            if workspace_id in seen:
                raise ProviderError("Provider returned duplicate workspaces")
            seen.add(workspace_id)
            name = _text(workspace.get("display_name"), "workspace name")
            for project in self._catalog("project", workspace_id=workspace_id):
                if project.get("tenant_id") and project["tenant_id"] != workspace_id:
                    raise ProviderError("Provider returned a project from another workspace")
                projects.append({**project, "workspace_id": workspace_id, "workspace_name": name})
                if len(projects) > MAX_ITEMS:
                    raise ProviderError("Too many LangSmith projects; use a workspace-scoped key")
        return projects

    def test(self):
        self._begin()
        try:
            projects = (
                self._langsmith_projects()
                if self.provider == "langsmith"
                else self._catalog("project")
            )
        except _LimitReached:
            raise ProviderError("Connection check exceeded its request limit") from None
        normalized = []
        for project in projects:
            project_id = _text(project.get("id"), "project ID")
            row = {
                "id": project_id,
                "name": _text(project.get("name") or project_id, "project name"),
            }
            for key in ("workspace_id", "workspace_name"):
                if project.get(key):
                    row[key] = _text(project[key], key.replace("_", " "))
            # Scoped project reads may provide their tenant even when discovery is restricted.
            if self.provider == "langsmith" and "workspace_id" not in row:
                workspace_id = project.get("tenant_id") or self.connection.get("workspace_id")
                if workspace_id:
                    row["workspace_id"] = _text(workspace_id, "workspace ID")
            normalized.append(row)
        return self._clean({"status": "connected", "projects": normalized})

    def datasets(self):
        self._begin()
        try:
            return self._catalog("dataset")
        except _LimitReached:
            raise ProviderError("Dataset list exceeded its request limit") from None

    def publish_dataset(self, destination, events, publication_digest):
        """Reconcile a uniquely named Braintrust dataset before inserting missing row IDs."""
        if self.provider != "braintrust":
            raise ProviderError("Dataset publication supports Braintrust only")
        if not isinstance(destination, dict) or set(destination) != {"project", "name"}:
            raise ProviderError("Choose the reviewed dataset destination")
        project = _text(destination.get("project"), "destination project")
        name = _text(destination.get("name"), "dataset name")
        if not isinstance(publication_digest, str) or not re.fullmatch(
            r"[a-f0-9]{64}", publication_digest
        ):
            raise ProviderError("Invalid publication digest")
        if (
            not isinstance(events, list)
            or not 1 <= len(events) <= MAX_ITEMS
            or any(not isinstance(event, dict) for event in events)
        ):
            raise ProviderError("Publish between 1 and 1000 dataset examples")
        if len(json.dumps(events, allow_nan=False).encode()) > MAX_TOTAL_BYTES:
            raise ProviderError("Dataset publication exceeds 20 MB")
        expected = {_text(event.get("id"), "dataset row ID"): event for event in events}
        if len(expected) != len(events):
            raise ProviderError("Dataset publication requires unique row IDs")
        self._begin()
        try:
            # Same project/name returns the existing dataset unmodified. The marker
            # prevents touching a same-name dataset belonging to another publication.
            remote = self._request(
                "POST",
                "/v1/dataset",
                json={
                    "project_id": project,
                    "name": name,
                    "metadata": {"agentagon_publication": publication_digest},
                },
            )
            if (
                not isinstance(remote, dict)
                or remote.get("project_id") != project
                or remote.get("name") != name
                or not isinstance(remote.get("metadata"), dict)
                or remote.get("metadata", {}).get("agentagon_publication") != publication_digest
            ):
                raise ProviderError(
                    "Destination dataset is not owned by this publication; nothing was inserted"
                )
            dataset_id = _text(remote.get("id"), "published dataset ID")
            prefix = "/v1/dataset/" + quote(dataset_id, safe="")
            rows, complete, _ = self._pages(
                "POST",
                prefix + "/fetch",
                "events",
                cap=MAX_ITEMS + 1,
                style="cursor",
                unique_key="id",
            )
            if not complete:
                raise ProviderError("Cannot reconcile the complete destination dataset")
            present = {}
            for row in rows:
                key = row.get("id")
                content = {
                    field: row[field]
                    for field in ("id", "input", "expected", "metadata")
                    if field in row
                }
                # Optional absent references may be represented as JSON null by fetch.
                if (
                    key in expected
                    and "expected" not in expected[key]
                    and content.get("expected") is None
                ):
                    content.pop("expected", None)
                if key not in expected or digest(content) != digest(expected[key]):
                    raise ProviderError(
                        "Destination dataset changed; no publication rows were overwritten"
                    )
                present[key] = content
            missing = [event for event in events if event["id"] not in present]
            if missing:
                inserted = self._request("POST", prefix + "/insert", json={"events": missing})
                if not isinstance(inserted, dict) or inserted.get("row_ids") != [
                    event["id"] for event in missing
                ]:
                    raise ProviderError(
                        "Provider insertion receipt is incomplete; retry to reconcile"
                    )
            return self._clean(
                {
                    "provider": "braintrust",
                    "project": project,
                    "dataset_id": dataset_id,
                    "name": name,
                    "row_ids": list(expected),
                    "count": len(events),
                    "publication_digest": publication_digest,
                }
            )
        except _LimitReached:
            raise ProviderError(
                "Dataset publication exceeded its bounded request budget; retry to reconcile"
            ) from None

    def _selection(self, kind, selection):
        if kind not in {"traces", "dataset"} or not isinstance(selection, dict):
            raise ProviderError("Choose a trace or dataset import")
        if set(selection) - {
            "project",
            "dataset_id",
            "version",
            "start",
            "end",
            "cap",
            "filters",
            "trace_id",
        }:
            raise ProviderError("Unsupported import selection field")
        cap = selection.get("cap", 50)
        maximum = MAX_TRACES if kind == "traces" else MAX_ITEMS
        if type(cap) is not int or not 1 <= cap <= maximum:
            raise ProviderError(f"Import cap must be between 1 and {maximum}")
        result = dict(selection, cap=cap)
        result["project"] = selection.get("project") or self.connection.get("project")
        if kind == "traces" and selection.get("trace_id"):
            result["trace_id"] = _text(selection["trace_id"], "trace ID", maximum=200)
            if any(result.get(key) for key in ("start", "end", "filters")):
                raise ProviderError("A trace reference cannot also specify a batch selection")
            result["cap"] = 1
            result["project"] = _text(result["project"], "project ID")
        elif kind == "traces":
            for field in ("start", "end"):
                _text(result.get(field), field)
                timestamp_ns(result[field])
            if timestamp_ns(result["start"]) >= timestamp_ns(result["end"]):
                raise ProviderError("Trace end must be later than start")
            if self.provider != "langfuse":
                result["project"] = _text(result["project"], "project ID")
        else:
            result["dataset_id"] = _text(result.get("dataset_id"), "dataset ID")
            if result.get("version"):
                result["version"] = _text(result["version"], "dataset version", maximum=200)
                if self.provider == "langfuse":
                    timestamp_ns(result["version"])
        filters = result.get("filters", {})
        if filters is None:
            filters = {}
        if kind == "traces":
            if not isinstance(filters, (dict, str)) or (
                filters and (self.provider == "langfuse" or not isinstance(filters, str))
            ):
                raise ProviderError(
                    "Trace filters require a Braintrust or LangSmith filter expression"
                )
            if filters:
                _text(filters, "trace filter", maximum=4096)
        elif not isinstance(filters, dict) or (filters and self.provider != "langsmith"):
            raise ProviderError(
                "Dataset filters are supported only for LangSmith splits and metadata"
            )
        elif filters:
            if set(filters) - {"splits", "metadata"}:
                raise ProviderError("Supported dataset filters are splits and metadata")
            if "splits" in filters and (
                not isinstance(filters["splits"], list)
                or len(filters["splits"]) > 30
                or any(not isinstance(v, str) or not v or len(v) > 200 for v in filters["splits"])
            ):
                raise ProviderError("Dataset splits must be a bounded list of names")
            if "metadata" in filters and not isinstance(filters["metadata"], dict):
                raise ProviderError("Dataset metadata filter must be an object")
            try:
                size = len(json.dumps(filters, allow_nan=False))
            except (ValueError, TypeError):
                raise ProviderError("Dataset filters must contain JSON values") from None
            if size > 4096:
                raise ProviderError("Dataset filters exceed the size limit")
        result["filters"] = filters
        return result

    def _pages(self, method, path, key, params=None, body=None, *, cap, style, unique_key=None):
        """Return rows plus actual exhaustion; never call a capped selection complete."""
        rows, seen, cursor = [], set(), None
        identities, fetched = set(), 0
        params, body = dict(params or {}), dict(body or {})
        try:
            for page in range(MAX_PAGES):
                query, payload = dict(params), dict(body)
                target = payload if method == "POST" else query
                target["limit"] = min(PAGE_SIZE, cap + 1 - len(rows))
                if style == "offset":
                    target["offset"] = fetched
                elif style == "page":
                    target["page"] = page + 1
                    target["limit"] = PAGE_SIZE
                elif cursor and style != "btql":
                    target["cursor"] = cursor
                if style == "btql":
                    target["query"] += f" LIMIT {target.pop('limit')}"
                    if cursor:
                        target["query"] += f" OFFSET {json.dumps(cursor)}"
                data = self._request(
                    method, path, **({"json": payload} if method == "POST" else {"params": query})
                )
                batch = self._rows(data, key)
                fetched += len(batch)
                for row in batch:
                    if unique_key:
                        identity = _text(row.get(unique_key), "provider record ID")
                        if identity in identities:
                            continue
                        identities.add(identity)
                    rows.append(row)
                if len(rows) > cap:
                    return rows[:cap], False, "item_cap"
                if style in {"cursor", "btql"}:
                    cursor = data.get("cursor")
                    for field, name in (("meta", "cursor"), ("cursors", "next")):
                        envelope = data.get(field) or {}
                        if not isinstance(envelope, dict):
                            raise ProviderError("Provider returned invalid pagination metadata")
                        if not cursor:
                            cursor = envelope.get(name)
                    if not cursor:
                        return rows, True, None
                    if not isinstance(cursor, str) or cursor in seen:
                        return rows, False, "pagination_incomplete"
                    seen.add(cursor)
                elif not batch or len(batch) < target["limit"]:
                    return rows, True, None
        except _LimitReached:
            return rows, False, "request_limit"
        return rows, False, "page_limit"

    def _dataset(self, selection):
        dataset_id = selection["dataset_id"]
        cap, version = selection["cap"], selection.get("version")
        extra = {}
        if self.provider == "braintrust":
            path = f"/v1/dataset/{quote(dataset_id, safe='')}/fetch"
            if not version:
                first = self._request("POST", path, json={"limit": 1})
                probe = self._rows(first, "events")
                version = first.get("max_xact_id") or (probe[0].get("_xact_id") if probe else None)
                if probe and not version:
                    raise ProviderError(
                        "Provider did not supply a dataset version; choose one explicitly"
                    )
                if not probe:
                    return [], True, None, {"version": None, "snapshot_consistent": False}
            rows, complete, reason = self._pages(
                "POST",
                path,
                "events",
                body={"version": version},
                cap=cap,
                style="cursor",
                unique_key="id",
            )
            input_key, expected_key = "input", "expected"
        elif self.provider == "langsmith":
            # Resolve tags (including latest) once so pages cannot drift between versions.
            if version and version != "latest":
                try:
                    timestamp_ns(version)
                except AuditError:
                    resolved = self._request(
                        "GET",
                        f"/datasets/{quote(dataset_id, safe='')}/version",
                        params={"tag": version},
                    )
                    version = _text(resolved.get("as_of"), "resolved dataset version")
            else:
                version = now()
            params = {"dataset": dataset_id, "as_of": version}
            filters = selection["filters"]
            if "splits" in filters:
                params["splits"] = filters["splits"]
            if "metadata" in filters:
                params["metadata"] = json.dumps(filters["metadata"])
            rows, complete, reason = self._pages(
                "GET", "/examples", None, params=params, cap=cap, style="offset"
            )
            input_key, expected_key = "inputs", "outputs"
        else:
            version = version or now()
            catalog = self._catalog("dataset")
            match = next(
                (row for row in catalog if dataset_id in (row.get("id"), row.get("name"))), None
            )
            if not match:
                raise ProviderError("Dataset is not available to this Langfuse connection")
            dataset_name = _text(match.get("name"), "dataset name")
            extra.update({"dataset_id": match.get("id"), "dataset_name": dataset_name})
            dataset = self._request(
                "GET",
                f"/api/public/v2/datasets/{quote(dataset_name, safe='')}",
                params={"version": version},
            )
            extra["schema"] = {
                key: dataset[key]
                for key in ("inputSchema", "expectedOutputSchema")
                if key in dataset
            }
            # The versioned dataset endpoint returns the full item list.
            rows = self._rows(dataset, "items")
            complete, reason = len(rows) <= cap, None if len(rows) <= cap else "item_cap"
            rows = rows[:cap]
            input_key, expected_key = "input", "expectedOutput"
        items = []
        for row in rows:
            identifier = _text(row.get("id"), "dataset item ID")
            items.append(
                {
                    "id": identifier,
                    "input": row.get(input_key),
                    "expected": row.get(expected_key),
                    "expected_present": expected_key in row and row[expected_key] is not None,
                    "metadata": row.get("metadata") or {},
                    "source": {
                        key: row[key]
                        for key in (
                            "_xact_id",
                            "origin",
                            "created_at",
                            "modified_at",
                            "source_run_id",
                            "sourceTraceId",
                            "sourceObservationId",
                            "status",
                            "attachments",
                            "attachment_urls",
                        )
                        if key in row
                    },
                }
            )
        return items, complete, reason, {"version": version, "snapshot_consistent": True, **extra}

    def _trace_roots(self, selection, *, metadata=False):
        project, cap = selection["project"], selection["cap"]
        if selection.get("trace_id"):
            table = (
                f"project_logs({json.dumps(project)})" if self.provider == "braintrust" else None
            )
            return [], [selection["trace_id"]], True, None, table
        start, end, filters = selection["start"], selection["end"], selection["filters"]
        table = None
        if self.provider == "braintrust":
            table = f"project_logs({json.dumps(project)})"
            condition = f"is_root = true AND metrics.start >= {timestamp_ns(start) / 1e9} AND metrics.start < {timestamp_ns(end) / 1e9}"
            if filters:
                condition += f" AND ({filters})"
            fields = "root_span_id, span_id, metrics"
            if metadata:
                fields += ", span_attributes, metadata"
            query = f"SELECT {fields} FROM {table} WHERE {condition} LIMIT {cap + 1}"
            data = self._request("POST", "/btql", json={"query": query, "fmt": "json"})
            roots = self._rows(data, "data")
            selected = list(
                dict.fromkeys(row.get("root_span_id") or row.get("span_id") for row in roots)
            )
            complete = len(selected) <= cap and not data.get("cursor")
            reason = None if complete else "item_cap"
        elif self.provider == "langsmith":
            upper = f"lt(start_time, {json.dumps(end)})"
            body = {
                "session": [project],
                "is_root": True,
                "start_time": start,
                "filter": f"and({upper},{filters})" if filters else upper,
                "select": ["id", "trace_id", "start_time", "name", "run_type", "tags", "extra"]
                if metadata
                else ["id", "trace_id", "start_time"],
            }
            roots, complete, reason = self._pages(
                "POST", "/runs/query", "runs", body=body, cap=cap, style="cursor"
            )
            selected = list(dict.fromkeys(row.get("trace_id") or row.get("id") for row in roots))
        else:
            roots, complete, reason = self._pages(
                "GET",
                "/api/public/v2/observations",
                "data",
                params={
                    "fields": "core,basic,time,metadata,trace_context" if metadata else "core",
                    "parentObservationId": "",
                    "fromStartTime": start,
                    "toStartTime": end,
                },
                cap=cap,
                style="cursor",
                unique_key="traceId",
            )
            selected = list(dict.fromkeys(row.get("traceId") for row in roots))
        selected = [_text(value, "trace ID") for value in selected[:cap]]
        return roots[:cap], selected, complete, reason, table

    def trace_metadata(self, selection):
        self._begin()
        selection = self._selection("traces", selection)
        try:
            roots, selected, complete, reason, _table = self._trace_roots(selection, metadata=True)
        except _LimitReached:
            raise ProviderError("Trace metadata lookup reached its request limit") from None
        items = []
        seen = set()
        for row in roots:
            trace_id = (
                row.get("root_span_id") or row.get("span_id")
                if self.provider == "braintrust"
                else row.get("trace_id") or row.get("id")
                if self.provider == "langsmith"
                else row.get("traceId")
            )
            if trace_id in seen or trace_id not in selected:
                continue
            seen.add(trace_id)
            items.append(_trace_metadata_record(row, trace_id))
        return self._clean(
            {
                "items": items,
                "completeness": {
                    "complete": complete,
                    "reason": reason,
                    "count": len(items),
                },
            }
        )

    def _traces(self, selection):
        project = selection["project"]
        _roots, selected, complete, reason, table = self._trace_roots(selection)
        items, details_complete = [], True
        for trace_id in selected:
            remaining = MAX_SPANS - len(items)
            if remaining <= 0:
                details_complete, reason = False, "span_limit"
                break
            if self.provider == "braintrust":
                # Query the whole trace; child spans can start outside the root's window.
                query = f"SELECT * FROM {table} WHERE root_span_id = {json.dumps(trace_id)}"
                rows, exhausted, detail_reason = self._pages(
                    "POST",
                    "/btql",
                    "data",
                    body={"query": query, "fmt": "json"},
                    cap=remaining,
                    style="btql",
                )
            elif self.provider == "langsmith":
                rows, exhausted, detail_reason = self._pages(
                    "POST",
                    "/runs/query",
                    "runs",
                    body={
                        "session": [project],
                        "trace": trace_id,
                        "select": _LANGSMITH_TRACE_FIELDS,
                    },
                    cap=remaining,
                    style="cursor",
                )
            else:
                rows, exhausted, detail_reason = self._pages(
                    "GET",
                    "/api/public/v2/observations",
                    "data",
                    params={
                        "traceId": trace_id,
                        "fields": "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context",
                    },
                    cap=remaining,
                    style="cursor",
                )
            items.extend(rows)
            if not exhausted or not rows:
                details_complete = False
                reason = detail_reason or "trace_details_missing"
        return (
            items,
            complete and details_complete,
            reason,
            {
                "selected_trace_ids": selected,
                "selected_traces": len(selected),
                "trace_details_complete": details_complete,
                "snapshot_consistent": False,
            },
        )

    def preview(self, kind, selection):
        self._begin()
        selection = self._selection(kind, selection)
        try:
            items, complete, reason, extra = (
                self._dataset(selection) if kind == "dataset" else self._traces(selection)
            )
        except _LimitReached:
            raise ProviderError(
                "Provider import reached its request limit; narrow the selection"
            ) from None
        provenance = {
            "provider": self.provider,
            "connection_id": self.connection.get("id"),
            "endpoint": self.endpoint,
            "project": selection.get("project"),
            "workspace_id": self.connection.get("workspace_id"),
            "kind": kind,
            "selection": selection,
            "acquired_at": now(),
            **extra,
        }
        completeness = {"complete": complete, "reason": reason, "count": len(items)}
        if kind == "traces":
            completeness.update(
                {key: extra[key] for key in ("selected_traces", "trace_details_complete")}
            )
        return self._clean({"items": items, "provenance": provenance, "completeness": completeness})
