# Privacy and data

Agentagon saves evidence locally, but the location of storage and the location of processing are different questions. Your coding host, execution profile, and optional integrations determine which systems handle your data.

## What goes where?

| Data or action | Location and boundary |
|---|---|
| Captured source, traces, findings, and reports | Saved in `.agentagon/` inside the application directory. The coding host reads permitted evidence to reason about it. |
| Model reasoning and independent review | Run through your coding host and its configured model service. Agentagon does not guarantee local-only inference. |
| Candidate application edits | Authored in isolated local worktrees. Local execution is not a machine or network sandbox. |
| Evaluation commands | Run on the configured local, SSH, or E2B runner. Commands and applications may contact their configured services. |
| Remote execution inputs | Declared frozen inputs are transferred to the configured destination. Choose the permitted code and data before authorizing a remote run. |
| Provider trace retrieval | The local web app retrieves explicit selections through a project connection. Coding-agent workflows can also retrieve selected exports through available provider access. |
| Optional Intelligence | Receives separately prepared, redacted workflow fields when configured and used: context/focus for audits, context/goal for evaluations, and context/focus for fixes. Raw code, traces, saved goals and protected evaluation material are not automatically uploaded. |
| Local product funnel | Derived from project-local records, with a bounded journal for prepared actions and duplicate starts. It is visible and manually exportable in Data & privacy settings. It is never sent automatically. |
| Anonymous product telemetry | Disabled by default. If you explicitly enable it, Agentagon sends only the bounded skill/Intelligence usage fields described below. Local funnel records are never included. |
| GitHub publication | Pushes the selected branch and creates a draft PR only when publication is authorized. |

## Local evidence and credentials

`.agentagon/` is private workflow state and is excluded from Git locally when initialized in a repository. Use supported CLI commands to manage records. Do not commit this directory or rewrite immutable evidence to alter a result.

CLI settings use your user configuration file, with project overrides and environment-variable references for credentials. Web-app connections belong to one local project and retain credential references in the app database. Provider credentials automatically use a supported OS credential store when available, or session memory until the service stops. Secret values are not written to configuration or returned to the browser. See [connection setup](app.md).

Retained trace and task data use recognized-secret redaction. Pattern redaction is additional protection, not a guarantee that personal, proprietary, or sensitive content has been removed. Decide what your host and services may inspect before supplying it.

## Optional product telemetry

Agentagon does not send product telemetry until you explicitly opt in. Enable it for your user with:

```sh
agentagon _internal setup --scope user --set telemetry.enabled true
```

Disable it again for your user with:

```sh
agentagon _internal setup --scope user --set telemetry.enabled false
```

Or for a process and its children:

```sh
export AGENTAGON_TELEMETRY_DISABLED=1
```

Disabling clears pending events. Disabled-period events are not backfilled. A request that already started may finish. Telemetry does not block local workflows.

Allowed telemetry includes skill name, host enum, lookup outcome/timing, and validated knowledge-entry IDs. It excludes code, traces, prompts, goals, credentials, local paths, owner IDs, customer identifiers and finding text. Events use per-event random IDs, not stable installation or user IDs.

The analytics service sees connection metadata during processing. Provider privacy settings govern stored IP/enrichment behavior. See [Product telemetry](telemetry.md) for the complete payload, queue, and delivery contract. The local funnel export contains no project path, source, trace content, prompt, goal, or credential, and downloading it is a local browser action.

## Optional Intelligence processing

Intelligence is optional and requires separately configured access. Its request contains short privacy-safe workflow fields: audit context/focus, evaluation context/goal, or fix context/focus with focus required.

Read [Agentagon Intelligence](intelligence.md) before enabling it.

## Retention and removal

Audit evidence remains in the application’s local state, and unsuccessful fix attempts remain in history. Task events and artifacts have bounded retention per trial; those limits are not a process disk quota.

Stop active work before removing an application’s evidence or worktrees. Removing evidence can make old reports, recovery, or verification unusable. Do not delete locks or trial records to bypass a blocked workflow. Copies already sent to a coding host, provider, remote runner, or published repository are subject to those systems’ retention policies.

For a security report, use the repository’s [security reporting instructions](../SECURITY.md). Do not post sensitive evidence in a public issue.
