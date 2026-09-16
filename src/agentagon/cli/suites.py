"""Freeze and execute the full retained measurement suite."""

from pathlib import Path

import click

from agentagon.core.records import load_json
from agentagon.experiments import suites
from agentagon.storage.workspace import Workspace


def register(main, output):
    @main.group("suite")
    def suite_group():
        """Verify one candidate against every accepted focus without merging evaluators."""

    @suite_group.command("bind")
    @click.option("--run", "run_id", required=True)
    @click.option("--finalist-count", type=click.IntRange(1, 10), default=3)
    @click.option(
        "--manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
    )
    @click.pass_obj
    @output
    def bind(path, run_id, manifest, finalist_count):
        return suites.bind(
            Workspace(path), run_id, load_json(manifest), finalist_count=finalist_count
        )

    @suite_group.command("run")
    @click.option("--run", "run_id", required=True)
    @click.option("--candidate", "candidate_id")
    @click.pass_obj
    @output
    def run(path, run_id, candidate_id):
        return suites.advance(Workspace(path), run_id, candidate_id=candidate_id)

    @suite_group.command("status")
    @click.option("--run", "run_id", required=True)
    @click.pass_obj
    @output
    def status(path, run_id):
        return suites.status(Workspace(path), run_id)
