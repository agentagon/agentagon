# Troubleshooting

Inspect the task in the dashboard or with MCP `inspect_task`. Its state, pending question and retained evidence explain what remains. Start with the same project and task ID; creating a replacement can duplicate work.

## Installation and startup

Activate the Python environment where Agentagon is installed and run `agentagon --version`. Open a project with `agentagon --workspace /path/to/project`. MCP clients launch `agentagon mcp` and reuse the same local service without opening a browser. Configure the managed backend in Settings. Public skills and native registration are no longer used.

This release requires fresh state. It rejects older metadata and project schemas without rewriting them. Use a separate `AGENTAGON_APP_STATE` folder and a fresh project checkout; preserve old private state for inspection.

## Interrupted or queued tasks

Browser or MCP disconnection leaves tasks running. A stopped service marks active tasks interrupted; use Resume on that task or MCP `control_task` with `resume`. The runtime retains engine and session identities and reconciles existing execution before starting more work. Queued tasks wait for configured backend capacity.

If the service cannot bind loopback, enable local serving in the execution environment. Use `agentagon serve --port PORT` for an explicit foreground service. Controls require its exact origin and current session; refresh after a service restart.

## Questions, cancellation and limits

Answer the shared pending question from either interface. Repeat an uncertain submission with the same operation ID and identical request. Changed requests require a new ID. Cancellation stops owned execution; an uncertain remote result remains inconclusive until reconciled.

Task budgets remain bounded across resume. Exhaustion is not permission to increase them. Focused Fix freezes its retry and execution limits; a new authorized budget requires a new task.

## No verified repair

A Fix needs established expected behavior, a reproduced failure, a passing regression check, preserved regression gates and an independent review. Missing evidence is a limitation, even if the proposed code looks correct. Verified resolution describes the tested revision and does not prove production recovery.

Evaluation and repair need a clean committed Git checkout. Full Audit can inspect non-Git directories or uncommitted code. A prepared audit packet is unfinished review work, not evidence of a healthy agent. Modified protected inputs require a new evaluation version; do not rewrite saved digests or evidence.

## Trace import and issue ownership

Check the selected provider, connection, trace ID and supported [trace formats](traces.md). Pasted or uploaded JSON/JSONL is bounded. Missing parents, incomplete provider retrieval and malformed spans remain explicit limitations. Choose which issues to fix after discovery; discovery never starts bulk repairs. Confirm the owning application agent and code scope when a trace cannot establish them.

## Memory and Intelligence

Memory access requires explicit project and agent bindings. Use an empty folder when registering a group. Target-agent evaluations read a pinned snapshot; later updates cannot change a frozen comparison. See [memory groups](memory.md).

Optional [Intelligence](intelligence.md) uses a separately prepared, redacted request and its existing consent rules. Provider failures do not prevent local reasoning; credentials stay in backend references.

## Report a problem

Open an issue in the [repository](https://github.com/agentagon/agentagon/issues) with the version, OS, interface, expected behavior and a minimal synthetic reproduction. Include whether the task was new or resumed. Keep private state, source, credentials and customer traces out of public reports. Use the [security process](../SECURITY.md) for sensitive reports.
