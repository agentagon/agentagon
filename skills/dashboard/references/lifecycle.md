# Dashboard lifecycle

Init, Audit, Eval and Fix open the dashboard at start or resume, before substantive work. Standalone Dashboard requests use the same flow. Honor an explicit request to skip, close or stop the dashboard for the current session. Setup alone does not open one; when nested inside a journey, preserve that journey's existing dashboard and selection.

Use the application's original source directory or Git checkout, including when evaluation or fix work happens in isolated candidate worktrees. Reuse a running server and browser tab already owned by this host session for the same resolved checkout and controls mode. Retain the process handle, startup URL, checkout and tab handle across internal stages. Confirm that a known process still responds before reuse; restart it if it exited. Do not scan ports or attach to an unrelated session.

If no matching server is running, launch `agentagon --workspace CHECKOUT dashboard --no-open` with the host's persistent background-process tool. Wait for startup JSON with `state: "serving"`, retain the handle and open the returned URL with the host's browser tool. If the browser tool is unavailable, launch without `--no-open` so the CLI tries the default browser. If it cannot open a browser, present the URL. If serving or persistent execution is unavailable, report that briefly and continue the workflow; do not leave it waiting on a foreground server. When the CLI is missing, follow the Agentagon [prerequisites](../../audit/references/setup.md) before retrying.

Navigate the retained tab using the returned origin and these query parameters. Replace prior selection parameters, URL-encode IDs and never guess the port:

| Workflow | Before an ID exists | Active record |
|---|---|---|
| Audit or changes-only audit | `?view=audit` | `?audit=AUDIT_ID` |
| Evaluation preparation | `?view=eval` | `?evaluation=EVALUATION_ID` |
| Fix or delivery | `?view=fix` | `?run=RUN_ID` |
| Standalone Dashboard | Latest record at startup | Explicit requested selection |

Select the exact ID when a workflow creates or resumes a record, and refresh after saved milestones. Opening an empty checkout must work before initialization without creating state. Standalone requests may launch with `dashboard AUDIT_ID --no-open` or `dashboard --run RUN_ID --no-open`; do not combine those selectors. Evaluations use the URL selector. Verify the intended checkout and record are displayed, include the link in the workflow result, and reuse the server rather than creating another tab.
