# Repository guidance

## Authoritative references

| Need | Source |
|---|---|
| Setup and contribution workflow | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Usage and configuration | [README.md](README.md), [user guides](docs/README.md) |
| Architecture and behavioral boundaries | [Product decisions](docs/contributing/product-decisions.md) |
| Source and representative tests | [Implementation map](docs/contributing/capabilities.md) |
| Accepted records and instrumentation | [Versioned contracts](contracts/v1/), [task evidence](docs/task-evidence.md) |
| Packaging and validation | [pyproject.toml](pyproject.toml), [MANIFEST.in](MANIFEST.in), [CI](.github/workflows/tests.yml), [extended checks](docs/contributing/README.md) |

## Commands

- Use Python 3.12+, Git and Node.js 22 on macOS or Linux; run commands from the repository root with the virtual environment active.
- Substitute changed Python paths for focused lint/format checks; the table includes the full CI scope.

| Task | Command |
|---|---|
| Development setup | `python3 -m venv .venv && . .venv/bin/activate && python -m pip install -e '.[dev]'` |
| Focused test example | `python -m pytest tests/test_dashboard.py -q` |
| Full suite | `python -m pytest -q` |
| Lint | `python -m ruff check src tests skills/eval/helpers scripts examples` |
| Format check | `python -m ruff format --check src tests skills/eval/helpers scripts examples` |
| Browser setup and tests | `python -m pip install -e '.[dev,browser]' && python -m playwright install chromium && python -m pytest tests/test_dashboard_browser.py -q` |
| JavaScript syntax | `for script in src/agentagon/dashboard_assets/*.js skills/eval/helpers/*.cjs; do node --check "$script"; done` |
| Installer syntax | `sh -n scripts/install.sh` |
| Build sdist and wheel | `python -m build` |

- Validate packaging changes by installing the built wheel in a fresh environment outside the checkout using the [packaging checks](docs/contributing/README.md#packaging-checks).
- Local HTTP/browser tests need loopback access; require authorization before enabling [native registration or live SSH/E2B tests](docs/contributing/README.md#optional-integration-checks), and report skips separately from verified results.

## Project boundaries

- Keep model reasoning, candidate edits and independent reviews in the coding-agent host; Python handles evidence, validation and bounded execution.
- Preserve review scope over captured changes and the fixed audit rubric; full audits may inspect uncommitted or non-Git directories, while evaluation/fix workflows require clean committed inputs.
- Preserve frozen benchmark comparisons, failed/dominated experiment evidence and user choice among verified alternatives; selection, publication, merge and deployment remain separate actions.
- Keep dashboard reads within one checkout's display projections; controls remain opt-in with exact-origin/session validation, and reads must not execute work or reconnect to runners.
- Preserve explicit invocation > project override > user default precedence; store credential references rather than secret values.
- Optional [Intelligence](docs/intelligence.md) uses separately prepared privacy-safe context/focus; never automatically upload raw code, traces, credentials or the saved audit goal.
- Keep the remote worker and copyable Python event helper compatible with Python 3.10 and standard-library-only execution.

## Generated files and private state

- Regenerate build output and caches from source; do not commit `.venv/`, `build/`, `dist/`, `*.egg-info/`, `__pycache__/`, `.pytest_cache/` or `.ruff_cache/` (see [.gitignore](.gitignore)).
- Keep `.agentagon/` private and ignored; use CLI operations for saved state, reports and immutable evidence. Edit prepared host response/templates as workflow inputs, without rewriting frozen records or checksum-addressed artifacts.
- Treat `contracts/`, `signals/`, `skills/` and `src/agentagon/dashboard_assets/` as shipped source, not disposable generated output; keep packaging declarations and resource references aligned when changing them.

## Documentation and commits

- Lead docs with the decision, outcome or action; use plain words, short paragraphs and concrete examples, without filler or unnecessary qualifiers.
- Keep each doc focused and each detailed rule in one authoritative place, linking elsewhere instead of duplicating it.
- Preserve requirements, evidence, tradeoffs and uncertainty; use enough detail to prevent mistakes rather than imposing arbitrary length limits.
- Distinguish proposals, decisions, implemented behavior and verified results; put dated test counts and environment-specific limits in PR/release records.
- Preserve unrelated work and history; do not infer authorization to commit, publish, merge or deploy from a documentation workflow.
- When committing, follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/): one logical change, an imperative summary without a trailing period, and a scope only when useful.
- Use `feat` for features and `fix` for bugs; other types are allowed. Mark breaking changes with `!` after the type/scope or a `BREAKING CHANGE:` footer.
- Add a commit body when needed to explain the previous problem, why the change is necessary, and tradeoffs or consequences the diff cannot explain.
