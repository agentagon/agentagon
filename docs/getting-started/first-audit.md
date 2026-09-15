---
description: Agree on goals, prepare evals and establish your first agent baseline.
hide:
  - toc
---

# Quick start {#your-first-audit}

Inspect your agent, agree on what better means, and establish a scored baseline.

<span id="1-open-your-application"></span>
<span id="2-start-a-code-only-audit"></span>

1. **[Install Agentagon](install.md)** once.
2. **Open your AI agent’s repo** in a new Codex or Claude Code session.
3. **Run ag:init**

<div class="ag-quickstart" markdown>

=== "Codex"

    Select **ag:init** from the skill picker, then paste:

    ```text
    Inspect this agent and its existing evals. Propose behaviors, scoring
    and an execution budget, then establish a baseline using the settings
    I approve. Skip optional Intelligence.
    ```

=== "Claude Code"

    Paste this into Claude Code:

    ```text
    /ag:init Inspect this agent and its existing evals. Propose behaviors, scoring
    and an execution budget, then establish a baseline using the settings
    I approve. Skip optional Intelligence.
    ```

</div>

<span id="3-let-the-audit-finish"></span>
<span id="4-read-the-report"></span>
<span id="5-open-the-dashboard"></span>
<span id="what-should-i-do-next"></span>

**Your result:** accepted behaviors, scoring and limits, reviewed evals and a scored baseline when execution is ready. Use Fix to compare improvements against that baseline. Missing prerequisites are reported without losing the assessment.

[Define behaviors and scores](../init.md) · [Improve the baseline](../fix.md) · [Inspect results](../dashboard.md) · [Troubleshooting](../troubleshooting.md)
