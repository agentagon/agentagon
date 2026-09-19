"""Public local application and MCP entry points."""

from pathlib import Path

import click

from agentagon import __version__
from agentagon.cli.internal import main as internal


@click.group(invoke_without_command=True)
@click.option("--workspace", type=click.Path(path_type=Path), default=Path("."))
@click.version_option(__version__)
@click.pass_context
def main(ctx, workspace):
    """Open Agentagon. Use MCP to trigger workflows from a coding agent."""
    ctx.obj = workspace
    if ctx.invoked_subcommand is None:
        from agentagon.workflows.service_host import open_dashboard

        open_dashboard(workspace)


@main.command()
@click.option("--port", type=click.IntRange(0, 65535), default=0)
@click.pass_obj
def serve(workspace, port):
    """Run the shared local service in the foreground."""
    from agentagon.workflows.service_host import launch

    launch(workspace, port=port, open_browser=False)


@main.command()
def mcp():
    """Serve the local Model Context Protocol interface over stdio."""
    from agentagon.mcp.server import create_mcp

    create_mcp().run(transport="stdio")


main.add_command(internal, "_internal")
