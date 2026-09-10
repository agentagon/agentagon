# Audit your application

Use **ag:audit** to investigate your current application code, supplied execution traces, or both. You receive findings tied to evidence, improvement and evaluation recommendations, and saved issue history.

For a guided first run, follow [Your first audit](getting-started/first-audit.md). If you want feedback only on an uncommitted diff, use [ag:review](review.md).

## Install

[Install the CLI and native plugin](getting-started/install.md) for Codex or Claude Code. Full audits accept a local directory with or without Git, with or without commits, and with or without local edits. You do not need to commit your work to audit it.

## First audit and setup

Open the application in your coding host. Choose **ag:audit** in Codex, or invoke `/ag:audit` in Claude Code.

Use this example as a starting point, and change the focus to match what you want audited:

```text
Audit this application in code-only mode. Focus on how tool errors,
invalid inputs, and missing context affect the final answer.
Complete the report and suggest concrete evaluation cases.
```

Agentagon initializes private local state in `.agentagon/`. When Git is present, it excludes that directory through Git’s local exclude file, without changing tracked `.gitignore`.

On first use it may offer optional Intelligence access and ask whether to connect traces. You can decline both and continue. [Settings](settings.md) explains how to change those choices later.

## Scope and goal

Choose an evidence mode:

<div id="audit-evidence-modes" markdown="1">

| Mode | What the audit inspects | What you provide |
|---|---|---|
| `code` | Current selected source files | Application directory and optional path filters. |
| `traces` | Recorded executions | Supported exports or provider access, project, date range, and trace count. |
| `combined` | Current source plus recorded executions | Both, with any known source/revision limitations. |

</div>

An explicit mode wins. Otherwise, an audit uses combined mode when this checkout has traces enabled, and code-only mode when it does not.

A goal such as “reduce latency” changes emphasis. It does not remove required review coverage or suppress severe findings outside that goal. To narrow the inspected code, name specific paths in your request.

For example:

```text
Audit src/agent and tests in code-only mode. Investigate redundant tool
calls and missing failure handling. Treat latency improvements as
hypotheses until they are measured.
```

The direct CLI accepts repeated scopes, relative to the application root:

```sh
agentagon audit start --code-scope full --mode code --scope src/agent --scope tests
```

This creates audit state. Use the coding host to complete the evidence, diagnosis, and reporting stages; the CLI does not perform model reasoning on its own.

## What the audit does

1. Captures selected source and imports the chosen trace evidence.
2. Prepares bounded evidence packets for the coding agent to review.
3. Validates the submitted findings and their evidence references.
4. Diagnoses and groups related findings into issues.
5. Writes Markdown and JSON reports with coverage and next actions.

A report can include defects, improvement opportunities, and evaluation-coverage recommendations. Those recommendations propose work; the audit does not implement or execute new evaluations, run your application, or instrument production.

## Add execution traces

Use [Connect execution traces](traces.md) to choose a provider or local export and an explicit time window and count. The host retrieves provider data; the CLI plans selection and imports it. Existing local exports need no provider connection.

Code and traces can describe different revisions. With uncommitted code or no Git revision, reports retain alignment limitations. Even a clean-checkout match is an assumption about provenance. Contradicting trace metadata prevents unsupported claims that a particular code path caused a recorded outcome.

## Review, resume and inspect

The skill opens the [dashboard](dashboard.md) automatically for the active audit or review. Inspect its progress, findings, evidence, and issue history. Use **ag:dashboard** in Codex or `/ag:dashboard` in Claude Code to reopen it or inspect an earlier audit.

If a session stops, ask the host to resume the same audit with unchanged inputs and goal. It continues from saved progress. If the captured files, goal, or selected trace inputs change, start a new audit. Historical reports remain readable.

An untouched response template leaves work pending. `pending_action` tells you what still needs the host’s attention. See [Read reports and issues](reports.md) and [Troubleshooting](troubleshooting.md).

## Coverage and data handling

- Binary files, credential files, symlinks, and code files over 2 MB are skipped with coverage limits. Large files are not silently treated as reviewed.
- Full audits split code into 100-line units. Partial work and unknown judgments cannot establish a clean application.
- Trace counts describe the selected and reviewed corpus, not estimated production prevalence. Keep trace count and packet size appropriate to the job.
- Recognized secrets are redacted, but you must still choose data permitted for your coding host to inspect. Local evidence storage does not imply local model inference.

Read [Privacy and data](privacy.md) before supplying sensitive runtime evidence. For direct record handling, see the [CLI reference](reference/cli.md).

**Next:** [Read the report](reports.md), then [prepare an evaluation](eval.md) for the concern you want to measure.
