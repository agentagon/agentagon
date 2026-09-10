# Contributing to Agentagon

Start with the [README quickstart](README.md#quick-start) to see the evidence workflow. Use the [implementation map](docs/contributing/capabilities.md) to find the source and tests for a behavior, and read the [product decisions](docs/contributing/product-decisions.md) before changing workflow boundaries.

For entry points and data flow, read [how Agentagon works](docs/contributing/architecture.md). The [extension walkthrough](docs/contributing/extending.md) follows a proposed normalization change from fixture to saved measurement.

Participation follows the [Code of Conduct](CODE_OF_CONDUCT.md). Report suspected vulnerabilities privately using [SECURITY.md](SECURITY.md), rather than a public issue or pull request. Use the repository's bug and feature templates for ordinary reports; include synthetic or redacted examples.

## Set up a development environment

Use Python 3.12+, Git and Node.js 22 on macOS or Linux. Node runs JavaScript syntax checks and event-helper tests; it is not required for the local example below. You need network access to install dependencies.

Clone the repository as shown in the [README](README.md#installation), or clone your fork if you will submit changes without repository write access. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
node --version
python examples/local-audit/demo.py
```

If you already created `.venv` for source installation, reuse it and start at the activation command. Editable installation makes Python source changes available without reinstalling. The `dev` extra supplies pytest, Ruff and the build frontend; versions and dependency bounds are defined in [pyproject.toml](pyproject.toml).

The example should print JSON with `pending_action: "evidence"` and paths to a packet and partial report. It does not need host registration, model access or provider credentials.

## Work locally

Create a branch for your change, then use the implementation map to locate the relevant module and tests. Run commands from the repository root with `.venv` active. For example, while changing settings behavior:

```sh
python -m pytest tests/test_config.py -q
```

| Location | Contents |
|---|---|
| `src/agentagon/cli/` | Click commands and JSON output |
| `src/agentagon/storage/`, `src/agentagon/core/`, `src/agentagon/telemetry/` | Saved state, record validation and trace processing |
| `src/agentagon/experiments/` | Evaluation preparation, candidate execution and delivery |
| `src/agentagon/dashboard.py`, `src/agentagon/dashboard_assets/` | Local dashboard server and browser assets |
| `skills/`, `contracts/v1/`, `signals/` | Host workflows, JSON contracts and audit criteria |
| `tests/`, `examples/`, `docs/` | Behavioral checks, runnable examples and documentation |

Use `AGENTAGON_CONFIG` to point manual experiments at a separate settings file; `--workspace PATH` selects a separate application directory. The test fixtures isolate their settings and create temporary application repositories. For dashboard work, follow the [example guide](examples/local-audit/README.md) and run the [browser checks](docs/contributing/README.md#browser-checks).

## Run checks before submitting

These commands match the checks in [.github/workflows/tests.yml](.github/workflows/tests.yml):

```sh
python -m ruff check src tests skills/eval/helpers scripts examples
python -m ruff format --check src tests skills/eval/helpers scripts examples
for script in src/agentagon/dashboard_assets/*.js skills/eval/helpers/*.cjs; do
  node --check "$script"
done
sh -n scripts/install.sh
python -m pytest -q
python -m build
```

Successful lint and formatting checks exit with status 0; pytest reports the pass/skip summary, and the build writes a source archive and wheel to `dist/`. CI tests Ubuntu with Python 3.12/3.13 and macOS with Python 3.13, then installs and checks the wheel outside the checkout.

Without the `browser` extra, pytest skips the dashboard browser module. Native host registration and live SSH/E2B tests are opt-in. Local dashboard and mock HTTP tests require loopback socket access. See [packaging, browser and integration checks](docs/contributing/README.md) for additional commands and test boundaries.

For documentation changes, execute changed command examples and check relative links. Report any checks you could not run instead of presenting them as verified.

## Submit a pull request

1. Keep the branch focused on one problem. Add or update behavioral tests when changing behavior, and update affected guides or contracts.
2. Review the diff for unrelated changes, credentials and generated evidence. Do not commit virtual environments, build output or `.agentagon/` state.
3. Commit and push your branch, then open a pull request against this repository.
4. Describe the problem, resulting behavior, and reproduction or usage example. Include the commands you ran, their results, and any skipped or unverified integrations.
5. Address review feedback and failing CI checks; rerun checks affected by subsequent changes.

Use [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) with an imperative summary and a scope only when useful. Use `feat` for features, `fix` for bugs, and mark breaking changes with `!` or a `BREAKING CHANGE:` footer. The [repository guidance](AGENTS.md#documentation-and-commits) defines the full commit rules.

Maintainers should follow [release preparation](docs/contributing/releasing.md) for version alignment, artifact verification and release notes. A contribution or documentation change does not itself authorize publication.

Keep introductory usage in the README, detailed configuration and limits in the [user guides](docs/README.md), and implementation rationale in [contributor references](docs/contributing/README.md). Put dated test counts and environment-specific validation results in the pull request or release record.
