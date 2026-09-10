"""Non-interactive JSON CLI for the customer's coding agent."""

import functools
import json
import sys
from pathlib import Path

import click

from agentagon import __version__
from agentagon.core.records import AuditError, encoded, load_json, resource_path
from agentagon.dashboard import serve
from agentagon.installation import SKILLS, install_plugins
from agentagon.lookup.client import lookup
from agentagon.operations import import_traces, prepare, start, status, submit
from agentagon.reporting import report
from agentagon.storage.config import Config
from agentagon.storage.issues import list_issues, update_issue
from agentagon.storage.workspace import Workspace
from agentagon.telemetry.selection import plan_acquisition


def output(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            click.echo(encoded(function(*args, **kwargs)))
        except (AuditError, OSError) as exc:
            message = (
                str(exc)
                if isinstance(exc, AuditError)
                else "local file operation failed; check paths and permissions"
            )
            click.echo(encoded({"state": "error", "error": message}), err=True)
            raise SystemExit(1) from exc

    return wrapped


@click.group()
@click.option("--workspace", type=click.Path(path_type=Path), default=Path("."), show_default=True)
@click.version_option(__version__)
@click.pass_context
def main(ctx: click.Context, workspace: Path) -> None:
    """Audit local code and traces. Model reasoning stays in your coding agent."""
    ctx.obj = workspace


@main.command("init")
@click.pass_obj
@output
def initialize(path: Path) -> dict:
    """Initialize private local state in the source directory."""
    workspace = Workspace(path)
    result = workspace.initialize()
    return {**result, **Config().summary(workspace.root)}


@main.command("setup")
@click.option("--scope", type=click.Choice(["user", "project"]), default="project")
@click.option("--set", "pairs", nargs=2, multiple=True, metavar="KEY VALUE")
@click.option("--unset", "unset", multiple=True, metavar="KEY")
@click.option("--profile", "profile_name", help="Named execution profile to save.")
@click.option("--profile-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
@output
def setup(
    path: Path,
    scope: str,
    pairs: tuple,
    unset: tuple[str, ...],
    profile_name: str | None,
    profile_file: Path | None,
) -> dict:
    """Show or configure user defaults and checkout overrides in one local file."""
    root = Workspace(path).root if scope == "project" else None
    config = Config()
    if profile_name is not None or profile_file is not None:
        if not profile_name or profile_file is None:
            raise AuditError("--profile and --profile-file must be supplied together")
        if pairs or unset:
            raise AuditError("profile setup cannot be combined with --set or --unset")
        return config.update_profile(scope, profile_name, load_json(profile_file), root)
    values = {}
    for key, value in pairs:
        if key in values or key in unset:
            raise AuditError("each setting may be changed only once per invocation")
        if key in {"intelligence.access_presented", "telemetry.enabled"}:
            if value not in {"true", "false"}:
                raise AuditError(f"{key} must be true or false")
            value = json.loads(value)
        values[key] = value
    if values or unset:
        return config.update(scope=scope, root=root, values=values, unset=unset)
    return config.summary(root)


@main.command("telemetry")
@click.argument("event")
@click.option("--data", required=True, help="JSON object of allowed event fields.")
@click.pass_obj
@output
def telemetry(path: Path, event: str, data: str) -> dict:
    """Record anonymous usage. Network failures never interrupt the workflow."""
    from agentagon.usage import track

    if len(data.encode("utf-8")) > 8192:
        return {"status": "invalid_event"}
    try:
        fields = json.loads(data)
    except ValueError:
        return {"status": "invalid_event"}
    if not isinstance(fields, dict) or "workspace" in fields:
        return {"status": "invalid_event"}
    if event != "skill_invoked":
        fields["workspace"] = path
    return track(event, **fields)


@main.command("status")
@click.option("--audit", "audit_id")
@click.option("--run", "run_id")
@click.option("--candidate", "candidate_id")
@click.pass_obj
@output
def show_status(
    path: Path, audit_id: str | None, run_id: str | None, candidate_id: str | None
) -> dict:
    """Show pending work, coverage, and artifact locations."""
    workspace = Workspace(path)
    if audit_id is not None and run_id is not None:
        raise AuditError("select an audit or a fix run, not both")
    if candidate_id is not None and run_id is None:
        raise AuditError("--candidate requires --run")
    if run_id is not None:
        from agentagon.experiments import engine

        return {
            **engine.status(workspace, run_id=run_id, candidate_id=candidate_id),
            **Config().summary(workspace.root),
        }
    result = {**status(workspace, audit_id), **Config().summary(workspace.root)}
    if audit_id is None:
        from agentagon.experiments import engine

        runs = engine.status(workspace)
        result.update(fix_runs=runs["runs"], latest_fix_run_id=runs["latest_run_id"])
    return result


@main.command("resources")
@output
def resources() -> dict:
    """Locate the bundled skill, signal catalog, and JSON contracts."""
    return {
        "skills": {name: str(resource_path(f"skills/{name}/SKILL.md")) for name in SKILLS},
        "catalog": str(resource_path("signals/audit-v1.json")),
        "contracts": str(resource_path("contracts/v1")),
    }


@main.command("install")
@click.option("--host", "hosts", type=click.Choice(["codex", "claude-code"]), multiple=True)
@output
def install(hosts: tuple[str, ...]) -> dict:
    """Install the ag plugin into selected or detected coding agents."""
    return install_plugins(list(hosts) or None)


@main.command("dashboard")
@click.argument("audit_id", required=False)
@click.option("--run", "run_id", help="Show a fix run instead of an audit.")
@click.option("--port", type=click.IntRange(0, 65535), default=0, show_default=True)
@click.option("--open/--no-open", "open_browser", default=True)
@click.option("--controls", is_flag=True, help="Enable authenticated controls for fix runs.")
@click.pass_obj
def dashboard(
    path: Path,
    audit_id: str | None,
    run_id: str | None,
    port: int,
    open_browser: bool,
    controls: bool = False,
) -> None:
    """Show this checkout's latest audit, or a selected audit, on localhost."""
    try:
        if audit_id is not None and run_id is not None:
            raise AuditError("select an audit or a fix run, not both")
        arguments = {"audit_id": audit_id, "port": port, "open_browser": open_browser}
        if run_id is not None:
            arguments["run_id"] = run_id
        if controls:
            arguments["controls"] = True
        serve(Workspace(path), **arguments)
    except (AuditError, OSError) as exc:
        message = str(exc) if isinstance(exc, AuditError) else "unable to start local dashboard"
        click.echo(encoded({"state": "error", "error": message}), err=True)
        raise SystemExit(1) from exc


@main.group("audit")
def audit_group() -> None:
    """Start, analyze, resume, and report a local audit."""


@audit_group.command("changes")
@click.option("--scope", "scopes", multiple=True, help="Limit changes to a checkout path.")
@click.pass_obj
@output
def audit_changes(path: Path, scopes: tuple[str, ...]) -> dict:
    """Inspect local changes without initializing state or exposing source bodies."""
    snapshot = Workspace(path).changes(list(scopes))
    return {
        "revision": snapshot["revision"],
        "scopes": snapshot["scopes"],
        "changed_files": len(snapshot["changes"]) + len(snapshot["skipped"]),
        "eligible_files": len(snapshot["changes"]),
        "skipped_files": len(snapshot["skipped"]),
        "changes": [
            {key: change[key] for key in ("path", "old_path", "new_path", "change_type")}
            for change in snapshot["changes"]
        ],
        "skipped": snapshot["skipped"],
    }


@audit_group.command("start")
@click.option("--mode", type=click.Choice(["traces", "code", "combined"]))
@click.option("--code-scope", type=click.Choice(["full", "changes"]), default="full")
@click.option("--goal", help="Investigation emphasis; every fixed rubric facet still applies.")
@click.option(
    "--source", type=click.Choice(["braintrust", "langfuse", "langsmith", "phoenix", "otlp"])
)
@click.option("--project")
@click.option("--from", "start_time")
@click.option("--to", "end_time")
@click.option("--limit", "limit_text", help="Positive trace count or 'all'; required for traces.")
@click.option(
    "--scope", "scopes", multiple=True, help="Code file/directory inside the checkout; repeatable."
)
@click.option("--host", default="unknown", show_default=True)
@click.option("--model", default="unknown", show_default=True)
@click.pass_obj
@output
def audit_start(
    path: Path,
    mode: str | None,
    code_scope: str,
    goal: str | None,
    source: str | None,
    project: str | None,
    start_time: str | None,
    end_time: str | None,
    limit_text: str | None,
    scopes: tuple[str, ...],
    host: str,
    model: str,
) -> dict:
    workspace = Workspace(path)
    settings = Config().effective(workspace.root)["traces"]
    mode = mode or (
        "combined" if code_scope == "full" and settings["state"] == "enabled" else "code"
    )
    if mode != "code":
        source = source or settings["source"]
        project = project or settings["project"]
    limit = None
    if limit_text is not None:
        if limit_text == "all":
            limit = "all"
        else:
            try:
                limit = int(limit_text)
            except ValueError as exc:
                raise AuditError("--limit must be a positive integer or all") from exc
    return start(
        workspace,
        mode=mode,
        source=source,
        project=project,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        scopes=list(scopes),
        host=host,
        model=model,
        goal=goal,
        code_scope=code_scope,
    )


@audit_group.command("plan")
@click.argument("audit_id")
@click.argument("inventory", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
@output
def audit_plan(path: Path, audit_id: str, inventory: Path) -> dict:
    """Estimate a trace download from a provider's root-only inventory."""
    return plan_acquisition(Workspace(path), audit_id, inventory)


@audit_group.command("import")
@click.argument("audit_id")
@click.argument("export_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--acquisition",
    type=click.Path(exists=True, path_type=Path),
    help="Provider acquisition receipt JSON.",
)
@click.pass_obj
@output
def audit_import(path: Path, audit_id: str, export_path: Path, acquisition: Path | None) -> dict:
    return import_traces(Workspace(path), audit_id, export_path, acquisition)


@audit_group.command("prepare")
@click.argument("audit_id")
@click.option("--stage", type=click.Choice(["evidence", "diagnosis", "clustering"]), required=True)
@click.option("--batch-size", default=5, type=int, show_default=True)
@click.option("--max-bytes", default=200000, type=int, show_default=True)
@click.pass_obj
@output
def audit_prepare(path: Path, audit_id: str, stage: str, batch_size: int, max_bytes: int) -> dict:
    return prepare(Workspace(path), audit_id, stage, batch_size=batch_size, max_bytes=max_bytes)


@audit_group.command("submit")
@click.argument("audit_id")
@click.argument("response", type=click.Path(exists=True, path_type=Path))
@click.pass_obj
@output
def audit_submit(path: Path, audit_id: str, response: Path) -> dict:
    return submit(Workspace(path), audit_id, response)


@audit_group.command("report")
@click.argument("audit_id")
@click.pass_obj
@output
def audit_report(path: Path, audit_id: str) -> dict:
    return report(Workspace(path), audit_id)


@audit_group.command("lookup")
@click.argument("audit_id")
@click.option("--context-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--focus-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--limit", default=5, type=click.IntRange(min=1))
@click.option("--phase", type=click.Choice(["initial", "follow_up"]), default="initial")
@click.option("--refresh", is_flag=True, help="Explicitly repeat a previously completed lookup.")
@click.pass_obj
@output
def audit_lookup(
    path: Path,
    audit_id: str,
    context_file: Path | None,
    focus_file: Path | None,
    limit: int,
    phase: str,
    refresh: bool,
) -> dict:
    """Retrieve guidance using abstract project context, specific asks, or both."""
    return _lookup_files(path, audit_id, "audit", context_file, focus_file, limit, phase, refresh)


def _lookup_files(
    path: Path,
    owner_id: str,
    workflow: str,
    context_file: Path | None,
    focus_file: Path | None,
    limit: int,
    phase: str,
    refresh: bool,
) -> dict:
    if context_file is None and focus_file is None:
        option = "--goal-file" if workflow == "eval" else "--focus-file"
        raise AuditError(f"supply --context-file, {option}, or both")
    fields = {}
    question_field = "goal" if workflow == "eval" else "focus"
    for field, source in (("context", context_file), (question_field, focus_file)):
        if source is None:
            continue
        if source.stat().st_size > 24 * 1024:
            raise AuditError(f"{field} file exceeds 24 KiB; supply a short abstract summary")
        try:
            fields[field] = source.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise AuditError(f"{field} file must be UTF-8 text") from exc
    return lookup(
        Workspace(path),
        owner_id,
        workflow=workflow,
        **fields,
        limit=limit,
        phase=phase,
        refresh=refresh,
    )


@audit_group.group("issues")
def issues_group() -> None:
    """Inspect persistent issues and record customer resolution decisions."""


@issues_group.command("list")
@click.pass_obj
@output
def issues_list(path: Path) -> dict:
    return {"issues": list_issues(Workspace(path))}


@issues_group.command("update")
@click.argument("issue_id")
@click.option(
    "--status",
    "new_status",
    type=click.Choice(
        ["open", "in_progress", "resolved_user", "resolved_verified", "dismissed", "reopened"]
    ),
    required=True,
)
@click.option("--reason", required=True)
@click.option("--evidence", multiple=True, help="Existing finding/evidence ID; repeatable.")
@click.option("--verification", type=click.Path(exists=True, path_type=Path))
@click.option("--run", "run_id", help="Engine run supporting a verified resolution.")
@click.option("--candidate", "candidate_id", help="Verified candidate in that run.")
@click.pass_obj
@output
def issues_update(
    path: Path,
    issue_id: str,
    new_status: str,
    reason: str,
    evidence: tuple[str, ...],
    verification: Path | None,
    run_id: str | None,
    candidate_id: str | None,
) -> dict:
    if (run_id is None) != (candidate_id is None):
        raise AuditError("--run and --candidate must be supplied together")
    if verification and (new_status == "resolved_verified" or run_id):
        raise AuditError(
            "manual verification receipts cannot verify resolution; use --run and --candidate"
        )
    return update_issue(
        Workspace(path),
        {"issue_id": issue_id, "status": new_status, "reason": reason, "evidence": list(evidence)},
        verification,
        run_id=run_id,
        candidate_id=candidate_id,
    )


@main.group("eval")
def eval_group() -> None:
    """Prepare, validate and independently review reusable evaluations."""


@eval_group.command("lookup")
@click.argument("evaluation_id")
@click.option("--context-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--goal-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--limit", default=5, type=click.IntRange(min=1))
@click.option("--phase", type=click.Choice(["initial", "follow_up"]), default="initial")
@click.option("--refresh", is_flag=True, help="Explicitly repeat a previously completed lookup.")
@click.pass_obj
@output
def eval_lookup(
    path: Path,
    evaluation_id: str,
    context_file: Path | None,
    goal_file: Path | None,
    limit: int,
    phase: str,
    refresh: bool,
) -> dict:
    """Retrieve optional guidance for preparing an evaluation."""
    return _lookup_files(
        path, evaluation_id, "eval", context_file, goal_file, limit, phase, refresh
    )


@eval_group.command("start")
@click.option("--profile", "profile_name", required=True)
@click.option(
    "--budget-file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--goal")
@click.option("--issue", "issue_ids", multiple=True)
@click.option("--author", required=True)
@click.option("--from", "from_id")
@click.option("--audit", "audit_id", help="Carry selected --issue evidence from this saved audit.")
@click.pass_obj
@output
def eval_start(
    path: Path,
    profile_name: str,
    budget_file: Path,
    goal: str | None,
    issue_ids: tuple[str, ...],
    author: str,
    from_id: str | None,
    audit_id: str | None,
) -> dict:
    from agentagon.experiments import preparation

    return preparation.start(
        Workspace(path),
        profile_name,
        load_json(budget_file),
        goal=goal,
        issue_ids=list(issue_ids),
        author=author,
        from_id=from_id,
        audit_id=audit_id,
    )


@eval_group.command("check")
@click.argument("evaluation_id")
@click.option(
    "--plan-file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.pass_obj
@output
def eval_check(path: Path, evaluation_id: str, plan_file: Path) -> dict:
    from agentagon.experiments import preparation

    return preparation.check(Workspace(path), evaluation_id, load_json(plan_file))


@eval_group.command("freeze")
@click.argument("evaluation_id")
@click.option(
    "--review-file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.pass_obj
@output
def eval_freeze(path: Path, evaluation_id: str, review_file: Path) -> dict:
    from agentagon.experiments import preparation

    return preparation.freeze(Workspace(path), evaluation_id, load_json(review_file))


@eval_group.command("status")
@click.argument("evaluation_id")
@click.pass_obj
@output
def eval_status(path: Path, evaluation_id: str) -> dict:
    from agentagon.experiments import preparation

    return preparation.status(Workspace(path), evaluation_id)


@main.group("fix")
def fix_group() -> None:
    """Explore measured changes and select a verified, reviewable branch."""


@fix_group.command("lookup")
@click.argument("run_id")
@click.option("--context-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--focus-file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--limit", default=5, type=click.IntRange(min=1))
@click.option("--phase", type=click.Choice(["initial", "follow_up"]), default="initial")
@click.option("--refresh", is_flag=True, help="Explicitly repeat a previously completed lookup.")
@click.pass_obj
@output
def fix_lookup(
    path: Path,
    run_id: str,
    context_file: Path | None,
    focus_file: Path | None,
    limit: int,
    phase: str,
    refresh: bool,
) -> dict:
    """Retrieve optional guidance for a measured fix run."""
    return _lookup_files(path, run_id, "fix", context_file, focus_file, limit, phase, refresh)


@fix_group.command("start")
@click.option(
    "--spec",
    "spec_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--evaluation", "evaluation_id", help="Reviewed frozen evaluation package.")
@click.option("--profile", "profile_name", required=True)
@click.option("--goal")
@click.option("--issue", "issue_ids", multiple=True)
@click.pass_obj
@output
def fix_start(
    path: Path,
    spec_path: Path | None,
    evaluation_id: str | None,
    profile_name: str,
    goal: str | None,
    issue_ids: tuple[str, ...],
) -> dict:
    from agentagon.experiments import engine, preparation

    workspace = Workspace(path)
    if spec_path and evaluation_id:
        raise AuditError("choose either --spec or --evaluation")
    if not spec_path and not evaluation_id:
        return {
            "state": "needs_evaluation",
            "goal": goal,
            "issue_ids": list(issue_ids),
            "next_action": "Use ag:eval to inspect existing benchmarks and declare a preparation budget, then eval start/check/freeze.",
        }
    specification = (
        preparation.fix_spec(workspace, evaluation_id) if evaluation_id else load_json(spec_path)
    )
    if evaluation_id and (
        goal is not None
        and goal != specification["goal"]
        or issue_ids
        and set(issue_ids) != set(specification["issue_ids"])
    ):
        raise AuditError("a packaged evaluation retains its reviewed goal and saved issues")

    return engine.start(
        workspace, specification, profile_name, goal=goal, issue_ids=list(issue_ids)
    )


@fix_group.command("next")
@click.argument("run_id")
@click.option("--host-id")
@click.pass_obj
@output
def fix_next(path: Path, run_id: str, host_id: str | None) -> dict:
    from agentagon.experiments import orchestration

    return orchestration.next_packet(Workspace(path), run_id, host_id)


@fix_group.command("bind")
@click.argument("run_id")
@click.option("--session-id", required=True)
@click.option("--host", type=click.Choice(["codex", "claude-code"]), required=True)
@click.option("--pause", is_flag=True, help="Pause continuation while waiting for user input.")
@click.pass_obj
@output
def fix_bind(path: Path, run_id: str, session_id: str, host: str, pause: bool) -> dict:
    from agentagon.experiments import hooks

    return hooks.bind(Workspace(path), run_id, session_id, host, pause=pause)


@fix_group.command("hook", hidden=True)
def fix_hook() -> None:
    """Read a bounded native lifecycle event from stdin; return only hook-protocol JSON."""
    from agentagon.experiments import hooks

    raw = sys.stdin.buffer.read(1048577)
    try:
        result = hooks.handle(json.loads(raw)) if len(raw) <= 1048576 else {}
    except (ValueError, UnicodeError):
        result = {}
    click.echo(encoded(result))


@fix_group.command("round")
@click.argument("run_id")
@click.option(
    "--briefs", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.pass_obj
@output
def fix_round(path: Path, run_id: str, briefs: Path) -> dict:
    from agentagon.experiments import orchestration

    return orchestration.reserve_round(Workspace(path), run_id, load_json(briefs))


@fix_group.command("assign")
@click.argument("run_id")
@click.argument("candidate_id")
@click.option("--host-id", required=True)
@click.option("--agent-id", required=True)
@click.pass_obj
@output
def fix_assign(path: Path, run_id: str, candidate_id: str, host_id: str, agent_id: str) -> dict:
    from agentagon.experiments import orchestration

    return orchestration.assign(Workspace(path), run_id, candidate_id, host_id, agent_id)


@fix_group.command("inspect")
@click.argument("run_id")
@click.argument("candidate_id")
@click.pass_obj
@output
def fix_inspect(path: Path, run_id: str, candidate_id: str) -> dict:
    from agentagon.experiments import inspection

    return inspection.candidate(Workspace(path), run_id, candidate_id)


@fix_group.command("ideate")
@click.argument("run_id")
@click.option(
    "--response-file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.pass_obj
@output
def fix_ideate(path: Path, run_id: str, response_file: Path) -> dict:
    from agentagon.experiments import orchestration

    return orchestration.ideate(Workspace(path), run_id, load_json(response_file))


@fix_group.command("new")
@click.argument("run_id")
@click.option("--parent", "parent_id")
@click.option("--hypothesis", required=True)
@click.option("--author", default="unknown", show_default=True)
@click.option("--round", "round_id")
@click.option("--operation-id", help="Reuse a candidate reservation after interruption.")
@click.pass_obj
@output
def fix_new(
    path: Path,
    run_id: str,
    parent_id: str | None,
    hypothesis: str,
    author: str,
    round_id: str | None,
    operation_id: str | None,
) -> dict:
    from agentagon.experiments import engine

    return engine.new(
        Workspace(path),
        run_id,
        parent_id=parent_id,
        hypothesis=hypothesis,
        author=author,
        round_id=round_id,
        operation_id=operation_id,
    )


@fix_group.command("run")
@click.argument("run_id")
@click.argument("candidate_id", required=False)
@click.option("--review-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--continue", "continue_run", is_flag=True, help="Continue a stopped run with explicit limits."
)
@click.option("--limits-file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
@output
def fix_run(
    path: Path,
    run_id: str,
    candidate_id: str | None,
    review_file: Path | None,
    continue_run: bool,
    limits_file: Path | None,
) -> dict:
    if limits_file and not continue_run:
        raise AuditError("--limits-file requires --continue")
    from agentagon.experiments import engine

    return engine.run(
        Workspace(path),
        run_id,
        candidate_id=candidate_id,
        review=load_json(review_file) if review_file else None,
        continue_run=continue_run,
        limits=load_json(limits_file) if limits_file else None,
    )


@fix_group.command("select")
@click.argument("run_id")
@click.argument("candidate_id")
@click.pass_obj
@output
def fix_select(path: Path, run_id: str, candidate_id: str) -> dict:
    from agentagon.experiments import engine

    return engine.select(Workspace(path), run_id, candidate_id)


@fix_group.command("stop")
@click.argument("run_id")
@click.pass_obj
@output
def fix_stop(path: Path, run_id: str) -> dict:
    from agentagon.experiments import engine

    return engine.stop(Workspace(path), run_id)


@fix_group.command("steer")
@click.argument("run_id")
@click.option(
    "--control-file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.pass_obj
@output
def fix_steer(path: Path, run_id: str, control_file: Path) -> dict:
    """Submit an idempotent control, acknowledge host work, or record scan insights."""
    from agentagon.experiments.controls import submit as submit_control

    return submit_control(Workspace(path), run_id, load_json(control_file))


@fix_group.command("cleanup")
@click.argument("run_id")
@click.option("--operation-id")
@click.option("--author")
@click.option(
    "--finish",
    "candidate_id",
    help="Compare a measured and independently reviewed cleanup with its original winner.",
)
@click.pass_obj
@output
def fix_cleanup(
    path: Path, run_id: str, operation_id: str | None, author: str | None, candidate_id: str | None
) -> dict:
    from agentagon.experiments import cleanup

    if candidate_id:
        if operation_id or author:
            raise AuditError("--finish cannot also reserve a new cleanup")
        return cleanup.finish(Workspace(path), run_id, candidate_id)
    if not operation_id or not author:
        raise AuditError("cleanup requires --operation-id and --author, or --finish CANDIDATE_ID")
    return cleanup.start(Workspace(path), run_id, operation_id, author)


@fix_group.command("ship")
@click.argument("run_id")
@click.option("--candidate", "candidate_id", help="Must match the user's selected candidate.")
@click.option("--remote", default="origin", show_default=True)
@click.option("--base", help="Target GitHub branch; defaults to the configured remote default.")
@click.option(
    "--publish", is_flag=True, help="Authorize pushing the selected branch and creating a draft PR."
)
@click.pass_obj
@output
def fix_ship(
    path: Path,
    run_id: str,
    candidate_id: str | None,
    remote: str,
    base: str | None,
    publish: bool,
) -> dict:
    """Prepare selected-branch delivery; publish a draft PR only with --publish."""
    from agentagon.experiments.delivery import ship

    return ship(Workspace(path), run_id, candidate_id, remote=remote, base=base, publish=publish)
