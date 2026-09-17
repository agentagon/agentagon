import os
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ASSETS = Path(__file__).parents[1] / "src/agentagon/dashboard_assets"


class WorkspaceFixture:
    def __init__(self):
        self.mutations = []
        self.projects = [
            {
                "id": "project_alpha",
                "name": "Support platform",
                "path": "/projects/support",
                "branch": "main",
                "available": True,
            },
            {
                "id": "project_beta",
                "name": "Research tools",
                "path": "/projects/research",
                "branch": "experiments",
                "available": True,
            },
        ]
        self.agents = {
            "project_alpha": [
                {
                    "id": "agent_support",
                    "project_id": "project_alpha",
                    "name": "Support agent",
                    "responsibility": "Resolve customer questions with the correct tools.",
                    "status": "confirmed",
                    "code_scopes": ["src/support_agent.py"],
                    "shared_dependencies": ["src/tools"],
                    "trace_selector": {},
                    "revision": 2,
                },
                {
                    "id": "agent_suggested",
                    "project_id": "project_alpha",
                    "name": "Example agent",
                    "responsibility": "",
                    "status": "suggested",
                    "code_scopes": ["examples/example_agent.py"],
                    "shared_dependencies": [],
                    "trace_selector": {},
                    "revision": 1,
                },
            ],
            "project_beta": [
                {
                    "id": "agent_research",
                    "project_id": "project_beta",
                    "name": "Research agent",
                    "responsibility": "Find and summarize primary sources.",
                    "status": "confirmed",
                    "code_scopes": ["src/research.py"],
                    "shared_dependencies": [],
                    "trace_selector": {},
                    "revision": 1,
                }
            ],
        }
        self.goals = {
            "agent_support": [
                {
                    "id": "goal_correctness",
                    "project_id": "project_alpha",
                    "agent_id": "agent_support",
                    "name": "Task success and correctness",
                    "category": "correctness",
                    "objective": "Create exactly one correct ticket for each request.",
                    "ideal_behavior": "Retries never create duplicate tickets.",
                    "state": "active",
                    "measurement": {
                        "evaluation_id": "eval_support",
                        "baseline_id": "baseline_support",
                    },
                    "measurement_plan": {"state": "accepted", "revision": 3},
                    "readiness": {
                        "evaluation": {"ready": True, "reason": "Frozen evaluator available."},
                        "baseline": {"ready": True, "reason": "Verified baseline available."},
                    },
                }
            ],
            "agent_research": [],
        }
        self.tasks = {
            "project_alpha": [
                {
                    "id": "task_decision",
                    "project_id": "project_alpha",
                    "workflow": "fix",
                    "workflow_version": 1,
                    "agent_id": "agent_support",
                    "agent_name": "Support agent",
                    "goal_id": "goal_correctness",
                    "goal_name": "Task success and correctness",
                    "title": "Improve duplicate ticket handling",
                    "state": "needs_input",
                    "needs_attention": True,
                    "updated_at": "2026-09-17T12:00:00Z",
                    "conversation": [],
                    "events": [{"type": "progress", "text": "Compared two verified candidates."}],
                    "question": {
                        "id": "question_candidate",
                        "prompt": "Choose the candidate to prepare for delivery.",
                    },
                    "next_action": "Choose a verified candidate.",
                    "can_resume": False,
                    "can_cancel": True,
                    "result": None,
                },
                {
                    "id": "task_complete",
                    "project_id": "project_alpha",
                    "workflow": "baseline",
                    "workflow_version": 1,
                    "agent_id": "agent_support",
                    "agent_name": "Support agent",
                    "goal_id": "goal_correctness",
                    "goal_name": "Task success and correctness",
                    "title": "Measure current ticket behavior",
                    "state": "completed",
                    "needs_attention": False,
                    "updated_at": "2026-09-16T12:00:00Z",
                    "conversation": [],
                    "events": [{"type": "result", "text": "Baseline recorded."}],
                    "question": None,
                    "next_action": None,
                    "can_resume": False,
                    "can_cancel": False,
                    "result": {"baseline_id": "baseline_support"},
                },
            ],
            "project_beta": [],
        }
        self.connections = {
            "project_alpha": [
                {
                    "id": "connection_braintrust",
                    "project_id": "project_alpha",
                    "provider": "braintrust",
                    "name": "Production support",
                    "project": "remote_support",
                    "project_name": "Production support",
                    "status": "connected",
                    "last_checked_at": "2026-09-17T10:00:00Z",
                }
            ],
            "project_beta": [],
        }
        self.discovery = None

    @staticmethod
    def skills():
        values = [
            ("design-measurements", "design", "Design measurements", True),
            ("prepare-evaluation", "eval", "Prepare an evaluation", True),
            ("run-baseline", "baseline", "Run a baseline", True),
            ("improve-agent", "fix", "Improve an agent", True),
            ("audit-agent", "audit", "Audit an agent", False),
        ]
        return [
            {
                "id": identifier,
                "version": 1,
                "workflow": workflow,
                "name": name,
                "purpose": f"Run {name.lower()} with saved project evidence.",
                "requires_goal": requires_goal,
                "inputs": ["Agent", "Goal"] if requires_goal else ["Agent"],
                "outputs": ["Reviewable result"],
            }
            for identifier, workflow, name, requires_goal in values
        ]

    def route(self, route, request):
        parsed = urlsplit(request.url)
        path = parsed.path
        if not path.startswith("/api/"):
            name = "webapp.html" if path == "/" or "." not in Path(path).name else path[1:]
            asset = ASSETS / name
            if asset.is_file():
                content_types = {
                    ".html": "text/html",
                    ".js": "text/javascript",
                    ".css": "text/css",
                }
                route.fulfill(
                    path=str(asset),
                    content_type=content_types.get(asset.suffix, "application/octet-stream"),
                )
            else:
                route.fulfill(status=404, body="Missing asset")
            return
        if path.endswith("/events"):
            route.fulfill(status=200, content_type="text/event-stream", body="retry: 60000\n\n")
            return
        payload = request.post_data_json if request.post_data else None
        if request.method != "GET":
            self.mutations.append({"path": path, "method": request.method, "payload": payload})
        result = self.respond(path, parsed.query, request.method, payload)
        route.fulfill(json=result)

    def respond(self, path, query, method, payload):
        if path == "/api/session":
            return {"token": "test-session", "controls_enabled": True}
        if path == "/api/projects":
            return {"projects": self.projects, "selected_project_id": "project_alpha"}
        if path == "/api/skills":
            return {"skills": self.skills()}
        if path == "/api/connector-types":
            return {
                "connector_types": [
                    {
                        "id": name,
                        "name": display,
                        "capabilities": ["Traces", "Datasets"],
                        "default_endpoint": f"https://{name}.example.test",
                    }
                    for name, display in (
                        ("braintrust", "Braintrust"),
                        ("langsmith", "LangSmith"),
                        ("langfuse", "Langfuse"),
                    )
                ]
            }
        if path == "/api/assistants":
            return {
                "assistants": [
                    {
                        "id": "codex",
                        "name": "Codex",
                        "available": True,
                        "authenticated": True,
                    },
                    {"id": "claude", "name": "Claude", "available": False},
                ],
                "defaults": {
                    "default_agent": "codex",
                    "models": {"codex": "gpt-test", "claude": "claude-test"},
                    "concurrency": 1,
                },
            }
        parts = path.strip("/").split("/")
        project_id, resource = parts[2:4]
        if resource == "agents":
            records = self.agents[project_id]
            if len(parts) == 4:
                return {
                    "agents": records,
                    "confirmed": [item for item in records if item["status"] == "confirmed"],
                    "suggestions": [item for item in records if item["status"] != "confirmed"],
                }
            agent_id = parts[4]
            if len(parts) == 5:
                return next(item for item in records if item["id"] == agent_id)
            if parts[5] == "goals":
                if len(parts) == 6:
                    if method == "POST":
                        goal = {
                            "id": "goal_new",
                            "project_id": project_id,
                            "agent_id": agent_id,
                            "name": payload.get("name") or "Task success and correctness",
                            "category": payload["category"],
                            "objective": payload["objective"],
                            "ideal_behavior": payload.get("ideal_behavior"),
                            "state": "active",
                            "measurement": None,
                        }
                        self.goals.setdefault(agent_id, []).append(goal)
                        return goal
                    return {"goals": self.goals.get(agent_id, [])}
                return next(item for item in self.goals[agent_id] if item["id"] == parts[6])
            if parts[5] == "overview":
                return {
                    **self.overview(project_id),
                    "agent": next(item for item in records if item["id"] == agent_id),
                    "readiness": {},
                }
        if resource == "overview":
            return self.overview(project_id)
        if resource == "tasks":
            if len(parts) == 4:
                if method == "POST":
                    task = {
                        "id": "task_started",
                        "project_id": project_id,
                        "workflow": payload["workflow"],
                        "workflow_version": 1,
                        "agent_id": payload["agent_id"],
                        "agent_name": "Support agent",
                        "goal_id": payload.get("goal_id"),
                        "goal_name": "Task success and correctness",
                        "title": "New workflow task",
                        "state": "running",
                        "needs_attention": False,
                        "conversation": [],
                        "events": [],
                        "question": None,
                        "can_resume": False,
                        "can_cancel": True,
                    }
                    self.tasks[project_id].insert(0, task)
                    return {"id": task["id"]}
                filters = {key: values[0] for key, values in parse_qs(query).items()}
                values = self.tasks[project_id]
                for name, key in (
                    ("agent_id", "agent_id"),
                    ("workflow", "workflow"),
                    ("status", "state"),
                ):
                    if filters.get(name):
                        values = [item for item in values if item.get(key) == filters[name]]
                hidden = {
                    "conversation",
                    "events",
                    "question",
                    "result",
                    "can_resume",
                    "can_cancel",
                }
                return {
                    "tasks": [
                        {key: value for key, value in item.items() if key not in hidden}
                        for item in values
                    ],
                    "next_cursor": None,
                }
            task = next(item for item in self.tasks[project_id] if item["id"] == parts[4])
            if len(parts) == 6 and parts[5] == "reply":
                task.update(state="running", needs_attention=False, question=None)
            return task
        if resource == "workflows":
            params = {key: values[0] for key, values in parse_qs(query).items()}
            blockers = []
            if not params.get("agent_id"):
                blockers.append({"code": "agent", "message": "Select an agent.", "action": "agent"})
            if parts[4] != "audit" and not params.get("goal_id"):
                blockers.append({"code": "goal", "message": "Select a goal.", "action": "goal"})
            return {
                "workflow": parts[4],
                "workflow_version": 1,
                "ready": not blockers,
                "blockers": blockers,
                "next_action": "start" if not blockers else blockers[0]["action"],
            }
        if resource == "connectors":
            if len(parts) == 4:
                if method == "POST":
                    connection = {
                        "id": "connection_new",
                        "project_id": project_id,
                        "provider": self.discovery["provider"],
                        "name": "Selected project",
                        "project": payload["project"],
                        "status": "connected",
                    }
                    self.connections[project_id].append(connection)
                    return connection
                return {"connections": self.connections[project_id]}
            if parts[4] == "discover":
                self.discovery = payload
                return {
                    "discovery_id": "discovery_one",
                    "projects": [{"id": "remote_one", "name": "Selected project"}],
                }
            connection = next(
                item for item in self.connections[project_id] if item["id"] == parts[4]
            )
            if method == "DELETE":
                self.connections[project_id].remove(connection)
            return connection
        if resource == "settings":
            return {
                "settings": {},
                "profiles": {"local": {"runner": {"kind": "local"}}},
            }
        if resource == "application-agents" and parts[-1] == "discover":
            return {"agents": self.agents[project_id], "limitations": []}
        return {}

    def overview(self, project_id):
        return {
            "project": next(item for item in self.projects if item["id"] == project_id),
            "audits": [],
            "evaluations": [],
            "runs": [],
            "baselines": [{"baseline_id": "baseline_support", "state": "completed"}]
            if project_id == "project_alpha"
            else [],
            "issues": [],
            "jobs": [],
            "datasets": [{"id": "dataset_support", "name": "Support cases"}]
            if project_id == "project_alpha"
            else [],
            "traces": [{"id": "trace_support", "name": "Recent failures"}]
            if project_id == "project_alpha"
            else [],
            "settings": {
                "settings": {},
                "profiles": {"local": {"runner": {"kind": "local"}}},
            },
        }


@pytest.fixture
def webapp_page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as value:
        executable = os.environ.get("AGENTAGON_TEST_CHROMIUM")
        browser = value.chromium.launch(headless=True, executable_path=executable)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(8_000)
        fixture = WorkspaceFixture()
        page.route("**/*", fixture.route)
        page.goto("http://agentagon.test/")
        page.get_by_role("heading", name="Support platform").wait_for()
        yield page, fixture
        context.close()
        browser.close()


def test_named_agents_and_project_switch_are_scoped(webapp_page):
    page, _ = webapp_page
    sidebar = page.locator(".sidebar")
    assert sidebar.get_by_role("link", name="Support agent").count() == 1
    assert sidebar.get_by_role("link", name="Research agent").count() == 0
    sidebar.get_by_role("link", name="Tasks", exact=True).click()
    page.get_by_role("link", name="Improve duplicate ticket handling").click()
    page.get_by_role("complementary", name="Task details").wait_for()
    page.locator("#project-picker").select_option("project_beta")
    page.wait_for_url("**/projects/project_beta/home")
    sidebar.get_by_role("link", name="Research agent").wait_for()
    assert sidebar.get_by_role("link", name="Research agent").count() == 1
    assert sidebar.get_by_role("link", name="Support agent").count() == 0
    assert page.get_by_role("complementary", name="Task details").count() == 0


def test_agent_goal_workspace_uses_one_stage_rail(webapp_page):
    page, _ = webapp_page
    page.locator(".sidebar").get_by_role("link", name="Support agent").click()
    page.get_by_role("heading", name="Support agent").wait_for()
    assert page.get_by_role("button", name="Edit agent").count() == 1
    assert page.get_by_role("button", name="Add focus").count() == 0
    page.get_by_role("link", name="Task success and correctness").click()
    rail = page.get_by_role("navigation", name="Goal stages")
    rail.get_by_role("button").first.wait_for()
    assert rail.get_by_role("button").all_inner_texts() == [
        "✓\nDefine\nCOMPLETE",
        "✓\nMeasure\nCOMPLETE",
        "3\nImprove\nREADY",
        "4\nReview\nLOCKED",
    ]
    page.get_by_role("button", name="Measure").click()
    page.get_by_text("eval_support", exact=True).wait_for()
    assert page.get_by_text("baseline_support", exact=True).is_visible()


def test_skills_and_goal_actions_share_task_submission(webapp_page):
    page, fixture = webapp_page
    page.get_by_role("link", name="Skills").click()
    page.get_by_role("heading", name="Skills").wait_for()
    assert page.locator(".skill-card").count() == 5
    page.locator(".skill-card").filter(has_text="Design measurements").get_by_role(
        "button", name="Start"
    ).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("combobox").nth(0).select_option("agent_support")
    dialog.get_by_role("combobox").nth(1).select_option("goal_correctness")
    dialog.get_by_role("button", name="Start task").click()
    page.wait_for_timeout(100)
    assert any(item["path"] == "/api/projects/project_alpha/tasks" for item in fixture.mutations)
    page.wait_for_url("**/projects/project_alpha/tasks/task_started")
    request = next(
        item for item in fixture.mutations if item["path"] == "/api/projects/project_alpha/tasks"
    )
    assert request["payload"] == {
        "operation_id": request["payload"]["operation_id"],
        "workflow": "design",
        "agent_id": "agent_support",
        "goal_id": "goal_correctness",
        "options": {},
    }
    assert page.get_by_role("complementary", name="Task details").is_visible()


def test_task_history_has_stable_urls_and_bound_decisions(webapp_page):
    page, fixture = webapp_page
    page.goto("http://agentagon.test/projects/project_alpha/tasks/task_decision")
    panel = page.get_by_role("complementary", name="Task details")
    panel.get_by_text("Choose the candidate to prepare for delivery.").wait_for()
    panel.get_by_label("Response").fill("Use the safer verified candidate.")
    panel.get_by_role("button", name="Send response").click()
    page.wait_for_timeout(50)
    reply = fixture.mutations[-1]
    assert reply["path"] == "/api/projects/project_alpha/tasks/task_decision/reply"
    assert uuid.UUID(reply["payload"].pop("operation_id"))
    assert reply["payload"] == {
        "question_id": "question_candidate",
        "answer": {"text": "Use the safer verified candidate."},
    }


def test_task_approvals_send_an_explicit_decision(webapp_page):
    page, fixture = webapp_page
    task = fixture.tasks["project_alpha"][0]
    task["question"] = {
        "id": "question_approval",
        "kind": "approval",
        "text": "Approve the proposed command?",
    }
    page.goto("http://agentagon.test/projects/project_alpha/tasks/task_decision")
    panel = page.get_by_role("complementary", name="Task details")
    panel.get_by_role("button", name="Approve").click()
    page.wait_for_timeout(50)
    reply = fixture.mutations[-1]
    assert uuid.UUID(reply["payload"].pop("operation_id"))
    assert reply["payload"] == {
        "question_id": "question_approval",
        "answer": {"decision": "accept"},
    }


def test_connectors_are_project_resources(webapp_page):
    page, _ = webapp_page
    page.get_by_role("link", name="Connectors").click()
    page.get_by_role("heading", name="Connectors").wait_for()
    assert page.get_by_text("Production support", exact=True).is_visible()
    page.locator(".connector-card").filter(has_text="LangSmith").get_by_role(
        "button", name="Connect"
    ).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("API key").fill("secret-value")
    dialog.get_by_role("button", name="Find projects").click()
    dialog.get_by_label("Provider project").select_option("remote_one")
    dialog.get_by_role("button", name="Connect").click()
    dialog.wait_for(state="detached")


def test_assistant_settings_only_request_relevant_credentials(webapp_page):
    page, _ = webapp_page
    page.get_by_role("link", name="Settings").click()
    page.get_by_role("heading", name="Settings").wait_for()
    assert page.get_by_label("Claude API key").count() == 0
    page.get_by_label("Default coding assistant").select_option("claude")
    assert page.get_by_label("Claude API key").is_visible()
    page.get_by_label("Default coding assistant").select_option("codex")
    assert page.get_by_label("Claude API key").count() == 0


def test_mobile_navigation_and_task_panel_are_full_width(webapp_page):
    page, _ = webapp_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Open navigation").click()
    assert page.locator(".sidebar").evaluate("element => element.classList.contains('is-open')")
    page.get_by_role("link", name="Tasks", exact=True).click()
    page.get_by_role("link", name="Improve duplicate ticket handling").click()
    panel = page.get_by_role("complementary", name="Task details")
    assert panel.bounding_box()["width"] == pytest.approx(390, abs=1)


def test_new_user_sees_project_onboarding():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as value:
        executable = os.environ.get("AGENTAGON_TEST_CHROMIUM")
        browser = value.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page()
        page.set_default_timeout(8_000)
        fixture = WorkspaceFixture()
        fixture.projects = []
        page.route("**/*", fixture.route)
        page.goto("http://agentagon.test/")
        page.get_by_role("heading", name="Measure what matters. Improve what is proven.").wait_for()
        page.get_by_role("button", name="Add project").click()
        page.get_by_role("dialog").get_by_label("Project directory").fill("/projects/new")
        browser.close()
