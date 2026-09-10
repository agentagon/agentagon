# Coordinate work in the active host task

`ag:fix` coordinates native authors, independent reviewers, scan workers and hypothesis authors. Durable reservations let a task resume without duplicating candidates or execution spending. There is no independent supervisor: when the host is unavailable, work waits for it.

Start with a [serial local fix run](fix.md) before adding parallel authors. More simultaneous candidates do not imply that benchmark trials have independent compute capacity; concurrent measurements on shared hardware can distort comparisons.

## Configure a future run

Use `ag:setup` to save explicit orchestration settings in a complete execution profile. For example, request two candidate authors with two host slots while keeping benchmark trials serial. Choose branch depth and resource limits appropriate to the host before starting the run. A profile change does not alter an active run.

The JSON below illustrates the available orchestration fields. These are choices for your workload, not limits that Agentagon silently enables:

```json
{
  "orchestration": {
    "round_width": 3,
    "host_capacity": 3,
    "branch_depth": 2,
    "resource_slots": 3,
    "scan_workers": 2,
    "ideation_passes": 1
  }
}
```

Add these settings to a saved execution profile before starting a run. New runs default to one author, one resource slot, depth one, one scan worker and one ideation pass. `spec.resources.slots` declares slots required per benchmark execution and defaults to one for scheduling. The existing parallel trial setting still limits measurements; shared local or SSH execution requires explicit independent capacity before parallel trials are allowed. Profile edits apply to future runs.

## Inspect and resume

Record the run ID returned by `ag:fix`. To continue after a host interruption, open the same application with the same configuration and ask the coding agent to inspect the run and resume its recorded reservations. Do not create new candidates merely because existing author or reviewer work is pending.

Read `fix next RUN_ID` for a work packet, reserve with `fix round RUN_ID --briefs FILE`, then dispatch the returned identities. `fix assign` records the native agent for resumption. `fix inspect` returns the sealed diff, review, trial logs, task evidence and lessons without executing anything remotely. See the [host role and round contract](../skills/fix/references/roles.md) for request fields, depth expansion, scans and continuation hooks.

The experiment tree, objective selectors, task failures, logs and artifact downloads are available in the local dashboard. It remains read-only by default; `--controls` enables the existing authenticated controls. Queued host work is separate from execution and independent review. Neither a dashboard read nor a session-start hook launches work.

Historical runs remain readable under their saved contracts. `fix next` reports manual operations for runs created without orchestration settings; reading them does not add defaults or rewrite frozen profiles.

Lifecycle behavior follows the native [Codex hooks](https://learn.chatgpt.com/docs/hooks) and [Claude Code hooks](https://code.claude.com/docs/en/hooks#stop) contracts. Native hook trust remains under user control. Unsupported or unobserved callbacks are reported as manual resume, not unattended support.
