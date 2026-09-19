# Install Agentagon

Use macOS or Linux, Python 3.12+, and Git for evaluation/repair. Install an authenticated Codex CLI, or use the optional Claude Agent SDK with API-key access.

```sh
pipx install agentagon
cd /path/to/your-agent
agentagon
```

Optional extras are `agentagon[claude,credentials]`. The command starts or reuses a detached local service and opens the React dashboard. Closing the browser or terminal does not cancel work. Use `agentagon serve` for foreground service operation, and `agentagon mcp` for stdio MCP.

This breaking release requires fresh state. Existing private data is neither migrated nor deleted. Choose fresh project/application state explicitly; incompatible schema versions are rejected. Public skill installation and host hooks have been removed.

`AGENTAGON_APP_STATE` isolates application settings, registrations and tasks. Project evidence is stored separately in the selected folder's `.agentagon` directory. A new application-state directory does not replace that project state.

If Analyze project reports incompatible workspace state, stop the service and preserve the old `.agentagon` directory in a backup outside the project. Restart the service with the same application settings and retry analysis; it creates fresh project state. Keep the backup intact, and do not change version numbers to bypass validation. This does not require cloning the source folder or using GitHub.

[Dashboard guide](../app.md) · [MCP setup](../mcp.md)
