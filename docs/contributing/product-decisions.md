# Product decisions

These decisions explain the workflow boundaries contributors should preserve. Commands and configuration belong in the [user guides](../README.md); the [implementation map](capabilities.md) links behavior to source and tests.

<span id="separate-triggers-define-the-evidence-scope"></span>

## Three primary journeys preserve explicit evidence scope

`ag:init` onboards an application, agrees on behaviors/scoring/limits, prepares reusable evals and establishes a baseline. `ag:fix` improves the saved goal or named issue through measurement, independent verification and draft delivery. `ag:dashboard` inspects history, explicitly reruns baselines and manages existing settings. Audit and Eval remain independently discoverable deep dives. Setup, review and delivery are shared supporting procedures.

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

## One config file, two settings scopes

Shared user defaults reduce repeated setup; checkout overrides keep project-specific choices separate. Distinct worktrees have distinct project entries. Explicit invocation choices take precedence over saved configuration.

Keep credentials as references and runtime evidence in the checkout's ignored `.agentagon/` directory. Separating settings from run records lets users change future defaults while existing work retains its saved evidence and execution settings. See [configuration](../audit.md#first-audit-and-setup).

## Dashboard covers one checkout

The dashboard reads the same saved records as CLI status and reports. Its checkout scope makes the relationship between source, findings and experiments explicit. Opening it shows existing progress without starting an audit or model service.

Init, Audit, Eval and Fix open it automatically through the [shared dashboard lifecycle](../../skills/dashboard/references/lifecycle.md). The coding host owns the background process, browser tab and active selection, reusing them within a session. Direct CLI operations and standalone Setup do not implicitly launch a browser. An unavailable dashboard does not block the evidence workflow.

Controls are opt-in and use the CLI's validated operations. Work requiring the coding agent remains queued until the active host acknowledges it. See [inspection](../audit.md#review-resume-and-inspect) and [controls](../reference/fix.md#dashboard-controls-and-delivery).

## Intelligence requests require explicit consent

Intelligence defaults to `ask`, including existing configured installations. The host shows the exact redacted request and destination, obtains consent, then submits the single-use approval ID. The shared client binds consent to the owner generation and request and consumes it before one HTTP attempt. Changed requests and retries need new consent; cached local receipts do not send data.

Only an explicit Agentagon `full_access` setting skips approval prompts. It never inherits coding-host permissions and still shows each call, preserves privacy restrictions and retains receipts. Declining does not block local work.

## Fixes preserve measurement and user choice

The host authors candidates and supplies independent reviews; the engine freezes execution inputs, records measurements and applies constraints. Freezing the benchmark before the baseline keeps comparisons consistent. Search policies and retained lessons guide exploration without replacing execution or review.

The accepted quality score ranks candidates while required behaviors and limits remain separate pass/fail gates. Select the highest-scoring independently verified candidate that establishes improvement over baseline and satisfies every gate; preserve user choice among verified alternatives. Report the winner and next two qualifying alternatives when available. Retain the baseline if improvement is not established. Failed and dominated experiments remain useful evidence. Historical policy-driven runs retain their original records and explicit selection behavior.

Selection, publication, merging and deployment are separate actions. A selected branch is reviewable before publication, and changes introduced during delivery need fresh verification. See [measured fixes](../fix.md), [evaluation preparation](../eval.md) and the [shipping procedure](../../skills/fix/references/delivery.md).

An unmeasured application patch requires an explicit user request. Reviewed patches without a trusted baseline use a separate record and independent review of exact source, available checks and limitations. They never enter the measured frontier or establish verified issue resolution. Known failures cannot be bypassed by relabeling the result. Local delivery accepts measured fixes, reviewed eval changes and reviewed unmeasured patches without a remote; publication validates the destination separately.

## Definitions and measurements have separate identities

Save accepted user intent, behavior mappings, score direction/aggregation/missing-data policy, optional target and budget as versioned private records. Keep executable cases, assertions, judge prompts, scorers and harness code in the repository. A changed behavior, dataset, scoring rule or judge configuration creates a new evaluator version. Audit discovery facets remain separate from quality scoring.

A baseline references the evaluator and a particular clean committed application revision. A compatible later commit or another branch can use the same immutable evaluator. Reruns create new measurement identities; completed-run resume does not substitute for execution. Preserve score components, execution settings and evidence status.

Recent traces form a separate population from fixed benchmark cases. Refresh only through saved authorized provider scope and retain time window, sample cap, completeness and deployment alignment. Missing or insufficient trace evidence is unknown, and changing trace scores cannot establish a controlled code improvement.

## Optimizer proposals do not authorize execution

Omni uses real upstream GEPA composition with Agentagon native-host AutoResearch and Meta-Harness adapters. Host/model overrides affect authoring, independently of judge settings. One durable request/reply bridge binds proposals, grading and reviews to role, source, evaluator and scope; unavailable host work stays pending. The finite coordinator resumes recorded work without silently switching hosts.

Agentagon owns attempt history, trial admission, metric validity, gates, independent review and selection. Starting Fix allocations are 20% preparation/baseline, 60% optimization and 20% final verification. Check minimum feasibility first, move unused preparation capacity to optimization and protect verification capacity. Every actual execution, retry and final trial counts; host work counts against applicable time/cost limits. Never invent subscription prices or expand the overall limit automatically.

The starting Omni schedule spends three quarters of optimization capacity on exploration, evenly divided across the three engines, then one quarter on fresh GEPA refinement. Respect actual host concurrency. Advanced standalone engines remain available; old search policies remain compatibility features rather than primary journey choices.

## Delivery preserves the evaluator/application relationship

Deliver reviewed eval source as an eval-only draft PR. Deliver a verified application winner as a draft PR when the destination and authentication are configured, otherwise as a local branch/patch. An application child of an open eval PR must match the exact eval-parent source and preserve its evaluator files; setting a PR base alone cannot establish ancestry. Merge the eval PR first. Merging and deployment remain separate actions.

Readable and JSON report exports include accepted goals, scoring, behavior outcomes, evidence summaries and source/evaluator identities, excluding raw traces, private inputs and credentials. Detailed local evidence remains separate. One saved invitation after the first successful journey may ask the user to star the project; never star automatically or repeat it for every run.
