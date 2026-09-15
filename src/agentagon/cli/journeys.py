"""Shared intent, baseline jobs, and native-host work commands."""

from pathlib import Path

import click

from agentagon.core.records import AuditError, load_json
from agentagon.experiments import baselines, journeys
from agentagon.experiments.host_bridge import HostBridge
from agentagon.storage.workspace import Workspace

FILE = click.Path(exists=True, dir_okay=False, path_type=Path)


def register(main, output):
    @main.group("journey")
    def journey_group():
        """Save accepted goals, scoring and budgets; export privacy-safe results."""

    @journey_group.command("save")
    @click.option("--file", "file_path", type=FILE, required=True)
    @click.option("--evaluation", "evaluation_id")
    @click.pass_obj
    @output
    def save(path, file_path, evaluation_id):
        return journeys.save(Workspace(path), load_json(file_path), evaluation_id)

    @journey_group.command("status")
    @click.argument("intent_id", required=False)
    @click.pass_obj
    @output
    def intent_status(path, intent_id):
        return journeys.status(Workspace(path), intent_id)

    @journey_group.command("invitation")
    @click.pass_obj
    @output
    def invitation(path):
        return journeys.invitation(Workspace(path))

    @journey_group.command("export")
    @click.option("--baseline", "baseline_id")
    @click.option("--run", "run_id")
    @click.pass_obj
    @output
    def export(path, baseline_id, run_id):
        from agentagon.reporting import export_journey_report

        if bool(baseline_id) == bool(run_id):
            raise AuditError("choose exactly one baseline or fix run")
        return export_journey_report(Workspace(path), baseline_id=baseline_id, run_id=run_id)

    @main.group("baseline")
    def baseline_group():
        """Create fresh measurements using a frozen evaluator."""

    @baseline_group.command("start")
    @click.option("--evaluation", "evaluation_id", required=True)
    @click.option("--intent", "intent_id")
    @click.option("--profile", "profile_name")
    @click.option("--request-id")
    @click.pass_obj
    @output
    def start(path, evaluation_id, intent_id, profile_name, request_id):
        return baselines.start(Workspace(path), evaluation_id, intent_id, profile_name, request_id)

    @baseline_group.command("rerun")
    @click.argument("baseline_id")
    @click.option("--request-id")
    @click.pass_obj
    @output
    def rerun(path, baseline_id, request_id):
        return baselines.rerun(Workspace(path), baseline_id, request_id)

    @baseline_group.command("run")
    @click.argument("baseline_id")
    @click.pass_obj
    @output
    def run(path, baseline_id):
        return baselines.advance(Workspace(path), baseline_id)

    @baseline_group.command("status")
    @click.argument("baseline_id", required=False)
    @click.pass_obj
    @output
    def baseline_status(path, baseline_id):
        workspace = Workspace(path)
        return (
            baselines.status(workspace, baseline_id)
            if baseline_id
            else {"baselines": baselines.list_baselines(workspace)}
        )

    @baseline_group.command("traces")
    @click.argument("baseline_id")
    @click.option("--file", "file_path", type=FILE, required=True)
    @click.pass_obj
    @output
    def traces(path, baseline_id, file_path):
        return baselines.attach_traces(Workspace(path), baseline_id, load_json(file_path))

    @baseline_group.command("import-traces")
    @click.argument("baseline_id")
    @click.option("--export-path", type=FILE, required=True)
    @click.option("--acquisition-file", type=FILE)
    @click.option("--source")
    @click.option("--project")
    @click.pass_obj
    @output
    def import_traces(path, baseline_id, export_path, acquisition_file, source, project):
        return baselines.import_traces(
            Workspace(path), baseline_id, export_path, acquisition_file, source, project
        )

    @baseline_group.command("score-traces")
    @click.argument("baseline_id")
    @click.pass_obj
    @output
    def score_traces(path, baseline_id):
        from agentagon.experiments import trace_scoring

        return trace_scoring.advance(Workspace(path), baseline_id)

    @main.group("host")
    def host_group():
        """Claim and complete durable work on the explicitly requested coding host."""

    @host_group.command("pending")
    @click.argument("run_id")
    @click.pass_obj
    @output
    def pending(path, run_id):
        return {"requests": HostBridge(Workspace(path), run_id).pending()}

    @host_group.command("start")
    @click.argument("run_id")
    @click.argument("request_id")
    @click.pass_obj
    @output
    def claim(path, run_id, request_id):
        return HostBridge(Workspace(path), run_id).start(request_id)

    @host_group.command("reply")
    @click.argument("run_id")
    @click.argument("request_id")
    @click.option("--file", "file_path", type=FILE, required=True)
    @click.option("--host", required=True)
    @click.option("--model", required=True)
    @click.option("--binding-digest", required=True)
    @click.pass_obj
    @output
    def reply(path, run_id, request_id, file_path, host, model, binding_digest):
        return HostBridge(Workspace(path), run_id).reply(
            request_id, load_json(file_path), host=host, model=model, binding_digest=binding_digest
        )

    @host_group.command("cancel")
    @click.argument("run_id")
    @click.argument("request_id")
    @click.option("--reason", required=True)
    @click.pass_obj
    @output
    def cancel(path, run_id, request_id, reason):
        return HostBridge(Workspace(path), run_id).cancel(request_id, reason=reason)
