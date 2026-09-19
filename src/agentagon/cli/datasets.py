"""Validated private dataset snapshot access for managed coding agents."""

import click

from agentagon.capabilities.evaluation import datasets
from agentagon.capabilities.traces import snapshots
from agentagon.storage.state import AppState
from agentagon.storage.workspace import Workspace


def register(main, output):
    @main.group("dataset")
    def dataset():
        """Inspect imported datasets or materialize private evaluation inputs."""

    @dataset.command("list")
    @click.pass_obj
    @output
    def list_datasets(path):
        return {
            "datasets": [
                s for s in snapshots.list_snapshots(Workspace(path)) if s["kind"] == "dataset"
            ]
        }

    @dataset.command("inspect")
    @click.argument("snapshot_id")
    @click.pass_obj
    @output
    def inspect(path, snapshot_id):
        workspace = Workspace(path)
        record = snapshots.load(workspace, snapshot_id)
        datasets.assert_development(workspace, record)
        return record

    @dataset.command("materialize")
    @click.argument("snapshot_id")
    @click.option("--evaluation", "evaluation_id", required=True)
    @click.pass_obj
    @output
    def materialize(path, snapshot_id, evaluation_id):
        return snapshots.materialize(Workspace(path), snapshot_id, evaluation_id)

    @dataset.command("materialize-final")
    @click.argument("split_id")
    @click.option("--run", "run_id", required=True)
    @click.option("--candidate", "candidate_id", required=True)
    @click.option("--suite-digest", "manifest_digest", required=True)
    @click.option("--source-revision", required=True)
    @click.option("--source-digest", required=True)
    @click.option("--correctness-evaluation", "correctness_evaluation_id", required=True)
    @click.option("--evaluation", "evaluation_id", required=True)
    @click.pass_obj
    @output
    def materialize_final(
        path,
        split_id,
        run_id,
        candidate_id,
        manifest_digest,
        source_revision,
        source_digest,
        correctness_evaluation_id,
        evaluation_id,
    ):
        """Claim one final use and prepare inputs for a separately reviewed final evaluator."""
        state = AppState()
        project_id = state.project_id(path)
        binding = {
            "run_id": run_id,
            "candidate_id": candidate_id,
            "manifest_digest": manifest_digest,
            "source_revision": source_revision,
            "source_digest": source_digest,
        }
        datasets.claim_final(
            state,
            project_id,
            split_id,
            binding,
            correctness_evaluation_id=correctness_evaluation_id,
        )
        return datasets.materialize_final(
            state,
            project_id,
            split_id,
            binding,
            evaluation_id,
            correctness_evaluation_id=correctness_evaluation_id,
        )
