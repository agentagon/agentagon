# Troubleshooting

Start by checking the application directory and saved state. Many apparent failures are an unfinished workflow, a different checkout, or a mismatch between the installed plugin and CLI.

```sh
agentagon --version
agentagon --workspace /path/to/application setup
agentagon --workspace /path/to/application status
```

Replace the path with your application. These commands inspect configuration and progress; they do not execute a new evaluation.

## The CLI is not found

Use the command path printed by the installer. For a default native installation:

```sh
export PATH="$HOME/.local/bin:$PATH"
agentagon --version
```

For a virtual-environment installation, reactivate that environment. If a terminal can find the CLI but a desktop host cannot, restart the host after fixing its PATH, or provide the absolute CLI path.

## The Agentagon skills are missing or outdated

Confirm you installed for the intended host and start a new session. Installing the Python package alone does not register the plugin. Editing source alone does not refresh an installed native plugin.

Rerun the appropriate [native installer](getting-started/install.md#2-install-for-your-coding-host) from the source version you intend to use. If the host lacks native plugin commands, update or configure the host first. Do not replace an unmanaged install destination; choose a separate one.

## The example says “No module named agentagon”

The Python running the example is not the one where Agentagon is installed. Use the active installation environment or the default installer runtime:

```sh
"$HOME/.local/share/agentagon/venv/bin/python" examples/local-audit/demo.py
```

Run from the Agentagon source checkout. For a custom prefix, use its `bin/python`. See [the local example](getting-started/local-example.md).

## The report has no issues, but says evidence is pending

The audit has not finished. A prepared packet and response template are inputs to a model review. They are not findings.

Ask the coding agent to resume the audit by its ID with the same goal and captured inputs. It must complete evidence review, diagnosis, and clustering. Do not mark an untouched template as approved or infer a clean application from an empty partial report.

## A review says there are no changes

`ag:review` inspects the net local difference from `HEAD`. If that diff is empty, the workflow stops. Use `ag:audit` to inspect current application code.

For a scoped review, check that your selected paths contain eligible changes. Excluded-only files do not establish a clean review. Resolve merge conflicts before capturing a changes review.

## Captured inputs changed or a submission is stale

The saved report must stay tied to the source and evidence it reviewed. If the captured files, diff baseline, goal, or selected trace inputs changed, start a new audit or review. Retain the old report as history.

For a frozen evaluation or fix, changed protected files require a new draft/run. Do not edit saved digests, packets, trial output, or review identities to force acceptance.

## An evaluation or fix requires a clean checkout

Evaluation and fix workflows need Git, an existing commit, and clean committed application source. Full audits do not have that requirement.

Review the unrelated edits with their owner. Keep private workflow files under ignored `.agentagon/`. If Git or the initial commit is missing, explicitly set up an appropriate baseline before resuming; `agentagon init` creates Agentagon state, not a Git repository.

## The evaluation cannot freeze

Open **ag:dashboard** and inspect the evaluation’s progress and validation evidence. Ask your coding agent to explain the failure that is preventing freezing.

Typical causes are missing expected behavior, a broken harness, negative controls that do not establish sensitivity, exhausted preparation limits, or missing independent review. Correct the actual problem in the preparation workflow. A frozen package must be revised through `eval start --from`, not edited in place.

## A candidate ran but is not verified

Execution success is only one requirement. Check for missing/nonfinite metrics, failed hard constraints, incomplete repetitions, protected-file edits, mismatched evidence, and missing or rejected independent review.

Use the candidate details and trial logs in the [dashboard](dashboard.md). A review cannot overwrite engine exit codes or supply missing measurements. Failed attempts remain visible intentionally.

## The run stopped or reached a limit

Choose **ag:dashboard** in Codex or run `/ag:dashboard` in Claude Code, and ask it to open the run. Read its completion state, pending work, and limits in the [dashboard](dashboard.md) first.

For an intentionally stopped run, explicit continuation is required:

```sh
agentagon fix run RUN_ID --continue
```

If limits are exhausted, choose a deliberate extension and provide `--limits-file LIMITS_JSON` with the new limits. Consumed counts and elapsed time remain recorded. See [continuation behavior](reference/fix.md#explore-review-and-select).

Completed repetitions are reused. A confirmed cancellation may require a replacement attempt; uncertain remote delivery is reconciled against the existing attempt. Unknown outcomes remain inconclusive. Do not delete trial records or locks to buy a new execution.

## A dashboard action stays queued

Directives, expansion, and continuation can require the active coding host to acknowledge and process them. The dashboard does not run a background model or host supervisor.

Resume the owning host session, inspect `status --run RUN_ID`, and let it consume its queued work. Acknowledging an expansion should reuse its candidate reservation instead of proposing another candidate.

## The dashboard will not open or shows the wrong work

Keep the server process running and open its exact printed URL. Check the `--workspace` directory and selected audit/run ID. Another checkout has separate state.

If loopback binding is blocked, allow local serving in your execution environment. To select a port explicitly, use `agentagon dashboard --port PORT --no-open`, replacing `PORT` with an available port.

Controls require the exact origin and current session. Restarting the server creates a new session; use its new URL rather than an old credential or copied control request.

## A control has a revision conflict

The run changed after you read it. Inspect current status before sending a new operation. Retry an uncertain operation with its identical request and operation ID. Reusing that ID with different content is rejected.

See [run controls](reference/fix.md#steer-a-run-without-losing-queued-work) for the request format and states.

## Traces cannot be imported or matched to code

Check the supported [export formats](../skills/audit/references/formats.md), provider/project, timezone-aware dates, and count. Inspect import diagnostics instead of treating skipped data as reviewed.

A dirty checkout, missing Git revision, or contradictory trace metadata limits alignment. Supply better-matched evidence if needed; otherwise retain the limitation. See [Connect execution traces](traces.md).

## Intelligence is unavailable

Verify that both a service origin and the referenced key are available to the CLI process. The key itself should never be pasted into chat. Invalid endpoint syntax is a configuration error; missing access, service failures and timeouts do not prevent the owning audit, evaluation preparation or fix run.

Ask your coding agent to check the Intelligence configuration and explain any lookup failure. See [Agentagon Intelligence](intelligence.md), or continue the workflow locally without it.

## Shipping rejects the selected branch or base

The delivery no longer matches its verified evidence, or the destination is ambiguous. Inspect the selected source and destination. If source or base changed, obtain fresh matching verification. Do not overwrite unrelated remote branches or bypass the selection check.

After an interrupted publication, retry the same run so the delivery receipt can reconcile an existing branch or PR. [Delivery](delivery.md) explains the boundary between preparation and publication.

## Ask for help

For a reproducible product problem, open an issue in the [Agentagon repository](https://github.com/agentagon/agentagon/issues). Include:

- Agentagon and host versions, OS, and installation method.
- The command or skill request and the expected versus observed behavior.
- A minimal synthetic example and a redacted error message.
- Whether the workflow was new, resumed, or using changed source.

Do not upload `.agentagon/`, credentials, private source, or customer traces. Use the [security reporting process](../SECURITY.md) for vulnerabilities or sensitive reports.
