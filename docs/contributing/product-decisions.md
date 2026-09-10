# Product decisions

These decisions explain the workflow boundaries contributors should preserve. Commands and configuration belong in the [user guides](../README.md); the [implementation map](capabilities.md) links behavior to source and tests.

## Separate triggers define the evidence scope

Review and audit share an analysis engine but answer different questions. `ag:review` assesses local uncommitted changes; `ag:audit` investigates the current application broadly. Separate entry points let users choose the scope without having it expand silently. Related code can explain a change, but review findings must concern the captured changes.

Reviews default to code-only. Runtime evidence can inform a combined review when its relationship to the changes is stated. Full audits can use supplied traces with uncommitted code or without Git, retaining uncertainty about revision alignment. A missing finding in a narrow review cannot resolve an existing issue.

See [scope and goal](../audit.md#scope-and-goal) for capture rules and trace requirements.

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

Skills open it automatically through the [shared dashboard lifecycle](../../skills/dashboard/SKILL.md#automatic-workflow-start). The coding host owns the background process, browser tab and active selection, reusing them within a session. Direct CLI operations do not implicitly launch a browser. An unavailable dashboard does not block the evidence workflow.

Controls are opt-in and use the CLI's validated operations. Work requiring the coding agent remains queued until the active host acknowledges it. See [inspection](../audit.md#review-resume-and-inspect) and [controls](../reference/fix.md#dashboard-controls-and-delivery).

## Fixes preserve measurement and user choice

The host authors candidates and supplies independent reviews; the engine freezes execution inputs, records measurements and applies constraints. Freezing the benchmark before the baseline keeps comparisons consistent. Search policies and retained lessons guide exploration without replacing execution or review.

Keep every verified alternative that is not dominated across the declared objectives. The user chooses a final candidate because tradeoffs between objectives depend on their priorities. Failed and dominated experiments remain useful evidence.

Selection, publication, merging and deployment are separate actions. A selected branch is reviewable before publication, and changes introduced during delivery need fresh verification. See [measured fixes](../fix.md), [evaluation preparation](../eval.md) and the [shipping procedure](../../skills/ship/SKILL.md).
