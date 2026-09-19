# What Agentagon can do

Inspect your agents, define custom scores, prepare reusable evals and compare measured fixes. The [introduction's task guide](README.md) helps you choose the right workflow, from investigating failures to preparing a draft PR with verified improvements. Start there or follow the [quickstart](getting-started/first-audit.md).

## Execution and integrations

See [where work runs](execution.md) for local, SSH, and E2B execution boundaries. [Settings and profiles](settings.md) explains how to configure execution limits.

[Connect execution traces](traces.md) covers supported exports and provider access. [Agentagon Intelligence](intelligence.md) explains separately prepared investigation context and its evidence requirements.

## Advanced workflows

- [Coordinate authors and reviewers](orchestration.md) within the active host task, with saved reservations for resumption.
- [Choose search policies](reference/fix.md), [steer a run](reference/fix.md), and [learn from completed experiments](reference/fix.md).
- [Record task events and artifacts](task-evidence.md) for benchmark diagnostics.

For implementation details and representative tests, see the contributor [implementation map](contributing/capabilities.md).
