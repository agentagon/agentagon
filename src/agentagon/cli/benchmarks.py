"""Internal CLI operations for dataset audit evidence and preparation handoff."""

from pathlib import Path

import click

from agentagon.core.records import load_json
from agentagon.experiments import benchmarks
from agentagon.storage.workspace import Workspace


def register(main: click.Group, output) -> None:
    @main.group("benchmark")
    def benchmark() -> None:
        """Record dataset audit drafts and connect ready drafts to evaluation validation."""

    @benchmark.command("draft")
    @click.option(
        "--assessment", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
    )
    @click.option("--audit", "audit_id")
    @click.option(
        "--new",
        is_flag=True,
        help="Explicitly create another draft even when the assessment and source match.",
    )
    @click.pass_obj
    @output
    def draft(path: Path, assessment: Path, audit_id: str | None, new: bool) -> dict:
        return benchmarks.draft(Workspace(path), load_json(assessment), audit_id=audit_id, new=new)

    @benchmark.command("status")
    @click.argument("benchmark_id")
    @click.option("--profile", "profile_name")
    @click.option("--budget", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.pass_obj
    @output
    def status(
        path: Path, benchmark_id: str, profile_name: str | None, budget: Path | None
    ) -> dict:
        return benchmarks.status(
            Workspace(path),
            benchmark_id,
            profile_name=profile_name,
            budget=load_json(budget) if budget else None,
        )

    @benchmark.command("prepare")
    @click.argument("benchmark_id")
    @click.option("--profile", "profile_name", required=True)
    @click.option(
        "--budget", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
    )
    @click.option("--author", required=True)
    @click.pass_obj
    @output
    def prepare(
        path: Path, benchmark_id: str, profile_name: str, budget: Path, author: str
    ) -> dict:
        return benchmarks.prepare(
            Workspace(path), benchmark_id, profile_name, load_json(budget), author=author
        )
