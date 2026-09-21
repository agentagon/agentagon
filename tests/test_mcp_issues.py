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


def test_mcp_issue_update_mirrors_revision_bound_http_contract():
    client = RecordingClient()
    server = create_mcp(client)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert set(tools["update_issue"].inputSchema["required"]) == {
        "project_id",
        "issue_id",
        "action",
        "expected_revision",
    }

    call(
        server,
        "update_issue",
        {
            "project_id": "project one",
            "issue_id": "issue/one",
            "action": "set_expectation",
            "expected_revision": 7,
            "reason": "Reviewed against the product contract.",
            "expected_behavior": "Use only facts supplied by the selected evidence.",
        },
    )

    assert client.calls == [
        (
            "POST",
            "/api/projects/project%20one/issues/issue%2Fone",
            {
                "action": "set_expectation",
                "expected_revision": 7,
                "reason": "Reviewed against the product contract.",
                "expected_behavior": "Use only facts supplied by the selected evidence.",
            },
            None,
        )
    ]
