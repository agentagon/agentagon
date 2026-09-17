import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from agentagon.core.records import AuditError
from agentagon.webapp import providers
from agentagon.webapp.providers import CredentialStore, ProviderClient, ProviderError


def client(provider, handler, **overrides):
    credentials = CredentialStore()
    names = ("public_key", "secret_key") if provider == "langfuse" else ("api_key",)
    refs = {name: credentials.set(f"private-{name}-value") for name in names}
    connection = {
        "id": "connection",
        "provider": provider,
        "project": "project-1",
        "credentials": refs,
        **overrides,
    }
    return ProviderClient(connection, credentials, transport=httpx.MockTransport(handler))


def trace_selection(**values):
    return {
        "start": "2026-09-01T00:00:00Z",
        "end": "2026-09-02T00:00:00Z",
        "cap": 10,
        **values,
    }


def test_credentials_references_and_session_lifetime(monkeypatch):
    store = CredentialStore()
    reference = store.set("a-secret-value")
    assert "a-secret-value" not in reference
    assert store.resolve(reference) == "a-secret-value"
    with pytest.raises(ProviderError, match="unavailable"):
        CredentialStore().resolve(reference)
    store.delete(reference)
    with pytest.raises(ProviderError, match="unavailable"):
        store.resolve(reference)
    monkeypatch.setenv("PROVIDER_KEY", "environment-secret")
    assert store.resolve("env:PROVIDER_KEY") == "environment-secret"
    store.delete("env:PROVIDER_KEY")
    assert store.resolve("env:PROVIDER_KEY") == "environment-secret"
    with pytest.raises(ProviderError, match="Invalid"):
        store.resolve("environment-secret")


def test_os_keyring_and_sanitized_failure():
    class Keyring:
        values = {}

        def set_password(self, service, name, value):
            self.values[(service, name)] = value

        def get_password(self, service, name):
            return self.values.get((service, name))

        def delete_password(self, service, name):
            del self.values[(service, name)]

    backend = Keyring()
    first = CredentialStore(backend)
    reference = first.set("persistent-secret", "keyring")
    second = CredentialStore(backend)
    assert second.resolve(reference) == "persistent-secret"
    second.delete(reference)
    with pytest.raises(ProviderError, match="unavailable"):
        first.resolve(reference)

    class FailingKeyring:
        def set_password(self, *args):
            raise RuntimeError("persistent-secret leaked by OS")

    with pytest.raises(ProviderError) as error:
        CredentialStore(FailingKeyring()).set("persistent-secret", "keyring")
    assert "persistent-secret" not in str(error.value)


@pytest.mark.parametrize(
    "provider,path,header,body",
    [
        ("braintrust", "/v1/project", "authorization", {"objects": [{"id": "p"}]}),
        ("langsmith", "/sessions", "x-api-key", [{"id": "p"}]),
        ("langfuse", "/api/public/projects", "authorization", {"data": [{"id": "p"}]}),
    ],
)
def test_connection_authentication(provider, path, header, body):
    def handler(request):
        if provider == "langsmith" and request.url.path == "/api/v1/workspaces":
            assert request.headers[header]
            return httpx.Response(200, json=[{"id": "workspace-1", "display_name": "Team"}])
        assert request.url.path == path
        assert request.headers[header]
        if provider == "langsmith":
            assert request.headers["X-Tenant-Id"] == "workspace-1"
        if provider == "langfuse":
            encoded = request.headers["authorization"].removeprefix("Basic ")
            assert (
                base64.b64decode(encoded).decode()
                == "private-public_key-value:private-secret_key-value"
            )
        return httpx.Response(200, json=body)

    result = client(provider, handler, workspace_id="workspace-1").test()
    project = {"id": "p", "name": "p"}
    if provider == "langsmith":
        project.update(workspace_id="workspace-1", workspace_name="Team")
    assert result == {"status": "connected", "projects": [project]}


def test_automatic_credentials_prefer_keyring_and_fall_back_to_memory(monkeypatch):
    class Keyring:
        def set_password(self, service, reference, value):
            self.reference, self.value = reference, value

        def get_password(self, service, reference):
            assert reference == self.reference
            return self.value

    backend = Keyring()
    saved = CredentialStore(backend).set_auto("secure-value")
    assert saved.startswith("keyring:")
    assert CredentialStore(backend).resolve(saved) == "secure-value"
    assert "secure-value" not in saved

    def unavailable(*args):
        raise RuntimeError("OS details containing secure-value")

    backend.set_password = unavailable
    store = CredentialStore(backend)
    fallback = store.set_auto("secure-value")
    assert fallback.startswith("session:") and store.resolve(fallback) == "secure-value"
    with pytest.raises(ProviderError, match="unavailable"):
        CredentialStore(backend).resolve(fallback)
    monkeypatch.setattr(providers.importlib, "import_module", unavailable)
    missing = CredentialStore()
    reference = missing.set_auto("secure-value")
    assert reference.startswith("session:") and missing.resolve(reference) == "secure-value"


def test_automatic_credentials_never_use_plaintext_keyring_backend(monkeypatch):
    class Plaintext:
        __module__ = "keyrings.alt.file"

        def set_password(self, *args):
            pytest.fail("plaintext storage must never be used")

    monkeypatch.setattr(
        providers.importlib, "import_module", lambda _: SimpleNamespace(get_keyring=Plaintext)
    )
    store = CredentialStore()
    reference = store.set_auto("only-in-memory")
    assert reference.startswith("session:") and store.resolve(reference) == "only-in-memory"


def test_langsmith_discovers_all_workspace_projects_and_preserves_scope(monkeypatch):
    monkeypatch.setattr(providers, "PAGE_SIZE", 2)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "api.smith.langchain.com"
        assert request.headers["x-api-key"] == "private-api_key-value"
        if request.url.path == "/api/v1/workspaces":
            assert "x-tenant-id" not in request.headers
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "team-a",
                        "display_name": "Alpha",
                        "data_plane_url": "https://untrusted.example",
                    },
                    {"id": "team-b", "display_name": "Beta"},
                    {"id": "deleted", "display_name": "Old", "is_deleted": True},
                ],
            )
        workspace = request.headers["x-tenant-id"]
        if request.url.path == "/datasets":
            assert workspace == "team-b"
            return httpx.Response(200, json=[])
        assert request.url.path == "/sessions"
        rows = [{"id": "shared-id", "name": "Customer support", "extra": "omit this"}]
        if workspace == "team-a":
            rows += [{"id": "other", "name": "Other"}, {"id": "third", "name": "Third"}]
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=rows[offset : offset + 2])

    provider = client("langsmith", handler)
    result = provider.test()
    assert result["status"] == "connected"
    assert [(p["workspace_id"], p["id"]) for p in result["projects"]] == [
        ("team-a", "shared-id"),
        ("team-a", "other"),
        ("team-a", "third"),
        ("team-b", "shared-id"),
    ]
    assert {p["workspace_name"] for p in result["projects"]} == {"Alpha", "Beta"}
    assert all(
        set(p) == {"id", "name", "workspace_id", "workspace_name"} for p in result["projects"]
    )
    assert "workspace_id" not in provider.connection
    bound = ProviderClient(
        {**provider.connection, "workspace_id": "team-b"}, provider.credentials, provider.transport
    )
    assert bound.datasets() == []
    assert len(calls) == 5


def test_langsmith_scoped_key_can_discover_projects_without_workspace_listing():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(403, text="private-api_key-value")
        assert request.url.path == "/sessions"
        return httpx.Response(
            200, json=[{"id": "project", "name": "Scoped", "tenant_id": "workspace"}]
        )

    assert client("langsmith", handler).test()["projects"] == [
        {"id": "project", "name": "Scoped", "workspace_id": "workspace"}
    ]
    assert calls == ["/api/v1/workspaces", "/sessions"]


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_langsmith_discovery_failure_is_safe_and_does_not_guess_endpoints(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private-api_key-value")

    with pytest.raises(ProviderError) as error:
        client("langsmith", handler).test()
    assert "private-api_key-value" not in str(error.value) and len(calls) == 1


@pytest.mark.parametrize("bound", ["MAX_WORKSPACES", "MAX_ITEMS", "MAX_REQUESTS"])
def test_langsmith_discovery_has_shared_workspace_project_and_request_bounds(monkeypatch, bound):
    monkeypatch.setattr(providers, bound, 1)

    def handler(request):
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(
                200, json=[{"id": "a", "display_name": "A"}, {"id": "b", "display_name": "B"}]
            )
        return httpx.Response(200, json=[{"id": request.headers["x-tenant-id"], "name": "Project"}])

    with pytest.raises(ProviderError, match="Too many|request limit"):
        client("langsmith", handler).test()


def test_langsmith_discovery_keeps_custom_api_prefix_and_redacts_names():
    def handler(request):
        assert request.url.host == "local.example"
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(
                200, json=[{"id": "workspace", "display_name": "private-api_key-value"}]
            )
        assert request.url.path == "/api/v1/sessions"
        assert request.headers["x-tenant-id"] == "workspace"
        return httpx.Response(200, json=[{"id": "project", "name": "private-api_key-value"}])

    result = client("langsmith", handler, endpoint="https://local.example/api/v1").test()
    assert "private-api_key-value" not in json.dumps(result)


@pytest.mark.parametrize(
    "workspaces,project,reason",
    [
        ({"unexpected": []}, {}, "record list"),
        ([{"id": "bad\nheader", "display_name": "Team"}], {}, "workspace ID"),
        ([{"id": "team", "display_name": "Team"}] * 2, {"id": "p"}, "duplicate"),
        (
            [{"id": "team", "display_name": "Team"}],
            {"id": "p", "tenant_id": "other"},
            "another workspace",
        ),
    ],
)
def test_langsmith_discovery_rejects_invalid_workspace_and_project_bindings(
    workspaces, project, reason
):
    def handler(request):
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(200, json=workspaces)
        return httpx.Response(200, json=[project])

    with pytest.raises(ProviderError, match=reason):
        client("langsmith", handler).test()


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_provider_errors_never_include_bodies_or_follow_redirects(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="private-api_key-value",
            headers={"Location": "https://another-provider.test/secret"},
        )

    with pytest.raises(ProviderError) as error:
        client("braintrust", handler).test()
    assert "private-api_key-value" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "endpoint",
    ["http://example.com", "https://user:secret@example.com", "https://example.com?secret=x", 42],
)
def test_invalid_endpoints_fail_before_network(endpoint):
    with pytest.raises(AuditError):
        client("braintrust", lambda _: pytest.fail("unexpected request"), endpoint=endpoint)


@pytest.mark.parametrize(
    "selection",
    [{"cap": True}, {"cap": 0}, {"cap": 1001}, {"unknown": True}, {"filters": []}],
)
def test_invalid_dataset_selection_fails_before_network(selection):
    with pytest.raises(ProviderError):
        client("braintrust", lambda _: pytest.fail("unexpected request")).preview(
            "dataset", {"dataset_id": "data", **selection}
        )


def test_braintrust_dataset_freezes_version_and_deduplicates_history():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/dataset/data/fetch"
        if len(requests) == 1:
            return httpx.Response(200, json={"events": [{"id": "one", "_xact_id": "version-2"}]})
        assert body["version"] == "version-2"
        if "cursor" not in body:
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "id": "one",
                            "_xact_id": "version-2",
                            "input": {
                                "messages": [
                                    {"role": "user", "content": "hello"},
                                    {"role": "assistant", "content": "hi"},
                                ]
                            },
                            "expected": {"answer": "accepted"},
                        }
                    ],
                    "cursor": "next",
                },
            )
        assert body["cursor"] == "next"
        return httpx.Response(
            200,
            json={
                "events": [
                    {"id": "one", "_xact_id": "version-1", "expected": "obsolete"},
                    {"id": "two", "input": "unlabeled"},
                ]
            },
        )

    result = client("braintrust", handler).preview("dataset", {"dataset_id": "data", "cap": 10})
    assert result["provenance"]["version"] == "version-2"
    assert result["completeness"]["complete"]
    assert len(result["items"]) == 2
    assert result["items"][0]["expected"] == {"answer": "accepted"}
    assert len(result["items"][0]["input"]["messages"]) == 2
    assert result["items"][1]["expected"] is None
    assert not result["items"][1]["expected_present"]


def test_langsmith_dataset_resolves_tag_paginates_and_marks_cap(monkeypatch):
    monkeypatch.setattr(providers, "PAGE_SIZE", 2)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/version"):
            assert request.url.params["tag"] == "stable"
            return httpx.Response(200, json={"as_of": "2026-08-01T00:00:00Z"})
        assert request.url.path == "/examples"
        assert request.url.params["as_of"] == "2026-08-01T00:00:00Z"
        assert request.url.params["splits"] == "test"
        offset = int(request.url.params["offset"])
        rows = [{"id": str(i), "inputs": {"x": i}, "outputs": 0} for i in range(offset, offset + 2)]
        return httpx.Response(200, json=rows)

    result = client("langsmith", handler).preview(
        "dataset",
        {
            "dataset_id": "data",
            "version": "stable",
            "cap": 3,
            "filters": {"splits": ["test"]},
        },
    )
    assert len(requests) == 3
    assert len(result["items"]) == 3
    assert all(item["expected_present"] for item in result["items"])
    assert result["completeness"] == {"complete": False, "reason": "item_cap", "count": 3}


def test_langfuse_dataset_resolves_id_preserves_schema_and_source():
    def handler(request):
        if request.url.path == "/api/public/v2/datasets":
            return httpx.Response(200, json={"data": [{"id": "data-id", "name": "folder/data"}]})
        assert request.url.path == "/api/public/v2/datasets/folder/data"
        assert request.url.params["version"] == "2026-08-01T00:00:00Z"
        return httpx.Response(
            200,
            json={
                "inputSchema": {"type": "object"},
                "items": [
                    {
                        "id": "one",
                        "input": {"messages": []},
                        "expectedOutput": False,
                        "sourceTraceId": "trace-1",
                    }
                ],
            },
        )

    result = client("langfuse", handler).preview(
        "dataset",
        {
            "dataset_id": "data-id",
            "version": "2026-08-01T00:00:00Z",
        },
    )
    assert result["provenance"]["dataset_name"] == "folder/data"
    assert result["provenance"]["schema"] == {"inputSchema": {"type": "object"}}
    assert result["items"][0]["expected_present"]
    assert result["items"][0]["source"]["sourceTraceId"] == "trace-1"


@pytest.mark.parametrize("provider", ["braintrust", "langsmith", "langfuse"])
def test_trace_import_fetches_children_outside_root_window(provider):
    calls = []

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        calls.append((request, body))
        if len(calls) == 1:
            if provider == "braintrust":
                assert "metrics.start" in body["query"]
                payload = {"data": [{"root_span_id": "trace-1", "span_id": "root"}]}
            elif provider == "langsmith":
                assert body["is_root"] is True
                assert "lt(start_time" in body["filter"]
                payload = {"runs": [{"id": "root", "trace_id": "trace-1"}]}
            else:
                assert request.url.params["parentObservationId"] == ""
                assert "isRootObservation" not in request.url.params
                payload = {"data": [{"id": "root", "traceId": "trace-1"}]}
        else:
            rows = [{"id": "root"}, {"id": "child", "input": {"nested": ["preserved"]}}]
            if provider == "braintrust":
                assert 'root_span_id = "trace-1"' in body["query"]
                assert "metrics.start" not in body["query"]
                payload = {"data": rows}
            elif provider == "langsmith":
                assert body["trace"] == "trace-1"
                assert "inputs" in body["select"] and "outputs" in body["select"]
                assert "start_time" not in body
                payload = {"runs": rows}
            else:
                assert request.url.params["traceId"] == "trace-1"
                assert "fromStartTime" not in request.url.params
                payload = {"data": rows}
        return httpx.Response(200, json=payload)

    result = client(provider, handler).preview("traces", trace_selection())
    assert len(calls) == 2
    assert result["items"][1]["input"] == {"nested": ["preserved"]}
    assert result["completeness"]["selected_traces"] == 1
    assert result["completeness"]["trace_details_complete"]


@pytest.mark.parametrize("provider", ["braintrust", "langsmith", "langfuse"])
def test_trace_metadata_reads_roots_without_inputs_or_outputs(provider):
    calls = []

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        calls.append((request, body))
        if provider == "braintrust":
            assert "is_root = true" in body["query"]
            payload = {
                "data": [
                    {
                        "root_span_id": "trace-1",
                        "metadata": {"agent_name": "Support router"},
                        "input": "private input",
                    }
                ]
            }
        elif provider == "langsmith":
            assert body["is_root"] is True
            assert "inputs" not in body["select"] and "outputs" not in body["select"]
            payload = {
                "runs": [
                    {
                        "trace_id": "trace-1",
                        "name": "Support router",
                        "extra": {"metadata": {"agent_name": "Support router"}},
                        "inputs": "private input",
                    }
                ]
            }
        else:
            assert "io" not in request.url.params["fields"]
            payload = {
                "data": [
                    {
                        "traceId": "trace-1",
                        "name": "Support router",
                        "metadata": {"agent_name": "Support router"},
                        "input": "private input",
                    }
                ]
            }
        return httpx.Response(200, json=payload)

    result = client(provider, handler).trace_metadata(trace_selection())

    assert len(calls) == 1
    assert result["items"][0]["trace_id"] == "trace-1"
    assert "private input" not in json.dumps(result)
    assert result["completeness"]["count"] == 1


def test_trace_metadata_deduplicates_root_rows_without_mislabeling_them():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"root_span_id": "trace-1", "metadata": {"agent_name": "First"}},
                    {"root_span_id": "trace-1", "metadata": {"agent_name": "Duplicate"}},
                    {"root_span_id": "trace-2", "metadata": {"agent_name": "Second"}},
                ]
            },
        )

    result = client("braintrust", handler).trace_metadata(trace_selection())

    assert [item["trace_id"] for item in result["items"]] == ["trace-1", "trace-2"]
    assert result["items"][1]["metadata"]["metadata.agent_name"] == "Second"


def test_repeated_cursor_is_partial_not_an_infinite_loop():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "events": [{"id": "one", "input": "case"}],
                "cursor": "repeated",
            },
        )

    result = client("braintrust", handler).preview(
        "dataset",
        {
            "dataset_id": "data",
            "version": "fixed",
            "cap": 10,
        },
    )
    assert result["completeness"]["reason"] == "pagination_incomplete"
    assert not result["completeness"]["complete"]


def test_trace_cap_does_not_cap_children():
    def handler(request):
        body = json.loads(request.content)
        if body.get("is_root"):
            return httpx.Response(
                200,
                json={
                    "runs": [
                        {"id": "root1", "trace_id": "trace-1"},
                        {"id": "root2", "trace_id": "trace-2"},
                    ]
                },
            )
        return httpx.Response(200, json={"runs": [{"id": str(i)} for i in range(4)]})

    result = client("langsmith", handler).preview("traces", trace_selection(cap=1))
    assert len(result["items"]) == 4
    assert not result["completeness"]["complete"]
    assert result["completeness"]["trace_details_complete"]


def test_credentials_are_removed_from_provider_data():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "one",
                        "input": "contains private-api_key-value",
                        "metadata": {
                            "authorization": "anything",
                            "nested": ["private-api_key-value"],
                        },
                    }
                ]
            },
        )

    result = client("braintrust", handler).preview(
        "dataset", {"dataset_id": "data", "version": "v"}
    )
    assert "private-api_key-value" not in json.dumps(result)
    assert result["items"][0]["metadata"]["authorization"] == "[REDACTED]"


def test_oversized_response_and_invalid_shape_fail(monkeypatch):
    monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 64)
    with pytest.raises(ProviderError, match="size limit"):
        client("braintrust", lambda _: httpx.Response(200, content=b"x" * 65)).test()
    with pytest.raises(ProviderError, match="record list"):
        client("braintrust", lambda _: httpx.Response(200, json={"wrong": []})).test()
