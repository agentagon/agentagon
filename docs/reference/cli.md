# Application commands

| Command | Behavior |
|---|---|
| `agentagon [--workspace PATH]` | Open the dashboard; start or reuse the detached service |
| `agentagon [--workspace PATH] serve [--port PORT]` | Run the shared loopback service in the foreground |
| `agentagon mcp` | Serve stdio MCP using that same service, without opening a browser |
| `agentagon --help` | Show the public command surface |

Workflows start through dashboard or MCP. There are no old workflow CLI aliases or native skill registration commands. The hidden `_internal` command group is an implementation detail for Agentagon-managed brain sessions. Engine reference pages describe that private surface; it is not a separate user workflow runtime.

See [MCP](../mcp.md) for the typed start contract and task controls.
