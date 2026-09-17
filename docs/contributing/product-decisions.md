# Product decisions

These decisions explain the workflow boundaries contributors should preserve. Commands and configuration belong in the [user guides](../README.md); the [implementation map](capabilities.md) links behavior to source and tests.

<span id="separate-triggers-define-the-evidence-scope"></span>

## The local web app is the primary interface

Bare `agentagon` opens a local web app and registers the current project, reusing a running service when available. `agentagon app` is an explicit alias. Named subcommands and optional skills remain available. The project opens to an application-agent inventory; Goals leads to an accepted measurement plan, Eval, a baseline and Fix within the selected agent and focus. Metrics, Traces and settings support that journey. Audit is a separate CLI/skill workflow; existing audits and findings remain readable evidence. The project selector does not combine evidence across checkouts.

The local backend owns provider connections scoped to one registered project and explicitly started coding-agent sessions. Opening project pages does not launch agents or acquire trace or dataset records; explicit project discovery and import dialogs may query provider catalogs. Codex uses an app-owned app-server session and exposes the installed backend's available models for selection. Claude uses the optional Agent SDK with API-key access and a separate model setting. Neither adapter takes over an unrelated terminal conversation. Workflow procedures are bundled runtime inputs and do not require skill installation.

Closing the browser leaves tasks running in the service. Service shutdown or lost ownership leaves work interrupted; restarting requires explicit resumption using saved identities. Permission requests wait for an explicit answer. A task's saved settings, evidence and total budget survive resumption; restarting must not silently enlarge its allowance. Publication, merge and deployment remain separate actions.

The supported app is local and loopback-only. Hosted access, organizations, tenant isolation and remote coding-agent connections are deferred; the local service must not be exposed as a hosted backend.

## Application agents preserve focused intent

An application agent has a stable identity within a project and versioned code scopes, shared dependencies and trace selectors. Discovery proposes evidence-backed bindings for confirmation; its bounded scan is not an exhaustive inventory. Codex and Claude are coding backends, distinct from these application agents.

The first browser discovery records project-specific source choices. Local code scanning always runs. A saved opt-in may ask the authenticated default coding agent for a read-only review of the bounded local suggestions; its structured response can rename or exclude suggestions only after deterministic path validation and cannot confirm an agent. A separate saved opt-in may read at most 100 recent root-trace metadata records from one tested connection assigned to the project. That lookup covers the last seven days and excludes trace inputs, outputs and child spans. Trace matches are advisory identity evidence, not evidence of behavioral correctness. Enrichment failure preserves the local scan and reports its limitation.

Versioned focuses record the user's objective, source evidence and accepted measurements. An investigation freezes the selected agent binding, focus and evidence scope. Focus changes emphasis without narrowing the required audit rubric or resolving unrelated historical issues. Trace-only diagnosis cannot silently expand into a full-code audit or authorize source edits.

Measurement proposals are editable drafts until explicit acceptance. Each accepted version pins scoring, required behaviors, evidence analysis and native evaluation choices. A goal does not require production traces: unavailable evidence or instrumentation remains a visible prerequisite. Reusing a frozen evaluator cannot change its scoring or substitute different inputs.

Accepted metric definitions, references and guardrails remain attached to their focus versions. A new focus cannot turn a missing measurement into a pass or erase prior accepted checks. A focused Fix binds the required suite across active focuses and agents affected by permitted changes, then verifies each reference/finalist pair with every required frozen evaluator. The requested finalist count is one to ten, defaults to three and excludes the baseline; budget admission reserves the corresponding verification work. Selection and delivery require complete evidence for that exact candidate. Failed guards disqualify a candidate; incomplete evidence cannot establish improvement.

## Workflow entry points preserve explicit evidence scope

`ag:init` onboards an application, agrees on behaviors/scoring/limits, prepares reusable evals and establishes a baseline. `ag:fix` improves the saved goal or named issue through measurement, independent verification and draft delivery. `ag:dashboard` inspects history, explicitly reruns baselines and manages existing settings. Audit and Eval remain independently discoverable deep dives. Setup, review and delivery are shared supporting procedures.

Init starts with focused discovery sufficient to recommend a useful measurement; a full audit is not a prerequisite for that recommendation. Its conversation leads with the developer outcome, material limits and next decision, while detailed definitions and evidence remain in saved records. Requested full audits retain their complete rubric and evidence requirements. Deterministic checks must not be presented as evidence of task or answer quality beyond what they actually measure. See the [Init procedure](../../skills/init/SKILL.md).

Audit defaults to the current application broadly. A named agent resolves to explicit paths. Changes-only scope includes the captured uncommitted diff; related code can explain a change but cannot expand the finding scope. A missing finding in a narrow audit cannot resolve an existing issue.

Changes-only audits default to code without traces. Full audits accept dirty and non-Git directories and retain uncertainty about trace revision alignment. Existing-eval assessments save content-pinned drafts independently of execution readiness. Running and freezing a benchmark still requires clean committed source, trustworthy expectations, authorized limits, sensitivity checks and independent review. Missing execution prerequisites do not block the assessment.

Init, Audit, Eval and Fix share one eval-authoring procedure. Reuse suitable existing evals; missing or unusable eval creation/running requires confirmation unless explicitly requested already. Dataset creation or repair creates a new evaluator version. Compare coverage, labels and sensitivity at fixed application source; aggregate scores on changed datasets are not a like-for-like application improvement. Application candidates still cannot change their frozen evaluator.

See [scope and goal](../audit.md#scope-and-goal) and [benchmark readiness](../benchmarks.md).

## Goals change emphasis

A goal directs investigation depth and presentation while preserving the required rubric and evidence standards. This lets a latency-focused audit still surface a severe permissions defect. Changes reviews retain their narrower evidence scope regardless of the goal.

Save the goal with the audit so resumed work preserves its intent. Optional Intelligence receives separately prepared, redacted workflow fields; the stored audit, evaluation or fix goal is never uploaded automatically. See [guidance preparation](../intelligence.md#use-during-a-workflow).

## Audit facets require context and evidence

The [signal catalog](../../signals/audit-v1.json) defines the authoritative facet boundaries and counterexamples. Goal, requirements and outcome describe evidence; fulfillment assesses task completion. LLM facets assess a span's responsibility, while user sentiment is contextual information. Code facets guide inspection rather than produce numeric scores. These facets overlap deliberately for diagnosis and must not be summed into a universal quality grade or counted as independent failures.

The distinction between a claimed result and verified state follows [Anthropic's agent evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) and the final-state evaluation used by [τ-bench](https://arxiv.org/abs/2406.12045). Context-specific requirements and representative validation also follow [NIST's validity and reliability guidance](https://airc.nist.gov/airmf-resources/airmf/3-sec-characteristics/). These sources support the design principles; they do not validate Agentagon's particular labels or judge accuracy.

CLI validation checks record structure, required coverage, allowed values and evidence references. The host must still establish whether cited evidence supports a judgment. Negative values, partial fulfillment and mixed feedback require diagnosis; they do not establish agent fault. The [analysis procedure](../../skills/audit/references/analysis.md) defines that handoff and the improvement categories.

Before using these judgments as performance metrics, calibrate them on representative traces labeled by human reviewers, including incomplete evidence, intermediate turns, scope changes, valid refusals and recovered failures. Measure disagreements, false alarms and missed defects per facet, retaining unknown and not-applicable coverage separately. Recheck after changing the rubric or judge. Passing CLI tests demonstrates contract behavior, not empirical validity of model judgments.

<span id="one-config-file-two-settings-scopes"></span>

## Configuration separates preferences from evidence

Shared user defaults reduce repeated setup; checkout overrides keep project-specific choices separate. Distinct worktrees have distinct project entries. Explicit invocation choices take precedence over saved configuration.

SQLite in the sibling app-state directory is authoritative for app metadata: project registrations, application agents and bindings, focuses, project connections, coding-agent settings, jobs, events and approvals. Engine records, immutable imported evidence, artifacts and Git work areas remain in the checkout's ignored `.agentagon/` directory. Generated task context files are read projections, not independent mutable job state. Backups need both database and project artifacts.

Use short transactions for metadata; keep model calls, provider requests, Git operations and measurements outside them. Job acceptance binds the raw submission before derived defaults, so retries return the same task even when surrounding settings change. Persist native session and request identities; uncertain external execution must be reconciled rather than duplicated. A database transaction does not make external effects exactly once.

No legacy app-state import, compatibility writer or migration layer is required. Project connections are explicit and are not synthesized from CLI trace settings. The app still uses user/project configuration precedence for execution settings. Provider credentials automatically prefer a supported OS store, with session memory as the fallback; retain only references in app metadata. CLI and execution credentials remain environment references. Changing future defaults does not rewrite a run's frozen settings. See [configuration](../settings.md) and [app state](../app.md#keep-or-resume-work).

## Provider imports are immutable inputs

Braintrust, LangSmith and Langfuse connections read explicitly selected traces and datasets. Saving a connection does not authorize background collection, instrumentation or provider write-back. Retain selection bounds, provider/version identity, acquisition provenance and completeness; partial pagination or missing trace children remain visible limits.

Imported datasets start as drafts. Preserve structured inputs, conversation ordering and reference provenance; observed production outputs do not become ground truth. Missing expectations need an accepted correctness rule. Refresh creates a new snapshot, and changing benchmark data requires a new evaluator version. Private imports remain outside delivered source and cannot bypass execution, sensitivity or independent-review gates. See [the import workflow](../app.md#connect-traces-and-datasets).

Trace-derived drafts and grouped development/final splits retain provenance and related-case boundaries. Development inputs can be materialized for evaluator preparation. Final use requires explicit handling and a separately reviewed evaluator; a split alone is not a sealed holdout or evidence of independent generalization.

## Dashboard covers one checkout

The legacy `agentagon dashboard` viewer reads the same saved records as CLI status and reports. Its checkout scope makes the relationship between source, findings and experiments explicit. Opening it shows existing progress without starting an audit or model service. The multi-project web app routes each selected project's reads to those same checkout records.

The optional skills open the viewer through the [shared dashboard lifecycle](../../skills/dashboard/references/lifecycle.md). In that path, the coding host owns its background process, browser tab and active selection. Named workflow subcommands and standalone Setup do not implicitly launch a browser; bare `agentagon` and `agentagon app` explicitly launch the primary app. An unavailable viewer does not block the evidence workflow.

Legacy viewer controls remain opt-in and use the CLI's validated operations. Its host work remains queued until the active coding host acknowledges it. The primary app has its own explicit task actions and exact-origin/session validation. See [inspection](../audit.md#review-resume-and-inspect) and [legacy controls](../reference/fix.md#dashboard-controls-and-delivery).

## Intelligence requests require explicit consent

Intelligence defaults to `ask`, including existing configured installations. The host shows the exact redacted request and destination, obtains consent, then submits the single-use approval ID. The shared client binds consent to the owner generation and request and consumes it before one HTTP attempt. Changed requests and retries need new consent; cached local receipts do not send data.

Only an explicit Agentagon `full_access` setting skips approval prompts. It never inherits coding-host permissions and still shows each call, preserves privacy restrictions and retains receipts. Declining does not block local work.

## Fixes preserve measurement and user choice

The host authors candidates and supplies independent reviews; the engine freezes execution inputs, records measurements and applies constraints. Freezing the benchmark before the baseline keeps comparisons consistent. Search policies and retained lessons guide exploration without replacing execution or review.

The accepted quality score ranks candidates while required behaviors and limits remain separate pass/fail gates. Select the highest-scoring independently verified candidate that establishes improvement over baseline and satisfies every gate; preserve user choice among the configured number of verified alternatives. Retain the baseline if improvement is not established. Failed and dominated experiments remain useful evidence.

Selection, publication, merging and deployment are separate actions. A selected branch is reviewable before publication, and changes introduced during delivery need fresh verification. See [measured fixes](../fix.md), [evaluation preparation](../eval.md) and the [shipping procedure](../../skills/fix/references/delivery.md).

An unmeasured application patch requires an explicit user request. Reviewed patches without a trusted baseline use a separate record and independent review of exact source, available checks and limitations. They never enter the measured frontier or establish verified issue resolution. Known failures cannot be bypassed by relabeling the result. Local delivery accepts measured fixes, reviewed eval changes and reviewed unmeasured patches without a remote; publication validates the destination separately.

## Definitions and measurements have separate identities

Save accepted user intent, behavior mappings, score direction/aggregation/missing-data policy, optional target and budget as versioned private records. Keep executable cases, assertions, judge prompts, scorers and harness code in the repository. A changed behavior, dataset, scoring rule or judge configuration creates a new evaluator version. Audit discovery facets remain separate from quality scoring.

A baseline references the evaluator and a particular clean committed application revision. A compatible later commit or another branch can use the same immutable evaluator. Reruns create new measurement identities; completed-run resume does not substitute for execution. Preserve score components, execution settings and evidence status.

Recent traces form a separate population from fixed benchmark cases. Refresh only through saved authorized provider scope and retain time window, sample cap, completeness and deployment alignment. Missing or insufficient trace evidence is unknown, and changing trace scores cannot establish a controlled code improvement.

## Optimizer proposals do not authorize execution

Omni uses Agentagon's [optimizer runtime](../../src/agentagon/experiments/runtime.py) to compose GEPA search with native-host AutoResearch and Meta-Harness adapters. Host/model overrides affect authoring, independently of judge settings. One durable request/reply bridge binds proposals, grading and reviews to role, source, evaluator and scope; unavailable host work stays pending. The finite coordinator resumes recorded work without silently switching hosts.

GEPA uses its upstream default proposer through a callable `reflection_lm`; Agentagon supplies no custom candidate proposer. Forward the fully assembled upstream text prompt exactly, then return the raw final text for upstream extraction. A dedicated native session serves each logical reflection request and resumes only that saved identity. Keep raw terminal artifacts separate from sanitized browser progress. Persist completed responses for replay and reject unsupported prompt types rather than flattening them. Candidate validation, execution budgets and independent reviews remain Agentagon's responsibility. AutoResearch and Meta-Harness retain their own proposal contracts.

Accepted evidence analysis is frozen before baseline execution and supplied as upstream optimization background. Completed, consented Intelligence receipts may extend this background before configuration freezes; that operation performs no lookup. GEPA batch sampling uses its upstream proposer and ordered prompt transport. Independent candidate work runs in isolated worktrees; short admission and state-update locks must not serialize whole evaluations.

Agentagon owns attempt history, trial admission, metric validity, gates, independent review and selection. Starting Fix allocations are 20% preparation/baseline, 60% optimization and 20% final verification. Check minimum feasibility first, move unused preparation capacity to optimization and protect verification capacity. Every actual execution, retry and final trial counts; host work counts against applicable time/cost limits. Never invent subscription prices or expand the overall limit automatically.

The starting Omni schedule spends three quarters of optimization capacity on exploration, evenly divided across the three engines, then one quarter on fresh GEPA refinement. Respect actual host concurrency. Advanced standalone engines remain additional options.

## Delivery preserves the evaluator/application relationship

Deliver reviewed eval source as an eval-only draft PR. Deliver a verified application winner as a draft PR when the destination and authentication are configured, otherwise as a local branch/patch. An application child of an open eval PR must match the exact eval-parent source and preserve its evaluator files; setting a PR base alone cannot establish ancestry. Merge the eval PR first. Merging and deployment remain separate actions.

Readable and JSON report exports include accepted goals, scoring, behavior outcomes, evidence summaries and source/evaluator identities, excluding raw traces, private inputs and credentials. Detailed local evidence remains separate. One saved invitation after the first successful journey may ask the user to star the project; never star automatically or repeat it for every run.
