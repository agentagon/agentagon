# Explore the dashboard

The dashboard gives you a local browser view of an application’s saved audits, evaluations, and fix experiments. Use it to inspect evidence and compare results while your coding agent works.

**You need:** Agentagon installed and a local application directory. The dashboard can open before initialization and shows an empty state until work is saved. Follow the [quickstart](getting-started/first-audit.md) to create an audit, or [run the local example](getting-started/local-example.md) to preview an unfinished one.

## Opens with your workflow

**ag:audit**, **ag:review**, **ag:eval**, **ag:fix**, **ag:ship**, and checkout-scoped **ag:setup** automatically open the dashboard when they start or resume. The coding host keeps its server running in the background, reuses the same session's server and tab for that application, and selects the active audit, evaluation or fix run once its ID is available. Nested skills reuse that dashboard too. User-wide setup without an application directory has no dashboard to open.

The host refreshes the page at workflow milestones; use **Refresh** to inspect saved progress in between. You can explicitly ask to skip or close the dashboard for the session. If the host cannot open a browser, it provides the local URL. If it cannot keep a dashboard server running, it reports that limitation and continues the workflow. Direct CLI commands retain their explicit dashboard launch behavior.

## Open the latest audit

Choose **ag:dashboard** in Codex or run `/ag:dashboard` in Claude Code. It opens the latest audit in your application’s dashboard. Keep the dashboard server running while you use it.

## Open a specific result

Ask your coding agent to open the audit, review, or fix run you want using **ag:dashboard**. Include its ID if you have one, or use the dashboard’s audit selector to browse history.

??? details "Open from a terminal"

    From the application directory:

    ```sh
    agentagon dashboard
    ```

    For a specific audit or fix run, replace the uppercase ID with the one returned by the workflow:

    ```sh
    agentagon dashboard AUDIT_ID --no-open
    agentagon dashboard --run RUN_ID --no-open
    ```

    Use either an audit ID or a fix run, not both. `--no-open` prints the URL without launching a browser. Open that exact URL. When outside the application, prefix the command with `agentagon --workspace /path/to/application`. Keep the terminal open and stop the server with Ctrl+C when finished.

## What you can inspect

| View | What to look for |
|---|---|
| Audits and history | Completion state, scope, coverage, findings, and persistent issues. |
| Evaluations | Preparation state, benchmark coverage, and recorded evidence. |
| Experiment tree | Baseline, candidates, parent relationships, and retained failed attempts. |
| Objective comparisons | Measured tradeoffs, checks, constraints, and variation. |
| Candidate details | Sealed diff, independent review, and execution logs. |
| Task diagnostics | Recorded task inputs, outputs, failures, and retained artifact downloads. |

An empty workspace displays an empty state. It does not automatically start an audit. An unfinished report stays unfinished until the coding host completes the pending work.

## Enable fix controls deliberately

Ask your coding agent to open **ag:dashboard** for your fix run with controls enabled.

Controls can change search policy, queue directives or expansion, stop or continue work, select a candidate, and invalidate or exhaust approaches. They use the same validated operations as the CLI.

Controls use an exact-origin session. Open the URL printed by that server and do not reuse a credential from an earlier dashboard process. See [run controls](reference/fix.md#steer-a-run-without-losing-queued-work) for operation states and retry behavior.

## If the page is empty or stale

Check that the server is still running, that you opened the correct checkout, and that you selected the expected audit or run. Inspect completion state before assuming a missing result is a UI failure. [Troubleshooting](troubleshooting.md) covers the common recovery paths.

**Next:** [Read reports and issues](reports.md) to interpret findings, or [select and deliver a fix](delivery.md) after comparing verified candidates.
