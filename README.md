# Agentagon

**Find failures. Test fixes. Ship with evidence.**

Agentagon helps you audit, evaluate, and improve any AI agent using its code and execution traces. Find failures, test candidate fixes, and compare results against a recorded baseline.

The Python CLI captures evidence, runs checks and saves reports. The `ag` plugin guides your coding assistant through the reasoning, reviews and candidate edits. Supported coding hosts and setup commands are listed below.

Start with **[Audit your agent](https://github.com/agentagon/agentagon/blob/main/docs/getting-started/first-audit.md)** to investigate a concern, or **[Improve your agent](https://github.com/agentagon/agentagon/blob/main/docs/getting-started/bring-one-failure.md)** to bring one known failure through evaluation and repair. Review, Eval and Ship support the later steps. [Install the plugin](https://github.com/agentagon/agentagon/blob/main/docs/getting-started/install.md) if needed.

## Prerequisites

- **Python 3.12+** with `pip` and `venv`, on macOS or Linux. The implementation uses Unix facilities such as `fcntl`.
- **Git** to review changes and run evaluation or fix workflows. Full code audits also accept directories without Git.
- **Codex or Claude Code with its native plugin manager** and working model access for agent-led workflows.

Installation downloads Python dependencies. No Agentagon account or API key is required for the quickstart or core workflows; your coding host and configured services have their own access requirements.

## Installation

Install the CLI from PyPI using [pipx](https://pipx.pypa.io/stable/installation/):

```sh
pipx install agentagon
agentagon install --host codex
# Or: agentagon install --host claude-code
```

Start a new coding-host session after registration. CLI installation and host
registration are separate steps; select the host you use. Downloadable wheels,
source archives and checksums are also available in [GitHub Releases](https://github.com/agentagon/agentagon/releases).

For source installation in an isolated environment:

```sh
git clone https://github.com/agentagon/agentagon.git
cd agentagon
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
agentagon --help
```

Ensure `python3 --version` reports 3.12 or newer. Keep this environment active for the following commands; reactivate it in each new shell.

For a persistent CLI and native host plugins, use the [plugin installer](https://github.com/agentagon/agentagon/blob/main/docs/audit.md#install). It creates a separate runtime and registers `ag` with selected or detected hosts.

Anonymous skill and Intelligence usage telemetry is enabled by default. Disable it with `agentagon setup --scope user --set telemetry.enabled false` or `AGENTAGON_TELEMETRY_DISABLED=1`. See [collected fields, privacy and delivery](https://github.com/agentagon/agentagon/blob/main/docs/telemetry.md).

Optional Agentagon Intelligence is available through live `/v1/audit`, `/v1/eval` and `/v1/fix` routes. Configure the issued origin explicitly and follow the [workflow-specific request and privacy rules](https://github.com/agentagon/agentagon/blob/main/docs/intelligence.md).

## Quick start

After installation, open **your AI agent’s code directory** in a new Codex or Claude Code session.

In **Codex**, select **ag:audit** from the skill picker, then paste:

```text
Complete a code-only audit of this AI agent.
Show the top findings and next steps.
Skip traces and optional Intelligence.
```

In **Claude Code**, paste:

```text
/ag:audit Complete a code-only audit of this AI agent.
Show the top findings and next steps.
Skip traces and optional Intelligence.
```

Agentagon audits your code and saves a report with findings and recommended next steps. The [dashboard](https://github.com/agentagon/agentagon/blob/main/docs/dashboard.md) opens automatically; use **ag:dashboard** to reopen it. See the [quickstart guide](https://github.com/agentagon/agentagon/blob/main/docs/getting-started/first-audit.md) for details.

Optional examples: [check your installation offline](https://github.com/agentagon/agentagon/blob/main/examples/local-audit/README.md), or [compare fixes in the ticket-retry demonstration](https://github.com/agentagon/agentagon/blob/main/examples/ticket-retry/README.md).

## Workflows

| Goal | Workflow and guide |
|---|---|
| Investigate code, traces or both | [ag:audit](https://github.com/agentagon/agentagon/blob/main/docs/audit.md) |
| Review staged, unstaged and new files | [ag:review](https://github.com/agentagon/agentagon/blob/main/docs/review.md) |
| Prepare and review a benchmark | [ag:eval](https://github.com/agentagon/agentagon/blob/main/docs/eval.md) |
| Measure candidate fixes against a baseline | [ag:fix](https://github.com/agentagon/agentagon/blob/main/docs/fix.md) |
| Prepare a selected fix for delivery | [ag:ship](https://github.com/agentagon/agentagon/blob/main/docs/fix.md) |

Evaluation and fix workflows require a clean committed checkout and configured execution limits. See the [capability overview](https://github.com/agentagon/agentagon/blob/main/docs/capabilities.md) for integrations and workflow boundaries.

## Configuration and saved data

The code-only quickstart needs no trace-provider connection, evaluation setup or Intelligence key. To inspect settings for your current directory:

```sh
agentagon setup
```

| Setting or location | Purpose |
|---|---|
| `--workspace PATH` before the subcommand | Select the application directory; defaults to `.` |
| `AGENTAGON_CONFIG` | Override the configuration file path |
| `$XDG_CONFIG_HOME/agentagon/config.json` | Default settings file; falls back to `~/.config/agentagon/config.json` |
| `.agentagon/` in the application directory | Evidence, reports and experiment state; initialization excludes it from Git |

Project overrides take precedence over user defaults. Credential settings store environment-variable names, not secret values. Use [ag:setup and the configuration guide](https://github.com/agentagon/agentagon/blob/main/docs/audit.md#first-audit-and-setup) for traces and preferences, [execution profiles](https://github.com/agentagon/agentagon/blob/main/docs/fix.md#configure-execution-once) for evaluations and fixes, and [Intelligence setup](https://github.com/agentagon/agentagon/blob/main/docs/intelligence.md) for optional audit, evaluation and fix guidance.

Evidence is stored locally; your coding host and configured services determine where model processing occurs.

## Development and contributing

See [CONTRIBUTING.md](https://github.com/agentagon/agentagon/blob/main/CONTRIBUTING.md) for editable installation, local development, tests and the pull request workflow. The [documentation index](https://github.com/agentagon/agentagon/blob/main/docs/README.md) links deeper guides and references.

To explore the implementation, start with [how Agentagon works](https://github.com/agentagon/agentagon/blob/main/docs/contributing/architecture.md) and the [extension walkthrough](https://github.com/agentagon/agentagon/blob/main/docs/contributing/extending.md). Maintainers can follow [release preparation](https://github.com/agentagon/agentagon/blob/main/docs/contributing/releasing.md).

Report vulnerabilities through [SECURITY.md](https://github.com/agentagon/agentagon/blob/main/SECURITY.md). Participation follows the [Code of Conduct](https://github.com/agentagon/agentagon/blob/main/CODE_OF_CONDUCT.md).

## License

[Apache-2.0](https://github.com/agentagon/agentagon/blob/main/LICENSE).
