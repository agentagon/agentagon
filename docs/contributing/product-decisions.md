# Product decisions

These decisions explain the workflow boundaries contributors should preserve. Commands and configuration belong in the [user guides](../README.md); the [implementation map](capabilities.md) links behavior to source and tests.

<span id="separate-triggers-define-the-evidence-scope"></span>

## Dashboard and MCP share one local runtime

The product has dashboard, brain, memory, workflows and capabilities. Dashboard HTTP and stdio MCP call the same application operations. A detached local service owns tasks and launches managed Codex or Claude sessions. Application-agent discovery is separate from coding-backend detection. The React application remains the dashboard; the old viewer, public skills, native registration and host lifecycle hooks are deleted.

A workflow starts from a goal, issue, trace or description with an operation ID, optional agent, scope and limits. Identical retries return the same task. Closing an interface does not cancel tasks. Service interruption retains identities and evidence but requires explicit resume. Questions are shared between interfaces. Publication, merge and deployment remain separate actions.

The service is loopback-only. Hosted access, organizations, automatic bulk repair, and remote coding-agent connections are deferred. Explicitly enabled local monitors schedule bounded observation tasks while the service is available. Provider connections remain scoped to registered projects; reads do not acquire evidence or execute work.

## Application agents preserve focused intent

An application agent has a stable identity within a project and versioned code scopes, shared dependencies and trace selectors. An explicit Detect agents action performs a bounded deterministic scan and makes valid detected bindings usable immediately; it does not require a coding model or individual acceptance. Detection records automatic adoption separately from user review, preserves exclusions and user-edited fields, and never turns proposed dependencies into approved edit scope. Trace-only identities support read-only work until code is bound. The bounded scan is not an exhaustive inventory. Codex and Claude are coding backends, distinct from these application agents.

Onboarding detects local agents in one action. Optional Assess project work uses the shared task runtime to inspect local code and explicitly selected trace evidence, enrich responsibilities, and recommend evidence-backed actions. Discovery during an assessment also activates valid bindings. Structured coding review can enrich detected details while preserving user edits; it cannot silently exclude an already active identity. Exclusion and restoration are explicit user actions, and restoration makes an identity usable immediately. Trace matches remain advisory identity evidence. Provider or authentication failures retain useful partial results. See [production monitoring](../production.md) for assessment and monitoring defaults.

Versioned goals record the user's objective, source evidence and accepted measurements. An investigation freezes the selected agent binding, goal and evidence scope. Goal changes emphasis without narrowing the required audit rubric or resolving unrelated historical issues. Trace-only diagnosis cannot silently expand into a full-code audit or authorize source edits.

A goal has one Go action. It authorizes a bounded background run to design useful measurements, accept the exact generated version, prepare an independently reviewed evaluator, measure a baseline, and search for verified improvements. Optional details refine the work. The coordinator reuses compatible evidence and asks for missing inputs; users do not advance internal workflow stages. Accepted versions pin scoring, required behaviors, evidence analysis and native evaluation choices. Users can still inspect or edit measurement drafts through advanced controls. The run records the Go authorization, child operations, exact accepted revisions, aggregate allowance and retained results. Reads never dispatch work. Restarted runs require explicit resume so uncertain execution is reconciled. Routine local execution defaults are resolved only after Go; unavailable credentials, source prerequisites and ambiguous expectations remain actionable blockers. A goal does not require production traces: unavailable evidence or instrumentation remains a visible prerequisite. Reusing a frozen evaluator cannot change its scoring or substitute different inputs.

Accepted metric definitions, references and guardrails remain attached to their goal versions. A new focus cannot turn a missing measurement into a pass or erase prior accepted checks. Optimize binds the required suite across active goals and agents affected by permitted changes, then verifies each reference/finalist pair with every required frozen evaluator. The requested finalist count is one to ten, defaults to three and excludes the baseline; budget admission reserves the corresponding verification work. Selection and delivery require complete evidence for that exact candidate. Failed guards disqualify a candidate; incomplete evidence cannot establish improvement.

## Workflows preserve explicit evidence scope

Discover issues normalizes explicitly selected traces, extracts signals, asks the managed brain for diagnoses, and retains grouped issues with evidence and uncertainty. It does not repair them. Several issues can share a trace; an issue can have many occurrences. Repeated analysis does not duplicate occurrences. Issues are independent records, not reconstructed audit views.

Fix accepts an issue, trace or description without a saved goal or full audit. It establishes expected behavior, prepares a regression check, reproduces failure, authors a focused repair and verifies it independently. Ambiguous ownership or expectations require input. Missing reproduction or verification remains a limitation. Direct Fix uses bounded retries; Optimize retains bounded search, frozen comparisons, failed/dominated experiments and verified alternatives.

Full Audit retains the entire rubric and explicit code/trace scope. Changes-only audits restrict findings to captured changes and default to code-only. Full audits can inspect dirty or non-Git directories. Measurement requires clean committed inputs, trustworthy expectations, sensitivity checks, authorized execution and independent review. An absent finding does not resolve another issue.

Untracked files and folders do not block measured work. Execution checkouts use the committed application revision; untracked source content stays in the original folder and is not automatically copied into those checkouts. Staged or unstaged changes to tracked files still require the user to commit or stash them. Explicitly declared evaluation inputs retain their frozen-input validation.

Evaluation preparation and baseline measurement are composable built-ins. Evaluator edits create versions; changes to data or scoring are not like-for-like application improvement. Candidate code cannot alter frozen evaluation definitions.

## Goals change emphasis

A goal directs investigation depth and presentation while preserving the required rubric and evidence standards. This lets a latency-focused audit still surface a severe permissions defect. Changes reviews retain their narrower evidence scope regardless of the goal.

Save the goal with the audit so resumed work preserves its intent. Optional Intelligence receives separately prepared, redacted workflow fields; the stored audit, evaluation or fix goal is never uploaded automatically. See [guidance preparation](../intelligence.md).

## Audit facets require context and evidence

The [signal catalog](../../signals/audit-v1.json) defines the authoritative facet boundaries and counterexamples. Goal, requirements and outcome describe evidence; fulfillment assesses task completion. LLM facets assess a span's responsibility, while user sentiment is contextual information. Code facets guide inspection rather than produce numeric scores. These facets overlap deliberately for diagnosis and must not be summed into a universal quality grade or counted as independent failures.

The distinction between a claimed result and verified state follows [Anthropic's agent evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) and the final-state evaluation used by [τ-bench](https://arxiv.org/abs/2406.12045). Context-specific requirements and representative validation also follow [NIST's validity and reliability guidance](https://airc.nist.gov/airmf-resources/airmf/3-sec-characteristics/). These sources support the design principles; they do not validate Agentagon's particular labels or judge accuracy.

CLI validation checks record structure, required coverage, allowed values and evidence references. The host must still establish whether cited evidence supports a judgment. Negative values, partial fulfillment and mixed feedback require diagnosis; they do not establish agent fault. The [analysis procedure](../../src/agentagon/workflows/audit/references/analysis.md) defines that handoff and the improvement categories.

Before using these judgments as performance metrics, calibrate them on representative traces labeled by human reviewers, including incomplete evidence, intermediate turns, scope changes, valid refusals and recovered failures. Measure disagreements, false alarms and missed defects per facet, retaining unknown and not-applicable coverage separately. Recheck after changing the rubric or judge. Passing CLI tests demonstrates contract behavior, not empirical validity of model judgments.

<span id="one-config-file-two-settings-scopes"></span>

## Configuration separates preferences from evidence

Shared user defaults reduce repeated setup; checkout overrides keep project-specific choices separate. Distinct worktrees have distinct project entries. Explicit invocation choices take precedence over saved configuration.

SQLite in the local service-state directory is authoritative for app metadata: project registrations, application agents and bindings, goals, project connections, coding-agent settings, tasks, events and approvals. Engine records, immutable imported evidence, artifacts and Git work areas remain in the checkout's ignored `.agentagon/` directory. Generated task context files are read projections, not independent mutable task state. Backups need both database and project artifacts.

Use short transactions for metadata; keep model calls, provider requests, Git operations and measurements outside them. Task acceptance binds the raw submission before derived defaults, so retries return the same task even when surrounding settings change. Persist native session and request identities; uncertain external execution must be reconciled rather than duplicated. A database transaction does not make external effects exactly once.

No legacy app-state import, compatibility writer or migration layer is required. Project connections are explicit and are not synthesized from CLI trace settings. The app still uses user/project configuration precedence for execution settings. Provider credentials automatically prefer a supported OS store, with session memory as the fallback; retain only references in app metadata. CLI and execution credentials remain environment references. Changing future defaults does not rewrite a run's frozen settings. See [configuration](../settings.md) and [app state](../app.md).

## Provider imports are immutable inputs

Braintrust, LangSmith and Langfuse connections read explicitly selected traces and datasets. Saving a connection does not authorize background collection, instrumentation or provider write-back. Creating an enabled monitor explicitly authorizes its saved bounded acquisition and observation policy. Retain selection bounds, provider/version identity, acquisition provenance and completeness; partial pagination or missing trace children remain visible limits.

Imported datasets start as drafts. Preserve structured inputs, conversation ordering and reference provenance; observed production outputs do not become ground truth. Missing expectations need an accepted correctness rule. Refresh creates a new snapshot, and changing benchmark data requires a new evaluator version. Private imports remain outside delivered source and cannot bypass execution, sensitivity or independent-review gates. See [the import workflow](../app.md).

Trace-derived drafts and grouped development/final splits retain provenance and related-case boundaries. Development inputs can be materialized for evaluator preparation. Final use requires explicit handling and a separately reviewed evaluator; a split alone is not a sealed holdout or evidence of independent generalization.

## Dashboard reads stay within the selected checkout

The React dashboard displays one selected project's evidence at a time. Reads never start work or reconnect to runners. Explicit task controls require the exact local origin and session token. MCP uses that same authenticated service. There is no separate dashboard control or lifecycle implementation.

## Memory groups keep knowledge and evidence distinct

Named local groups can use separate folders and explicit project/agent read and write bindings. Improvement lessons and target-agent memory share versioned entry storage with evidence references and uncertainty. Workflows recall lessons and record unsuccessful as well as successful outcomes. Target agents use explicit MCP recall/record operations. Automatic instrumentation, procedural extraction and external stores are deferred.

Evaluations pin the target-agent memory snapshot as immutable input. Concurrent writes cannot change a comparison. Memory is advisory; raw evidence and validated engine records remain authoritative. Verified issue resolution identifies the tested revision and never implies production recovery.

## Intelligence requests require explicit consent

Intelligence defaults to `ask`, including existing configured installations. The host shows the exact redacted request and destination, obtains consent, then submits the single-use approval ID. The shared client binds consent to the owner generation and request and consumes it before one HTTP attempt. Changed requests and retries need new consent; cached local receipts do not send data.

Only an explicit Agentagon `full_access` setting skips approval prompts. It never inherits coding-host permissions and still shows each call, preserves privacy restrictions and retains receipts. Declining does not block local work.

## Optimize preserves measurement and user choice

The host authors candidates and supplies independent reviews; the engine freezes execution inputs, records measurements and applies constraints. Freezing the benchmark before the baseline keeps comparisons consistent. Search policies and retained lessons guide exploration without replacing execution or review.

The accepted quality score ranks candidates while required behaviors and limits remain separate pass/fail gates. Select the highest-scoring independently verified candidate that establishes improvement over baseline and satisfies every gate; preserve user choice among the configured number of verified alternatives. Retain the baseline if improvement is not established. Failed and dominated experiments remain useful evidence.

Selection, publication, merging and deployment are separate actions. A selected branch is reviewable before publication, and changes introduced during delivery need fresh verification. See [measured fixes](../fix.md), [evaluation preparation](../eval.md) and the [shipping procedure](../../src/agentagon/workflows/optimize/references/delivery.md).

An unmeasured application patch requires an explicit user request. Reviewed patches without a trusted baseline use a separate record and independent review of exact source, available checks and limitations. They never enter the measured frontier or establish verified issue resolution. Known failures cannot be bypassed by relabeling the result. Local delivery accepts measured fixes, reviewed eval changes and reviewed unmeasured patches without a remote; publication validates the destination separately.

## Definitions and measurements have separate identities

Save accepted user intent, behavior mappings, score direction/aggregation/missing-data policy, optional target and budget as versioned private records. Keep executable cases, assertions, judge prompts, scorers and harness code in the repository. A changed behavior, dataset, scoring rule or judge configuration creates a new evaluator version. Audit discovery facets remain separate from quality scoring.

A baseline references the evaluator and a particular clean committed application revision. A compatible later commit or another branch can use the same immutable evaluator. Reruns create new measurement identities; completed-run resume does not substitute for execution. Preserve score components, execution settings and evidence status.

Recent traces form a separate population from fixed benchmark cases. Refresh only through saved authorized provider scope and retain time window, sample cap, completeness and deployment alignment. Missing or insufficient trace evidence is unknown, and changing trace scores cannot establish a controlled code improvement.

## Optimizer proposals do not authorize execution

Omni uses Agentagon's [optimizer runtime](../../src/agentagon/capabilities/experiments/runtime.py) to compose GEPA search with native-host AutoResearch and Meta-Harness adapters. Host/model overrides affect authoring, independently of judge settings. One durable request/reply bridge binds proposals, grading and reviews to role, source, evaluator and scope; unavailable host work stays pending. The finite coordinator resumes recorded work without silently switching hosts.

GEPA uses its upstream default proposer through a callable `reflection_lm`; Agentagon supplies no custom candidate proposer. Forward the fully assembled upstream text prompt exactly, then return the raw final text for upstream extraction. A dedicated native session serves each logical reflection request and resumes only that saved identity. Keep raw terminal artifacts separate from sanitized browser progress. Persist completed responses for replay and reject unsupported prompt types rather than flattening them. Candidate validation, execution budgets and independent reviews remain Agentagon's responsibility. AutoResearch and Meta-Harness retain their own proposal contracts.

Accepted evidence analysis is frozen before baseline execution and supplied as upstream optimization background. Completed, consented Intelligence receipts may extend this background before configuration freezes; that operation performs no lookup. GEPA batch sampling uses its upstream proposer and ordered prompt transport. Independent candidate work runs in isolated worktrees; short admission and state-update locks must not serialize whole evaluations.

Agentagon owns attempt history, trial admission, metric validity, gates, independent review and selection. Starting Optimize allocations are 20% preparation/baseline, 60% optimization and 20% final verification. Check minimum feasibility first, move unused preparation capacity to optimization and protect verification capacity. Every actual execution, retry and final trial counts; host work counts against applicable time/cost limits. Never invent subscription prices or expand the overall limit automatically.

The starting Omni schedule spends three quarters of optimization capacity on exploration, evenly divided across the three engines, then one quarter on fresh GEPA refinement. Respect actual host concurrency. Advanced standalone engines remain additional options.

## Delivery preserves the evaluator/application relationship

Deliver reviewed eval source as an eval-only draft PR. Deliver a verified application winner as a draft PR when the destination and authentication are configured, otherwise as a local branch/patch. An application child of an open eval PR must match the exact eval-parent source and preserve its evaluator files; setting a PR base alone cannot establish ancestry. Merge the eval PR first. Merging and deployment remain separate actions.

Readable and JSON report exports include accepted goals, scoring, behavior outcomes, evidence summaries and source/evaluator identities, excluding raw traces, private inputs and credentials. Detailed local evidence remains separate. One saved invitation after the first successful journey may ask the user to star the project; never star automatically or repeat it for every run.

## Breaking state contract

Fresh state is required. Prior private data is left untouched; incompatible versions are rejected. No compatibility wrappers or state migrations are shipped.

## Recursive improvement and production evidence

Verified candidates have durable improvement records referencing engine evidence. User-declared deployments retain their actual release, revision, environment and provenance. Observations compare versioned measurements over explicit release cohorts; partial coverage, missing labels and ambiguous versions cannot establish recovery. Frozen production references remain retained after deployment. Operational state stays in SQLite and immutable evidence, while memory records advisory lessons and references.

The local scheduler owns saved observation windows and idempotent task submissions. Interrupted tasks require explicit resume or discard before that monitor advances. Collection and diagnosis respect provider, execution, daily and storage bounds. Monitoring recommends; repair, candidate selection, publication and deployment remain separate user actions.
