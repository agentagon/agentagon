# Unreleased: recursive self-improvement for AI agents

Dashboard and stdio MCP now use one local service with managed Codex/Claude sessions. Built-in workflows own their definitions, instructions and handlers. The runtime owns durable tasks, questions, cancellation, resumption and execution limits.

Discover issues analyzes selected trace evidence without starting repairs. Fix accepts an issue, trace or description without a saved goal or full audit, reproduces the failure and independently verifies a focused repair. Optimize retains frozen comparisons, regression gates, failed attempts and verified alternatives. Issues are independent SQLite records with trace occurrences, diagnoses, repair tasks and tested-revision evidence.

Local memory groups have explicit project/agent access, configurable folders, versioned entries, evidence references and bounded recall. Evaluations pin target-agent memory snapshots. React adds Goals, Issues and Memory journeys and renames the catalog to Workflows.

The old dashboard, public skills, plugin manifests, native registration, continuation hooks and public workflow CLI interfaces are removed. Useful procedures and helpers ship inside workflow packages. Public commands open the dashboard, run the service, or serve MCP; managed sessions use a private engine command surface.

Guided setup now saves repository, backend and optional trace scope before starting Assess project. It discovers code and trace identities, retains uncertain bindings, routes uniquely matched findings, and recommends measured or unmeasured improvement paths. Missing authentication retains partial progress. Agents have persistent Improvements and Production views.

Verified candidates become improvements linked to their engine evidence. Deployment declarations and exact trace-reported release links remain distinct. Explicitly enabled local monitors schedule Observe production through the shared runtime, retaining stable windows, checkpoints, bounded catch-up, backoff and actionable failures. Restart requires explicit resume or discard for interrupted tasks.

Accepted, versioned measurement criteria govern production classifications. Incomplete windows, missing scores, mixed releases and revisions different from the tested candidate cannot establish improvement or recovery. Recurrence requires explicit reviews pinned to trace evidence; empty findings do not imply success. Material outcomes feed improvement memory, and tasks retain validated references to recalled lessons. Users still start repairs, select changes and control publication/deployment.

## Upgrade boundary

This is a breaking working-tree change, not a published release. Older metadata and project state are rejected without rewriting them. Use fresh application state and a fresh project checkout; preserve existing private data separately. No compatibility wrappers or migrations are provided. No changes have been committed, published, merged or deployed by this implementation task.

## Validation — 2026-09-18

Environment: macOS, Python 3.13, Chromium, official MCP Python SDK. The fresh pre-extension baseline collected 1,133 tests: 1,108 passed and seven skipped; the sandbox blocked local HTTP/browser work, producing ten failures and eight setup errors. The following integration runs used permitted loopback access.

The complete run passed **1,141 tests**, with **seven optional live SSH/E2B tests skipped**. Subsequent source changes and one added assessment-ownership scenario passed a 97-test focused run covering production, catalog, workflows, browser journeys and packaged definitions. The extended trace-to-repair-to-deployment/observation journey passed in the final 10-test workflow rerun. Current source collects 1,149 tests: 1,142 distinct tests passed across the complete run and final focused reruns, with the same seven skips.

Production coverage includes code-only assessment, provider fixtures, uncertain identities, explicit recurrence reviews, changed trace evidence, conservative comparison criteria, duplicate submissions, concurrent scheduler ticks, service restart, interrupted work, memory failure/retry and pinned lesson versions. Browser and official stdio MCP clients exercise the actual local interfaces. The repair journey reproduces a supplied trace without a goal or audit, independently verifies the candidate, records deployment and production evidence, and preserves unknown production recovery.

Additional passing checks:

- Ruff lint and formatting, TypeScript, frontend build, shipped JavaScript syntax and Git whitespace checks.
- Strict documentation build and website link/asset validation across 49 pages.
- Source distribution and wheel build; wheel installed in a fresh environment outside the checkout.
- Installed workflow handlers/resources, contracts, event helpers, React assets, bounded offline GEPA search and local audit example.
- Installed official MCP client starts an assessment, deduplicates submission, disconnects, and reconnects to the same detached service and task.
- Extracted source distribution builds its site and passes all five standalone site tests.
- Python 3.10 syntax checks for the standard-library remote worker and copyable Python event helper.

Managed model responses and provider acquisitions in integration tests are deterministic fixtures. The installed code-only assessment explicitly runs without a coding backend. These checks do not establish live Codex/Claude execution, live provider behavior or production recovery. Live provider, SSH and E2B integrations were not run. No execution limits, comparison criteria or review gates were relaxed to make tests pass.
