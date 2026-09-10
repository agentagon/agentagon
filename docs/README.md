---
title: Introduction
description: Audit, evaluate, and improve AI agents with evidence-backed findings and measured comparisons.
hide:
  - toc
---

<div class="ag-hero" markdown>

<span id="understand-your-agent-measure-the-improvement"></span>

<p class="ag-label">Agentagon documentation</p>

# Inspect your agent.<br><em>Measure the improvement.</em>

</div>

<div class="ag-intro" markdown>

Agentagon helps you **audit, evaluate, and improve AI agents**. Investigate failures, build benchmarks, and compare fixes against a recorded baseline.

Start with a code-only audit to investigate a concern, or [bring one known failure](getting-started/bring-one-failure.md) through evaluation and repair. An audit produces a saved report with evidence-backed findings and concrete recommendations for what to test next.

</div>

[Install Agentagon](getting-started/install.md){ .md-button .md-button--primary }
[Already installed? Run the quickstart →](getting-started/first-audit.md)

For a seeded example using an actual model, follow the [ticket-retry demonstration](../examples/ticket-retry/README.md).

<span id="start-with-one-audit"></span>

## Your first run

1. **Install** the CLI and plugin for your coding host.
2. **Open your AI agent’s code repo** in Codex or Claude Code and request a code-only audit.
3. **Read the report** and open the local dashboard to inspect findings and coverage.

<span id="before-you-begin"></span>

You need macOS or Linux with Python 3.12+ and a working coding-agent session. A first audit needs no evaluation suite, execution traces, or Agentagon API key.

The [quickstart](getting-started/first-audit.md) shows what to enter and how to recognize a completed result.

!!! tip "Get smarter suggestions with Agentagon Intelligence"
    Request an Agentagon API key for smarter suggestions to guide your audits, fixes, and evaluations. Email [hello@agentagon.ai](mailto:hello@agentagon.ai) to request access.

    Intelligence is optional. See [how it works](intelligence.md) for setup and data handling.

<span id="what-can-i-use-it-for"></span>

## Choose the task that fits

| You want to | Start with | What you get |
|---|---|---|
| Investigate an AI agent's behavior | [Audit your AI agent](audit.md) | Findings from source, supplied traces, or both, with saved issue history. |
| Check local changes before committing | [Review your changes](review.md) | Findings and suggested regression cases tied to the captured diff. |
| Turn a concern into a benchmark | [Prepare an evaluation](eval.md) | A reviewed, frozen evaluation package. |
| Compare possible improvements | [Measure candidate fixes](fix.md) | Isolated candidates measured against the same benchmark. |
| Deliver a chosen result | [Select and deliver a fix](delivery.md) | A reviewable branch and delivery package; publication is a separate choice. |
| Inspect saved progress | [Explore the dashboard](dashboard.md) | Findings, comparisons, diffs, and task diagnostics. |

An audit is one starting point. If you already have a goal and benchmark, you can start a measured fix directly.

## What runs where?

Your **coding agent** reasons about your AI agent, authors candidate changes, and supplies independent reviews. The **Agentagon CLI** captures evidence, executes declared checks, and retains results. The **dashboard** lets you inspect that saved work.

Evaluations run locally or remotely. Candidate authoring stays in your coding host. See [where work runs](execution.md) for the boundaries and [how it works](concepts.md) for benchmarks, verification, and selection.

Core workflows need no Agentagon account. Your coding host, benchmarks, and optional services may have their own access and costs. Evidence is saved locally; your host determines where model processing occurs. Anonymous product telemetry is enabled by default and can be disabled. See [privacy and data](privacy.md).
