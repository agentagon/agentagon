# Use the local web app

Run `agentagon` from your AI agent's project directory to open the web app. It registers the directory and reuses the running local service when available. `agentagon app` is an alias; existing CLI subcommands continue to work.

```sh
cd /path/to/your-agent
agentagon
```

Use the project selector to switch between registered directories or add another project. Each checkout keeps its own findings, imports, evaluations and runs. Removing a project from the selector preserves its evidence.

The app runs on your computer and binds to loopback. Hosted access and team accounts are outside this release. Local hosting does not make coding-agent or provider requests offline.

## Select an application agent and focus

The project opens to **Agents**. Discover suggestions from code and imported traces, confirm their boundaries, or add an agent manually. Discovery is a bounded scan; review each suggested code scope and trace selector before using it. Separate checkouts, including Git worktrees, remain separate projects.

An **application agent** is the workflow you are investigating, such as a support agent. A **coding agent** is Codex or Claude, which Agentagon uses to perform the work. One project can contain several application agents and shared dependencies.

Choose an application agent, then add a focus: correctness, reliability, grounding, safety, security, latency, cost, interaction, or a custom objective. Describe the desired outcome, attach a saved issue, or investigate a bounded selection of recent traces. A focus preserves its goal and measurement history. Its code binding and trace selection determine the investigation scope; the audit rubric still applies within that scope.

Start with a goal, review its proposed measurements, establish a baseline, then improve and compare candidates. Audit is available when you need a deeper investigation. Existing reviewed evaluations and baselines can be reused when compatible. Full-project audits are also available through the CLI without first defining an application agent. Trace-only agents require a code binding before measured fixes.

Request a measurement proposal from the goal view. The coding agent inspects code, existing evals and selected evidence, then proposes behaviors, metrics and an evaluation approach. Edit metric units, aggregation, weights and scaling; choose a primary metric, weighted score or custom evaluator output. Required behaviors remain separate gates. Missing measurements stay unknown. Traces are optional: a proposed behavior can instead require instrumentation or an executable check, with that prerequisite shown explicitly.

Save and accept the proposal before preparing its evaluation. Acceptance freezes a version; later edits create a new version and cannot rewrite a baseline. Reusing a frozen evaluator preserves its scoring. Changing cases, scores or required behaviors requires a new reviewed evaluator. Discovery also finds native Braintrust, DeepEval, pytest and custom evaluation entrypoints for the coding agent to adapt without replacing the project's usual evaluation command.

## Connect a coding agent

Open **Settings → Coding agents** and choose the agent used for new tasks. Skill or plugin registration is optional: the app supplies the bundled workflow procedures to sessions it manages.

| Agent | Required setup | Session behavior |
|---|---|---|
| Codex | Install the Codex CLI, make `codex` available on `PATH`, and sign in with `codex login` | The app starts a Codex app-server process and creates or resumes its own recorded task session. |
| Claude | Install `agentagon[claude]` and provide an Anthropic API key in Settings or `ANTHROPIC_API_KEY` | The app uses the Claude Agent SDK. Claude subscription authentication is not used by this integration. |

Select the Claude extra when installing with pipx: `pipx install 'agentagon[claude]'`. In a Python environment, use `python -m pip install 'agentagon[claude]'`.

These sessions are separate from a conversation already open in your terminal or desktop app. Agent detection reports local prerequisites; it does not prove that a model request will succeed. Your coding-agent provider determines model availability, account limits and charges.

Choose a Codex model from the installed CLI's model catalog in Settings; the app validates the selection before starting a session. The catalog reflects that installation and account, so it can differ between machines. Claude has its own model setting. Model selection is separate from the execution profile used to run evaluations.

Set coding-agent capacity in Settings. Fix has a separate requested parallelism limit; actual work stays within the shared capacity and execution budget. The app queues tasks from the same project, while candidate work uses isolated worktrees. Permissions and questions appear in the task view; a pending approval waits for your explicit answer. Independent reviews use separate managed sessions.

## Connect traces and datasets

Select your local project, then open **Settings → Connections**. Each connection belongs to that project. To use the same provider in another checkout, connect it there too.

1. Choose Braintrust, LangSmith or Langfuse. The API URL is prefilled; edit it for your region or self-hosted service.
2. Enter the provider credentials and choose **Find projects**.
3. Select the remote project from the dropdown and choose **Connect**.

| Provider | Credentials |
|---|---|
| Braintrust | API key. |
| LangSmith | API key. Agentagon discovers the workspace internally. |
| Langfuse | Public key and secret key. The keys determine project access. |

**Find projects** checks access to project metadata. If no projects appear, check the credentials, API URL and provider permissions. Trace or dataset reads can still require additional permissions or a compatible server version. Langfuse trace import uses Observations API v2; older self-hosted deployments need a compatible export or an upgrade.

Credential storage is automatic: Agentagon prefers a supported OS credential store and falls back to memory for the current service session. The connection indicates when credentials last only until the app stops; reconnect afterward. To enable OS storage, install `agentagon[credentials]`; combine extras as `agentagon[claude,credentials]` when needed. Configuration stores references, never secret values, and the browser does not receive stored credentials.

Choose a connection in the trace or dataset import flow, set the selection, and preview it before saving. Trace selections require a timezone-aware date window and a cap on whole traces. Dataset selections preserve their provider version, structured inputs, reference outputs and source metadata. LangSmith also supports split and metadata filters.

Imports are bounded to 100 selected traces or 1,000 dataset items, with separate limits of 10,000 trace spans and 20 MB of retrieved data. The preview reports incomplete pagination, capped selections and missing trace details. It does not turn a sample into a production-wide measurement. Saving creates an immutable local snapshot; refreshing creates another snapshot. Connecting alone starts no collection or publication.

A dataset can be exported as a Braintrust or DeepEval starter bundle with its native adapter and local cases. Missing expectations remain missing; adapters require the application's task and accepted scorers. Structured DeepEval inputs require an explicit case mapping. Braintrust publication is a separate action: preview the exact connection, destination, rows and missing expectations, then confirm publishing. Retrying the same operation reconciles its saved receipt. No other provider write-back is supported.

## Choose a workflow

| View | Use it to |
|---|---|
| Agents | Discover, confirm and select application agents in the project. |
| Goals | Inspect the selected agent's focuses, readiness, recent tasks and results. |
| Measurement plan | Propose, edit and accept behaviors, scores, evidence and evaluation choices for the selected goal. |
| Audit | Investigate the selected focus using captured local code, changes, traces or both. Read findings with their evidence and coverage limits. |
| Eval | Import or select a dataset, agree on expectations and scoring, and prepare a reviewed evaluator. |
| Fix | Select a saved issue or goal, declare scope and limits, and compare candidates against a frozen evaluator. |
| Eval → Baseline history | Inspect measurements, compare compatible baselines or explicitly start a rerun. |
| Audit → Saved issues | Review saved findings and their recorded status. |
| Metrics | Inspect retained measurements, evaluator versions and accepted guardrails. Missing measurements remain unknown. |
| Traces | Inspect imported snapshots and their provenance; choose evidence matching the agent. |
| Settings | Manage connections, coding agents, execution profiles and project settings. |

A dataset import remains a **draft**. Missing reference outputs require accepted expectations or another explicit correctness rule. Conversation messages retain their ordering and structure; tool state and replay requirements still need an evaluator. Observed production responses are not automatically correct labels.

Dataset preparation can derive drafts from traces, group related cases before splitting, and materialize development inputs. Final-test use needs a separately reviewed evaluator and explicit final-use handling. These controls do not establish a sealed holdout or automatically create a trustworthy final evaluator.

Running an evaluation requires a runnable entrypoint, clean committed source, an execution profile and agreed limits. Freezing additionally requires successful validation, sensitivity checks and independent review. Imported private inputs stay out of delivered source. See [evaluation preparation](eval.md) and [measured fixes](fix.md) for the evidence requirements.

Baseline comparison requires completed measurements with the same frozen evaluator, scoring definition and execution settings. It reports the saved benchmark score difference. Recent traces retain their own windows, coverage and alignment; their scores do not establish a controlled comparison.

Fix defaults to Omni. GEPA uses its upstream proposer: Agentagon sends the exact assembled reflection prompt to a dedicated coding-agent session and returns its raw final response for upstream extraction. Agentagon still validates candidate scope, runs measurements and requires independent review. AutoResearch and Meta-Harness retain their separate proposal contracts.

Evidence analysis happens while proposing measurements, before the baseline. Its accepted context, evidence references and coverage limits become frozen optimization background. Evaluation feedback then adds observations from each candidate. Optional Intelligence adds only completed, approved guidance receipts bound to that run; configuring optimization does not initiate a lookup.

Choose how many verified candidates to retain: three by default, from one to ten, excluding the baseline. This count is separate from parallelism and affects the final-verification reserve. Fix binds the required suite across active focuses and agents affected by permitted changes. Each finalist is compared with the reference under every required frozen evaluator and independent review. Missing prerequisites block the run; candidates that fail a prior guard cannot be selected. Fewer qualifying candidates means fewer results, and no verified improvement retains the baseline. Inspect the suite scope before drawing conclusions about other agents.

Compare each verified candidate's scores, behaviors, checks and changes, select one, then explicitly create its draft PR or local delivery. Every candidate retains its own source identity and worktree evidence. Selection does not publish, merge or deploy.

Starting a task authorizes its selected workflow. Publishing, merging and deploying remain separate actions. Optional Intelligence keeps its own request consent and data-handling rules.

## Keep or resume work

The local service owns running tasks. Closing the browser leaves them running; keep the terminal that launched the service open. Stopping that process interrupts active work. After restarting, inspect the saved task and choose **Resume** explicitly. Reopening the app does not automatically restart model work or expand its budget.

Pause and cancellation retain task history and completed evidence. Resumption uses the recorded managed-agent session when available. An unavailable session or exhausted limit needs attention rather than a silent replacement session or a larger budget.

The app's authoritative metadata is SQLite at `~/.config/agentagon/config.app/app.sqlite3`, or `$XDG_CONFIG_HOME/agentagon/config.app/app.sqlite3`. It stores project registrations, application agents and focuses, project connection references, coding-agent settings, jobs, events and approval state. `AGENTAGON_APP_STATE` overrides the app directory; `AGENTAGON_CONFIG` continues to select the settings file.

Each checkout's ignored `.agentagon/` directory contains engine records, immutable imported evidence and artifacts, reports and isolated work areas. Generated task context files are projections, not another task-state authority. Preserve both the app database and project evidence when backing up work. Do not edit saved records directly.

This app state format has no migration or compatibility writer. An earlier development database requires a new `AGENTAGON_APP_STATE` directory; the old files remain untouched. Existing CLI trace settings do not create app connections; connect each project explicitly in Settings.

The web app is a loopback application for one local user. The existing `agentagon dashboard` command remains a separate checkout viewer with read-only defaults and explicitly enabled controls. See [the dashboard guide](dashboard.md) if you use the skill-based workflow.
