# CLI reference

Use the coding-host skills for agent-led workflows. Use the CLI to inspect state, configure settings, or execute a specific workflow operation. The CLI validates records and runs declared commands; it does not generate model reasoning or independent review by itself.

## Command structure

```sh
agentagon --workspace /path/to/application COMMAND
```

`--workspace` is a global option and goes **before** the subcommand. It defaults to the current directory. Uppercase IDs and file paths in these docs are placeholders; replace them with actual values returned by your workflow.

Discover options from your installed version:

```sh
agentagon --help
agentagon audit --help
agentagon eval --help
agentagon fix --help
agentagon fix run --help
```

Most commands return JSON. Workflow output supplies IDs, paths, pending actions, and templates for subsequent operations. Preserve those identities when resuming. An error is not a valid result file.

## Inspect and configure

| Command | Purpose |
|---|---|
| `agentagon --version` | Show the installed CLI version. |
| `agentagon init` | Create private local Agentagon state; does not initialize Git. |
| `agentagon setup` | Inspect effective settings and onboarding state. |
| `agentagon setup --scope user --set KEY VALUE` | Save a user default. |
| `agentagon setup --scope project --set KEY VALUE` | Save a checkout override. |
| `agentagon setup --scope project --unset KEY` | Remove an override. |
| `agentagon setup --scope project --profile NAME --profile-file FILE` | Save a complete named profile. |
| `agentagon status` | List saved audits and fix-run summaries. |
| `agentagon resources` | Locate installed skills, catalog, and versioned contracts. |

See [Settings and profiles](../settings.md) for supported keys and precedence.

## Review and audit

| Command | Purpose |
|---|---|
| `agentagon audit changes` | Inspect the net local diff before starting a changes review. |
| `agentagon audit start --code-scope changes --mode code` | Start a code-only local changes review. |
| `agentagon audit start --code-scope full --mode code` | Start a current-code audit. |
| `agentagon audit prepare AUDIT_ID --stage evidence` | Prepare a bounded packet and response template. |
| `agentagon audit submit AUDIT_ID RESPONSE_JSON` | Validate and save the host’s actual response. |
| `agentagon audit prepare AUDIT_ID --stage diagnosis` | Prepare diagnosis after evidence review. |
| `agentagon audit prepare AUDIT_ID --stage clustering` | Prepare grouping after diagnosis. |
| `agentagon audit report AUDIT_ID` | Write current Markdown and JSON reports. |
| `agentagon audit lookup AUDIT_ID [--context-file FILE] [--focus-file FILE] [--phase initial\|follow_up] [--limit N] [--refresh]` | Request optional privacy-safe audit guidance from `/v1/audit`; one or both files are required. |
| `agentagon status --audit AUDIT_ID` | Inspect pending audit work. |
| `agentagon audit issues list` | Read persistent issue history. |

A single packet may not cover the full audit. Continue each stage until its eligible work is complete. Untouched templates leave the audit pending. For trace selection/import and exact submission fields, see [commands and submissions](../../skills/audit/references/records.md) and [trace acquisition](../../skills/audit/references/acquisition.md).

## Evaluation preparation

| Command | Purpose |
|---|---|
| `agentagon eval start --goal TEXT --profile NAME --budget-file FILE --author AUTHOR` | Create an isolated preparation draft. |
| `agentagon eval check EVALUATION_ID --plan-file FILE` | Execute declared baseline and sensitivity checks. |
| `agentagon eval lookup EVALUATION_ID [--context-file FILE] [--goal-file FILE] [--phase initial\|follow_up] [--limit N] [--refresh]` | Request optional privacy-safe evaluation guidance from `/v1/eval`; either file may be omitted, but one is required. |
| `agentagon eval freeze EVALUATION_ID --review-file FILE` | Freeze an eligible independently reviewed package. |
| `agentagon eval status EVALUATION_ID` | Inspect preparation progress. |
| `agentagon eval start --from EVALUATION_ID --profile NAME --budget-file FILE --author AUTHOR` | Create a new draft from a frozen evaluation. |

See the [evaluation walkthrough](../eval.md) or [preparation reference](evaluation.md). Budget, plan, and review files must satisfy their returned/versioned contracts; they are not arbitrary JSON.

## Measured fixes

| Command | Purpose |
|---|---|
| `agentagon fix start --evaluation EVALUATION_ID --profile NAME` | Start from a frozen evaluation. |
| `agentagon fix start --spec SPEC_JSON --profile NAME` | Start from an explicit measurement specification. |
| `agentagon fix lookup RUN_ID [--context-file FILE] --focus-file FILE [--phase initial\|follow_up] [--limit N] [--refresh]` | Request optional privacy-safe fix guidance from `/v1/fix`; `--focus-file` is required. |
| `agentagon fix run RUN_ID` | Execute or resume baseline/run work. |
| `agentagon fix new RUN_ID --hypothesis TEXT --author AUTHOR` | Reserve an isolated candidate. |
| `agentagon fix run RUN_ID CANDIDATE_ID` | Seal and execute that candidate. |
| `agentagon fix run RUN_ID CANDIDATE_ID --review-file FILE` | Submit its actual independent review and continue. |
| `agentagon status --run RUN_ID --candidate CANDIDATE_ID` | Inspect candidate state. |
| `agentagon fix stop RUN_ID` | Stop the run and cancel owned execution. |
| `agentagon fix run RUN_ID --continue` | Explicitly continue stopped work. |
| `agentagon fix steer RUN_ID --control-file FILE` | Submit a revision-checked control operation. |
| `agentagon fix select RUN_ID CANDIDATE_ID` | Select a verified candidate into a reviewable branch. |
| `agentagon fix ship RUN_ID` | Prepare local delivery of the selection. |
| `agentagon fix ship RUN_ID --publish` | Publish the selected branch and a draft PR when authorized. |

See [Measured fixes](../fix.md) for the walkthrough and [Fix configuration and controls](fix.md) for limits, seeds, parent selection, review, and continuation. [Delivery](../delivery.md) explains cleanup and publication. Advanced coordination uses [host orchestration](../orchestration.md).

## Dashboard

```sh
agentagon dashboard
agentagon dashboard AUDIT_ID --no-open
agentagon dashboard --run RUN_ID --no-open
agentagon dashboard --run RUN_ID --controls --no-open
```

Select an audit or a run, not both. The default dashboard is read-only. `--controls` explicitly enables authenticated fix controls. The server stays running until stopped. See [Explore the dashboard](../dashboard.md).

## Environment variables

| Variable | Purpose |
|---|---|
| `AGENTAGON_CONFIG` | Explicit user/project settings file. |
| `XDG_CONFIG_HOME` | Base directory for default user settings. |
| `AGENTAGON_TELEMETRY_DISABLED=1` | Disable telemetry in the current process and children. |
| Credential variable names chosen in settings/profile | Supply secrets through references rather than JSON values. |
| `AGENTAGON_RESULT_PATH` | Runner-provided destination for a benchmark’s metrics JSON. |
| `AGENTAGON_SEED` | Runner-provided seed for the current repetition. |
| `AGENTAGON_EVENTS_PATH` | Runner-provided task-event JSONL destination. |
| `AGENTAGON_ARTIFACTS_DIR` | Runner-provided retained-artifact directory. |

The last four are supplied to benchmark execution. Do not invent result paths or trial identities when submitting evidence. See [Task events and artifacts](../task-evidence.md).
