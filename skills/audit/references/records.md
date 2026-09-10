# Commands and submissions

Prefix these subcommands with `agentagon --workspace CHECKOUT`. Uppercase arguments are placeholders; quote substituted values as data, never shell expressions.

```sh
audit changes
audit start --code-scope full --mode traces --source braintrust --project PROJECT --from START --to END --limit COUNT --host codex --model MODEL
audit import AUDIT_ID EXPORT_DIRECTORY --acquisition RECEIPT_JSON
audit prepare AUDIT_ID --stage evidence
audit submit AUDIT_ID RESPONSE_JSON
audit prepare AUDIT_ID --stage diagnosis
audit prepare AUDIT_ID --stage clustering
audit report AUDIT_ID
```

For a current-code audit: `audit start --code-scope full --mode code --scope src --scope tests`. The default code scope is `full`, including for existing CLI callers; it accepts source directories without Git and checkouts with uncommitted changes or no commits. Supplied traces with local changes or no Git revision carry an explicit warning that code may differ and analysis may be incomplete or incorrect. Contradictory revision metadata is also flagged. Changes to captured files after audit start require a new audit.

For a local changes review: `audit start --code-scope changes --mode code`. Changes scope defaults to code-only even when traces are enabled in saved settings. Explicit `--mode combined` uses trace arguments; `--mode traces` is incompatible with changes scope. `--scope PATH` is a repeatable path filter in either scope; for changes it narrows the captured diff, including deleted paths.

`audit changes` is read-only and works before `init`; it reports the baseline revision, paths, change types and eligible/skipped counts. It accepts the same path filters. Local changes are the net difference from `HEAD` across staged, unstaged and non-ignored untracked files, using an empty baseline before the first commit. Changes that cancel out are absent. An empty diff does not start an audit; exclusions are coverage limits, not evidence of a clean review. Conflicts must be resolved before capture. Branch comparisons and staged-only review are not supported.

For combined mode, supply trace arguments and optional code scopes. Add `--goal TEXT` when supplied. Count is a positive integer or `all`; dates require a timezone.

## Packets

`prepare` returns packet and response-template paths. Read both; edit only the response. Preserve contract/rubric versions, audit ID, packet ID/digest, and stage. Record the actual host/model when known. Submit each listed unit once. `--batch-size` adjusts the default five units; `packet: null` means no eligible pending work for that stage.

Read the submission schema returned by `agentagon resources` for finding, flag-disposition, and group fields absent from the template. Evidence IDs come from the packet index. A reviewed healthy trace has `findings: []` and still accounts for every flag.

`--max-bytes` bounds packet size (default 200,000 bytes). When `requires_file_read` is true, read the referenced content in sections: packet `details_path` holds complete units, evidence index, any issue catalog, and displaced goal, scope emphasis or trace-alignment details; units without inline content use `path` or code `content_path`. Never judge omitted content from metadata. The response template still lists every required judgment.

## Submissions

The CLI assigns finding IDs. Choose `basis: implementation_only` for code evidence, `runtime_only` for runtime evidence, or `correlated` for both with a `correlation_rationale`. Severity is `critical`, `high`, `medium`, or `low`; unsupported hypotheses stay null.

Changes-review findings require registered change evidence and a rationale tying the finding to changed behavior; runtime-only findings cannot satisfy this scope. Use `improvement: true` for opportunities and `kind: evaluation_coverage` for eval recommendations, with the concrete target/scenario/assertion in `hypothesis`. Structural validation cannot prove the reasoning is correct.

Clustering uses empty `items` and populated `groups`. Cover every packet finding once. Use unique group keys; target a previously created issue through `issue_id`, otherwise null.

Identical submissions are idempotent. Complete partial judgments through another submission; completed reviews/diagnoses are immutable. Changed captured inputs or altered packets are rejected; a changed baseline or selected change set requires a new review. Resume through `status --audit AUDIT_ID`. Reports retain workflow, scope, baseline, before/after evidence locations and coverage, and historical records remain readable with their saved scope semantics.
