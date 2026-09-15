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

[Inspect agent behavior](audit.md), [define scores and evals](init.md), or use [Fix](fix.md) to compare verified improvements and prepare a draft PR.

</div>

[Install Agentagon](getting-started/install.md){ .md-button .md-button--primary }
[Already installed? Run the quickstart →](getting-started/first-audit.md)

For a seeded example using an actual model, follow the [ticket-retry demonstration](../examples/ticket-retry/README.md).

<span id="start-with-one-audit"></span>

## Your first run

1. **Install** the CLI and plugin for your coding host.
2. **Open your AI agent’s code repo** in Codex or Claude Code and run **ag:init**.
3. **Agree on behaviors, scoring and limits**, then review the evaluator and measured baseline.

<span id="before-you-begin"></span>

You need macOS or Linux with Python 3.12+ and a working coding-agent session. Discovery needs no evaluation suite, execution traces, or Agentagon API key. Measurement needs a runnable application, clean committed inputs and agreed execution limits.

The [quickstart](getting-started/first-audit.md) shows what to enter and how to recognize a completed result.

!!! tip "Get smarter suggestions with Agentagon Intelligence"
    Request an Agentagon API key for smarter suggestions to guide your audits, fixes, and evaluations. Email [hello@agentagon.ai](mailto:hello@agentagon.ai) to request access.

    Intelligence is optional. See [how it works](intelligence.md) for setup and data handling.

<span id="what-can-i-use-it-for"></span>

## Choose the task that fits

| You want to | Start with | What you get |
|---|---|---|
| Inspect agent behavior and find failures | [Audit](audit.md) | Evidence-backed findings from code and execution traces. |
| Improve saved goals or a named issue | [Fix](fix.md) | Bounded optimization, verified winner and alternatives, draft PR or local delivery. |
| Define behaviors, custom scores and evals | [Init](init.md) | Accepted behaviors, scoring and limits, reviewed evals and a baseline. |
| Inspect history, rerun or manage settings | [Dashboard](dashboard.md) | Baselines, benchmark and recent-trace results, comparisons and explicit controls. |

[Eval](eval.md) is independently callable for creating, repairing or validating evaluations. [Setup](settings.md), independent review and [delivery](delivery.md) support these workflows. [Intelligence](intelligence.md) requires its own consent before outgoing requests unless you explicitly enable full access.

## What runs where?

Your **coding agent** reasons about your AI agent, authors candidate changes, and supplies independent reviews. The **Agentagon CLI** captures evidence, executes declared checks, and retains results. The **dashboard** lets you inspect that saved work.

Evaluations run locally or remotely. Candidate authoring stays in your coding host. See [where work runs](execution.md) for the boundaries and [how it works](concepts.md) for benchmarks, verification, and selection.

Core workflows need no Agentagon account. Your coding host, benchmarks, and optional services may have their own access and costs. Evidence is saved locally; your host determines where model processing occurs. Anonymous product telemetry is enabled by default and can be disabled. See [privacy and data](privacy.md).
