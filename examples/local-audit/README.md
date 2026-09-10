# Local audit example

With Agentagon installed in your active Python environment, run from the repository root:

```sh
python examples/local-audit/demo.py
```

The example creates a synthetic application in a new temporary directory, initializes private
audit state, captures its source, prepares an evidence packet and writes a partial report.
It uses the installed CLI with an isolated configuration file. No coding host, model, provider
credentials or network services are needed, and it does not change this repository.

The printed JSON contains `workspace`, `audit_id`, `packet`, `response_template`, `report`, and
`pending_action: "evidence"`. Paths and the audit ID vary per run. Open the packet and partial
report to inspect the saved evidence. No findings or reviews are fabricated: the audit remains
pending until a coding agent reviews the evidence and submits its judgments.

To view it, substitute the printed workspace path:

```sh
agentagon --workspace /printed/workspace/path dashboard
```

The temporary directory remains available for inspection. Remove that printed example directory
when finished. See [review and audit](../../docs/audit.md) to continue with a coding host.
