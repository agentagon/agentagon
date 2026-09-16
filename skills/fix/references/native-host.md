# Service native-host work

Use this procedure for pending optimizer proposals, coding-host grading, independent reviews and trace acquisition. Read the request's exact source/evaluator, role, allowed scope, host/model, payload and deadline before working. Do not switch hosts or invent an unavailable model identity.

The bridge owner is the returned `budget_id` when present, otherwise the returned execution run or evaluation ID. Use that identity as `OWNER_ID` below; it may differ from the displayed application run ID when preparation and baseline share one budget.

```sh
agentagon host pending OWNER_ID
agentagon host start OWNER_ID REQUEST_ID
```

Claim a pending request before performing work. Retain the exact request ID and binding digest. A running/replayed claim means reconcile its existing host task and reply; do not dispatch another copy. A cancelled request stays cancelled. Missing host capability leaves a clear pending action.

## Fulfill the requested role

- **GEPA reflection proposal (`payload.protocol == "gepa-reflection-v1"`):** send `payload.prompt` unchanged to a fresh dedicated native coding-agent session. Resume that exact session if interrupted; do not reuse the workflow author's or reviewer's conversation. GEPA has already assembled the goal, current component, feedback, and output instructions. Preserve the exact terminal assistant response in `{"text": "RAW FINAL RESPONSE"}`; do not extract candidates, strip fences, combine progress messages, or rewrite GEPA's instructions. The application-managed workflow handles this with `needs_reflection: {"owner_id": "...", "request_id": "..."}` and the current `run_id`. Unsupported multimodal prompts remain blocked explicitly. Keep this session read-only: the bounded executor materializes and measures the extracted candidate. Never supply claimed scores.
- **Other proposals:** inspect the objective and retained diagnostic feedback. The application optimizer supplies `payload.candidate` as JSON text containing a `files` map. Start from that map and make scoped candidate edits; preserve every protected evaluator and ordinary source file outside the proposal. Return an object whose `candidate` is the serialized JSON `{"files": {"repository/path": "complete UTF-8 contents"}}`; a null file value requests deletion. The bounded executor materializes and measures it in isolation. Never supply claimed scores.
- **Judging:** read the actual saved outputs and frozen rubric in the payload. Perform a separate grading pass. Return exactly `trial_id`, `rubric_version`, `metrics` (only the named finite values within frozen bounds), and a nonempty `explanation` grounded in those outputs. Do not replace runner checks, infer missing output or grade an application that did not execute. Preserve the **coding-agent judged** label.
- **Review:** dispatch an independent reviewer other than the candidate/eval author. Give it the exact sealed source, retained execution evidence, grading observations where present, and returned review template. It must assess the actual diff and evidence; fill the template without changing its binding fields or measured results. If independent review is unavailable, leave it pending.
- **Acquisition:** use the saved authorized provider/project, time window, filters and cap through available host tools. Save actual acquisition evidence and completeness; missing credentials, empty samples and partial retrieval remain explicit. Normalize a downloaded provider export with `baseline import-traces BASELINE_ID --export-path EXPORT_JSON --acquisition-file ACQUISITION_JSON`; saved settings supply the provider and project. Already normalized retained evidence can use `baseline traces BASELINE_ID --file RECEIPT_JSON`. Resolve `baseline-acquisition.json` and `acquisition.json` through `agentagon resources`. Do not turn an export request into permission to create eval cases.

Save the response in a workflow input file, then submit it with exact provenance:

```sh
agentagon host reply OWNER_ID REQUEST_ID --file RESPONSE_JSON --host ACTUAL_HOST --model ACTUAL_MODEL --binding-digest EXACT_DIGEST
```

Resume the operation that requested work: `fix optimize RUN_ID`, `baseline run BASELINE_ID`, `baseline score-traces BASELINE_ID` for recent-trace judgments, `fix run RUN_ID`, or `eval check EVALUATION_ID --plan-file PLAN_JSON`. A reply records host evidence; the caller validates and aggregates it. Late replies remain evidence but cannot establish an eligible result. Repeat the same reply after an uncertain response; completed replies cannot be replaced. Honor cancellation using `host cancel OWNER_ID REQUEST_ID --reason TEXT`.

## Start or continue Omni

After resolving accepted intent, a frozen scored evaluator and a clean source, start the measured run with `fix start --evaluation EVALUATION_ID --profile NAME`. Configure the current native host before any candidate work:

```sh
agentagon fix optimize RUN_ID --host ACTUAL_HOST --model ACTUAL_MODEL --intent INTENT_ID --engine omni --host-concurrency ACTUAL_CAPACITY --finalist-count 3
agentagon fix optimize-status RUN_ID
```

Optional engines are `gepa`, `autoresearch` and `meta_harness`. Meta-Harness overrides use `--meta-harness-host HOST --meta-harness-model MODEL`; judge configuration remains frozen separately. Honor the host's actual concurrency and the returned budget limits.

Service pending work using the role procedure above, then call `agentagon fix optimize RUN_ID` to continue. Continue until the bounded coordinator reports completion, exhausted budget or an actionable blocker. Do not leave serviceable requests pending merely because the CLI cannot perform host reasoning itself. Final selection comes from independently verified Agentagon evidence, never an upstream optimizer's claimed best score.
