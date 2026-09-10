---
description: Install the Agentagon CLI and native plugin for Codex or Claude Code.
---

# Install Agentagon

Install the CLI and the `ag` plugin, then open a new coding-agent session in **your AI agent’s code repo**. You install Agentagon once and use it across repositories.

## Check prerequisites

| Requirement | Why you need it |
|---|---|
| macOS or Linux | Supported CLI environments. Native Windows is not supported. |
| Python 3.12 or newer, with `pip` and `venv` | Runs the CLI and its isolated installation. |
| Codex or Claude Code | Registers and runs the agent-led workflows. |

Check your terminal:

```sh
python3 --version
```

## 1. Install the CLI from PyPI

Use [pipx](https://pipx.pypa.io/stable/installation/) to keep the CLI in an isolated environment:

```sh
pipx install agentagon
```

For an existing installation, run `pipx upgrade agentagon` and register the host again to refresh its bundled skills. Downloadable wheels, source archives and checksums are available in [GitHub Releases](https://github.com/agentagon/agentagon/releases).

## 2. Install for your coding host

=== "Codex"

    ```sh
    agentagon install --host codex
    ```

    Start a **new Codex session** after installation. Choose Agentagon workflows from the native skill picker.

=== "Claude Code"

    ```sh
    agentagon install --host claude-code
    ```

    Start a **new Claude Code session** after installation. Invoke workflows with commands such as `/ag:audit`.

=== "Both hosts"

    ```sh
    agentagon install --host codex --host claude-code
    ```

    Restart both host sessions before using the updated plugin.

The registration command installs the bundled `ag` plugin for the selected host. It preserves unrelated files and refuses to overwrite unmanaged plugin files. `pipx ensurepath` can add the CLI to your shell path if needed.

## Source installation

The source installer creates a persistent isolated runtime and registers the selected host:

```sh
git clone https://github.com/agentagon/agentagon.git
cd agentagon
bash scripts/install.sh --host codex
# Or: bash scripts/install.sh --host claude-code
```

It prints the installed CLI path and refuses to overwrite destinations it does not manage.

## 3. Confirm the CLI and skills

```sh
agentagon --version
agentagon --help
```

If `agentagon` is not found, use the CLI path printed by the installer. With the default installation, add its bin directory to your terminal’s path:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Add that line to your shell startup file if you want it to persist. Ensure the coding host can also find the CLI; a desktop session may need to restart after a PATH change.

You should have these seven workflows:

| Skill | Use it to |
|---|---|
| `ag:audit` | Inspect your AI agent’s code or traces. |
| `ag:review` | Review uncommitted changes. |
| `ag:setup` | Configure preferences, traces, and execution profiles. |
| `ag:eval` | Prepare a reusable benchmark. |
| `ag:fix` | Compare measured candidate improvements. |
| `ag:ship` | Prepare a selected fix for delivery. |
| `ag:dashboard` | Inspect Agentagon workflow progress and results in a browser. |

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

**Next:** [Run your first audit](first-audit.md), or [try the synthetic local example](local-example.md).

</div>
