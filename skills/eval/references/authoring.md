# Shared goals, scoring and eval authoring

Init, Audit, Eval and Fix use this procedure. Read existing code, requirements, evals and permitted traces before proposing definitions. Save accepted intent with validated journey CLI operations; use the returned version and evidence references throughout preparation and execution. Never edit canonical `.agentagon/` records by hand.

The fields below define the saved evaluation, not a checklist for the user's reply. Derive a concrete recommendation from existing requirements and evals. Show the outcome it measures, material tradeoffs and total limits; ask only for unresolved intent or authorization. Keep implementation details in the saved definition and linked evidence unless needed for that decision. Do not silently replace task-quality measurement with narrower deterministic checks because they are easier to run.

## Agree on observable behavior

Propose a small set of behaviors relevant to the user's goal. For each behavior, record its natural-language intent, evidence, executable check or frozen judge rubric, and whether it is required. For example, “unknown tools produce a validation error and invoke no tool” maps to an assertion over the returned error and recorded calls. Use actual outputs, final state and relevant trajectory/tool evidence. Fewer steps or retries are useful only when the agreed criteria make them useful.

Resolve existing evals by inspecting their assertions, execution and coverage, not filenames. Reuse usable evals. If none exist or they are unusable, explain that finding and confirm the proposed creation and execution of missing evals. An explicit eval-creation request already authorizes it. Do not treat an inspection-only request as permission to create data or change application code.

Expected results must come from requirements, trusted labels, known examples or agreed rules. Observed trace outputs are evidence, not ground truth. Include ordinary successes, boundary cases and known failures. Preserve uncertainty and coverage gaps. The fixed Audit rubric provides discovery coverage and must never be summed into a universal quality grade.

## Choose one score and separate gates

Support the user's primary metric, agreed weighted score, or existing/custom scoring function. Record:

- Each metric's name, units, direction and aggregation.
- The missing-data policy and optional target.
- How behavior outcomes map to executable assertions or judge rubrics.
- The numeric score passed to optimization, always higher-is-better.
- Required behaviors and resource/permission limits as separate pass/fail gates.

A score cannot compensate for a failed gate. Preserve original metric values, units, behavior results and evidence alongside the optimization score. Missing measurements and execution failures remain explicit unknown/unmeasured/error states; never invent zero scores. For a custom scorer, keep the executable implementation and its tests in the repository, with a frozen source reference in the definition.

Use known correct variants to validate metric direction and meaningful separation. Validate judges on representative known examples. Schema validation alone does not establish relevance, sensitivity or judge accuracy. Read [the preparation contract](preparation.md) for negative controls and metric discrimination.

## Resolve the judge

Reuse the application's configured evaluator/provider and credential references. Prefer a configured API-backed judge when the behavior needs judgment. Freeze the rubric, prompt, model/provider configuration and aggregation with the evaluator.

If that judge is unavailable, propose a separate coding-host grading pass over actual saved trial outputs using the frozen rubric. Record judgments, explanations, rubric version, host/model provenance and exact trial/evaluator bindings through the grading operation; aggregate in code. Label resulting evidence **coding-agent judged** and explain that it may be slower and less consistent or accurate. Never replace runner validation with arbitrary host-written score JSON. An unavailable application cannot be rescued by a fallback judge.

For recent traces, reuse the same definition through a frozen repository `judge.trace_command` and `trace_input_path`, or through the declared coding-host rubric. A repository trace command reads one saved normalized trace at the unused input path and writes metric JSON to `AGENTAGON_RESULT_PATH`; its source belongs in the evaluator's protected paths. Agentagon runs it with the saved profile and credential references, verifies the retained runner result, and counts each trace evaluation against the existing budget. Do not infer metric mappings from similarly named telemetry fields or mark executable checks as passed without execution evidence. Keep recent-trace aggregates separate from fixed benchmark scores.

## Author, freeze and budget

Cases, assertions, judge prompts, scorers and execution logic belong in the user's repository. Private intent, accepted proposals, versions, execution references, inputs and evidence belong in ignored `.agentagon/` storage through CLI operations. Explicitly declare intended eval source delivery paths; do not move implementation into private inputs merely to hide it from review.

Follow [evaluation preparation](../../fix/references/evaluation.md), which preserves source isolation, sensitivity checks and independent review. Changing behaviors, data, scoring or judge configuration creates a new evaluator version. Application candidates cannot modify their frozen evaluator.

Propose one total time and evaluation-run budget and reuse the user's accepted configuration. Define a trial as one actual seeded/repeated evaluation execution; retries and final verification count. For Fix, begin with 20% preparation/baseline, 60% optimization and 20% final verification. Check that the baseline and minimum verification fit before starting. Unused preparation can flow to optimization; verification retains its reserve. Init/baseline-only work budgets only its applicable stages. Count host proposals, judging and review against applicable time/cost limits. Show measured monetary cost; do not invent a price for host subscription usage. Never expand limits automatically.

Discovery can complete with dirty or non-Git inputs. Measured execution requires clean committed source and an authorized profile. Preserve the assessment and explain missing prerequisites without committing, stashing or discarding unrelated work.

## Persist the accepted definition

Prepare an intent JSON using the versioned `journey-intent.json` and `score-definition.json` contracts returned by `agentagon resources`. Record `version`, `goal`, `accepted_by`, `scoring`, `discovery` and `budget`. Before measured work, include `host.name` and `host.model` using the current native host's actual identities, so later acquisition and review requests route to that host. Do not invent model names or use placeholder identities. Discovery includes `status` (`usable`, `missing` or `unusable`), explicit eval `paths`, supporting `evidence` and `creation_authorized`. Do not mark consent as granted without the user's actual instruction. Optional `acquisition` records the provider/project, lookback, filters, cap and existing authorization; credentials remain references in settings.

```sh
agentagon journey save --file INTENT_JSON
agentagon journey status INTENT_ID
```

Pass `--intent INTENT_ID` to `eval start` so preparation records the accepted discovery/creation authorization and uses the shared budget. Use the same `scoring` definition in the evaluation plan's `spec.scoring`. Its `source_paths` must be protected evaluator files. Required behaviors map to either a named check or a metric bound; a custom scorer emits the named `custom_metric` from frozen repository code. After freezing, save the accepted intent with `--evaluation EVALUATION_ID` to bind the matching score definition.

```sh
agentagon journey save --file INTENT_JSON --evaluation EVALUATION_ID
agentagon baseline start --evaluation EVALUATION_ID --intent INTENT_ID --profile NAME
agentagon baseline run BASELINE_ID
agentagon baseline status BASELINE_ID
```

Use returned IDs. Baseline status identifies pending execution, host grading or acquisition work; service those records using the shared [native-host procedure](../../fix/references/native-host.md), without inventing a replacement result. `baseline rerun BASELINE_ID --request-id REQUEST_ID` creates a new measurement of current committed code with the saved evaluator and settings. Replay the same request ID after an uncertain response.
