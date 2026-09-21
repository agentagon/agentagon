import asyncio
import json

from agentagon.mcp.server import create_mcp


class RecordingClient:
    def __init__(self):
        self.calls = []

    def request(self, method, resource, body=None, *, query=None):
        self.calls.append((method, resource, body, query))
        return {"method": method, "resource": resource}


def call(server, name, arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    return json.loads(result[0].text)


def test_mcp_declares_complete_connection_lifecycle():
    server = create_mcp(RecordingClient())

    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert {
        "list_connections",
        "discover_connection",
        "save_connection",
        "test_connection",
        "disconnect_connection",
    } <= tools.keys()
    assert set(tools["discover_connection"].inputSchema["required"]) == {
        "project_id",
        "provider",
        "credentials",
    }
    assert set(tools["save_connection"].inputSchema["required"]) == {
        "project_id",
        "discovery_id",
        "project_selection_id",
    }


def test_mcp_connection_tools_mirror_http_contracts_without_mutating_credentials():
    client = RecordingClient()
    server = create_mcp(client)
    credentials = {"public_key": "public", "secret_key": "secret"}

    call(
        server,
        "discover_connection",
        {
            "project_id": "project one",
            "provider": "langfuse",
            "credentials": credentials,
            "endpoint": "https://traces.example.test",
            "connection_id": "connection/old",
        },
    )
    call(
        server,
        "save_connection",
        {
            "project_id": "project one",
            "discovery_id": "discovery_123",
            "project_selection_id": "choice_456",
        },
    )
    call(
        server,
        "test_connection",
        {"project_id": "project one", "connection_id": "connection/old"},
    )
    call(
        server,
        "disconnect_connection",
        {"project_id": "project one", "connection_id": "connection/old"},
    )

    assert credentials == {"public_key": "public", "secret_key": "secret"}
    assert client.calls == [
        (
            "POST",
            "/api/projects/project%20one/connectors/discover",
            {
                "provider": "langfuse",
                "credentials": {"public_key": "public", "secret_key": "secret"},
                "endpoint": "https://traces.example.test",
                "id": "connection/old",
            },
            None,
        ),
        (
            "POST",
            "/api/projects/project%20one/connectors",
            {"discovery_id": "discovery_123", "project": "choice_456"},
            None,
        ),
        (
            "POST",
            "/api/projects/project%20one/connectors/connection%2Fold/test",
            {},
            None,
        ),
        (
            "DELETE",
            "/api/projects/project%20one/connectors/connection%2Fold",
            None,
            None,
        ),
    ]
