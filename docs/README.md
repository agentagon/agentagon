---
title: Introduction
description: Inspect and improve AI agents with measured fixes and custom scores.
hide:
  - toc
---

<div class="ag-hero" markdown>

<span id="understand-your-agent-measure-the-improvement"></span>

<p class="ag-label">Agentagon documentation</p>

# Inspect your agent.<br><em>Measure the improvement.</em>

</div>

<div class="ag-intro" markdown>

Agentagon helps you **inspect and improve AI agents with measured fixes and custom scores**. Agree on behaviors and scoring, prepare reusable evals to establish a baseline, and compare independently verified improvements.

Open the [local web app](app.md) to connect your coding agent and trace providers, inspect behavior, prepare evaluations and compare fixes. Skills and CLI subcommands remain available.

</div>

[Install Agentagon](getting-started/install.md){ .md-button .md-button--primary }
[Already installed? Open the web app →](app.md)

For a seeded example using an actual model, follow the [ticket-retry demonstration](../examples/ticket-retry/README.md).

<span id="start-with-one-audit"></span>

## Your first run

1. **Install** Agentagon and run `agentagon` from your AI agent's project directory.
2. **Configure a coding agent in Settings** and optionally connect Braintrust, LangSmith or Langfuse for traces and datasets.
3. **Choose Audit, Eval or Fix**, then review evidence, questions and results in the browser.

<span id="before-you-begin"></span>

You need macOS or Linux with Python 3.12+. Agent-led tasks require an authenticated Codex CLI or the optional Claude Agent SDK with an Anthropic API key. Skill installation is optional. Discovery needs no evaluation suite, execution traces or Agentagon API key. Measurement needs a runnable application, clean committed inputs and agreed execution limits.

The [web app guide](app.md) covers project switching, connections and task resumption. The [skill quickstart](getting-started/first-audit.md) covers the optional coding-host workflow.

!!! tip "Get smarter suggestions with Agentagon Intelligence"
    Request an Agentagon API key for smarter suggestions to guide your audits, fixes, and evaluations. Email [hello@agentagon.ai](mailto:hello@agentagon.ai) to request access.

    Intelligence is optional. See [how it works](intelligence.md) for setup and data handling.

<span id="what-can-i-use-it-for"></span>

## Choose the task that fits

| You want to | Start with | What you get |
|---|---|---|
| Inspect agent behavior and find failures | [Audit](audit.md) | Evidence-backed findings from code and execution traces. |
| Improve saved goals or a named issue | [Fix](fix.md) | Bounded optimization, verified winner and alternatives, draft PR or local delivery. |
| Define behaviors, custom scores and evals | [Eval](eval.md) | Accepted expectations, scoring and limits, reviewed evaluations and reusable benchmarks. |
| Inspect history, rerun or manage settings | [Web app](app.md) | Project results, baselines, connections and managed coding-agent tasks. |

[Init](init.md) remains available through the optional skills. The existing [dashboard](dashboard.md) remains a checkout viewer with read-only defaults. [Setup](settings.md), independent review and [delivery](delivery.md) support these workflows. [Intelligence](intelligence.md) requires its own consent before outgoing requests unless you explicitly enable full access.

## What runs where?

Your **coding agent** reasons about your AI agent, authors candidate changes and supplies independent reviews. **Agentagon's Python engine** captures evidence, executes declared checks and retains results. The **local web app** manages explicitly started agent sessions and displays their work across registered projects. Closing the browser does not stop tasks; stopping the local service interrupts them until you resume.

Evaluations run locally or remotely. Candidate authoring stays in a coding-agent session, managed by the app or your optional host workflow. See [where work runs](execution.md) for runner boundaries and [how it works](concepts.md) for benchmarks, verification and selection.

Core workflows need no Agentagon account. Your coding host, benchmarks, and optional services may have their own access and costs. Evidence is saved locally; your host determines where model processing occurs. Anonymous product telemetry is enabled by default and can be disabled. See [privacy and data](privacy.md).
