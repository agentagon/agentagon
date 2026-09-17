---
description: Install Agentagon and open its local web app, with optional coding-host skills.
---

# Install Agentagon

Install Agentagon, then run `agentagon` in **your AI agent’s code repo** to open the local web app. One installation supports multiple projects. The `ag` skills are optional.

## Check prerequisites

| Requirement | Why you need it |
|---|---|
| macOS or Linux | Supported CLI environments. Native Windows is not supported. |
| Python 3.12 or newer, with `pip` and `venv` | Runs the CLI and its isolated installation. |
| Authenticated Codex CLI or Claude Agent SDK with an Anthropic API key | Runs app-managed author and reviewer sessions. |

Check your terminal:

```sh
python3 --version
```

## 1. Install the CLI from PyPI

Use [pipx](https://pipx.pypa.io/stable/installation/) to keep the CLI in an isolated environment:

```sh
pipx install agentagon
```

For an existing installation, run `pipx upgrade agentagon`. Downloadable wheels, source archives and checksums are available in [GitHub Releases](https://github.com/agentagon/agentagon/releases). Use `pipx ensurepath` if the command is not on your shell path.

Install optional extras with `pipx install 'agentagon[claude,credentials]'` for Claude SDK sessions and OS credential storage. Claude's app integration uses API-key authentication.

## 2. Open the web app

```sh
cd /path/to/your-agent
agentagon
```

The command opens the app and reuses an existing local service. Keep its launching terminal open while tasks run. In **Settings → Coding agents**, select Codex or provide Claude API-key access. Provider connections are optional; you can inspect existing evidence immediately. See [the web app guide](../app.md) for connections, execution profiles and task resumption.

<span id="2-install-for-your-coding-host"></span>

## Optional: install skills for your coding host

Register the bundled skills if you also want to start workflows inside an existing coding-host session. Repeat registration after upgrades to refresh those skills.

=== "Codex"

    ```sh
    agentagon install --host codex
    ```

    Start a **new Codex session** after installation. Choose Agentagon workflows from the native skill picker.

=== "Claude Code"

    ```sh
    agentagon install --host claude-code
    ```

    Start a **new Claude Code session** after installation. Invoke workflows with commands such as `/ag:init`.

=== "Both hosts"

    ```sh
    agentagon install --host codex --host claude-code
    ```

    Restart both host sessions before using the updated plugin.

The registration command installs the bundled `ag` plugin for the selected host. It preserves unrelated files and refuses to overwrite unmanaged plugin files. `pipx ensurepath` can add the CLI to your shell path if needed.

## Source installation

Install directly from a checkout into a virtual environment:

```sh
git clone https://github.com/agentagon/agentagon.git
cd agentagon
python3 -m venv .venv
. .venv/bin/activate
python -m pip install '.[credentials]'
agentagon
```

Reactivate the environment in later shells. The optional `bash scripts/install.sh --host codex` source installer creates a persistent isolated runtime and registers the selected plugin; it preserves unmanaged destinations.

<span id="3-confirm-the-cli-and-skills"></span>

## 3. Confirm the installation

```sh
agentagon --version
agentagon --help
```

If `agentagon` is not found, use the CLI path printed by the installer. With the default installation, add its bin directory to your terminal’s path:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Add that line to your shell startup file if you want it to persist. Ensure the coding host can also find the CLI; a desktop session may need to restart after a PATH change.

If you registered skills, choose one for the work you need:

| Skill | Use it to |
|---|---|
| `ag:audit` | Investigate code, local changes, traces or eval coverage. |
| `ag:fix` | Improve saved goals or a named issue and prepare verified delivery. |
| `ag:init` | Agree on behaviors, scoring and limits; prepare evals and a baseline. |
| `ag:eval` | Create, repair or validate reusable evaluations. |
| `ag:dashboard` | Inspect results, rerun baselines and manage settings. |
| `ag:setup` | Configure preferences, providers and execution profiles. |

!!! tip "You can start without connecting anything"
    An Agentagon account, Intelligence key, and trace-provider connection are unnecessary for your first code audit. Your coding host still needs its own working model access.

??? details "Use the CLI without registering a host plugin"

    For the local example or direct CLI use, install from the source checkout in a virtual environment:

    ```sh
    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install .
    agentagon --help
    ```

    Reactivate this environment in later shells. This installs CLI resources but does not register native skills. CLI preparation alone does not supply model reasoning or complete an audit.

??? details "Choose installation paths or use a wheel"

    The source installer accepts `--source /absolute/path/to/agentagon.whl`. It also supports `AGENTAGON_INSTALL_PREFIX`, `AGENTAGON_INSTALL_BIN_DIR`, and `AGENTAGON_INSTALL_PYTHON` for a runtime directory, command directory, and Python executable. Prefix and bin paths must be absolute.

    If the installer reports an unmanaged destination, select another path. Do not delete an existing environment or executable to force installation.

<div class="ag-next" markdown>

**Next:** [Use the web app](../app.md), [use the optional skill workflow](first-audit.md), or [try the synthetic local example](local-example.md).

</div>
