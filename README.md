# Agentagon

**Improve your AI agents.**

Inspect and improve AI agents with measured fixes and custom scores. Agree on behaviors and scoring, prepare reusable evals to establish a baseline, and compare verified improvements before preparing a draft PR.

Open the local web app with `agentagon`. Select a project, confirm its application agents and choose what to improve. Connect Codex or Claude and optional trace providers, then audit behavior, prepare evaluations and compare measured fixes. Python captures evidence, runs checks and saves reports; managed coding-agent sessions supply reasoning, reviews and candidate edits. Skills remain optional.

[Open the web app](https://github.com/agentagon/agentagon/blob/main/docs/app.md), [inspect agent behavior](https://github.com/agentagon/agentagon/blob/main/docs/audit.md), or [compare measured fixes](https://github.com/agentagon/agentagon/blob/main/docs/fix.md).

## Prerequisites

- **Python 3.12+** with `pip` and `venv`, on macOS or Linux. The implementation uses Unix facilities such as `fcntl`.
- **Git** to review changes and run evaluation or fix workflows. Full code audits also accept directories without Git.
- **Codex CLI with working authentication**, or the optional **Claude Agent SDK with an Anthropic API key**, for tasks that use a coding agent. Skill installation is not required by the web app.

Installation downloads Python dependencies. No Agentagon account or API key is required for the quickstart or core workflows; your coding host and configured services have their own access requirements.

## Installation

Install the CLI from PyPI using [pipx](https://pipx.pypa.io/stable/installation/):

```sh
pipx install agentagon
cd /path/to/your-agent
agentagon
```

The command opens the web app and reuses an existing local service when available. Keep its launching terminal open while tasks run. Downloadable wheels, source archives and checksums are also available in [GitHub Releases](https://github.com/agentagon/agentagon/releases).

Optional extras are `agentagon[claude]` for the Claude Agent SDK and `agentagon[credentials]` for OS credential storage. Select both at installation with `pipx install 'agentagon[claude,credentials]'`. Claude's managed integration requires API-key access; it does not use subscription authentication.

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

To use skills inside an existing coding host, run `agentagon install --host codex` or `agentagon install --host claude-code`, then start a new host session. See the optional [plugin installer](https://github.com/agentagon/agentagon/blob/main/docs/audit.md#install).

Anonymous skill and Intelligence usage telemetry is enabled by default. Disable it with `agentagon setup --scope user --set telemetry.enabled false` or `AGENTAGON_TELEMETRY_DISABLED=1`. See [collected fields, privacy and delivery](https://github.com/agentagon/agentagon/blob/main/docs/telemetry.md).

Optional Agentagon Intelligence is available through live `/v1/audit`, `/v1/eval` and `/v1/fix` routes. Configure the issued origin explicitly and follow the [workflow-specific request and privacy rules](https://github.com/agentagon/agentagon/blob/main/docs/intelligence.md).

## Quick start

Run `agentagon` from **your AI agent's code directory**. `agentagon app` is an alias; the project selector lets you add and switch local directories.

1. In **Settings → Coding agents**, select Codex and a model from its installed catalog, or configure Claude API-key access and its model.
2. Optionally add Braintrust, LangSmith or Langfuse under **Settings → Connections**. Preview and import selected traces or a dataset.
3. In **Agents**, discover and confirm application agents or add one manually. Select an agent and add a focus describing what matters.
4. Use **Audit** to investigate that focus, **Eval** to prepare a trusted benchmark, **Run baseline** to measure it, and **Fix** to compare improvements. Existing compatible evidence can be reused.

Review task progress, questions and permission requests in the browser. Closing the browser leaves tasks running; stopping the local service interrupts them until you explicitly resume. Dataset imports remain drafts until expectations, execution and independent review are ready. See the [web app guide](https://github.com/agentagon/agentagon/blob/main/docs/app.md) for setup and limits, or the [skill quickstart](https://github.com/agentagon/agentagon/blob/main/docs/getting-started/first-audit.md) for the optional host-driven path.

Optional examples: [check your installation offline](https://github.com/agentagon/agentagon/blob/main/examples/local-audit/README.md), or [compare fixes in the ticket-retry demonstration](https://github.com/agentagon/agentagon/blob/main/examples/ticket-retry/README.md).

## Workflows

| Goal | Web app and result |
|---|---|
| Inspect agent behavior and find failures | [Audit](https://github.com/agentagon/agentagon/blob/main/docs/audit.md): evidence-backed findings from code and execution traces. |
| Improve a saved goal or named issue | [Fix](https://github.com/agentagon/agentagon/blob/main/docs/fix.md): bounded optimization, verified comparisons and local delivery. |
| Define behaviors, custom scores and evals | [Eval](https://github.com/agentagon/agentagon/blob/main/docs/eval.md): agreed expectations, reviewed evaluations and reusable benchmarks. |
| Inspect history, rerun a baseline or manage settings | [Agents, Metrics, Eval and Settings](https://github.com/agentagon/agentagon/blob/main/docs/app.md): agent focuses, retained measurements, explicit reruns and connections. |

The optional `ag` skills and existing CLI commands remain available. Discovery accepts dirty or non-Git directories; measurement requires clean committed inputs and authorized limits. Without a runnable baseline, Fix reports the blocker; an unmeasured application patch requires an explicit request. Publication, merge and deployment remain separate actions.

Intelligence is optional and asks for approval of each outgoing request by default. Set its explicit **full access** mode through Setup to skip prompts while keeping calls visible. See [Intelligence permissions](https://github.com/agentagon/agentagon/blob/main/docs/intelligence.md).

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
| Sibling `config.app/app.sqlite3`; `AGENTAGON_APP_STATE` overrides its directory | Authoritative app metadata: projects, agents, focuses, named connections, settings, jobs and approvals |
| `.agentagon/` in the application directory | Engine records, immutable evidence, reports and work areas; initialization excludes it from Git |

Project overrides take precedence over user defaults. Credentials use environment references, session memory or an optional OS credential store; secret values are not saved in configuration. Use [web-app settings](https://github.com/agentagon/agentagon/blob/main/docs/app.md) for connections and agents, [execution profiles](https://github.com/agentagon/agentagon/blob/main/docs/fix.md#configure-execution-once) for evaluations and fixes, and [Intelligence setup](https://github.com/agentagon/agentagon/blob/main/docs/intelligence.md) for optional guidance.

The app is hosted locally on loopback; hosted/team access is deferred. Evidence is stored locally, while your coding host and configured services determine where model processing occurs. There is no legacy app-state migration; named app connections are configured explicitly.

## Development and contributing

See [CONTRIBUTING.md](https://github.com/agentagon/agentagon/blob/main/CONTRIBUTING.md) for editable installation, local development, tests and the pull request workflow. The [documentation index](https://github.com/agentagon/agentagon/blob/main/docs/README.md) links deeper guides and references.

To explore the implementation, start with [how Agentagon works](https://github.com/agentagon/agentagon/blob/main/docs/contributing/architecture.md) and the [extension walkthrough](https://github.com/agentagon/agentagon/blob/main/docs/contributing/extending.md). Maintainers can follow [release preparation](https://github.com/agentagon/agentagon/blob/main/docs/contributing/releasing.md).

Report vulnerabilities through [SECURITY.md](https://github.com/agentagon/agentagon/blob/main/SECURITY.md). Participation follows the [Code of Conduct](https://github.com/agentagon/agentagon/blob/main/CODE_OF_CONDUCT.md).

## License

[Apache-2.0](https://github.com/agentagon/agentagon/blob/main/LICENSE).
