# Evaluation preparation reference

The coding host normally prepares these inputs and runs the CLI. Start with [Prepare an evaluation](../eval.md) for a guided workflow. For direct CLI use, run commands in the application checkout with actual IDs and file paths in place of uppercase placeholders.

## Revise or reuse an evaluation

A frozen package is immutable. Ask your coding agent to create a new draft from it. Replace `EVALUATION_ID` and adapt this example to the changes you want:

=== "Codex"

    Select **ag:eval**, then send:

    ```text
    Create a new evaluation draft from EVALUATION_ID. Add coverage for
    tool timeouts and empty search results, using the configured local profile.
    Agree the preparation limits with me, validate the revised benchmark,
    and obtain an independent review before freezing it.
    Preserve the original evaluation and its results.
    ```

=== "Claude Code"

    ```text
    /ag:eval Create a new evaluation draft from EVALUATION_ID. Add coverage
    for tool timeouts and empty search results, using the configured local
    profile. Agree the preparation limits with me, validate the revised
    benchmark, and obtain an independent review before freezing it.
    Preserve the original evaluation and its results.
    ```

To reuse the benchmark with different application source, ask the agent to validate it against that source in a new draft. Preserve the original package for the comparisons that used it.

??? details "Create a draft from the CLI"

    ```sh
    agentagon eval start --from EVALUATION_ID --profile local --budget-file BUDGET_JSON --author AUTHOR
    ```

    Supply a real author identity and a preparation budget file. The coding agent normally prepares these inputs for you.

## Direct CLI reference

The coding host normally prepares these files and runs these operations. For direct use, replace uppercase placeholders with actual IDs and files:

```sh
agentagon eval start --goal 'Check tool routing' --profile local --budget-file BUDGET_JSON --author AUTHOR
agentagon eval start --audit AUDIT_ID --issue ISSUE_ID --profile local --budget-file BUDGET_JSON --author AUTHOR
agentagon eval lookup EVALUATION_ID --context-file CONTEXT_FILE --goal-file GOAL_FILE --phase initial --limit 5
agentagon eval check EVALUATION_ID --plan-file PLAN_JSON
agentagon eval freeze EVALUATION_ID --review-file REVIEW_JSON
agentagon fix start --evaluation EVALUATION_ID --profile local
```

A preparation budget file has this shape:

```json
{
  "max_trials": 12,
  "max_elapsed_seconds": 1800,
  "trial_timeout_seconds": 60
}
```

Use the [plan and review contracts](../../skills/eval/references/preparation.md) and returned templates for the other files. Do not fill a review with claimed results that were not observed. See [task evidence helpers](../task-evidence.md) for benchmark diagnostics.

A passing benchmark establishes evidence for its declared cases. It does not erase coverage limitations or prove production improvement.
