import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from agentagon.cli.main import main
from agentagon.core.records import catalog, load_json
from agentagon.reporting import build_report
from agentagon.storage.config import Config

WINDOW = ["--from", "2026-08-10T00:00:00Z", "--to", "2026-08-11T00:00:00Z", "--limit", "5"]


def invoke(root, *arguments):
    result = CliRunner().invoke(main, ["--workspace", str(root), *arguments])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_settings_select_combined_audit_and_explicit_inputs_override(workspace):
    invoke(
        workspace.root,
        "setup",
        "--scope",
        "user",
        "--set",
        "traces.source",
        "braintrust",
        "--set",
        "traces.project",
        "user-project",
    )
    invoke(
        workspace.root,
        "setup",
        "--set",
        "traces.project",
        "checkout-project",
        "--set",
        "traces.state",
        "enabled",
    )
    selected = invoke(workspace.root, "audit", "start", *WINDOW)
    audit = workspace.read_audit(selected["audit_id"])
    assert (audit["mode"], audit["source"], audit["project"]) == (
        "combined",
        "braintrust",
        "checkout-project",
    )
    assert audit["code_units"]
    assert selected["pending_action"] == "import"

    explicit = invoke(
        workspace.root,
        "audit",
        "start",
        "--mode",
        "traces",
        "--source",
        "langsmith",
        "--project",
        "invocation-project",
        *WINDOW,
    )
    audit = workspace.read_audit(explicit["audit_id"])
    assert (audit["mode"], audit["source"], audit["project"]) == (
        "traces",
        "langsmith",
        "invocation-project",
    )
    assert audit["code_units"] == []
    code = invoke(workspace.root, "audit", "start", "--mode", "code")
    assert code["mode"] == "code"
    assert code["pending_action"] == "evidence"
    assert workspace.read_audit(code["audit_id"])["source"] is None
    assert Config().effective(workspace.root)["traces"]["project"] == "checkout-project"


@pytest.mark.parametrize("state", ["unset", "disabled"])
def test_no_trace_connection_defaults_to_code_and_explicit_traces_remain_available(
    workspace, state
):
    invoke(workspace.root, "setup", "--set", "traces.state", state)
    assert invoke(workspace.root, "audit", "start")["mode"] == "code"
    explicit = invoke(
        workspace.root,
        "audit",
        "start",
        "--mode",
        "traces",
        "--source",
        "braintrust",
        "--project",
        "requested-project",
        *WINDOW,
    )
    assert explicit["mode"] == "traces"


def test_user_setup_works_outside_git_without_creating_checkout_state(tmp_path):
    directory = tmp_path / "not-a-checkout"
    directory.mkdir()
    result = invoke(
        directory,
        "setup",
        "--scope",
        "user",
        "--set",
        "intelligence.endpoint",
        "https://guidance.example",
        "--set",
        "intelligence.access_presented",
        "true",
    )
    assert result["workspace"] is None
    assert Path(result["config_path"]).is_file()
    assert not result["intelligence"]["onboarding_pending"]
    assert not list(directory.iterdir())
    assert invoke(directory, "setup", "--scope", "user")["settings"] == result["settings"]


def test_first_run_onboarding_is_suppressed_after_setup_and_reinitialization(workspace):
    first = invoke(workspace.root, "init")
    assert first["intelligence"]["onboarding_pending"]
    assert "hello@agentagon.ai" in first["intelligence"]["access_message"]
    assert first["traces"]["onboarding_pending"]
    invoke(
        workspace.root,
        "setup",
        "--scope",
        "user",
        "--set",
        "intelligence.access_presented",
        "true",
    )
    invoke(workspace.root, "setup", "--set", "traces.state", "disabled")
    repeated = invoke(workspace.root, "init")
    assert not repeated["intelligence"]["onboarding_pending"]
    assert not repeated["traces"]["onboarding_pending"]
    assert not invoke(workspace.root, "status")["traces"]["onboarding_pending"]
    assert not (workspace.state / "config.json").exists()
    assert set(load_json(workspace.state / "workspace.json")) == {"contract_version", "created_at"}


def test_goal_survives_cli_workflow_and_does_not_allow_skipping_rubric_facets(workspace):
    goal = "Investigate latency around the weather tool"
    started = invoke(workspace.root, "audit", "start", "--goal", f"  {goal}  ")
    audit_id = started["audit_id"]
    assert started["goal"] == goal
    assert invoke(workspace.root, "status", "--audit", audit_id)["goal"] == goal
    prepared = invoke(workspace.root, "audit", "prepare", audit_id, "--stage", "evidence")
    packet = load_json(Path(prepared["packet"]))
    assert prepared["goal"] == packet["goal"] == goal
    expected = {facet["id"] for facet in catalog()["code"]}
    template_path = Path(prepared["response_template"])
    template = load_json(template_path)
    assert all(
        {judgment["facet"] for judgment in item["judgments"]} == expected
        for item in template["items"]
    )
    template["items"][0]["judgments"].pop()
    template_path.write_text(json.dumps(template), encoding="utf-8")
    rejected = CliRunner().invoke(
        main,
        ["--workspace", str(workspace.root), "audit", "submit", audit_id, str(template_path)],
    )
    assert rejected.exit_code == 1
    assert "every required subject/facet" in json.loads(rejected.output)["error"]
    generated = invoke(workspace.root, "audit", "report", audit_id)
    assert generated["goal"] == goal
    assert load_json(Path(generated["json_report"]))["goal"] == goal
    assert goal in Path(generated["report"]).read_text()


def test_build_report_is_read_only_even_when_a_report_already_exists(workspace, imported):
    invoke(workspace.root, "audit", "report", imported)

    def files():
        return {
            str(path.relative_to(workspace.root)): (path.stat().st_mtime_ns, path.read_bytes())
            for path in workspace.root.rglob("*")
            if path.is_file()
        }

    before = files()
    built = build_report(workspace, imported)
    assert built["audit_id"] == imported
    assert built["coverage"]["selected_traces"] == 1
    assert files() == before


def test_resources_expose_all_bundled_skills(workspace):
    result = invoke(workspace.root, "resources")
    assert set(result["skills"]) == {"audit", "review", "setup", "dashboard", "fix", "ship", "eval"}
    for name, path in result["skills"].items():
        text = Path(path).read_text()
        assert f"name: {name}" in text
    assert Path(result["catalog"]).is_file()
    assert Path(result["contracts"], "selection.json").is_file()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["setup", "--set", "traces.source", "invalid"], "traces.source must be one of"),
        (["setup", "--set", "intelligence.api_key", "secret"], "unsupported setting"),
        (
            ["setup", "--set", "traces.project", "one", "--set", "traces.project", "two"],
            "only once",
        ),
        (["audit", "start", "--goal", " "], "audit goal must be nonblank"),
        (["audit", "start", "--mode", "traces"], "trace audits require"),
        (
            ["audit", "start", "--mode", "code", "--project", "demo"],
            "code-only audits do not accept",
        ),
    ],
)
def test_cli_settings_and_audit_errors_are_actionable_json(workspace, arguments, message):
    result = CliRunner().invoke(main, ["--workspace", str(workspace.root), *arguments])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["state"] == "error"
    assert message in payload["error"]


def test_old_init_onboarding_option_is_not_an_alias(workspace):
    result = CliRunner().invoke(
        main, ["--workspace", str(workspace.root), "init", "--acknowledge-access"]
    )
    assert result.exit_code == 2
    assert "No such option" in result.output
