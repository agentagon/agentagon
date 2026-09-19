"""Internal fix stages for checked patches and delivery of every reviewed source."""

from pathlib import Path

import click

from agentagon.capabilities.experiments import delivery, patches
from agentagon.core.records import load_json
from agentagon.storage.workspace import Workspace


def register(fix_group: click.Group, output) -> None:
    @fix_group.group("patch")
    def patch_group():
        """Prepare an independently reviewed patch when baseline measurement is unavailable."""

    @patch_group.command("start")
    @click.option(
        "--plan",
        "plan_file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        required=True,
    )
    @click.option("--goal", required=True)
    @click.option("--author", required=True)
    @click.option("--reason-no-comparison", required=True)
    @click.pass_obj
    @output
    def start(path, plan_file, goal, author, reason_no_comparison):
        return patches.start(
            Workspace(path),
            load_json(plan_file),
            goal=goal,
            author=author,
            reason_no_comparison=reason_no_comparison,
        )

    @patch_group.command("check")
    @click.argument("patch_id")
    @click.pass_obj
    @output
    def check(path, patch_id):
        """Seal the current patch and execute its bounded check plan."""
        return patches.check(Workspace(path), patch_id)

    @patch_group.command("review")
    @click.argument("patch_id")
    @click.option(
        "--review",
        "review_file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        required=True,
    )
    @click.pass_obj
    @output
    def review(path, patch_id, review_file):
        return patches.review(Workspace(path), patch_id, load_json(review_file))

    @patch_group.command("status")
    @click.argument("patch_id")
    @click.pass_obj
    @output
    def status(path, patch_id):
        return patches.status(Workspace(path), patch_id)

    @fix_group.command("deliver")
    @click.option("--run", "run_id")
    @click.option("--evaluation", "evaluation_id")
    @click.option("--patch", "patch_id")
    @click.option("--remote", default="origin", show_default=True)
    @click.option("--base")
    @click.option("--eval-parent", "eval_parent_id")
    @click.option(
        "--publish",
        is_flag=True,
        help="Authorize pushing this reviewed branch and creating a draft PR.",
    )
    @click.pass_obj
    @output
    def deliver(path, run_id, evaluation_id, patch_id, remote, base, publish, eval_parent_id):
        """Prepare local delivery from one reviewed source; optionally publish a draft PR."""
        return delivery.deliver(
            Workspace(path),
            run_id=run_id,
            evaluation_id=evaluation_id,
            patch_id=patch_id,
            remote=remote,
            base=base,
            publish=publish,
            eval_parent_id=eval_parent_id,
        )
