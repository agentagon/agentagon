"""Finite native-host optimization commands over the existing execution pipeline."""

import click

from agentagon.core.records import AuditError
from agentagon.experiments import engine, optimize_run
from agentagon.experiments.optimizer import ENGINES, MetaHarnessConfig
from agentagon.storage.workspace import Workspace


def register(fix_group, output):
    @fix_group.command("optimize")
    @click.argument("run_id")
    @click.option("--host")
    @click.option("--model")
    @click.option("--intent", "intent_id")
    @click.option("--engine", "optimizer", type=click.Choice(ENGINES), default="omni")
    @click.option("--host-concurrency", type=click.IntRange(min=1), default=1)
    @click.option("--max-trials", type=click.IntRange(min=1))
    @click.option("--max-elapsed-seconds", type=click.IntRange(min=1))
    @click.option("--meta-harness-host")
    @click.option("--meta-harness-model")
    @click.pass_obj
    @output
    def optimize(
        path,
        run_id,
        host,
        model,
        intent_id,
        optimizer,
        host_concurrency,
        max_trials,
        max_elapsed_seconds,
        meta_harness_host,
        meta_harness_model,
    ):
        """Configure Omni once, then advance bounded work or expose pending host requests."""
        workspace = Workspace(path)
        if host or model:
            if not host or not model:
                raise AuditError("configure optimization with both actual --host and --model")
            optimize_run.configure(
                workspace,
                run_id,
                host=host,
                model=model,
                intent_id=intent_id,
                optimizer=optimizer,
                host_concurrency=host_concurrency,
                max_trials=max_trials,
                max_elapsed_seconds=max_elapsed_seconds,
                meta_harness=MetaHarnessConfig(host=meta_harness_host, model=meta_harness_model),
            )
        else:
            optimize_run.status(workspace, run_id)
        return optimize_run.advance(workspace, run_id)

    @fix_group.command("optimize-status")
    @click.argument("run_id")
    @click.pass_obj
    @output
    def status(path, run_id):
        return optimize_run.status(Workspace(path), run_id)

    @fix_group.command("select-best")
    @click.argument("run_id")
    @click.pass_obj
    @output
    def select_best(path, run_id):
        """Select the highest independently verified improvement under agreed scoring."""
        return engine.select_best(Workspace(path), run_id)
