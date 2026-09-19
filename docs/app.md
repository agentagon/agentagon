# Use the local dashboard

Run `agentagon` in your agent project. The React dashboard connects to a detached local service. Settings select a managed Codex or Claude brain and execution profiles; provider connections are optional.

Confirm the application agent and its code scope under **Agents**. Coding backends are configured separately under **Settings**. Under **Issues**, paste/upload supported trace JSON or JSONL, or select a provider connection and trace ID. Discover issues from selected traces, inspect confidence and evidence, then choose **Fix**. You can also start Fix from a problem description or an imported trace without defining a goal.

Use **Goals** inside an agent for broader improvement work. Accept its proposed measurement plan, prepare the evaluation and baseline, then run **Optimize**. The **Workflows** catalog exposes all built-in workflows, including full Audit. **Tasks** contains progress, shared questions, independent review, retained results, cancellation and resume controls. **Memory** registers local groups and offers explicit recall/record.

Tasks continue after dashboard or MCP disconnection. Service interruption leaves tasks interrupted; resume them explicitly. The dashboard never starts work merely by reading a page. Mutations require an exact local origin and session token.

[Memory groups](memory.md) · [MCP](mcp.md) · [Workflow behavior](workflows.md)

## Production feedback

First use opens resumable [setup](init.md). Review source scope before Analyze project. Each agent’s Improvements tab retains verified candidates and deployment declarations; its Production tab configures monitors and shows observations, coverage and uncertainty. Enable monitoring explicitly after confirming the trace selector. See [production monitoring](production.md).
