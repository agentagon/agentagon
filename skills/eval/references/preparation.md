# Evaluation preparation contract

`eval start` takes a named execution profile and a separate budget JSON:

```json
{"max_trials": 18, "max_elapsed_seconds": 1800, "trial_timeout_seconds": 60}
```

Elapsed time is measured from draft creation. Trial slots are reserved durably before dispatch; reconnecting cannot spend the same slot again. A baseline plus two negative cases with three seeds needs nine trials. Failed cases consume slots. The CLI does not extend a draft's budget automatically.

The plan passed to `eval check --plan-file` contains:

- `spec`: the existing fix specification: goal/issues, editable application paths, protected evaluation paths, benchmark command, metric directions/units, checks and their expected baseline outcome, constraints, seeds and repetitions. Keep the draft's goal and saved issue IDs. Every target issue requires an acceptance check.
- `provenance`: nonempty explanation of where data and expected behavior came from, including permissions and uncertainty. If expected behavior is unknown, ask the user instead of fabricating an oracle.
- `coverage`: `status` (`holdout` or `limited`), `rationale`, and `holdout_paths`. A holdout requires separate, frozen file paths. A limited fixture has an empty holdout array and explains what the benchmark cannot establish. The independent reviewer checks whether separation is meaningful and whether the harness actually uses it.
- `negative_cases`: nonempty array with unique `id`, `description` of deliberately incorrect behavior, `mutations` (each with a checkout-local `source` and application-relative `path`), and nonempty `expected_checks` containing check IDs. Mutation files belong in ignored state, outside the preparation worktree. They may alter permitted application files, never tests or frozen data. A negative case must run its benchmark successfully and make each named check exit 1. Other checks may exit 0 or 1; infrastructure failures do not count.
- `metric_cases`, `metric_comparisons`: optional paired, nonempty arrays for checking that objectives distinguish known correct behaviors; see [metric discrimination](#metric-discrimination).
- `deliver_paths`: optional paths naming intentional regression tests. It defaults to an empty array. All evaluation files are available during execution; only these tests accompany the fix branch. Inputs are always private and cannot be marked deliverable.

`spec.overlays` and `spec.inputs` can import regular checkout-local files into the evaluation. Their source may be in ignored `.agentagon/` storage; their destination is relative to the evaluation source tree. Inputs are excluded from the benchmark review branch. To keep generated helpers private, list them as inputs; ordinary instrumentation can be an evaluation file reviewed on the benchmark branch while omitted from fix delivery.

Preparation may add or replace evaluation files, but cannot delete files or modify application source. The frozen package is an overlay and cannot represent deletions. Use explicit evaluation paths; protecting `.` is not allowed during preparation.

The check result includes a `review_template`. Preserve `evaluation_id`, `validation_id`, `validation_digest`, and every evidence reference. Supply a distinct nonempty `reviewer`, `verdict: "pass"`, a substantive `rationale`, and `true` for every named assessment only after independent inspection. Changed benchmarks or evidence invalidate that review.

Preparation runs all checks and the benchmark even when a [preflight check](../../fix/references/contract.md#preflight-checks) fails. Negative controls need full observations to prove sensitivity. Setup failures, cancellation and deadlines still stop execution.

## Metric discrimination

Use known correct variants to check whether a declared objective rewards the intended improvement. These supplement the required negative controls. A constant score may reject nothing, and a reversed metric may favor worse behavior; a numeric result alone does not establish either relevance or sensitivity.

Each `metric_cases` entry has `id`, `description` and `mutations`, using the same path and source protections as negative cases. IDs must be unique across both case lists and cannot be `baseline`. Every metric case must execute the benchmark and pass **all** correctness checks with exit 0 on every repetition, including checks expected to fail on the original baseline.

Each `metric_comparisons` entry names a declared objective `metric`, distinct `better` and `worse` case IDs, and optional nonnegative `min_delta` in that metric's units (default 0). References may name metric cases or `baseline`, never negative cases. A baseline expecting any failed check cannot be used in a comparison; provide two correct variants instead.

For example, add these fields to a plan whose `latency` objective has direction `min` and whose editable scope includes `app.json`:

```json
{
  "metric_cases": [
    {
      "id": "faster-correct",
      "description": "Known correct implementation with less repeated work",
      "mutations": [{"source": ".agentagon/faster.json", "path": "app.json"}]
    }
  ],
  "metric_comparisons": [
    {"metric": "latency", "better": "faster-correct", "worse": "baseline", "min_delta": 5}
  ]
}
```

Comparisons use medians over the declared seeds, matching fix verification. The better case must strictly improve the objective in its declared direction and meet `min_delta`; a tie fails even when `min_delta` is zero. Choose a separation large enough for the workload's observed variation. This check does not establish statistical significance or generalization.

Every added case consumes one trial per seed under the same preparation budget. A baseline, one negative case and two metric cases with three seeds need twelve trials. Retried or resumed validation reuses existing reservations. Changed case contents, comparison expectations or evaluation files create new validation evidence under the remaining budget.

Results retain `metric_comparisons` with observed values, improvement and pass status, or a `metric_comparison_error` when the required correct observations are unavailable. A failed comparison prevents freezing. The independent reviewer checks the claimed ranking and its provenance; freeze recomputes comparisons from retained observations. Metric cases stay in private preparation evidence and are not exported as application changes.

`eval freeze` exports `.agentagon/evaluations/EVALUATION_ID/fix-spec.json` and a local `codex/eval/EVALUATION_ID` review branch. `fix start --evaluation` requires the exact original committed application revision. Use `eval start --from` and validate again when source or evaluation changes. Old packages remain readable and are never rewritten.
