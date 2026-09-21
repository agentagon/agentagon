# Product experience V2: source review appendix

Date: 2026-09-20. Scope: read-only inspection of the current React source, shared application operations, product decisions, and V1 plan. No tests, model sessions, provider requests, or product-state mutations were performed for this appendix. Source observations are not proof of live end-to-end behavior. Line numbers below refer to the inspected checkout and may move during implementation.

This appendix supports the [V2 master plan](product-experience-overhaul-v2.md). The master plan defines final labels, scope and sequencing if an exploratory proposal below differs. This appendix does not authorize or implement product changes.

## 1. P0: the result-to-delivery journey stops before users can consume the work

**Source observations**

- [GoalPage](../frontend/src/pages.tsx), lines 124–147: `stageState.review` is hardcoded to `locked`. The Review stage always displays `No verified candidate yet`; it does not inspect completed candidates.
- [ImprovementsView](../frontend/src/lifecycle.tsx), lines 61–73: `Inspect evidence and selection` links to the task page. `Record deployment` is disabled unless an improvement is already selected.
- [TaskPanel](../frontend/src/components.tsx), lines 102–128: task detail displays conversation, events, questions, summary, raw result JSON, resume, and cancel. It contains no candidate comparison, selection, or delivery action.
- The backend has candidate inspection and delivery surfaces: [dashboard/server.py](../src/agentagon/dashboard/server.py), lines 308–312 and 430–431, and [workflows/service.py](../src/agentagon/workflows/service.py), line 977 onward. Their existence does not establish that the full browser journey works.

**Proposal**

Complete one `Review improvement` page before expanding the navigation. Display the problem, before/after comparison, baseline identity, candidate identity, diff, regression gates, independent review, and limitations. Show failed or incomplete attempts in a disclosure.

Buttons are `Use this candidate`, `Keep current version`, and `Try another approach`. Once selected, show `Prepare local delivery`, then the prepared branch or patch and concrete application instructions. A configured remote may add `Create draft pull request`. Recording a deployment remains separate.

Selection here means an explicit user decision, not merely the managed task's internal engine selection. See [section 18](#18-managed-fix-selection-is-not-evidence-of-human-choice) for that distinction and [section 19](#19-delivery-needs-a-result-specific-application-adapter) for result-specific delivery inputs.

**Manual acceptance criterion:** a completed verified candidate is inspectable, selectable, and deliverable from its visible result without reading JSON, constructing API calls, or opening documentation. Alternatives remain available until the user chooses.

## 2. P0: the UI asks users to accept a measurement plan it does not display

**Source observations**

- [GoalPage](../frontend/src/pages.tsx), lines 131–142: the design acceptance request includes the expected plan revision. The rendered plan card displays only `Measurement plan`, its state, and `Accept measurement plan`; it does not show the plan's behaviors, metrics, scoring, evaluation, evidence, or limitations.
- [Designs](../src/agentagon/workflows/evaluate/designs.py), lines 12–113: the accepted design has substantive behavior, metric, scoring, evaluation, evidence, background, and limitation fields.
- [Product decisions](contributing/product-decisions.md), line 23: measurement proposals remain editable drafts until explicit acceptance; acceptance pins scoring and required behaviors.

**Proposal**
Render a readable proposal before acceptance:

| Section | Visible content |
|---|---|
| Behaviors | Scenario, expected behavior, required/optional, corresponding check |
| Score | Primary metric, direction, units, aggregation, missing-result policy |
| Guardrails | Required behaviors and pass/fail bounds |
| Cases | Source, representative examples, count, coverage gaps |
| Execution | Existing evaluation or proposed harness; command and required environment |
| Limits | Missing expectations, unsupported data, scoring uncertainty |

Use `Edit proposal`, `Ask for changes`, and `Accept measurement plan`. A later combined `Accept and run baseline` action may perform separate accepted-definition and bounded-run operations, but only when the concrete definition and execution scope are visible. Acceptance errors must stay beside the proposal without losing edits.

**Manual acceptance criterion:** an engineer can state what a pass means, which cases are measured, and what remains unknown before accepting.

## 3. P0: readiness is split across incomplete projections and submission rules

**Source observations**

- [LaunchWorkflowModal](../frontend/src/components.tsx), lines 160–177: readiness is queried using workflow, agent, and goal. It omits the selected trace or description, source scope, limits, and execution settings. The component adds its own input checks when disabling `Start task`.
- [Application.workflow_readiness](../src/agentagon/workflows/service.py), lines 206–267: the projection checks agent confirmation, goal presence, audit code scope, and selected evaluation prerequisites. It does not establish full backend authentication, source readiness, provider readiness, or executable input validity.
- [Workflow submission](../src/agentagon/workflows/requests.py), lines 12–130: submission separately validates typed inputs, trace existence, ambiguous ownership, allowed workflow/input combinations, and code scope. It can infer ownership from an issue or a sole confirmed agent even when the readiness surface required an explicit agent.

**Proposal**

Prepare the complete user intent through one shared application operation. Inputs include workflow intent, typed problem/evidence input, optional agent, scope, limits, and execution choices. Return `ready`, `ready_with_limits`, or `needs_input`, together with the proposed action, source identity, outputs still available, and typed missing requirements.

Each missing requirement has an inline resolution: `Choose agent`, `Review expected behavior`, `Configure Codex`, `Choose source revision`, or `Connect traces`. Preserve the user's draft and return to the original action after resolution. Start must revalidate mutable prerequisites and use the existing idempotent operation contract.

**Manual acceptance criterion:** the interface cannot show `Ready` merely because an agent and goal exist, then reject an already-known missing input only after submission. HTTP and MCP receive the same preparation result.

## 4. P0: preserve direct trace-to-Fix intent

**Source observations**

- [IssuesPage](../frontend/src/pages.tsx), lines 275–290: intake already retains either `discover` or `fix` intent while importing a trace, then opens the corresponding action.
- [Product decisions](contributing/product-decisions.md), line 31: Fix accepts an issue, trace, or description without a saved goal or full audit.
- [V1](product-experience-overhaul.md), line 679, instead prescribes `Do not combine import and repair` and requires choosing an issue after investigation.

**Proposal**

Offer `Understand this trace` and `Fix a problem in this trace`. Reuse ingestion and diagnosis, but preserve the selected intent. If one repair target is clear, show the proposed failure, expectation, ownership, code scope, and limits together. If several distinct issues exist, ask which one the user wants to address. Discovery never starts bulk repairs.

Do not force the user to return through an issue inventory solely to restate the intent they already supplied. A saved goal remains optional.

**Manual acceptance criterion:** a supplied trace can reach one focused Fix without a pre-created goal or full audit; ambiguity produces a targeted question with retained evidence.

## 5. P0: explain local-folder and non-Git limits accurately

**Source observations**

- [V1](product-experience-overhaul.md), line 306, says Git is `recommended for measured changes`.
- [Product decisions](contributing/product-decisions.md), line 33, requires clean committed inputs for measurement and permits full audit of dirty or non-Git folders.
- [Product decisions](contributing/product-decisions.md), lines 97 and 121, supports local delivery without a remote and keeps publication, merge, and deployment separate.

**Proposal**

Use capability-specific readiness:

| Project condition | Available now | Before measured changes |
|---|---|---|
| Non-Git folder | Bounded code assessment, discovery, audit, supplied-trace inspection | A user-approved clean committed source snapshot |
| Dirty Git checkout | Current-code inspection and diagnosis | Choose a committed revision or explicitly prepare a clean source snapshot |
| Clean local Git repository, no remote | Evaluation, Fix, Optimize, local branch/patch delivery | GitHub is not required |
| No authenticated coding backend | Bounded local scan | Configure a backend for semantic review and managed coding |
| No observability connection | Code-only work, existing checks, supplied traces | Connect production evidence for recurring observation |

Do not initialize Git, commit user work, or create a remote implicitly. Do not block useful inspection while presenting a measurement prerequisite. Show the limitation at the action that needs it and retain the pending intent.

An unmeasured patch remains a separate explicitly requested path with independent review and visible limitations. It must not become a shortcut around failed required checks.

## 6. P1: V1 adds interface weight while trying to remove it

**Plan observations**

[V1](product-experience-overhaul.md) prescribes seven agent tabs at line 261, six issue-detail tabs at line 632, a four-step Fix flow at line 683, a three-step monitoring wizard at line 852, and setup routing until assessment plus confirmation at line 1702. These are proposals, not observed runtime defects.

**Proposal**

Prefer a small persistent workspace and conditional detail pages:

- Default agent tabs: `Overview`, `Checks`, `Changes`, `Production`. Identity and configuration live under `More` or the agent header menu.
- Show issues and relevant lessons on Overview, with deep-link detail pages. Do not allocate permanent tabs to empty collections.
- Use one readable issue detail with anchors/disclosures, rather than six mostly empty tabs.
- When issue, ownership, accepted verification, and limits already exist, Fix needs one prefilled action sheet. Expand only unresolved requirements.
- Let users inspect suggested agents before confirmation. `Use this agent` can confirm the exact displayed binding as part of starting an authorized action. Ambiguous mappings still require explicit choice; never silently merge them.
- Keep optional bulk review for large inventories. Do not require reviewing every candidate to get first value from one agent.
- Keep source symbols, compact paths, failing spans, scorer definitions, and diffs easy to inspect. Hide opaque IDs and transport logs, not evidence needed to trust an engineering decision.

The V2 master plan may choose different final labels. The invariant is fewer forced transitions and no mandatory visit to every domain-object page.

## 7. P1: continuity and recovery need explicit interaction contracts

**Source observations**

[OnboardingPage](../frontend/src/lifecycle.tsx), lines 27–44, and [LaunchWorkflowModal](../frontend/src/components.tsx), lines 160–177, use local component state for important draft choices. Configuration links navigate to separate pages. This inspection did not establish a general persisted draft or return-to-intent contract.

**Proposal**

Specify these behaviors before adding more forms:

- Preserve problem text, evidence references, and selected intent across navigation, failed requests, and reload.
- After backend, provider, or identity configuration, return to the originating action with its draft intact.
- Distinguish loading, unavailable data, empty results, and request failure.
- Show conflict recovery when MCP or another task changes the edited record.
- Keep the submitted receipt visible after a double click or network retry; duplicate submission must not create another task.
- Preserve deep links and browser Back behavior across action sheets and result pages.
- Show retained results even when the original project folder is unavailable, with explicit limits on new execution.

These are first-release usability requirements, not optional animation or visual polish.

## 8. Sequencing recommendation

V1's application-operation and projection boundaries are useful, but moving every responsibility into new packages is not a prerequisite to making the product useful. Its claim that most required technical capabilities exist is not validated by this source review.

Implement in vertical slices:

1. Display and accept a real measurement proposal; make one completed improvement inspectable, selectable, and locally deliverable.
2. Add complete intent preparation and inline prerequisite recovery.
3. Make local-folder assessment, suggested-agent inspection, and direct trace-to-Fix continuous.
4. Improve production coverage, release linkage, and feedback only after the preceding result journey works.
5. Extract application/use-case modules as those slices touch them; keep existing validated engines and shared runtime boundaries.
6. Defer a public extension SDK, team system, generic policy framework, and cross-project learning until concrete integrations require them.

Do not prescribe an arbitrary state-version bump for navigation changes. Bump versions when actual persisted contracts change, reject incompatible state according to the existing release policy, and leave prior private evidence untouched.

The completion criterion is a coherent engineer journey: understand a real problem, approve a trustworthy check, obtain a verified change, inspect why it is better, use it locally, and later understand the production evidence.

## 9. Backend audit: preserve the foundations that already exist

These are verified source observations, not claims that live behavior was exercised or that automated checks passed. No tests, model calls, provider calls, or workflow executions were performed for this review.

| Existing capability | Source reference | Consequence for the implementation plan |
|---|---|---|
| MCP forwards to the same managed local HTTP service | [MCP server](../src/agentagon/mcp/server.py), lines 18–25; [service host](../src/agentagon/workflows/service_host.py), lines 118–162 | Preserve one coordinator. Add useful operations to this boundary rather than a second MCP execution path. |
| Task identity derives from project and operation ID, and changed retry payloads are rejected | [Runtime](../src/agentagon/workflows/runtime.py), lines 105–115 | Retain this submission contract in every new action sheet and MCP operation. |
| Task controls retain operation receipts and validate shared question identity | [Runtime](../src/agentagon/workflows/runtime.py), lines 880–982 | Show supported controls accurately. `discard` is not currently a task control; it exists for monitors and must not be presented as a universal task action without defining its semantics. |
| Service restart marks unfinished work interrupted rather than restarting it | [Runtime](../src/agentagon/workflows/runtime.py), lines 66–79 | A smoother experience must preserve explicit resumption of uncertain execution. |
| The metadata store checks record revisions and uses short SQLite transactions | [Metadata](../src/agentagon/storage/metadata.py), lines 184–219 and 254–298 | The existing generic-record store already has concurrency protection. A complete relational rewrite is not required for new navigation. |
| The scheduler persists pending windows and reuses ordinary task submission | [Scheduler](../src/agentagon/workflows/scheduler.py), lines 66–156 | Keep the local service owner, pending-task guard, stable operation identity, catch-up bounds, and backoff behavior. Improve visibility around them. |
| Workflow memory failures have a retry path | [Scheduler](../src/agentagon/workflows/scheduler.py), lines 158–176; [outcomes](../src/agentagon/workflows/outcomes.py), lines 65–124 | A generalized transactional outbox is not a prerequisite for the first usable learning loop. |
| Memory groups enforce project/agent access and freeze target-agent snapshots | [Memory store](../src/agentagon/memory/store.py), lines 123–145 and 279–296 | Preserve these boundaries while replacing the folder-administration UI with useful learning views. |
| Current task projections already filter protocol noise | [Projections](../src/agentagon/domain/projections.py), lines 31–47 and 115–144 | Do not describe generic command-message filtering as an entirely missing implementation. Focus on meaningful stages, result summaries, and next actions. |
| Native evaluator discovery already recognizes Braintrust, DeepEval, pytest, and custom evaluators | [Evaluator discovery](../src/agentagon/capabilities/evaluation/native.py), lines 18 and 42–78 | Integrate discovered checks into the journey before adding a new evaluator framework. |
| Verified improvement records reference authoritative engine evidence | [Improvements](../src/agentagon/domain/improvements.py), lines 6–45 | Keep candidate verification in the existing engine. New views must not manufacture a parallel verification state. |
| Manual deployment declarations and exact trace-reported release/revision linkage are distinct | [Improvements](../src/agentagon/domain/improvements.py), lines 62–108 and 111–150 | Keep the linkage provenance and exact-tested-revision distinction visible. |

The runtime uses a shared condition lock and local service ownership. The monitor scheduler checks unresolved tasks before submitting another window. This source review supports preserving those mechanisms; it does not establish concurrency behavior under every crash or multi-process failure scenario.

## 10. P0: readiness and submission must use the same rules

**Source observations**

[Application.workflow_readiness](../src/agentagon/workflows/service.py), lines 206–267, checks selected agents, confirmed bindings, goals, accepted measurement plans, evaluations, and baselines. Its Fix path does not check the confirmed code scope and existing regression-baseline requirements that [workflow submission](../src/agentagon/workflows/requests.py), lines 125–153, enforces.

Consequently, a readiness result can say an action is ready while submission rejects it. Improving button copy alone does not repair this gap.

**Proposal**

Extract a side-effect-free preparation operation used by both readiness and submission. It should return:

- accepted intent and input references;
- selected agent and binding revision;
- proposed edit scope;
- relevant accepted checks and baseline status;
- structured blocker codes and resolving action IDs;
- limits and meaningful retained evidence;
- the revisions that must still hold at submission.

Do not create memory groups, start sessions, execute evaluators, or contact providers merely to render readiness. Submission revalidates current records before dispatch. A separate explicitly invoked connection-validation operation may contact a provider and retain its result.

The frontend owns display wording; the backend owns prerequisite truth. Both HTTP and MCP consume the same preparation result. Avoid a second UI-only rules engine.

**Manual acceptance criterion:** a trace-only agent can be inspected and diagnosed; attempting Fix explains that code ownership is missing and returns the user to the same intent after binding code. A missing accepted regression baseline appears before the user presses the final start button.

## 11. P0: a verified repair currently drops out of follow-up recommendations

**Source observations**

[resolve_repair](../src/agentagon/workflows/outcomes.py), lines 49–60, records `status="resolved_verified"`, the tested revision, and `production_recovery_verified=False`. [Recommendation generation](../src/agentagon/workflows/production_runtime.py), lines 555–556, excludes issues whose status is `resolved_verified` or `resolved_user`.

The source correctly distinguishes tested repair from production recovery, but the recommendation path no longer proposes what to do next for that issue.

**Proposal**

Represent independent facets in the projection:

| Facet | Example values |
|---|---|
| Issue triage | Open, dismissed with reason, recurring |
| Repair evidence | No verified repair, verified on a named revision |
| User choice | Alternatives available, selected change |
| Delivery | Not prepared, local branch/patch prepared, published by user request |
| Deployment | Not recorded, user-declared, trace-reported exact revision |
| Production outcome | Not observed, insufficient evidence, not comparable, improved, regressed, no material change |

Generate the next useful action from these facets: `Review change`, `Use locally`, `Record deployment`, or `Observe this release`. Keep the original issue and failed attempts inspectable throughout.

Do not flatten this into one linear `open → fixed → deployed → resolved` state machine. There can be several verified alternatives, overlapping releases, later recurrence, or a repair that is never selected. User-declared closure is a valid disposition with provenance; it is not production verification.

**Manual acceptance criterion:** after a verified Fix, the issue detail shows the tested revision and explicit production uncertainty, and the workspace offers the next delivery or observation step instead of making the issue disappear.

## 12. P1: operational monitoring edits must preserve comparable history

**Source observations**

[Monitoring.save](../src/agentagon/domain/monitoring.py), lines 125–167, combines schedule, trace cap, storage budget, catch-up, diagnosis, selector, binding digest, and measurements into one policy digest. Any change increments `series`. Lines 187–199 clear the acquisition checkpoint, diagnosis state, references, and latest observation/task links for a changed policy.

Increasing a storage budget therefore creates a new comparison series even though the accepted measurement has not changed.

**Proposal**

Separate three identities:

1. **Acquisition policy revision:** schedule, fetch cap, storage budget, catch-up, diagnosis cadence.
2. **Measurement comparability signature:** population, scoring, aggregation, direction, accepted target/material-change criteria, comparison windows, sample and coverage requirements.
3. **Agent binding version:** confirmed code/trace selector ownership.

An operational change preserves the comparison series and past evidence. A fetch-cap change can affect sampling/population semantics and therefore requires comparability review; it is not automatically equivalent to a storage-budget change. A measurement-comparability change begins a new series with an explicit explanation. A binding change retains history and requires explicit scope confirmation before future collection. All changes still honor active-task/pending-window protection.

**Manual acceptance criterion:** raising a paused monitor's storage limit preserves the prior series and reference window. Changing accepted scoring starts a new series and leaves the old observations accessible.

## 13. Responsibility inference exists; make its state and recovery visible

**Source observations**

- [Discovery](../src/agentagon/capabilities/discovery.py), line 69, sets a 50-line source context; lines 142–164 enforce bounded local source access.
- [Production runtime](../src/agentagon/workflows/production_runtime.py), lines 179–233, prioritizes suggestions with missing descriptions, selects up to 100 candidates, and attaches excerpts.
- [Assessment handler](../src/agentagon/workflows/assess/handler.py), lines 62–90, requires every supplied candidate identity and a nonempty responsibility for each retained agent.
- [Catalog](../src/agentagon/domain/catalog.py), lines 196–262, validates and applies the coding review.

Thus a blank field observed in a running service does not establish that the current checkout lacks inference. It can reflect old state, a stale service, incomplete review, unavailable source context, or another failure. The current UI needs to explain which one applies.

**Proposal**

Add per-candidate inference state: `pending`, `ready`, `limited`, `failed`, or `edited`, with the scan/context reference, attempt/task identity, concise reason, and `Retry description` action. Retry selected unresolved candidates without reacquiring traces or rerunning every completed assessment step. Preserve deterministic candidates and user edits after semantic-review failure.

Keep confirmation independent: a generated description does not confirm code ownership or expand a trace binding. Do not implement a second inference pipeline beside the existing assessment machinery.

## 14. Remaining operations needed for a dependable workspace

These are proposed additions grounded in current operation gaps.

### Candidate review and identity

[Catalog.save_agent](../src/agentagon/domain/catalog.py), lines 329–383, supports a generic save with `suggested`, `confirmed`, and `archived`; [agents](../src/agentagon/domain/catalog.py), lines 129–150, omits archived records. There is no complete user review/merge operation.

Add explicit confirm, exclude with reason, restore, and duplicate-consolidation operations with expected revisions and operation IDs. Preserve discovery keys to avoid repeatedly showing excluded definitions. Before consolidation, show impact on goals, issues, monitors, memories, and bindings. Do not merge by copying fields and deleting a row.

### Recommendation disposition

[Recommendation generation](../src/agentagon/workflows/production_runtime.py), lines 552–627, computes Fix recommendations and three broad improvement categories, with no dismissal, snooze, accepted-task linkage, or evidence expiry.

Keep deterministic generation and store user disposition keyed by stable recommendation identity plus evidence version. Return the concrete reason, evidence date, samples/coverage, prerequisite resolver, and one primary action. Do not build an opaque ranking framework first.

### Issue triage

[Issue records](../src/agentagon/domain/issues.py), lines 73–199 and 232–289, retain ownership, history, occurrences, and verified-update rules. [HTTP mutations](../src/agentagon/dashboard/server.py), lines 356–455, have no issue mutation branch.

Expose assignment, expected-behavior authoring, dismissal with reason, reopen, and task linkage through the shared Application. Verified resolution remains engine-owned. Preserve manual closure provenance.

### Setup readiness

[Assessment scope](../src/agentagon/workflows/production_runtime.py), lines 61–85, validates connection ownership but not credential readiness. [Connection projection](../src/agentagon/workflows/service.py), lines 1127–1137, separately reports unavailable credentials.

Provide one setup projection with selected scope, provider readiness, last validation, available sample coverage, and resolving actions. Keep an incomplete connection choice resumable while letting the user continue code-only.

### Bounded page projections

[Project overview](../src/agentagon/workflows/service.py), lines 705–748, returns many raw resource collections. [Agent overview](../src/agentagon/workflows/service.py), lines 374–415, starts from that project bundle and filters selected collections.

Add focused, bounded first-use, inventory, agent-summary, activity, and production projections. Use pagination or explicit detail reads for large evidence collections. Avoid replacing the current broad endpoint with a larger all-purpose aggregate.

## 15. Service identity and live updates need smaller changes than V1 proposes

**Source observations**

The health response is `{"application":"agentagon","version":1}` in [dashboard server](../src/agentagon/dashboard/server.py), line 202. [Service reuse](../src/agentagon/workflows/service_host.py), lines 126–133, checks that protocol constant. This does not identify the loaded package or frontend build.

SSE already exists in [dashboard server](../src/agentagon/dashboard/server.py), lines 457–487. It serializes `public_task(job)` at line 473. [public_task](../src/agentagon/workflows/runtime.py), lines 32–46, excludes a limited set of internal fields; the task-detail projection applies additional event filtering.

**Proposal**

- Expose protocol version, installed package version, build fingerprint when available, frontend build ID, and service start time.
- Detect mismatched loaded service/frontend versions and offer a clear restart action after active work is handled. Never restart uncertain work automatically.
- Publish small invalidation events or explicit public summaries over the existing stream. Fetch diagnostics on demand through the same sanitized projection rules.
- Preserve exact-origin/session protection and loopback binding.

A new HTTP framework, event bus, or full schema generator is not required to deliver these improvements.

## 16. Narrow architecture direction for V2

Retain this shape:

```text
HTTP / MCP
  → transport validation and local session protection
  → Application façade: coherent operations and bounded projections
  → existing Catalog / Monitoring / Memory / TaskRuntime
  → existing workflow handlers and bounded capabilities
  → revision-checked SQLite metadata and immutable evidence
```

[Application](../src/agentagon/workflows/service.py), lines 41–68, already composes these services. It is large and has unrelated responsibilities, so extract cohesive operation modules as a vertical journey touches them. Keep one authoritative readiness/start operation and one authoritative result/verification path.

Do not make the following prerequisites to fixing first value:

- replacing the HTTP server with FastAPI/Uvicorn;
- replacing all generic metadata records with a new relational schema;
- introducing a generalized event/outbox system;
- generating every UI label in backend projections;
- creating a public executable plugin system;
- replacing existing evaluation, optimization, provider, or memory engines.

Specialized indexes, transport schemas, durable notification queues, or adapter ports can be added when a concrete query, external delivery, or second integration requires them. [Workflow registry](../src/agentagon/workflows/registry.py), lines 7–23, already provides explicit built-in registration and handler loading. Preserve that release boundary.

## 17. Disposable manual-demo provenance

To inspect screens beyond the real project's unconfirmed-agent state, the audit prepared an isolated current-source service under `/private/tmp/agentagon-v2-manual-4_u1xh9k`. It registered a small non-Git synthetic project, one confirmed synthetic support agent, and one basic grounding goal through existing Application operations.

The demo did not use user project state or credentials, seed verified results, start workflows, call models/providers, install dependencies, or run tests. Telemetry and execution were disabled. The service used `http://127.0.0.1:61050` for the manual walkthrough and was stopped after the browser walkthrough completed. Its temporary evidence was preserved.

This fixture demonstrates page behavior with simple confirmed records. It does not establish a successful real assessment, repair, evaluation, optimization, deployment, or production-monitoring run.

## 18. Managed Fix selection is not evidence of human choice

**Source observations**

- [Fix instructions](../src/agentagon/workflows/fix/instructions.md), line 2, direct the managed coding backend to use the private `fix start/new/run/select` commands.
- [Fix completion verification](../src/agentagon/workflows/fix/handler.py), lines 47–56, requires `record.selected`, then validates the selected candidate and reference evidence. Without selection it does not report a completed repair.
- [Improvement retention](../src/agentagon/domain/improvements.py), lines 18–19, obtains the Fix candidate from that engine selection. The public improvement projection at lines 48–58 derives `selected` from the same run field.

Thus a managed Fix can already have an engine-selected candidate before a human visits its result. That state alone does not demonstrate user approval to deliver it.

**V2 decision**

Retain engine selection as internal preparation of a verified candidate. Add a durable, idempotent user-decision receipt bound to the run, candidate, tested source identity, evaluation identity, decision, and operation ID. Display `Recommended by task` separately from `Selected by you`.

For Fix, the shared user-choice operation validates the internally prepared candidate and records the explicit decision. For Optimize, it validates eligibility, performs any required engine selection, and records the same type of receipt. Reconcile uncertain retries of one operation. The receipt records human intent; it does not duplicate or override engine verification authority. `Keep current version` records no permission to deliver the candidate.

**Manual acceptance criterion:** a newly completed managed Fix is not labeled as a human-selected change; dashboard and MCP expose the same explicit decision, and delivery checks that decision against the exact eligible result.

## 19. Delivery needs a result-specific application adapter

**Source observations**

- [Application.deliver](../src/agentagon/workflows/service.py), lines 979–991, accepts only `kind="optimize"` or `kind="eval"` and a `source_id`. At line 1022 it maps these to `run_id` or `evaluation_id` respectively. Passing the visible workflow name `fix` as `kind` is currently rejected.
- The underlying [delivery capability](../src/agentagon/capabilities/experiments/delivery.py), lines 746–760, supports exactly one of `run_id`, `evaluation_id`, or `patch_id`. It validates a reviewed patch through the patch backend at lines 776–784. The application adapter does not currently expose that patch source.
- Publication validation in [Application.deliver](../src/agentagon/workflows/service.py), lines 995–1019, requires remote/base and a matching prepared delivery receipt. Preserve that separate publication boundary.

**V2 decision**

Use typed result-specific delivery inputs rather than sending a UI result label directly to the existing adapter:

| Result | Authoritative source | Required user decision |
|---|---|---|
| Measured Fix or Optimize | Run ID, eligible candidate, exact source/evaluation evidence | Explicit candidate-choice receipt from section 18 |
| Reviewed evaluation change | Frozen, independently reviewed evaluation ID | Explicit acceptance to deliver that evaluation change; no application-candidate selection |
| Reviewed unmeasured patch | Independently reviewed patch ID | Explicit acceptance of that patch and its limitations; no measured-improvement claim |

Extend the shared application adapter deliberately for reviewed patches, preserving the engine's review and exact-source checks. Reuse its existing capability rather than creating another packager. The UI renders only actions appropriate to the result kind. Local preparation, publication, merge, deployment declaration, and production verification remain distinct.

**Manual acceptance criterion:** each deliverable result reaches the correct engine source with its required decision; evaluation and unmeasured-patch delivery do not require a nonexistent measured-candidate selection, and workflow `fix` is not mistakenly passed as an unsupported current delivery kind.
