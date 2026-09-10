# Try a local example

See what Agentagon captures before using it on your application. This bundled example creates a tiny synthetic application, prepares an evidence packet, and saves a partial report. It needs no coding host, credentials, or network services after installation.

## Run the example

[Install the CLI](install.md), then run this from the Agentagon source checkout using the Python environment where it is installed:

```sh
python examples/local-audit/demo.py
```

If you used the native installer’s default runtime and your shell’s `python` has no Agentagon module, use its Python explicitly:

```sh
"$HOME/.local/share/agentagon/venv/bin/python" examples/local-audit/demo.py
```

For a custom installation prefix, use that runtime’s `bin/python` instead.

## Inspect the output

The example prints JSON with paths and IDs similar to these:

```json
{
  "workspace": "/tmp/agentagon-example-…/app",
  "audit_id": "audit_…",
  "pending_action": "evidence",
  "packet": "/tmp/agentagon-example-…/app/.agentagon/audits/…/packets/….json",
  "response_template": "/tmp/agentagon-example-…/app/.agentagon/audits/…/packets/….response.json",
  "report": "/tmp/agentagon-example-…/app/.agentagon/reports/…/report.md"
}
```

Use the actual printed paths; the values above are abbreviated examples.

1. Open `packet` to see the captured evidence and review stage.
2. Open `report` to see coverage and pending work.
3. Notice that `pending_action` is `evidence`. The coding agent has not reviewed the evidence yet.

!!! note "A prepared packet is not a completed audit"
    This example does not fabricate findings or claim the application passed. To obtain a real review, open the returned workspace in your coding host and ask `ag:audit` to resume its pending code audit.

## View it in the dashboard

Substitute the printed workspace path:

```sh
agentagon --workspace /printed/workspace/path dashboard
```

The dashboard shows the unfinished audit. Keep its server running while inspecting it, then stop it with Ctrl+C.

The temporary directory remains after the example exits. Preserve it if you want to continue; remove only that returned example directory when you are finished. The example uses its own settings file and does not change your application.

**Next:** [Run your first audit](first-audit.md) on an application you want to improve.
