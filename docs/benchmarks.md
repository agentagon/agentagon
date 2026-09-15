# Audit existing evals and benchmark readiness

Use **ag:audit** to assess an existing eval dataset, either broadly or for a particular issue. Audit checks coverage, label correctness, duplication, representativeness, provenance, leakage and whether the evaluator catches known failures.

```text
Audit the support agent's existing evals for duplicate-ticket failures.
Check missing cases, labels and whether the checks catch the issue.
Prepare a reproducible benchmark and measure a baseline if the saved
execution configuration and limits are ready.
```

The host resolves the selected agent to its code and evals. Supporting code provides context; the result retains the requested scope and uncertainty. Model outputs and pasted traces are evidence, not automatically correct labels.

## Draft and readiness

Audit saves a **benchmark draft** with content-pinned dataset and entrypoint references, the assessment, proposed cases and any supplied evaluation plan. This works in dirty or non-Git directories without an execution profile. A draft is not a validated frozen benchmark.

A runnable benchmark also needs trusted expectations, a harness, metrics, clean committed source, an execution profile and authorized limits. When ready, Audit continues through validation, baseline measurements, sensitivity checks and independent review. Otherwise it reports what is missing and completes the assessment.

Audit preserves existing dataset contents. When no dataset exists, it proposes cases. Ask **ag:fix** to create or repair the dataset and prepare delivery. Dataset changes become a new evaluator version; coverage and sensitivity comparisons use fixed application source. Scores from changed datasets do not establish application improvement.

## Direct commands

The host prepares the assessment using the [assessment procedure](../skills/audit/references/benchmarks.md) and versioned contract returned by `agentagon resources`.

```sh
agentagon benchmark draft --assessment ASSESSMENT_JSON --audit AUDIT_ID
agentagon benchmark status BENCHMARK_ID
agentagon benchmark prepare BENCHMARK_ID --profile local --budget BUDGET_JSON --author AUTHOR
```

`--audit` is optional. Repeating preparation with the same source, profile, budget and author resumes the linked evaluation without granting more trials. To explicitly start again with changed limits or settings, create a new benchmark draft using `benchmark draft --assessment ASSESSMENT_JSON --new`. A changed committed application revision produces a distinct draft automatically.

Preparation hands off to the existing evaluation lifecycle; all [validation and freeze requirements](eval.md) still apply. A saved baseline remains historical evidence when source or inputs change; status retains current blockers separately. An unavailable benchmark never becomes a passing result merely because its draft was saved.
