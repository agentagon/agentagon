# Baselines and reruns

A baseline is an immutable measurement of one application revision using a frozen evaluator. It references existing evaluation and execution records; it does not replace their evidence lifecycle.

Each baseline retains branch and exact commit, evaluator version, settings, time, score components and evidence status. Compatible later commits and other branches can use the same evaluator after compatibility checks. Changing cases, behaviors, scoring or judge configuration creates a new evaluator version.

## Rerun current code

Use **ag:dashboard** to inspect history and explicitly rerun a baseline with the saved settings. A rerun measures current committed code, creates a new identity and retains earlier measurements. Resuming a completed execution does not create a fresh measurement.

Direct CLI operations are:

```sh
agentagon baseline start --evaluation EVALUATION_ID --intent INTENT_ID --profile local
agentagon baseline run BASELINE_ID
agentagon baseline status BASELINE_ID
agentagon baseline rerun BASELINE_ID --request-id UNIQUE_REQUEST_ID
```

Use IDs returned by the commands. Reuse the same request ID after an uncertain rerun request to avoid starting duplicate work. The new baseline may have pending execution, acquisition or host grading work; inspect its status and next action before claiming completion.

## Refresh recent traces

Saved authorized acquisition settings specify the provider, lookback duration, filters and sample cap. On rerun the window ends at the rerun time. A connected provider refreshes within that scope; acquisition requiring host tools remains pending until the host services it. If no provider is connected, supply a fresh export when useful.

Missing, partial, empty and failed acquisition remain visible. Retain provider, time window, completeness and deployment/revision alignment. Score eligible traces using the saved behavior definition; refreshing traces does not create new eval cases or reopen the goal discussion.

Import a provider export through the existing normalization and redaction pipeline:

```sh
agentagon baseline import-traces BASELINE_ID --export-path traces.json --source braintrust --project PROJECT --acquisition-file acquisition.json
```

The acquisition file follows the existing [acquisition contract](../contracts/v1/acquisition.json). Omit it when completeness is unknown; unsupported or incomplete evidence remains unmeasured. Saved provider settings supply `--source` and `--project` when present. Repeating the same import reuses its receipt. A different export requires a new baseline. Advanced integrations may attach retained normalized artifacts using `baseline traces BASELINE_ID --file RECEIPT.json` and the [baseline receipt contract](../contracts/v1/baseline-acquisition.json).

## Compare the right populations

| Result | What it can establish |
|---|---|
| Fixed benchmark | A controlled comparison when evaluator, settings and relevant execution conditions match. |
| Recent traces | How the observed trace sample meets the saved behavior definitions. |

Recent traces may come from another deployment or revision. A changing trace population cannot establish that a code change caused an improvement. Insufficient trace evidence remains unknown/unmeasured. Inspect both views together while preserving that distinction.

After attaching normalized trace evidence, collect the separate score with:

```sh
agentagon baseline score-traces BASELINE_ID
```

Trace scoring accepts retained normalized trace records using the existing [trace contract](../contracts/v1/trace.json). Complete traces with actual outputs inside the saved acquisition window are eligible; incomplete or unsupported records remain unmeasured.

An existing repository judge can declare `scoring.judge.trace_command` (`argv` and optional `cwd`) plus `trace_input_path`. The command reads one normalized trace from that unused path and writes named metrics to `AGENTAGON_RESULT_PATH`, using the saved profile and credential references. Include its source in the frozen evaluator. Each trace runs in an isolated snapshot with the original evaluator files and counts as one evaluation in the same budget. Failed execution or insufficient budget leaves the aggregate unmeasured instead of reporting only successful trials.

When the frozen judge uses the coding host and has no repository trace command, service the bound grading requests over retained outputs and trajectories, then repeat the command to aggregate. These results are labeled **coding-agent judged**. Metrics and executable check gates without a supported trace mapping remain unknown; an API provider setting alone does not define a trace scoring command.
