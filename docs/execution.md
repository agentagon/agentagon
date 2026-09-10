# Where work runs

Agentagon separates the coding agent that reasons about your application from the runner that executes a benchmark. Choose the execution environment when you save a profile, before measuring a baseline.

## Your coding host

Codex or Claude Code reads permitted evidence, proposes changes, and arranges independent reviews. Candidate authoring stays in local worktrees even when benchmark execution uses a remote runner. Your host's permissions and model provider determine where reasoning occurs.

A code-only audit inspects captured source without running the application. It needs neither an execution profile nor a trace provider. See the [quickstart](getting-started/first-audit.md).

## Benchmark execution

| Runner | Where commands execute | What you arrange |
|---|---|---|
| Local | On the host running the CLI. | Dependencies and appropriate local permissions. |
| SSH | On an existing remote machine. | An authorized destination and permission to transfer the declared execution inputs. |
| E2B | In a configured E2B sandbox. | The optional dependency, a template, and a credential reference. |

Local worktrees protect the original checkout's files; they are not a machine or network sandbox. Remote runners receive frozen execution inputs and return evidence. Only declared private inputs should be transferred.

Use [Settings and profiles](settings.md#configure-execution-before-evaluations-or-fixes) for your first setup, or the [profile and runner reference](reference/fix.md#configure-execution-once) for exact fields, concurrency requirements, and limits.

## A stable environment for comparison

A run keeps its frozen execution profile. Editing saved settings affects future runs. Changing the execution environment requires a fresh baseline, so results remain comparable.

Shared hardware and external model services can introduce variation. Inspect repeated measurements and the reported range when judging a candidate.

## Saved evidence and the dashboard

Workflow evidence lives in the application's ignored `.agentagon/` directory. The dashboard reads that checkout's saved projections; it does not run a separate model or reconnect to a runner when you open it.

See [Privacy and data](privacy.md) for credential handling, model processing, optional Intelligence, and telemetry.
