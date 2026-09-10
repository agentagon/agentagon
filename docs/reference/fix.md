# Fix configuration and controls

Describe the change you want to your coding agent. It prepares the configuration and runs the commands. For your first comparison, follow [Measure candidate fixes](../fix.md).

The prompts below are examples. Replace IDs, paths, metrics, and limits to fit your application.

| Task | In Codex | In Claude Code |
|---|---|---|
| Configure execution or saved defaults | Select **ag:setup**, then send the prompt. | Start the prompt with `/ag:setup`. |
| Define, run, or steer a comparison | Select **ag:fix**, then send the prompt. | Start the prompt with `/ag:fix`. |
| Inspect results or use visual controls | Select **ag:dashboard**, then send the prompt. | Start the prompt with `/ag:dashboard`. |
| Prepare a chosen candidate for delivery | Select **ag:ship**, then send the prompt. | Start the prompt with `/ag:ship`. |

Use the dashboard to compare results and operate run controls. Expand the technical details only when you need the exact CLI or file format.

## Configure execution once

Use **ag:setup** to save where evaluations run and how much work they may perform. Reuse that profile for later runs.

```text
Create a project execution profile named local. Run on this machine with
up to 6 candidates, 60 trials, and 30 minutes total. Run one candidate and
one trial at a time, with a 60-second timeout per trial. Stop after
3 rounds without improvement. Save credential references, not key values.
```

For remote execution, name your SSH host or E2B template and the inputs it may receive. See [where work runs](../execution.md) for the available choices.

??? details "Profile fields and setup commands"

    Start in a clean application Git checkout with an existing commit. Initialize ignored local state with `agentagon --workspace CHECKOUT init` if needed. Keep run specifications, private data and review responses in `.agentagon/`; do not commit them merely to satisfy the clean-checkout requirement. Existing unrelated edits must be handled by their owner before starting a run.

    If Git or the initial commit is missing, `ag:fix` and `ag:eval` stop, explain what is missing, and ask you to set it up or approve setup by the agent. They do not fall back to an untracked manual fix. Authorized setup reviews the files and ignore rules, excludes private state and credentials, and creates a local application baseline before resuming. It does not create a remote or publish code. `agentagon init` initializes Agentagon state, not a Git repository.

    Choose a named execution profile through `ag:setup`. The first use requires explicit limits for candidates, trials, elapsed time, candidate/trial concurrency and individual trial timeout. Reuse the saved profile for later runs. This example illustrates the shape; choose limits appropriate to the authorized work before saving it:

    ```json
    {
      "runner": {"kind": "local"},
      "env": {"MODEL_API_KEY": "CUSTOM_MODEL_API_KEY"},
      "setup": [],
      "limits": {
        "max_candidates": 6,
        "max_trials": 60,
        "max_elapsed_seconds": 1800,
        "parallel_candidates": 1,
        "parallel_trials": 1,
        "trial_timeout_seconds": 60,
        "stagnation_rounds": 3
      }
    }
    ```

    ```sh
    agentagon --workspace CHECKOUT setup --scope project --profile local --profile-file PROFILE_JSON
    agentagon --workspace CHECKOUT setup
    ```

    Use `--scope user` for a reusable default across checkouts. A project profile replaces the complete same-named user profile. Normal settings still use `--set`/`--unset`; those options cannot be combined with profile input. Profiles and credentials remain in the existing user-local config file. The `env` mapping stores environment-variable names, never their secret values.

    Runner choices:

    | Kind | Preparation and boundary |
    |---|---|
    | `local` | Runs evaluation commands on this host; worktrees protect the origin's files but are not a machine or network sandbox |
    | `ssh` | Uses an existing SSH `host` and `remote_root`, with optional `python`; requires authorized remote code/data transfer and execution |
    | `e2b` | Uses the optional `agentagon[e2b]` dependency, an existing `template`, and an `api_key_env` reference; optional `sandbox_timeout_seconds` bounds the sandbox |

    All three keep candidate authoring local. Remote runners receive frozen execution inputs and return evidence. A run keeps one runner profile; changing the execution environment requires a fresh baseline. Set the boolean `independent_capacity` to true only when the runner has actual independent capacity; concurrent local/SSH trials require this assertion. Increasing concurrency on shared hardware can distort latency comparisons.

    Profiles also save optional `search`, `scans` and `repetitions` settings. Reuse those choices across runs; changing a saved profile does not rewrite an existing run. Search and scan settings are described below.

## Define the benchmark and hard constraints

Use a [prepared evaluation](../eval.md) when you have one. Otherwise, describe the metrics, allowed edits, and required behavior to **ag:fix** so it can prepare the specification.

```text
Reduce response latency while preserving answer quality. Only edit app.py.
Use benchmark.py and tests/ as the protected evaluator. Minimize latency_ms
and maximize quality. Require quality of at least 0.95 and latency no worse
than 1.05 times the baseline. Use three repetitions with seeds 11, 22, and 33.
Show me the benchmark specification before starting the comparison.
```

Choose thresholds supported by your application's requirements. Multiple candidates can offer useful tradeoffs; you choose which result to deliver.

??? details "Benchmark specification and result contract"

    Choose objective metrics, directions, units and hard constraints. A lower latency objective and a higher quality objective can preserve multiple useful tradeoffs. The verified Pareto frontier contains feasible candidates for which no other verified candidate is no worse on every objective and strictly better on at least one. Agentagon does not invent a weighted score to select one for you.

    Freeze a specification before the baseline. For an application with `app.py`, `benchmark.py` and `tests/`, this is an example:

    ```json
    {
      "goal": "Reduce response latency while preserving answer quality",
      "issue_ids": [],
      "editable_paths": ["app.py"],
      "evaluation_paths": ["benchmark.py", "tests"],
      "benchmark": {"argv": ["python3", "benchmark.py"], "cwd": "."},
      "metrics": {
        "latency_ms": {"direction": "min", "unit": "ms"},
        "quality": {"direction": "max", "unit": "fraction"}
      },
      "constraints": [
        {"metric": "quality", "op": "gte", "bound": 0.95, "reference": "absolute"},
        {"metric": "latency_ms", "op": "lte", "bound": 1.05, "reference": "baseline_ratio"}
      ],
      "checks": [
        {"id": "regressions", "argv": ["python3", "-m", "pytest", "tests"], "baseline_expected": "pass"}
      ],
      "overlays": [],
      "inputs": [],
      "repetitions": 3,
      "seeds": [11, 22, 33]
    }
    ```

    The benchmark writes UTF-8 JSON to the path in `AGENTAGON_RESULT_PATH`:

    ```json
    {"metrics":{"latency_ms":125.0,"quality":0.98}}
    ```

    Every declared metric needs a finite numeric value. `AGENTAGON_SEED` identifies the trial's seed. Stdout and stderr are retained logs, not alternate result channels. Optional task-level evidence can be returned in `tasks`. The runner captures exit codes, timing, output and source identity itself; supplying a JSON file of claimed trial results is not supported.

    Three repetitions are the default. A saved profile can override `repetitions`; an explicit specification takes precedence. Agentagon freezes that choice and the seeds before execution, then reports the median and observed range.

    `evaluation_paths` protect the benchmark, controls and test harness from candidate edits. Use identical `overlays` on baseline and candidates when adding a new regression check. Each overlay declares its local file `source`, execution `path`, and whether it should be delivered on the selected branch (`deliver`, default true). `inputs` similarly declare file sources and execution paths for frozen data. Only declared private inputs should be transferred remotely. See the [complete input contract](../../skills/fix/references/contract.md).

    Constraints use `absolute`, `baseline_delta` or `baseline_ratio` references. Baseline-relative constraints stay anchored to the original baseline throughout the experiment tree. A successful baseline process can still have improvable metrics. Issue-specific checks can declare `baseline_expected: "fail"` when the intended defect should reproduce; a setup failure is not proof of reproduction. Changing the evaluation, protected inputs or objective definitions requires a new run.

## Choose how to explore

Tell **ag:fix** which measured results to explore next, or change the search policy through dashboard controls. This changes future exploration while preserving the benchmark and prior results.

```text
For run RUN_ID, cycle through the three best eligible candidates for
latency_ms when proposing new improvements. Keep the frozen benchmark,
hard constraints, and existing execution limits.
```

To make this a default for future runs, ask **ag:setup** to save the policy in your profile. Changing a saved profile does not change an existing run.

??? details "Search strategies and policy fields"

    Search chooses the next candidate's parent. Verification, hard constraints and the true Pareto frontier still decide which results qualify. An explicit `fix new --parent` overrides parent selection for that proposal, subject to the same eligibility checks. Without `--parent`, the engine uses the run's current search policy and records the choice.

    Set `search` in the complete profile saved by `setup --profile-file`:

    ```json
    {"strategy":"top_k","objective":"latency_ms","k":3,"seed":11}
    ```

    | Strategy | Parent selection and settings |
    |---|---|
    | `pareto` | Least recently expanded eligible frontier member; the default |
    | `argmax` | Best value of the named `objective`, respecting its frozen min/max direction |
    | `top_k` | Cycle through the best `k` candidates for `objective`; `k` defaults to 3 |
    | `epsilon_greedy` | Explore an eligible candidate with probability `epsilon`; otherwise use the best `objective`; default 0.1 |
    | `softmax` | Sample by `objective` value with positive `temperature` in that objective's units; default 1 |
    | `pareto_per_task` | Rotate through task winners; requires frozen `task_metrics` |

    All policies accept integer `seed`, default 0. The four scalar policies require an explicit `objective`; Agentagon never invents a combined score. Task-specialist search is separate from the verified Pareto frontier: it does not remove balanced tradeoffs from that frontier.

    For task-specialist search, add task definitions such as `"task_metrics":{"retrieval":{"direction":"max","unit":"fraction"}}` to the evaluation specification before starting the run. Each benchmark repetition must then return finite task values, for example `"tasks":{"retrieval":0.9}`, alongside its normal `metrics`. Missing declared task values cannot be used for verified selection.

    Use a `policy` control to change future parent selection in the current run. This retains the previous policy and decisions without changing the frozen evaluator or past results. Mark a candidate `exhaust` when it should no longer be expanded; its verified result remains eligible for the frontier. `invalidate` removes that candidate and its descendants from valid evidence and further exploration, preserves their history, and reconciles owned execution. Invalidating the baseline stops the run.

## Explore, review and select

Use **ag:fix** to run the comparison and **ag:dashboard** to inspect its progress, measurements, and candidate reviews.

```text
Continue run RUN_ID within its saved limits. Measure and independently
review candidate improvements, then show me the verified alternatives
in the dashboard so I can choose one.
```

If the run has exhausted its limits, specify the extension you want. To select a result, tell the agent which candidate you choose, for example: “Select candidate CANDIDATE_ID from run RUN_ID for delivery.”

Completed measurements and unsuccessful attempts remain in history. Selection prepares the verified source for delivery; follow [Select and deliver a fix](../delivery.md) for the next step.

??? details "Execution commands, budgets, and review requirements"

    The fix commands keep execution, steering and delivery explicit:

    | Command | Purpose |
    |---|---|
    | `fix start --spec SPEC_JSON --profile NAME` | Freeze the run; optional `--goal` and repeated `--issue` select its intent |
    | `fix lookup RUN_ID --context-file CONTEXT_FILE --focus-file FOCUS_FILE --phase initial --limit 5` | Request optional privacy-safe fix guidance; `--focus-file` is required and the coordinator owns the run lookup |
    | `fix new RUN_ID --hypothesis TEXT --author AUTHOR` | Create a local candidate; `--parent` selects its source candidate and `--round` groups alternatives |
    | `fix run RUN_ID [CANDIDATE_ID]` | Execute or resume engine work; `--review-file` supplies the independent host review |
    | `fix select RUN_ID CANDIDATE_ID` | Create the user's selected verified branch for review |
    | `fix stop RUN_ID` | Stop the bounded run and cancel owned execution |
    | `fix steer RUN_ID --control-file CONTROL_JSON` | Change policy, queue host work, acknowledge it, or record scan judgments |
    | `fix ship RUN_ID` | Prepare delivery of the selected branch; publication requires `--publish` and authorization |

    Run `fix run RUN_ID` to measure the baseline, then submit its independent review before creating candidates. After `fix new`, edit only the returned candidate worktree and permitted source paths. `fix run RUN_ID CANDIDATE_ID` seals that source and executes checks. Descendants start from their recorded parent. Retained Git references protect sealed snapshots from garbage collection.

    Machine checks and independent host review are separate requirements. After trials, use the returned review template. A reviewer other than the author inspects the sealed diff, frozen checks, issue evidence and engine results; the review must reference their exact identities. Continue with:

    ```sh
    agentagon --workspace CHECKOUT fix run RUN_ID CANDIDATE_ID --review-file REVIEW_JSON
    agentagon --workspace CHECKOUT status --run RUN_ID --candidate CANDIDATE_ID
    agentagon --workspace CHECKOUT dashboard --run RUN_ID --no-open
    ```

    This continuation reuses valid completed trials. Host review cannot replace metrics, rewrite exit codes or directly promote a candidate. Missing measurements, failed constraints, source changes, rejected reviews and incomplete remote evidence do not enter the verified frontier.

    `ag:fix` continues the proposal/edit/run/review loop within the saved limits. `max_candidates` counts proposals after the baseline; `max_trials` includes baseline executions, cancelled attempts and replacements. Three completed rounds without new nondominated metric vectors stop the loop by default. Equal-valued patches remain on the frontier but do not reset this counter.

    A stopped run requires `fix run RUN_ID [CANDIDATE_ID] --continue`; add `--limits-file LIMITS_JSON` when extending exhausted limits. Consumed counts and elapsed time remain recorded. Confirmed cancellation permits a new attempt for the unfinished repetition, while completed repetitions are reused. Connection loss instead reconciles the same attempt. Unknown outcomes remain inconclusive. Result collection and cleanup use recorded ownership; do not manually delete worktrees, locks or trial files to bypass recovery.

    Run transitions generate Markdown and JSON reports automatically. `status` and the run output return their paths. There is no separate fix-report command; `audit report` remains the audit-report command. Reports retain unsuccessful, dominated and interrupted experiments as well as the verified frontier.

    Select only after the user chooses a candidate. The resulting branch reproduces its verified source snapshot. Selection does not apply changes to the original checkout, merge, open a PR, deploy or resolve an audit issue. A candidate may improve the objective without fixing a particular issue. Separately authorized verified-resolution updates must cite the engine run and candidate and satisfy the current-source checks described in [issue history](../../skills/audit/references/history.md).

## Steer a run without losing queued work

Give **ag:fix** a specific direction, or use the dashboard controls to stop, continue, select, or change the search policy.

```text
For run RUN_ID, explore a candidate from CANDIDATE_ID that avoids repeated
retrieval calls within one request. Preserve access checks and the frozen
benchmark. Keep the current execution limits.
```

The coding agent handles control files and retries. Inspect pending actions and outcomes in the dashboard.

??? details "Control schema and retry behavior"

    `status --run RUN_ID` returns the current `revision`, `controls`, search policy and pending candidate work. Every new control includes version 1, a unique `operation_id`, that revision, an `action`, and the action's fields. Save requests in ignored `.agentagon/` files. For example, after substituting the current revision:

    ```json
    {
      "version": 1,
      "operation_id": "policy-latency-01",
      "expected_revision": 12,
      "action": "policy",
      "policy": {"strategy":"argmax","objective":"latency_ms","seed":11}
    }
    ```

    ```sh
    agentagon --workspace CHECKOUT status --run RUN_ID
    agentagon --workspace CHECKOUT fix steer RUN_ID --control-file CONTROL_JSON
    ```

    Retry an uncertain request with the identical file and operation ID. A reused ID with different contents is rejected. For a new action, refresh the revision and use a new ID. A revision conflict means the run changed; inspect it before resubmitting. The [control schema](../../contracts/v1/fix-control.json) defines supported fields.

    | Action | Additional fields and effect |
    |---|---|
    | `policy` | `policy`; changes future search decisions |
    | `directive` | `text`; queues guidance for the host |
    | `expand` | `parent_id`, `hypothesis`; queues a candidate proposal |
    | `continue` | Optional `limits`; queues continuation of a stopped run; exhausted limits need an explicit extension |
    | `stop` / `select` | Stop uses no extra fields; select uses `candidate_id` from the verified frontier |
    | `invalidate` / `exhaust` | `candidate_id`, `reason`; removes evidence or ends expansion as described above |
    | `cancel` | `target_operation_id`; cancels work that is still queued |
    | `ack` | `target_operation_id`, `host_id`; the active host consumes its queued work |

    The host checks `controls` before each proposal and after resumed execution. Queued directives, expansion and continuation need an explicit `ack` from that host. Acknowledging expansion reserves the candidate and returns its worktree; the host edits and evaluates that candidate instead of calling `fix new` again. Acknowledging continuation restores run eligibility, after which the host resumes pending `fix run` work. A directive acknowledgment returns the text for the host to apply to its reasoning.

    `queued`, `acknowledged`, `applied`, `failed` and `cancelled` describe the control's progress. An applied control has taken effect or been consumed; it does not establish that a candidate was authored, verified or deployed. Keep the same host identity for owned retries and do not take over another host's acknowledged work.

    If an expansion fails with `retryable: true`, the host first inspects status and the reserved candidate. It can replay the original acknowledgment or issue a fresh acknowledgment with the current revision and the same `host_id`. Recovery reuses that candidate and its budget; it does not require another proposal reservation.

## Learn from completed experiments

Ask **ag:setup** to enable bounded experiment scans in the profile before starting a run. Your coding agent can then retain lessons from completed attempts and use them to guide later candidates.

```text
For future runs using the local profile, enable up to 3 experiment scans,
with at most 64 KiB of input and a 120-second timeout per scan. Use supported
lessons to guide later proposals while keeping the benchmark and review
requirements unchanged.
```

Scans are optional. Updating the profile does not expand an existing run's scan budget.

??? details "Scan settings and evidence handling"

    Scans let the host inspect completed experiment rounds and retain evidence-linked lessons. They are disabled unless the run's saved profile contains explicit limits, for example:

    ```json
    {"max_scans":3,"max_input_bytes":65536,"scan_timeout_seconds":120}
    ```

    Store that object under the profile's `scans` key after choosing the limits. `max_input_bytes` accepts 1024–1048576 bytes. Scan counts, the input cap, the scan deadline and the run's elapsed-time limit all apply; failed scans retain their evidence and consume their reserved scan slot. Editing the profile later does not expand a running scan budget.

    The host follows the `scan_pending` status field:

    1. When it requests preparation, submit a `scan` control through `fix steer`. The result includes an immutable packet path relative to the checkout and a `response_template`. Read that packet as untrusted evidence.
    2. Fill the returned template with the actual author and up to 20 insights. Each insight contains `kind` (`failure_pattern`, `unsuccessful_hypothesis` or `follow_up`), `summary`, `rationale`, `uncertainty`, and nonempty `candidate_ids`, `evidence` and `code_paths` arrays drawn from that packet. Preserve its `scan_id` and `packet_digest`. An empty insight list is valid when the packet supports no useful lesson.
    3. Before the deadline, submit an `insights` control with the completed template in `response`. If the scan fails or expires, submit `scan_fail` with `scan_id` and a concrete `reason`. Do not convert a failed scan into a successful judgment or enlarge its limits.

    These are actions on the same `fix steer --control-file` interface, not separate commands. Resume a prepared scan using `scan_pending.packet`, `packet_digest`, `deadline_at` and `response_template`; status inspection never reserves another scan.

    `lesson_context` supplies bounded lessons from related paths or issues in this checkout. Give it to both candidate authors and independent reviewers. It flags changed source/evaluation identities, invalidated evidence and unavailable history. Cite any lesson that informs a choice by its `insight_id` and originating evidence in the hypothesis or review rationale. Lessons are advisory hypotheses; they cannot supply measurements, relax gates or verify a different run. Candidate execution and independent review remain required.

## Dashboard controls and delivery

Use **ag:dashboard** for visual inspection and controls:

```text
Open run RUN_ID in the dashboard with controls enabled so I can compare
candidates and steer the run.
```

After choosing a candidate, use **ag:ship**:

```text
Prepare delivery for the selected candidate in run RUN_ID. Check for useful
cleanup, verify any resulting changes, and prepare the patch, measurement
summary, and draft PR description. Stop before publication.
```

Tell the coding agent when you are ready to publish. See [the delivery guide](../delivery.md) for the full workflow.

??? details "Dashboard commands and delivery mechanics"

    The dashboard is read-only by default. Explicitly enable controls for a session with:

    ```sh
    agentagon --workspace CHECKOUT dashboard --run RUN_ID --controls --no-open
    ```

    Open the exact localhost URL from startup. The controls use the same revisions and operations as the CLI. They can change search policy, queue proposals/directives/continuation, stop, select, invalidate/exhaust candidates and cancel queued work. The page shows pending work and outcomes; it never calls a model service or acknowledges host work. Scans and host acknowledgment remain in the coding-agent workflow. Session credentials are temporary and are not stored in run records. See [ag:dashboard](../../skills/dashboard/SKILL.md).

    After selection, `ag:ship` inspects the application diff for useful cleanup. `fix cleanup RUN_ID --operation-id ID --author AUTHOR` reserves a separate candidate from that winner. It uses the same frozen benchmark and remaining optimization budget. Execute it with `fix run`, obtain a fresh independent review, then compare with `fix cleanup RUN_ID --finish CLEANUP_ID`. Automatic substitution requires frontier admission and no worse results on every objective, including declared task objectives. Failed or regressed cleanup preserves the original selection and evidence; changed tradeoffs require a user decision. Cleanup cannot edit frozen evaluation files.

    `agentagon --workspace CHECKOUT fix ship RUN_ID` prepares a local delivery summary and draft PR body, including any verified cleanup comparison. When publication to the chosen destination is authorized, repeat with `--publish` to push the exact selected branch and create a GitHub draft PR. Follow [ag:ship](../../skills/ship/SKILL.md) for evidence checks, destination choices and interruption recovery. Shipping does not merge, deploy or resolve an audit issue.

## What verification means

A verified candidate passed the frozen benchmark's checks and constraints and received an accepted independent review tied to its source snapshot and trial evidence. Choose benchmark cases and expected outcomes that represent your goal; verification applies to those cases, and deployment outcomes still need measurement.

Repeated trials and retained inputs help compare candidates, but external models and services can vary between runs. Use the reported variation when judging an improvement. Configure and check the selected [execution environment](#configure-execution-once) before running a benchmark.
