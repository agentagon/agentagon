# Contributor reference

Start with [CONTRIBUTING.md](../../CONTRIBUTING.md) for environment setup, everyday checks and the pull request workflow. Use the [implementation map](capabilities.md) to find source and tests, and [product decisions](product-decisions.md) for workflow boundaries.

Read the [architecture walkthrough](architecture.md) for entry points and data flow, try the [extension example](extending.md), or follow [release preparation](releasing.md) when producing distribution artifacts.

For the public documentation site, see [build, preview and hosting instructions](docs-site.md). Its end-user navigation starts at the [documentation introduction](../README.md).

## Packaging checks

The wheel bundles plugin manifests, all seven skills and their references, contracts, the signal catalog, event helpers and dashboard assets. Packaging inputs are declared in [pyproject.toml](../../pyproject.toml) and [MANIFEST.in](../../MANIFEST.in).

After running `python -m build` from the repository root, check the wheel in a fresh environment outside the checkout, as CI does:

```sh
ag_repo="$PWD"
ag_check_dir=$(mktemp -d)
python3 -m venv "$ag_check_dir/venv"
"$ag_check_dir/venv/bin/python" -m pip install "$ag_repo"/dist/*.whl
(
  cd "$ag_check_dir"
  "$ag_check_dir/venv/bin/python" "$ag_repo/scripts/check_installed.py"
  "$ag_check_dir/venv/bin/python" "$ag_repo/examples/local-audit/demo.py"
)
```

Use a `dist/` directory containing only the wheel you intend to test. If it contains older builds, replace the glob with the exact new wheel path.

The resource check prints:

```text
Installed package, plugin resources, contracts, helpers and dashboard assets are readable.
```

The example prints paths and `pending_action: "evidence"`, as shown in the README. These checks verify installed resources and local audit preparation; they do not register a plugin or perform a model review. The temporary environment and example directory remain available for inspection and can be removed afterward.

The copyable Python event helper targets Python 3.10, matching the remote worker, while the Agentagon CLI requires Python 3.12+. Keep its standard-library-only boundary when editing it. See [task evidence](../task-evidence.md) for helper usage and event formats.

## Browser checks

Browser tests run in a separate CI job. With the development environment active, install the extra and Chromium:

```sh
python -m pip install -e '.[dev,browser]'
python -m playwright install chromium
python -m pytest tests/test_dashboard_browser.py -q
```

The Linux CI job uses `python -m playwright install --with-deps chromium` to install browser system dependencies too. These tests exercise dashboard navigation, refresh, accessible empty states and theme persistence at mobile and desktop widths.

Without the browser extra, the regular suite skips this module. Once the extra is installed, Chromium must also be installed for these tests to run. Tests that serve the dashboard or mock HTTP endpoints require permission to bind loopback sockets.

## Optional integration checks

The regular suite exercises local execution and simulated integrations. The following environment variables enable tests that touch native hosts or remote services; leave them unset for ordinary local development:

| Variable | Effect |
|---|---|
| `AGENTAGON_TEST_NATIVE_CODEX=1` | Enable native registration checks when `codex` is on PATH |
| `AGENTAGON_TEST_NATIVE_CLAUDE=1` | Enable native registration checks when `claude` is on PATH |
| `AGENTAGON_LIVE_SSH_HOST` | Set an authorized SSH host alias to enable live SSH checks |
| `AGENTAGON_LIVE_E2B=1` | Enable live E2B checks; these also require the optional dependency and service credentials |

See [installation tests](../../tests/test_installation.py), [runner tests](../../tests/test_experiment_runners.py) and [live lifecycle tests](../../tests/test_live_fix.py) for their setup and assertions. Run live tests only with authorization for the host, credentials and any charges. Passing local or simulated tests does not establish that a live integration works in your environment.

## Documentation changes

Keep usage, configuration and operational limits in the [user guides](../README.md). Keep contracts and helper formats in advanced references, and design rationale and source/test mappings in this directory. Update related links when moving a page.

Record test counts, dated live checks and environment-specific blockers in the relevant PR or release record. Include the tested revision and environment so readers can distinguish local or simulated coverage from live integration results.
