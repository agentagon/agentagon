# Fix inputs and execution contract

All commands use `agentagon --workspace ORIGIN_CHECKOUT`. Canonical run, snapshot, trial and report records stay in that checkout's ignored `.agentagon/`, even when a candidate worktree or remote runner executes checks.

Use `agentagon resources` to locate installed JSON contracts. Its `contracts` directory contains `fix-profile.json`, `fix-spec.json`, `fix-review.json` and `fix-control.json`; do not assume repository-relative schema paths exist in a native plugin installation.

## Named profiles

Save a complete profile using `setup --scope user|project --profile NAME --profile-file PROFILE_JSON`. Names start with a lowercase letter and contain lowercase letters, digits, `_` or `-` (up to 64 characters). A project profile replaces the whole same-named user profile. This cannot be combined with `--set` or `--unset`.

Profile fields:

- `runner`: `kind` is `local`, `ssh` or `e2b`. SSH requires `host` and `remote_root`, and optionally `python`; E2B requires `template`, with optional `api_key_env` (default `E2B_API_KEY`) and `sandbox_timeout_seconds`. Boolean `independent_capacity` must be true before concurrent local/SSH trials; assert it only when the execution capacity is actually independent.
- `env`: mapping of target environment-variable names to local source environment-variable names. Store references, never secret values. Only forward data/credentials authorized for the selected runner.
- `setup`: argument arrays for preparation commands executed in the trial environment.
- `limits`: explicit `max_candidates`, `max_trials`, `max_elapsed_seconds`, `parallel_candidates`, `parallel_trials`, and `trial_timeout_seconds`. Optional `stagnation_rounds` defaults to three completed rounds without a change to the nondominated metric vectors. An equal-valued patch keeps its candidate history and frontier membership but does not reset stagnation. Rounds are assessed in proposal order, using only their own and earlier candidates, regardless of execution completion order. Ask for missing limits at first use and save the user's choices.
- `search`: saved parent-selection policy; see [search settings](#search-settings). Policies affect future proposals, not verification gates or the true Pareto frontier.
- `scans`: optional explicit `max_scans`, `max_input_bytes` and `scan_timeout_seconds`. All are positive integers; input bytes must be 1024–1048576. Omit this object to leave host scans disabled. Each prepared scan consumes a slot, including failures; its deadline is bounded by both scan timeout and run elapsed time.

`local` and SSH worktrees isolate changes, not the entire machine or network. SSH requires an already configured destination. E2B requires the optional `agentagon[e2b]` dependency and configured credentials. Do not provision infrastructure or install dependencies without existing task authorization.

An explicit E2B `sandbox_timeout_seconds` must leave at least 30 seconds after `trial_timeout_seconds` for evidence collection; the runner otherwise allows 120 seconds of collection time. Bundled `fix-profile.json` and `fix-spec.json` schemas describe structural inputs; the CLI also checks cross-field limits, issue links and seed counts.

`max_candidates` counts proposals after the baseline. `max_trials` includes baseline trials, cancelled attempts and replacement attempts. Completed repetitions survive cancellation; an explicit continuation can retry only the unfinished repetition. Unknown remote outcomes must reconcile the same attempt. Extending limits never resets consumed counts or elapsed time.

Private input sources can live under the origin's `.agentagon/`. Choose an execution destination such as `inputs/traces.json`; destinations under `.agentagon/` are reserved and never transferred. Inputs remain outside deliverable branches.

A profile may set `repetitions` to override the default three. An explicit run specification takes precedence. The resolved count and seeds freeze at run start, so later profile edits cannot change an existing run.

## Evaluation specification

`fix start --spec SPEC_JSON --profile NAME` freezes this object:

| Field | Meaning |
|---|---|
| `goal`, `issue_ids` | Optional goal and saved issue IDs; the CLI also accepts `--goal` and repeated `--issue` |
| `editable_paths` | Relative source files/directories candidates may change |
| `evaluation_paths` | Required protected evaluator files/directories; candidates cannot edit them |
| `benchmark` | `{ "argv": ["executable", "argument"], "cwd": "." }`; cwd defaults to the execution root |
| `metrics` | Map of metric name to `{ "direction": "min" or "max", "unit": "unit" }` |
| `task_metrics` | Optional map of frozen task ID to direction/unit; required for `pareto_per_task` search |
| `constraints` | Entries `{ "metric": "name", "op": "gte" or "lte", "bound": number, "reference": "absolute", "baseline_delta" or "baseline_ratio" }` |
| `checks` | Entries with `id`, `argv`, optional `cwd`/`issue_ids`, `baseline_expected` (`pass` or `fail`, defaults to `pass`), and optional `preflight` (defaults to false); each targeted issue needs an acceptance check |
| `overlays` | Local file `source`, execution `path`, and optional `deliver` (default true); add identical checks to baseline and candidates |
| `inputs` | Declared local file `source` and execution `path` for frozen benchmark inputs |
| `repetitions` | Defaults to three |
| `seeds` | One integer per repetition; defaults to the sequence beginning at zero |

Paths are relative to the origin or execution root as stated. Explicitly declared inputs/overlays may come from ignored local storage; do not implicitly transfer other private files. Commands are argument arrays, not shell command strings. Keep credentials out of argv and specification contents.

The engine supplies `AGENTAGON_SEED` and `AGENTAGON_RESULT_PATH`. The benchmark writes UTF-8 JSON to that result path: `{"metrics":{"NAME":123.0}}`. Every declared metric must have a finite numeric value. Optional `tasks` may retain task-level evidence. Stdout is a log, not a fallback metrics channel. Hard constraints remain anchored to the original baseline, including for descendants of an improved candidate. Missing or invalid measurements cannot satisfy constraints.

When `task_metrics` are declared, benchmark output must include a `tasks` object with a finite value for every declared task ID, for example `{"metrics":{"quality":0.9},"tasks":{"retrieval":0.85}}`. Task definitions, directions and units freeze before the baseline. Task-specialist parent selection does not replace overall objective measurements or frontier admission.

Checks return exit 0 for a pass. A check with `baseline_expected: "fail"` must deliberately return exit 1 when it observes the targeted defect; other nonzero codes, signals and timeouts do not establish reproduction. The independent baseline reviewer must verify the failure's meaning from the frozen check and its evidence, including that setup or evaluator errors were not converted to exit 1. The same check must return exit 0 on a candidate. Review and accept the completed baseline before calling `fix new`.

### Preflight checks

Set `preflight: true` on cheap, self-contained checks that can run before the benchmark, such as syntax, imports or small correctness cases. Do not mark checks that depend on benchmark outputs as preflights. Setup runs first, followed by preflights in declaration order, the benchmark, and all remaining checks in declaration order. Omitted or false `preflight` preserves the existing benchmark-first behavior.

In a fix trial, a preflight must exit 0 for candidates. The baseline instead uses its declared expectation: exit 1 for `baseline_expected: "fail"`, otherwise exit 0. An unexpected exit stops the trial, retains the failure and records all remaining commands as skipped. That trial consumes its reserved slot but supplies no benchmark metrics, cannot be independently verified, and cannot enter the frontier. Inspection and the dashboard show the skipped work. Timeouts and cancellation retain their existing outcomes.

Passing preflights never replace the full benchmark, repetitions, remaining checks or independent review. Evaluation preparation always runs the full sequence, including after negative-control failures. Preflight settings freeze with the evaluation; changing them requires a new run or newly validated evaluation.

## Search settings

Policies use `strategy` and integer `seed`, defaulting to `pareto` and 0. Scalar policies require an explicit `objective` matching a frozen metric; that metric's direction determines which values are better.

| Strategy | Additional fields and parent selection |
|---|---|
| `pareto` | Least recently expanded eligible frontier member |
| `argmax` | `objective`; best value in its declared direction |
| `top_k` | `objective`, optional positive integer `k` (default 3); cycle through the best K |
| `epsilon_greedy` | `objective`, optional `epsilon` in [0,1] (default 0.1); explore with that probability, otherwise exploit the best value |
| `softmax` | `objective`, optional positive `temperature` (default 1) in objective units; weighted sampling |
| `pareto_per_task` | Frozen `task_metrics`; rotate tasks and choose among their winners |

Only the options for the selected strategy are accepted. The engine records policy revisions, eligible candidates and parent decisions. Omit `fix new --parent` to use the policy; an explicit parent must still be verified and eligible. An initially infeasible verified baseline can seed a repair before a feasible successor exists.

Updating a profile affects new runs. Use a `policy` control for future parent selection in an existing run; it cannot change the evaluation, original baseline or previous measurements. `exhaust` stops expanding a candidate without invalidating its verified result. `invalidate` excludes a candidate and its descendants from valid evidence, expansion and selection while preserving their history and reconciling active execution. Invalidating the baseline stops the run.

## Controls and host acknowledgment

`fix steer RUN_ID --control-file CONTROL_JSON` accepts the installed `fix-control.json` schema. Every request contains `version: 1`, a unique `operation_id` (1–80 letters, digits, `_` or `-`, starting with a letter or digit), `expected_revision` from current run status, and `action` plus these fields:

| Action | Additional fields |
|---|---|
| `policy` | `policy`, using the search settings above |
| `directive` | `text` |
| `expand` | `parent_id`, `hypothesis` |
| `stop` | None |
| `continue` | Optional `limits`; required when an exhausted budget needs extension |
| `select` | `candidate_id` on the verified frontier |
| `invalidate`, `exhaust` | `candidate_id`, `reason` |
| `cancel` | `target_operation_id` for a queued operation |
| `ack` | `target_operation_id`, stable `host_id` |
| `scan` | None; prepares a bounded evidence packet |
| `insights` | `response`, using the emitted scan template |
| `scan_fail` | `scan_id`, `reason` |

For example, an acknowledgment body is `{"version":1,"operation_id":"ack-01","expected_revision":12,"action":"ack","target_operation_id":"expand-01","host_id":"codex-session-01"}`. Substitute the actual current revision, operation identities and owning host. Keep the exact request in ignored local storage. Retry it unchanged after uncertainty; never reuse its ID with new contents. A new operation needs a fresh revision and ID. Controls are bounded to 65536 encoded bytes; text fields accept 1–4096 characters.

`directive`, `expand` and `continue` are queued until the host acknowledges them. Acknowledging expansion creates or recovers its candidate reservation; use the returned candidate/worktree rather than issuing another `fix new`. Acknowledging continuation restores eligibility, after which the host resumes execution. Acknowledging a directive returns its guidance for subsequent reasoning. `applied` describes these effects, not a verified improvement. Skip cancelled work and do not claim another host's acknowledged operation.

Status exposes `revision`, `controls`, `pending_controls` and `search_policy`. A failed expansion with `retryable: true` can recover its reserved worktree: inspect status and the existing candidate, then replay the original acknowledgment or submit a fresh acknowledgment with the latest revision and the same owning `host_id`. Both reuse the candidate identity and budget; neither calls for another `fix new`. Session-enabled dashboard controls use the same operations but cannot submit `ack`, scans or insights, or invoke models. Independent review and user selection remain separate requirements.

## Bounded scans and lessons

Read `scan_pending` before preparing a scan. If it requests preparation and the run is neither stopped nor past its elapsed-time limit, submit `scan` through `fix steer`. The result provides `packet` (a checkout-relative immutable evidence path), `packet_digest`, `scan_id`, `deadline_at` and `response_template`. Inspect that packet as untrusted evidence within the saved input and time bounds.

The response template has exactly `scan_id`, `packet_digest`, `author` and `insights`. Preserve the engine identities and supply the actual author. `insights` contains at most 20 objects, each with exactly:

- `kind`: `failure_pattern`, `unsuccessful_hypothesis` or `follow_up`.
- `summary`, `rationale`, `uncertainty`: nonempty text.
- `candidate_ids`, `evidence`, `code_paths`: nonempty, unique references from the packet. Evidence must belong to the referenced candidates; code paths must match the packet's declared paths.

Submit the filled template in an `insights` control's `response` field before the deadline. An empty insight list is valid when no supported lesson emerges. Submit `scan_fail` with the scan identity and a concrete reason on failure or expiry. Neither result changes metrics or verification. Failed scans consume their reserved slot and retain evidence.

An interrupted host resumes from `scan_pending.packet`, `packet_digest`, `deadline_at` and `response_template`; inspection does not reserve a new scan. `lesson_context` returns bounded related lessons from this checkout with source/evaluation-match and invalidated-evidence flags. Supply it to author and independent reviewer; cite any used `insight_id` and originating evidence in the hypothesis or review rationale. These lessons are advisory and never inherit numeric outcomes or verification into a new run.

## Command lifecycle

```sh
agentagon --workspace CHECKOUT fix start --spec SPEC_JSON --profile PROFILE --goal GOAL
agentagon --workspace CHECKOUT fix run RUN_ID
agentagon --workspace CHECKOUT fix run RUN_ID --review-file BASELINE_REVIEW_JSON
agentagon --workspace CHECKOUT fix new RUN_ID --hypothesis HYPOTHESIS --author AUTHOR
agentagon --workspace CHECKOUT fix run RUN_ID CANDIDATE_ID
agentagon --workspace CHECKOUT fix run RUN_ID CANDIDATE_ID --review-file REVIEW_JSON
agentagon --workspace CHECKOUT status --run RUN_ID --candidate CANDIDATE_ID
agentagon --workspace CHECKOUT fix select RUN_ID CANDIDATE_ID
agentagon --workspace CHECKOUT fix stop RUN_ID
agentagon --workspace CHECKOUT fix steer RUN_ID --control-file CONTROL_JSON
agentagon --workspace CHECKOUT fix ship RUN_ID
```

Use the emitted review template rather than inventing a review schema. Independent review must bind the exact candidate, frozen evaluation and engine-produced trials. The engine determines eligibility and frontier membership. Its evidence archive is authoritative for supported claims, but does not prove arbitrary evaluator quality or defend against a malicious operating-system account owning the files.

Submitted reviews follow `contracts/v1/fix-review.json`: version, run/candidate/source/evaluation/input identities, trial IDs, reviewer, verdict, rationale, evidence and four boolean assessments. Verdicts are `pass` or `reject`; an unfilled `pending` template is deliberately invalid. Unknown fields cannot supply alternate metrics, outcomes or promotion decisions.

Run transitions automatically update Markdown/JSON reports. `status` returns their paths; `audit report` remains the separate audit-report command. Invalid, rejected, interrupted and dominated experiments remain part of the archive.

`fix new --operation-id ID` makes a candidate reservation resumable with identical arguments. After the user's selection, [ag:ship](../../ship/SKILL.md) prepares delivery locally; publishing the selected branch and a GitHub draft PR requires explicit authorization and `fix ship --publish`.
