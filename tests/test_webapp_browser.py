"""Browser coverage of project scoping, agent decisions and guided imports.

These tests exercise the browser contract and real local service with fake
adapters, without launching an external coding agent or contacting a provider.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="install the browser extra")
ASSETS = Path(__file__).parents[1] / "src" / "agentagon" / "dashboard_assets"


class AppFixture:
    def __init__(self):
        self.mutations = []
        self.projects = [
            {
                "id": "project_alpha",
                "name": "Support agent",
                "path": "/apps/support",
                "branch": "main",
                "active_jobs": 0,
            },
            {
                "id": "project_beta",
                "name": "Research agent",
                "path": "/apps/research",
                "branch": "main",
                "active_jobs": 0,
            },
        ]
        self.jobs = {item["id"]: [] for item in self.projects}
        self.application_agents = {
            "project_alpha": [
                {
                    "id": "agent_support",
                    "name": "Support triage",
                    "description": "Resolve customer support requests.",
                    "status": "confirmed",
                    "code_scopes": ["src/support"],
                }
            ],
            "project_beta": [
                {
                    "id": "agent_research",
                    "name": "Research assistant",
                    "status": "confirmed",
                    "code_scopes": ["src/research"],
                }
            ],
        }
        self.focuses = {
            "agent_support": [
                {
                    "id": "focus_correctness",
                    "name": "Task correctness",
                    "category": "correctness",
                    "goal": "Answer support requests correctly",
                    "state": "active",
                }
            ],
            "agent_research": [
                {
                    "id": "focus_research",
                    "name": "Grounded research",
                    "category": "grounding",
                    "goal": "Use supported research claims",
                    "state": "active",
                }
            ],
        }
        self.readiness = {key: {"ready": True} for key in ("evaluation", "baseline", "fix")}
        self.metrics = {"metrics": [], "guardrails": []}
        self.traces = []
        self.connections = []
        self.runs = []
        self.evaluations = []
        self.baselines = []
        self.datasets = []
        self.issues = []
        self.designs = {}
        self.native_evaluators = []
        self.frozen_evaluators = []
        self.agent_settings = {"default_agent": "codex", "models": {}, "concurrency": 1}
        self.agents = [{"id": "codex", "name": "Codex", "available": True}]
        self.profile = {
            "runner": {"kind": "local"},
            "limits": {"max_trials": 24, "max_elapsed_seconds": 1800},
        }

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
        if not path.startswith("/api/"):
            asset = ASSETS / ("webapp.html" if path == "/" else path.lstrip("/"))
            content_types = {
                ".html": "text/html",
                ".js": "application/javascript",
                ".css": "text/css",
                ".png": "image/png",
                ".webp": "image/webp",
            }
            if asset.is_file():
                route.fulfill(
                    path=str(asset),
                    content_type=content_types.get(asset.suffix, "application/octet-stream"),
                )
            else:
                route.fulfill(status=404, body="Missing asset")
            return
        payload = request.post_data_json if request.post_data else None
        if request.method != "GET":
            self.mutations.append(
                {
                    "path": path,
                    "method": request.method,
                    "payload": payload,
                    "token": request.headers.get("x-agentagon-token"),
                }
            )
        result = self.respond(path, request.method, payload)
        route.fulfill(json=result)

    def respond(self, path, method, payload):
        if path == "/api/session":
            return {"token": "browser-test-session"}
        if path == "/api/projects":
            if method == "POST":
                project = {
                    "id": "project_new",
                    "name": "New project",
                    "path": payload["path"],
                    "active_jobs": 0,
                }
                self.projects.append(project)
                self.jobs[project["id"]] = []
                return project
            return {"projects": self.projects, "selected_project_id": "project_alpha"}
        if path == "/api/agents/codex/models":
            return {
                "models": [
                    {
                        "id": "test-model",
                        "name": "GPT test",
                        "description": "Available OpenAI test model",
                        "default": True,
                    },
                    {"id": "codex-saved-model", "name": "Saved GPT", "default": False},
                ],
                "default_model": "test-model",
            }
        if path == "/api/agents":
            return {
                "agents": self.agents,
                "settings": self.agent_settings,
            }
        if path == "/api/connections":
            if method == "POST":
                connection = {key: value for key, value in payload.items() if key != "credentials"}
                connection["id"] = payload.get("id", "connection_one")
                self.connections = [
                    item for item in self.connections if item["id"] != connection["id"]
                ] + [connection]
                return connection
            return {"connections": self.connections}
        if path.endswith("/datasets"):
            return {"datasets": [{"id": "dataset_remote", "name": "Support examples"}]}
        if path.endswith("/test"):
            self.connections[0].update(
                status="connected",
                last_checked_at="2026-09-16T12:00:00Z",
                projects=[
                    {"id": "remote-a", "name": "Production support"},
                    {"id": "remote-b", "name": "Staging support"},
                ],
            )
            return self.connections[0]
        parts = path.split("/")
        project_id = parts[3] if len(parts) > 3 else None
        if "/application-agents" in path:
            agents = self.application_agents.setdefault(project_id, [])
            if path.endswith("/discover"):
                suggestion = {
                    "id": "agent_suggested",
                    "name": "Suggested router",
                    "status": "suggested",
                    "code_scopes": ["src/router"],
                    "evidence": ["Found router entrypoint"],
                    "confidence": 0.7,
                }
                agents.append(suggestion)
                return {
                    "agents": [suggestion],
                    "limitations": ["Static discovery may miss dynamic agents."],
                }
            if path.endswith("/application-agents"):
                if method == "POST":
                    value = {"id": "agent_manual", **payload}
                    agents.append(value)
                    self.focuses[value["id"]] = []
                    return value
                return {"agents": agents}
            agent_id = parts[5]
            if "/design" in path:
                focus_id = parts[7]
                draft = self.designs.get((agent_id, focus_id))
                if method == "POST":
                    assert payload["expected_revision"] == (draft["revision"] if draft else 0)
                    if path.endswith("/accept"):
                        draft.update(state="accepted", revision=draft["revision"] + 1)
                    else:
                        draft = {
                            "id": "design_one",
                            "revision": payload["expected_revision"] + 1,
                            "state": "draft",
                            **{
                                key: value
                                for key, value in payload.items()
                                if key != "expected_revision"
                            },
                        }
                        self.designs[agent_id, focus_id] = draft
                    return draft
                return {
                    "focus": next(
                        item for item in self.focuses[agent_id] if item["id"] == focus_id
                    ),
                    "draft": draft,
                    "evaluators": self.native_evaluators,
                    "frozen_evaluators": self.frozen_evaluators,
                    "snapshots": self.datasets,
                }
            if path.endswith("/focuses"):
                if method == "POST":
                    value = {"id": "focus_new", "state": "active", **payload}
                    self.focuses.setdefault(agent_id, []).insert(0, value)
                    return value
                return {"focuses": self.focuses.get(agent_id, [])}
            if path.endswith("/measurement"):
                focus = next(item for item in self.focuses[agent_id] if item["id"] == parts[7])
                focus["measurement"] = payload
                return focus
            if path.endswith("/metrics"):
                return self.metrics
            if method == "POST":
                value = next(item for item in agents if item["id"] == agent_id)
                value.update(payload)
                self.focuses.setdefault(agent_id, [])
                return value
        if path.endswith("/overview"):
            return {
                "project": next(item for item in self.projects if item["id"] == project_id),
                "audits": [],
                "evaluations": self.evaluations,
                "runs": self.runs,
                "baselines": self.baselines,
                "issues": self.issues,
                "datasets": self.datasets,
                "traces": self.traces,
                "focuses": self.focuses.get(parts[5], []) if "/application-agents/" in path else [],
                "readiness": self.readiness,
                "jobs": [
                    {
                        "application_agent_id": "agent_support"
                        if project_id == "project_alpha"
                        else "agent_research",
                        "focus_id": "focus_correctness",
                        **job,
                    }
                    for job in self.jobs[project_id]
                ],
                "settings": {"profiles": {"local": self.profile}},
            }
        if path.endswith("/jobs"):
            if method == "POST":
                job = {
                    "id": "job_one",
                    "project_id": project_id,
                    "kind": payload["kind"],
                    "goal": payload["goal"],
                    "agent": payload["agent"],
                    "application_agent_id": payload.get("application_agent_id"),
                    "focus_id": payload.get("focus_id"),
                    "state": "running",
                    "events": [{"type": "progress", "text": "Inspecting application source."}],
                }
                self.jobs[project_id].insert(0, job)
                return job
            return {"jobs": self.jobs[project_id]}
        if "/jobs/" in path:
            job = next(item for item in self.jobs[project_id] if item["id"] == parts[5])
            if path.endswith("/reply"):
                assert payload["question_id"] == job["question"]["id"]
                job.pop("question")
                job["state"] = "running"
            if path.endswith("/resume"):
                job["state"] = "running"
            return job
        if path.endswith("/imports/preview"):
            return {
                "preview_id": "preview_one",
                "items": [
                    {
                        "input": "What is the refund policy?",
                        "output": "<script>window.compromised = true</script>",
                    }
                ],
                "provenance": {"provider": "braintrust", "dataset_id": "dataset_remote"},
                "completeness": {"state": "partial"},
            }
        if path.endswith("/imports"):
            return {"id": "snapshot_one", "count": 1, "state": "draft"}
        if "/results/fix/" in path:
            return self.runs[0]
        if "/results/eval/" in path:
            return next(item for item in self.evaluations if item["evaluation_id"] == parts[-1])
        if "/candidates/" in path:
            return {
                "candidate": self.runs[0]["candidates"][1],
                "diff": {"text": "+ validate the tool name\n", "truncated": False},
                "review": {
                    "verdict": "pass",
                    "rationale": "The change preserves required behavior.",
                },
                "trials": [
                    {
                        "trial_id": "trial_one",
                        "state": "passed",
                        "commands": [
                            {
                                "id": "benchmark",
                                "exit_code": 0,
                                "stdout": "All expected cases passed.",
                                "stderr": "",
                            }
                        ],
                    }
                ],
            }
        if path.endswith("/control"):
            self.runs[0]["selected_branch"] = "agentagon/fix-result"
            self.runs[0]["selected_candidate_id"] = payload["candidate_id"]
            return {"state": "applied"}
        if path.endswith("/deliveries"):
            return {
                "state": "published" if payload["publish"] else "prepared",
                "branch": "agentagon/fix-result",
                "delivery_id": "delivery_exact",
                "pr": {"url": "https://github.com/example/support/pull/42"}
                if payload["publish"]
                else None,
                "summary": "Independent review and required gates passed.",
                "artifact_urls": {
                    "diff": "/api/projects/project_alpha/deliveries/delivery_one/artifacts/diff"
                },
            }
        return {}


@pytest.fixture
def webapp_page():
    fixture = AppFixture()
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=os.environ.get("AGENTAGON_TEST_CHROMIUM"))
        page = browser.new_page(viewport={"width": 1450, "height": 1000})
        # EventSource remains local to the mock contract; tests can inject updates.
        page.add_init_script(
            "window.EventSource = class { constructor(url) { this.url = url; this.listeners = {}; window.sources = [...(window.sources || []), this]; } addEventListener(name, fn) { this.listeners[name] = fn; } close() { this.closed = true; } };"
        )
        page.route("**/*", fixture.route)
        yield page, fixture
        browser.close()


def test_project_switch_and_guided_audit_are_scoped(webapp_page):
    page, fixture = webapp_page
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=overview")
    playwright.expect(
        page.get_by_role("heading", name="Support triage", exact=True)
    ).to_be_visible()
    assert fixture.mutations == []
    page.get_by_label("Project", exact=True).select_option("project_beta")
    playwright.expect(page.locator("#project-name")).to_have_text("Research agent")
    page.get_by_label("Application agent", exact=True).select_option("agent_research")
    page.get_by_role("button", name="Investigate", exact=True).click()
    page.get_by_label("Goal", exact=True).fill("Investigate duplicate citations")
    page.get_by_label("Audit scope").select_option("changes")
    page.get_by_role("dialog").get_by_role("button", name="Start audit", exact=True).click()
    playwright.expect(page.locator(".agent-goal")).to_have_text("Investigate duplicate citations")
    request = fixture.mutations[-1]
    assert request["path"] == "/api/projects/project_beta/jobs"
    assert request["payload"]["options"]["scope"] == "changes"
    assert request["payload"]["application_agent_id"] == "agent_research"
    assert request["payload"]["focus_id"] == "focus_research"
    assert request["payload"]["options"]["mode"] == "code"
    assert request["token"] == "browser-test-session"
    assert page.evaluate("window.sources[0].closed")
    assert page.evaluate("window.sources.at(-1).url") == "/api/projects/project_beta/events"


def test_approval_is_bound_to_question_and_does_not_leak_between_projects(webapp_page):
    page, fixture = webapp_page
    fixture.jobs["project_alpha"] = [
        {
            "id": "job_approval",
            "project_id": "project_alpha",
            "kind": "fix",
            "goal": "Improve tool routing",
            "agent": "codex",
            "state": "waiting_for_approval",
            "events": [],
            "question": {
                "id": "question_exact",
                "kind": "approval",
                "text": "Run the proposed evaluation?",
            },
        }
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=overview")
    playwright.expect(page.get_by_role("button", name="Approve", exact=True)).to_be_visible()
    page.get_by_label("Project", exact=True).select_option("project_beta")
    playwright.expect(page.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    page.evaluate(
        "window.sources[0].listeners.update({data: JSON.stringify({id: 'job_wrong', project_id: 'project_alpha', state: 'waiting_for_approval', question: {id: 'bad', kind: 'approval', text: 'Wrong project'}})})"
    )
    playwright.expect(page.get_by_text("Wrong project", exact=True)).to_have_count(0)
    page.get_by_label("Project", exact=True).select_option("project_alpha")
    page.get_by_role("button", name="Approve", exact=True).click()
    playwright.expect(page.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    request = fixture.mutations[-1]
    assert request["path"] == "/api/projects/project_alpha/jobs/job_approval/reply"
    assert request["payload"]["question_id"] == "question_exact"
    assert request["payload"]["answer"] == {"decision": "accept"}


def test_dataset_preview_requires_explicit_save_and_renders_provider_text_safely(webapp_page):
    page, fixture = webapp_page
    fixture.connections = [
        {
            "id": "connection_one",
            "name": "Braintrust staging",
            "provider": "braintrust",
            "project": "support",
            "project_ids": ["project_alpha"],
        }
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=eval&tab=datasets")
    page.get_by_role("button", name="Import dataset", exact=True).click()
    page.get_by_label("Dataset", exact=True).select_option("dataset_remote")
    playwright.expect(page.get_by_label("Provider project", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Preview import", exact=True).click()
    playwright.expect(page.get_by_role("heading", name="Review your import")).to_be_visible()
    playwright.expect(
        page.get_by_text("Some cases have no explicit expected behavior.", exact=False)
    ).to_be_visible()
    playwright.expect(page.locator(".data-preview")).to_contain_text(
        "<script>window.compromised = true</script>"
    )
    assert page.evaluate("window.compromised") is None
    assert [item["path"] for item in fixture.mutations] == [
        "/api/projects/project_alpha/imports/preview"
    ]
    pending_refresh = []
    page.route(
        "**/api/projects/project_alpha/overview", lambda route: pending_refresh.append(route)
    )
    with page.expect_request("**/api/projects/project_alpha/overview"):
        page.get_by_role("button", name="Save local snapshot", exact=True).click()
    # Keep completion atomic: closing early allows a late refresh to redirect newer navigation.
    playwright.expect(page.get_by_role("dialog")).to_be_visible()
    assert len(pending_refresh) == 1
    fixture.route(pending_refresh.pop())
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    assert fixture.mutations[-1]["path"] == "/api/projects/project_alpha/imports"
    assert fixture.mutations[-1]["payload"]["preview_id"] == "preview_one"


@pytest.mark.parametrize("width", [360, 1450])
def test_navigation_mobile_layout_and_provider_credentials(webapp_page, width):
    page, fixture = webapp_page
    page.set_viewport_size({"width": width, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=overview")
    playwright.expect(
        page.get_by_role("heading", name="Support triage", exact=True)
    ).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("button", name="Add connection", exact=True).click()
    page.get_by_label("Provider", exact=True).select_option("langfuse")
    page.get_by_label("Connection name").fill("Langfuse production")
    page.get_by_label("Public key", exact=True).fill("pk-test")
    page.get_by_label("Secret key", exact=True).fill("sk-test")
    page.get_by_role("button", name="Save connection", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    request = fixture.mutations[-1]
    assert request["payload"]["provider"] == "langfuse"
    assert request["payload"]["credential_mode"] == "keyring"
    assert request["payload"]["credentials"] == {"public_key": "pk-test", "secret_key": "sk-test"}
    assert "sk-test" not in page.locator("body").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert errors == []


def test_fix_evidence_selection_and_publication_are_separate(webapp_page):
    page, fixture = webapp_page
    baseline = {
        "id": "candidate_base",
        "state": "verified",
        "hypothesis": "Original routing",
        "metrics": {"accuracy": 0.7},
        "score": {"score": 0.7},
        "review_verdict": "pass",
    }
    winner = {
        "id": "candidate_best",
        "state": "verified",
        "hypothesis": "Validate tool names",
        "metrics": {"accuracy": 0.9},
        "score": {"score": 0.9},
        "checks": [{"id": "no unintended tools", "passed": True}],
        "review_verdict": "pass",
    }
    fixture.runs = [
        {
            "run_id": "run_one",
            "goal": "Improve routing quality",
            "state": "complete",
            "revision": 7,
            "baseline_id": "candidate_base",
            "frontier": ["candidate_best"],
            "candidates": [baseline, winner],
            "comparisons": {"result": "verified_improvement", "alternatives": [winner]},
            "usage": {"trials": 6},
            "limits": {"max_trials": 24},
        }
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=fix")
    page.get_by_role("button", name="Improve routing quality", exact=True).click()
    playwright.expect(page.get_by_text("Verified alternative", exact=True)).to_be_visible()
    page.get_by_role("button", name="Inspect diff & evidence", exact=True).nth(1).click()
    playwright.expect(page.get_by_text("+ validate the tool name", exact=False)).to_be_visible()
    playwright.expect(
        page.get_by_text("The change preserves required behavior.", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Select candidate", exact=True).click()
    playwright.expect(
        page.get_by_role("button", name="Prepare local delivery", exact=True)
    ).to_be_visible()
    playwright.expect(page.get_by_text("Selected candidate", exact=True)).to_be_visible()
    selection = fixture.mutations[-1]["payload"]
    assert selection["expected_revision"] == 7
    assert selection["candidate_id"] == "candidate_best"
    page.get_by_role("button", name="Prepare local delivery", exact=True).click()
    playwright.expect(
        page.get_by_role("button", name="Publish draft PR", exact=True)
    ).to_be_visible()
    assert fixture.mutations[-1]["payload"] == {
        "kind": "fix",
        "source_id": "run_one",
        "publish": False,
    }
    page.get_by_label("Git remote", exact=True).fill("origin")
    page.get_by_label("Pull request base branch", exact=True).fill("main")
    page.get_by_role("button", name="Publish draft PR", exact=True).click()
    assert not any(item["payload"].get("publish") for item in fixture.mutations)
    page.get_by_label("I reviewed the local delivery", exact=False).check()
    page.get_by_role("button", name="Publish draft PR", exact=True).click()
    playwright.expect(
        page.get_by_text("Draft PR publication completed.", exact=True)
    ).to_be_visible()
    playwright.expect(page.get_by_role("link", name="Open draft PR ↗")).to_have_attribute(
        "href", "https://github.com/example/support/pull/42"
    )
    assert fixture.mutations[-1]["payload"] == {
        "kind": "fix",
        "source_id": "run_one",
        "delivery_id": "delivery_exact",
        "publish": True,
        "remote": "origin",
        "base": "main",
    }


def test_dataset_to_measurement_keeps_frozen_evaluator_and_profile_limits(webapp_page):
    page, fixture = webapp_page
    fixture.profile["limits"] = {
        "max_trials": 8,
        "max_elapsed_seconds": 120,
        "trial_timeout_seconds": 10,
    }
    fixture.datasets = [{"id": "snapshot_cases", "name": "Trusted support cases", "count": 3}]
    fixture.evaluations = [
        {"evaluation_id": "eval_draft", "state": "draft", "goal": "Draft evaluator"},
        {"evaluation_id": "eval_frozen", "state": "frozen", "goal": "Validated evaluator"},
    ]
    fixture.baselines = [
        {
            "baseline_id": "baseline_saved",
            "evaluation_id": "eval_frozen",
            "profile_name": "local",
            "state": "completed",
            "branch": "main",
            "benchmark_score": {"value": 0.7},
        }
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=eval&tab=datasets")
    page.get_by_role("button", name="Create eval", exact=True).click()
    page.get_by_label("What should your agent do correctly?", exact=True).fill(
        "Use grounded support answers"
    )
    playwright.expect(page.get_by_label("Execution profile", exact=True)).to_have_value("local")
    playwright.expect(page.get_by_label("Maximum evaluation runs", exact=True)).to_have_value("8")
    page.get_by_role("button", name="Start eval", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    options = fixture.mutations[-1]["payload"]["options"]
    assert options["dataset_snapshot_id"] == "snapshot_cases"
    assert (
        options["max_trials"],
        options["max_elapsed_seconds"],
        options["trial_timeout_seconds"],
    ) == (8, 120, 10)
    page.get_by_role("button", name="Evaluations", exact=True).click()
    page.get_by_role("button", name="Draft evaluator", exact=True).click()
    playwright.expect(
        page.get_by_role("button", name="Continue preparation", exact=True)
    ).to_be_visible()
    playwright.expect(page.get_by_role("button", name="Run baseline", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Close dialog", exact=True).click()
    page.get_by_role("button", name="Baseline history", exact=True).click()
    page.get_by_role("button", name="Rerun baseline", exact=True).click()
    playwright.expect(page.get_by_label("Evaluation", exact=True)).to_be_disabled()
    playwright.expect(page.get_by_label("Evaluation", exact=True)).to_have_value("eval_frozen")
    page.get_by_role("button", name="Close dialog", exact=True).click()
    page.get_by_role("button", name="Start fix", exact=True).click()
    page.get_by_label("Goal", exact=True).fill("Improve support correctness")
    page.get_by_text("Additional options", exact=True).click()
    page.get_by_label("Optimization engine", exact=True).select_option("gepa")
    playwright.expect(page.get_by_label("Finalists to verify", exact=True)).to_have_value("3")
    page.get_by_label("Finalists to verify", exact=True).fill("4")
    page.get_by_label("Concurrent coding sessions", exact=True).fill("2")
    page.get_by_role("dialog").get_by_role("button", name="Start fix", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    options = fixture.mutations[-1]["payload"]["options"]
    assert options["baseline_id"] == "baseline_saved"
    assert options["evaluation_id"] == "eval_frozen"
    assert options["engine"] == "gepa"
    assert options["finalist_count"] == 4
    assert options["host_concurrency"] == 2


def test_selected_task_reload_and_grouped_answers_remain_distinct(webapp_page):
    page, fixture = webapp_page
    fixture.jobs["project_alpha"] = [
        {
            "id": "job_newer",
            "project_id": "project_alpha",
            "kind": "audit",
            "goal": "Another audit",
            "agent": "codex",
            "state": "running",
            "events": [],
        },
        {
            "id": "job_saved",
            "project_id": "project_alpha",
            "kind": "eval",
            "goal": "Prepare a useful evaluator",
            "agent": "codex",
            "state": "needs_input",
            "events": [],
            "question": {
                "id": "question_saved",
                "kind": "question",
                "questions": [
                    {
                        "id": "data",
                        "question": "Which data can be used?",
                        "options": [
                            {"label": "Synthetic", "description": "Generate examples."},
                            {"label": "Existing", "description": "Reuse accepted cases."},
                        ],
                    },
                    {"id": "expectations", "question": "What is correct behavior?"},
                ],
            },
        },
    ]
    page.goto(
        "http://127.0.0.1:8765/?agent=agent_support&project=project_alpha&view=overview&job=job_saved"
    )
    playwright.expect(page.locator("#job-select")).to_have_value("job_saved")
    page.get_by_label("Which data can be used?", exact=True).select_option("Existing")
    page.get_by_label("What is correct behavior?", exact=True).fill("Reject unknown tool names")
    page.get_by_role("button", name="Send answer", exact=True).click()
    playwright.expect(page.get_by_role("button", name="Send answer", exact=True)).to_have_count(0)
    payload = fixture.mutations[-1]["payload"]
    assert payload["question_id"] == "question_saved"
    assert payload["answer"] == {
        "answers": {"data": "Existing", "expectations": "Reject unknown tool names"}
    }
    fixture.jobs["project_alpha"][1].update(
        state="needs_input", next_action="Confirm the expected output."
    )
    page.reload()
    playwright.expect(page.locator("#job-select")).to_have_value("job_saved")
    page.get_by_label("Your answer", exact=True).fill("Return an explicit validation error")
    page.get_by_role("button", name="Send and resume", exact=True).click()
    playwright.expect(page.get_by_role("button", name="Send and resume", exact=True)).to_have_count(
        0
    )
    assert [item["path"].rsplit("/", 1)[1] for item in fixture.mutations[-2:]] == [
        "message",
        "resume",
    ]


def test_connection_check_exposes_project_picker_and_check_time(webapp_page):
    page, fixture = webapp_page
    fixture.connections = [
        {
            "id": "connection_one",
            "name": "Braintrust",
            "provider": "braintrust",
            "project_ids": ["project_alpha"],
            "credential_mode": "session",
        }
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=settings")
    page.get_by_role("button", name="Test", exact=True).click()
    playwright.expect(page.get_by_text("Last checked", exact=False)).to_be_visible()
    picker = page.get_by_label("Provider project", exact=True)
    playwright.expect(picker).to_have_count(1)
    picker.select_option(label="Staging support")
    page.get_by_role("button", name="Use selected project", exact=True).click()
    playwright.expect(page.locator("#notice")).to_have_text(
        "Provider project saved for this connection."
    )
    assert fixture.mutations[-1]["payload"]["project"] == "remote-b"


def test_agent_authentication_and_cross_agent_model_selection(webapp_page):
    page, fixture = webapp_page
    fixture.agents = [
        {"id": "codex", "available": True, "authenticated": False},
        {"id": "claude", "available": True},
    ]
    fixture.agent_settings["models"]["codex"] = "codex-saved-model"
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=overview")
    page.get_by_role("button", name="Investigate", exact=True).click()
    playwright.expect(
        page.get_by_role("dialog").get_by_role("button", name="Start audit", exact=True)
    ).to_be_disabled()
    playwright.expect(page.get_by_text("Run codex login", exact=False)).to_be_visible()
    page.get_by_role("dialog").get_by_label("Coding agent", exact=True).select_option("claude")
    playwright.expect(page.get_by_label("Model", exact=True)).to_have_value("")
    playwright.expect(
        page.get_by_role("dialog").get_by_role("button", name="Start audit", exact=True)
    ).to_be_enabled()
    page.get_by_role("button", name="Close dialog", exact=True).click()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("button", name="Coding agents", exact=True).click()
    playwright.expect(page.get_by_label("Store new credential", exact=True)).to_have_value(
        "keyring"
    )
    playwright.expect(page.get_by_label("Default model", exact=True)).to_have_value(
        "codex-saved-model"
    )
    page.get_by_label("Default coding agent", exact=True).select_option("claude")
    playwright.expect(page.get_by_label("Default model", exact=True)).to_have_value("")


def test_task_codex_default_overrides_saved_model(webapp_page):
    page, fixture = webapp_page
    fixture.agent_settings["models"]["codex"] = "codex-saved-model"
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=overview")
    page.get_by_role("button", name="Investigate", exact=True).click()
    model = page.get_by_role("dialog").get_by_label("Model", exact=True)
    playwright.expect(model).to_have_value("codex-saved-model")
    model.select_option(label="Codex default")
    page.get_by_role("dialog").get_by_role("button", name="Start audit", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    request = fixture.mutations[-1]
    assert request["path"] == "/api/projects/project_alpha/jobs"
    assert request["payload"]["agent"] == "codex"
    assert request["payload"]["model"] == ""
    assert fixture.agent_settings["models"]["codex"] == "codex-saved-model"


def test_audit_issue_handoff_retains_issue_and_audit_identity(webapp_page):
    page, fixture = webapp_page
    fixture.issues = [
        {"issue_id": "issue_one", "audit_id": "audit_parent", "title": "Incorrect tool selection"}
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=audit")
    page.get_by_role("button", name="Create eval", exact=True).click()
    page.get_by_role("button", name="Start eval", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    options = fixture.mutations[-1]["payload"]["options"]
    assert options["issue_id"] == "issue_one"
    assert options["audit_id"] == "audit_parent"


def test_saved_issue_focus_keeps_evidence_identity_and_existing_focuses(webapp_page):
    page, fixture = webapp_page
    fixture.issues = [
        {
            "issue_id": "issue_one",
            "latest_audit_id": "audit_parent",
            "title": "Incorrect tool selection",
        }
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=overview")
    page.get_by_role("button", name="Add goal", exact=True).click()
    page.get_by_label("Start from", exact=True).select_option("issue")
    playwright.expect(page.get_by_role("button", name="Save goal", exact=True)).to_be_disabled()
    page.get_by_label("Saved issue", exact=True).select_option("issue_one")
    page.get_by_label("Goal name", exact=True).fill("Choose the correct tool")
    page.get_by_label("Desired behavior", exact=True).fill("Use the support lookup tool")
    page.get_by_role("button", name="Save goal", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    assert fixture.mutations[-1]["payload"]["source"] == {
        "kind": "issue",
        "issue_id": "issue_one",
        "audit_id": "audit_parent",
    }
    assert {focus["id"] for focus in fixture.focuses["agent_support"]} == {
        "focus_correctness",
        "focus_new",
    }

    fixture.issues = []
    page.reload()
    page.get_by_role("button", name="Goals", exact=True).click()
    page.get_by_role("button", name="Add goal", exact=True).click()
    page.get_by_label("Start from", exact=True).select_option("issue")
    playwright.expect(
        page.get_by_text("This agent has no saved issues.", exact=False)
    ).to_be_visible()
    playwright.expect(page.get_by_role("button", name="Save goal", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Start from a goal", exact=True).click()
    playwright.expect(page.get_by_label("Start from", exact=True)).to_have_value("goal")
    playwright.expect(page.get_by_role("button", name="Save goal", exact=True)).to_be_enabled()


def test_baseline_comparison_preserves_unknowns_and_rejects_incompatibility(webapp_page):
    page, fixture = webapp_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    fixture.baselines = [
        {
            "baseline_id": "baseline_left",
            "evaluation_id": "eval_shared",
            "state": "completed",
            "branch": "main",
            "source_revision": "abc123",
        },
        {
            "baseline_id": "baseline_right",
            "evaluation_id": "eval_shared",
            "state": "completed",
            "branch": "improved",
            "source_revision": "def456",
        },
        {
            "baseline_id": "baseline_other",
            "evaluation_id": "eval_shared",
            "state": "completed",
            "branch": "other",
            "source_revision": "abc789",
        },
        {
            "baseline_id": "baseline_draft",
            "evaluation_id": "eval_shared",
            "state": "prepared",
            "branch": "draft",
        },
    ]
    response = {
        "status": 200,
        "json": {
            "compatible": True,
            "benchmark": {
                "state": "measured",
                "left": 0.7,
                "right": 0.9,
                "delta": 0.2,
                "left_eligible": True,
                "right_eligible": True,
            },
            "recent_traces": {
                "left": {"state": "unavailable"},
                "right": {"state": "unavailable"},
                "limitations": ["Recent trace populations are not controlled comparisons."],
            },
            "limitations": ["Scores do not establish statistical significance."],
        },
    }
    page.route("**/baselines/*/compare/*", lambda route: route.fulfill(**response))
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=eval&tab=baselines")
    page.get_by_role("button", name="Compare", exact=True).first.click()
    playwright.expect(page.get_by_label("Compare with", exact=True)).to_have_value("baseline_right")
    assert page.get_by_label("Compare with", exact=True).locator("option").count() == 2
    with page.expect_response("**/baselines/baseline_left/compare/baseline_right"):
        page.get_by_role("button", name="Compare measurements", exact=True).click()
    table = page.get_by_role("table", name="Fixed benchmark comparison", exact=True)
    playwright.expect(table).to_contain_text("0.2")
    playwright.expect(
        page.get_by_text("Recent traces · separate populations", exact=True)
    ).to_be_visible()
    response["json"]["benchmark"].update(
        state="unavailable", right=None, delta=None, right_eligible=None
    )
    page.get_by_role("button", name="Compare measurements", exact=True).click()
    playwright.expect(table).to_contain_text("Unmeasured")
    playwright.expect(table).not_to_contain_text("0.2")
    response.update(
        status=400,
        json={"error": "baseline evaluator definitions differ; choose the same frozen evaluator"},
    )
    page.get_by_role("button", name="Compare measurements", exact=True).click()
    playwright.expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text(
        "evaluator definitions differ"
    )
    playwright.expect(table).to_have_count(0)
    page.get_by_label("Compare with", exact=True).select_option("baseline_other")
    playwright.expect(page.get_by_role("dialog").locator(".form-error")).to_be_empty()
    assert errors == []
    assert fixture.mutations == []


def test_agent_discovery_confirmation_focus_and_scoped_launch(webapp_page):
    page, fixture = webapp_page
    fixture.application_agents["project_alpha"] = []
    page.goto("http://127.0.0.1:8765/")
    playwright.expect(
        page.get_by_role("heading", name="Your application agents", exact=True)
    ).to_be_visible()
    assert fixture.mutations == []
    page.get_by_role("button", name="Discover agents", exact=True).click()
    assert fixture.mutations == []
    page.get_by_role("button", name="Scan for agents", exact=True).click()
    playwright.expect(
        page.get_by_role("button", name="Review and confirm", exact=True)
    ).to_be_visible()
    assert fixture.application_agents["project_alpha"][0]["status"] == "suggested"
    page.get_by_role("button", name="Review and confirm", exact=True).click()
    page.get_by_label("Agent name", exact=True).fill("Routing agent")
    page.get_by_label("Agent code paths", exact=True).fill("src/router, prompts/routing")
    page.get_by_role("button", name="Confirm agent", exact=True).click()
    playwright.expect(page.get_by_role("heading", name="Routing agent", exact=True)).to_be_visible()
    page.get_by_role("button", name="Choose a goal", exact=True).click()
    page.get_by_label("Goal category", exact=True).select_option("latency")
    page.get_by_label("Goal name", exact=True).fill("Faster routing")
    page.get_by_label("Desired behavior", exact=True).fill(
        "Route simple requests within one second"
    )
    page.get_by_role("button", name="Save goal", exact=True).click()
    playwright.expect(
        page.get_by_role("heading", name="Measurement plan", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Goals", exact=True).click()
    playwright.expect(page.get_by_label("Current goal", exact=True)).to_have_value("focus_new")
    page.reload()
    playwright.expect(page.get_by_label("Current goal", exact=True)).to_have_value("focus_new")
    page.get_by_role("button", name="Investigate", exact=True).click()
    playwright.expect(page.get_by_label("Code paths", exact=True)).to_have_value(
        "src/router, prompts/routing"
    )
    playwright.expect(page.get_by_label("Goal", exact=True)).to_have_value(
        "Route simple requests within one second"
    )
    page.get_by_label("Model", exact=True).select_option(label="Saved GPT")
    page.get_by_role("dialog").get_by_role("button", name="Start audit", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    job = fixture.mutations[-1]["payload"]
    assert job["application_agent_id"] == "agent_suggested"
    assert job["focus_id"] == "focus_new"
    assert job["model"] == "codex-saved-model"
    assert job["options"]["code_scopes"] == ["src/router", "prompts/routing"]


def test_recent_trace_focus_requires_retained_evidence(webapp_page):
    page, fixture = webapp_page
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=traces")
    page.get_by_role("button", name="Find failures", exact=True).click()
    playwright.expect(page.get_by_role("button", name="Save goal", exact=True)).to_be_disabled()
    playwright.expect(
        page.get_by_text("Import a bounded trace sample before", exact=False)
    ).to_be_visible()
    page.get_by_role("button", name="Close dialog", exact=True).click()
    fixture.traces = [
        {"id": "snapshot_traces", "name": "Latest support requests", "provider": "braintrust"}
    ]
    page.reload()
    page.get_by_role("button", name="Find failures", exact=True).click()
    page.get_by_label("Goal name", exact=True).fill("Recent failures")
    page.get_by_label("Desired behavior", exact=True).fill("Investigate incomplete requests")
    page.get_by_label("Imported trace snapshot", exact=True).select_option("snapshot_traces")
    page.get_by_label("Most recent completed traces", exact=True).fill("25")
    page.get_by_role("button", name="Save goal", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    assert fixture.mutations[-1]["payload"]["source"] == {
        "kind": "recent_traces",
        "count": 25,
        "trace_snapshot_id": "snapshot_traces",
    }


def test_measurement_readiness_and_retained_metric_guardrails(webapp_page):
    page, fixture = webapp_page
    fixture.readiness = {
        "evaluation": {"ready": True},
        "baseline": {"ready": False, "reason": "A compatible baseline is missing."},
        "fix": {"ready": False, "reason": "Measure the reviewed evaluator before optimizing."},
    }
    fixture.evaluations = [
        {
            "evaluation_id": "eval_frozen",
            "state": "frozen",
            "goal": "Support correctness",
            "metrics": {"success": {"direction": "max", "unit": "ratio"}},
        }
    ]
    fixture.baselines = [
        {
            "baseline_id": "baseline_one",
            "evaluation_id": "eval_frozen",
            "state": "completed",
            "branch": "main",
            "source_revision": "abc123",
        }
    ]
    fixture.metrics = {
        "guardrails": [
            {
                "id": "guard_success",
                "name": "Task success",
                "focus_id": "focus_correctness",
                "state": "failed",
                "threshold": 0.9,
                "value": 0.8,
            }
        ],
        "metrics": [
            {
                "id": "success",
                "name": "Task success",
                "unit": "ratio",
                "direction": "max",
                "evaluator_id": "eval_frozen",
                "measurements": [
                    {
                        "value": 0.8,
                        "state": "measured",
                        "source_revision": "abc123",
                        "baseline_id": "baseline_one",
                    }
                ],
            }
        ],
    }
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=overview")
    page.get_by_role("button", name="Start fix", exact=True).first.click()
    playwright.expect(
        page.get_by_role("heading", name="Get ready to improve", exact=True)
    ).to_be_visible()
    assert fixture.mutations == []
    page.get_by_role("button", name="Review measurement plan", exact=True).click()
    page.get_by_label("Reference baseline", exact=True).select_option("baseline_one")
    page.get_by_label("Focus metric", exact=True).select_option("success")
    page.get_by_text("Add an explicit required guardrail", exact=True).click()
    page.get_by_label("Require an additional metric threshold", exact=True).check()
    page.get_by_label("Guardrail metric", exact=True).fill("success")
    page.get_by_label("Guardrail bound", exact=True).fill("0.9")
    page.get_by_label("Compare against", exact=True).select_option("absolute")
    page.get_by_role("button", name="Save measurement plan", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    assert fixture.mutations[-1]["payload"] == {
        "evaluation_id": "eval_frozen",
        "baseline_id": "baseline_one",
        "primary_metric": "success",
        "guardrails": [{"metric": "success", "op": "gte", "bound": 0.9, "reference": "absolute"}],
    }
    page.get_by_role("button", name="Metrics", exact=True).click()
    playwright.expect(
        page.get_by_role("table", name="Task success history", exact=True)
    ).to_contain_text("0.8")
    playwright.expect(page.locator("#page .badge").filter(has_text="failed")).to_have_count(1)


def test_real_service_browser_project_settings_import_and_scoped_approval(tmp_path, monkeypatch):
    from test_webapp import Provider, running

    from agentagon.webapp.service import Application

    answers = []

    def execute(request, emit, ask, cancelled):
        emit({"type": "session", "session_id": "browser-test-session", "model": "test-model"})
        answers.append(ask({"kind": "approval", "text": "Inspect this project's evaluator?"}))
        return {"state": "interrupted", "session_id": "browser-test-session"}

    monkeypatch.setattr(
        "agentagon.webapp.service.detect_agents",
        lambda: [{"agent": "codex", "available": True}],
    )
    application = Application(tmp_path / "app-state", execute=execute, provider_factory=Provider)
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "app.py").write_text("def answer():\n    return 42\n")
    (second_root / "app.py").write_text("def answer():\n    return 43\n")
    first, second = application.register(str(first_root)), application.register(str(second_root))

    with running(application) as (_, server), playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=os.environ.get("AGENTAGON_TEST_CHROMIUM"))
        try:
            page = browser.new_page(viewport={"width": 1450, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route(
                "**/api/agents/codex/models",
                lambda route: route.fulfill(
                    json={
                        "models": [{"id": "test-model", "name": "Test GPT", "default": True}],
                        "default_model": "test-model",
                    }
                ),
            )
            page.goto(f"http://127.0.0.1:{server.server_port}/?project={first['id']}")
            playwright.expect(page.locator("#project-name")).to_have_text("first")
            page.get_by_label("Project", exact=True).select_option(second["id"])
            playwright.expect(page.locator("#project-name")).to_have_text("second")
            page.get_by_role("button", name="Add agent", exact=True).click()
            page.get_by_label("Agent name", exact=True).fill("Answer agent")
            page.get_by_label("Agent code paths", exact=True).fill("app.py")
            page.get_by_role("button", name="Save agent", exact=True).click()
            playwright.expect(
                page.get_by_role("heading", name="Answer agent", exact=True)
            ).to_be_visible()
            saved_agent = application.catalog.agents(second["id"])[0]
            page.get_by_role("button", name="Edit agent", exact=True).click()
            page.get_by_label("Agent name", exact=True).fill("Reviewed answer agent")
            page.get_by_role("button", name="Save agent", exact=True).click()
            playwright.expect(
                page.get_by_role("heading", name="Reviewed answer agent", exact=True)
            ).to_be_visible()
            edited_agent = application.catalog.agent(second["id"], saved_agent["id"])
            assert edited_agent["name"] == "Reviewed answer agent"
            assert edited_agent["revision"] == saved_agent["revision"] + 1
            page.get_by_role("button", name="Choose a goal", exact=True).click()
            page.get_by_label("Goal name", exact=True).fill("Answer correctly")
            page.get_by_label("Desired behavior", exact=True).fill("Inspect answer behavior")
            page.get_by_role("button", name="Save goal", exact=True).click()
            playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
            page.get_by_role("button", name="Write a plan", exact=True).click()
            page.get_by_role("button", name="Add behavior", exact=True).click()
            page.get_by_label("Behavior 1", exact=True).fill("Answer equals the accepted reference")
            page.get_by_label("Check name", exact=True).fill("reference_match")
            page.get_by_role("button", name="Add metric", exact=True).click()
            page.get_by_label("Primary metric", exact=True).select_option("metric_1")
            page.get_by_role("button", name="Save draft", exact=True).click()
            playwright.expect(
                page.get_by_role("button", name="Accept plan", exact=True)
            ).to_be_enabled()
            focus = application.catalog.focuses(second["id"], edited_agent["id"])[0]
            draft = application.designs.get(second["id"], edited_agent["id"], focus["id"])
            assert draft["state"] == "draft"
            assert (
                application.designs.accepted(second["id"], edited_agent["id"], focus["id"]) is None
            )
            page.get_by_role("button", name="Accept plan", exact=True).click()
            playwright.expect(
                page.get_by_role("button", name="Prepare evaluator", exact=True)
            ).to_be_visible()
            accepted = application.designs.accepted(second["id"], edited_agent["id"], focus["id"])
            assert accepted["behaviors"][0]["check"] == "reference_match"
            assert accepted["scoring"] == {"mode": "primary", "primary": "metric_1"}
            page.get_by_role("button", name="Settings", exact=True).click()
            page.get_by_role("button", name="Add connection", exact=True).click()
            page.get_by_label("Connection name").fill("Test evidence")
            page.get_by_label("Provider project", exact=True).fill("remote")
            page.get_by_label("API key", exact=True).fill("private-test-value")
            page.get_by_label("Store credentials", exact=True).select_option("session")
            page.get_by_role("button", name="Save connection", exact=True).click()
            playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
            page.get_by_role("button", name="Execution", exact=True).click()
            page.get_by_role("button", name="New profile", exact=True).click()
            page.get_by_role("button", name="Save profile", exact=True).click()
            playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
            assert "local" in application.settings(second["id"])["profiles"]
            assert application.settings(first["id"])["profiles"] == {}

            page.get_by_role("button", name="Eval", exact=True).click()
            page.get_by_role("button", name="Import dataset", exact=True).click()
            page.get_by_label("Dataset", exact=True).select_option("dataset-one")
            page.get_by_role("button", name="Preview import", exact=True).click()
            playwright.expect(
                page.get_by_role("heading", name="Review your import")
            ).to_be_visible()
            page.get_by_role("button", name="Save local snapshot", exact=True).click()
            playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
            assert len(application.overview(second["id"])["datasets"]) == 1
            assert application.overview(first["id"])["datasets"] == []

            page.get_by_role("button", name="Audit", exact=True).click()
            page.get_by_role("button", name="New audit", exact=True).click()
            page.get_by_label("Goal", exact=True).fill("Inspect answer behavior")
            page.get_by_label("Model", exact=True).select_option("test-model")
            page.get_by_role("dialog").get_by_role("button", name="Start audit", exact=True).click()
            playwright.expect(
                page.get_by_role("button", name="Approve", exact=True)
            ).to_be_visible()
            page.get_by_label("Project", exact=True).select_option(first["id"])
            playwright.expect(page.get_by_role("button", name="Approve", exact=True)).to_have_count(
                0
            )
            page.get_by_label("Project", exact=True).select_option(second["id"])
            page.get_by_role("button", name="Approve", exact=True).click()
            playwright.expect(page.get_by_role("button", name="Resume", exact=True)).to_be_visible()
            assert answers == [{"decision": "accept"}]
            assert application.jobs.list(first["id"]) == []
            assert len(application.jobs.list(second["id"])) == 1
            assert errors == []
        finally:
            browser.close()


def test_dataset_split_uses_development_input_and_keeps_final_cases_reserved(webapp_page):
    page, fixture = webapp_page
    fixture.datasets = [{"id": "snapshot_source", "name": "Support conversations", "count": 12}]
    split_calls = []

    def split_response(route):
        split_calls.append(route.request.post_data_json)
        route.fulfill(
            json={
                "id": "split_one",
                "development_snapshot_id": "snapshot_dev",
                "holdout_snapshot_id": "snapshot_final",
                "counts": {
                    "development": 8,
                    "holdout": 2,
                    "duplicates_removed": 2,
                    "missing_expectations": 1,
                },
            }
        )

    page.route("**/datasets/snapshot_source/split", split_response)
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=eval&tab=datasets")
    page.get_by_role("button", name="Split dataset", exact=True).click()
    page.get_by_role("button", name="Create split", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).to_contain_text("2 reserved final cases")
    playwright.expect(page.get_by_role("dialog")).to_contain_text(
        "1 cases still need accepted expectations"
    )
    assert split_calls == [{"holdout_fraction": 0.2}]
    page.get_by_role("button", name="Create development eval", exact=True).click()
    page.get_by_role("button", name="Start eval", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    assert fixture.mutations[-1]["payload"]["options"]["dataset_snapshot_id"] == "snapshot_dev"

    fixture.datasets = [
        {
            "id": "snapshot_final",
            "name": "Final conversations",
            "count": 2,
            "provenance": {"dataset_partition": "final_holdout"},
        }
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=eval&tab=datasets")
    playwright.expect(
        page.get_by_text("Reserved final inputs · separate verification required", exact=True)
    ).to_be_visible()
    playwright.expect(page.get_by_role("button", name="Create eval", exact=True)).to_have_count(0)


def test_trace_only_agent_audit_cannot_silently_include_checkout_code(webapp_page):
    page, fixture = webapp_page
    fixture.application_agents["project_alpha"][0]["code_scopes"] = []
    fixture.traces = [{"id": "snapshot_trace", "name": "Support sample", "kind": "traces"}]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=audit")
    page.get_by_role("button", name="New audit", exact=True).click()
    playwright.expect(page.get_by_label("Audit scope", exact=True)).to_have_value("traces")
    playwright.expect(page.get_by_label("Code paths", exact=True)).not_to_be_visible()
    page.get_by_label("Trace evidence", exact=True).select_option("snapshot_trace")
    page.get_by_role("dialog").get_by_role("button", name="Start audit", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    options = fixture.mutations[-1]["payload"]["options"]
    assert options["scope"] == options["mode"] == "traces"
    assert options["code_scopes"] == []


def measurement_proposal():
    return {
        "id": "design_one",
        "revision": 1,
        "state": "draft",
        "background": "Create exactly one ticket for each request, including retries.",
        "evidence": ["requirements/tickets.md"],
        "behaviors": [
            {
                "id": "one_ticket",
                "description": "Repeated requests create one ticket.",
                "required": True,
                "check": "no_duplicate_tickets",
                "evidence": ["requirements/tickets.md:8"],
                "prerequisites": ["Record tool-call IDs during instrumented evaluation runs."],
            }
        ],
        "metrics": {
            "success": {
                "direction": "max",
                "unit": "ratio",
                "aggregation": "mean",
                "missing": "fail",
            }
        },
        "scoring": {"mode": "primary", "primary": "success"},
        "evaluation": {
            "mode": "create",
            "framework": "pytest",
            "scorer": "Expected ticket count is one.",
        },
        "limitations": [
            "Production traces are absent; instrumented test runs can supply trajectory evidence."
        ],
    }


def test_measurement_plan_is_editable_and_requires_saved_explicit_acceptance(webapp_page):
    page, fixture = webapp_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    fixture.designs["agent_support", "focus_correctness"] = measurement_proposal()
    fixture.focuses["agent_support"][0]["measurement"] = {"evaluation_id": "eval_old"}
    fixture.evaluations = [
        {"evaluation_id": "eval_old", "goal": "Previous evaluator", "state": "frozen"}
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=design")
    playwright.expect(
        page.get_by_text("Production traces are optional.", exact=False)
    ).to_be_visible()
    assert fixture.mutations == []
    page.get_by_label("Behavior 1", exact=True).fill("Retries keep the original ticket ID.")
    page.get_by_label("Scoring approach", exact=True).select_option("weighted")
    page.get_by_label("Normalization scale", exact=True).fill("0.1")
    page.get_by_label("Weight", exact=True).fill("2")
    playwright.expect(page.get_by_role("button", name="Accept plan", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Save draft", exact=True).click()
    playwright.expect(page.get_by_role("button", name="Accept plan", exact=True)).to_be_enabled()
    saved = fixture.mutations[-1]["payload"]
    assert saved["expected_revision"] == 1
    assert saved["scoring"] == {"mode": "weighted"}
    assert saved["metrics"]["success"]["scale"] == 0.1
    assert saved["metrics"]["success"]["weight"] == 2
    assert saved["behaviors"][0]["required"] is True
    assert saved["behaviors"][0]["check"] == "no_duplicate_tickets"
    assert saved["behaviors"][0]["prerequisites"] == [
        "Record tool-call IDs during instrumented evaluation runs."
    ]
    assert "command" not in saved["evaluation"]
    assert not any(item["path"].endswith("/jobs") for item in fixture.mutations)
    page.get_by_role("button", name="Accept plan", exact=True).click()
    playwright.expect(
        page.get_by_role("button", name="Prepare evaluator", exact=True)
    ).to_be_enabled()
    assert fixture.mutations[-1]["path"].endswith("/focuses/focus_correctness/design/accept")
    assert fixture.mutations[-1]["payload"] == {"expected_revision": 2}
    page.get_by_text("Goal context and supporting evidence", exact=True).click()
    page.get_by_label("Background and requirements", exact=True).fill("A changed requirement")
    playwright.expect(
        page.get_by_role("button", name="Prepare evaluator", exact=True)
    ).to_be_disabled()
    # Refreshing asynchronous activity must preserve unsubmitted local edits.
    page.get_by_role("button", name="Refresh workspace", exact=True).click()
    playwright.expect(page.get_by_label("Background and requirements", exact=True)).to_have_value(
        "A changed requirement"
    )
    page.get_by_role("button", name="Reload saved plan", exact=True).click()
    playwright.expect(page.get_by_label("Background and requirements", exact=True)).to_have_value(
        measurement_proposal()["background"]
    )
    page.get_by_role("button", name="Prepare evaluator", exact=True).click()
    playwright.expect(page.get_by_label("Evaluation", exact=True)).to_be_disabled()
    playwright.expect(page.get_by_label("Evaluation", exact=True)).to_have_value("")
    page.get_by_role("dialog").get_by_role("button", name="Start eval", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    assert fixture.mutations[-1]["payload"]["kind"] == "eval"
    assert fixture.mutations[-1]["payload"]["focus_id"] == "focus_correctness"
    assert "evaluation_id" not in fixture.mutations[-1]["payload"]["options"]
    assert errors == []


def test_goal_plan_proposal_is_scoped_and_never_accepted_automatically(webapp_page):
    page, fixture = webapp_page
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=design")
    page.get_by_role("button", name="Ask agent for a plan", exact=True).click()
    page.get_by_role("button", name="Propose plan", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).not_to_be_visible()
    request = fixture.mutations[-1]["payload"]
    assert request["kind"] == "design"
    assert request["application_agent_id"] == "agent_support"
    assert request["focus_id"] == "focus_correctness"
    assert "profile" not in request["options"]
    fixture.designs["agent_support", "focus_correctness"] = measurement_proposal()
    page.reload()
    playwright.expect(page.get_by_label("Behavior 1", exact=True)).to_have_value(
        "Repeated requests create one ticket."
    )
    playwright.expect(page.get_by_role("button", name="Accept plan", exact=True)).to_be_enabled()
    assert len(fixture.mutations) == 1
    assert fixture.designs["agent_support", "focus_correctness"]["state"] == "draft"


def test_native_evaluator_review_and_frozen_scoring_reuse_are_distinct(webapp_page):
    page, fixture = webapp_page
    proposal = measurement_proposal()
    proposal["scoring"]["target"] = 0.95
    fixture.designs["agent_support", "focus_correctness"] = proposal
    fixture.native_evaluators = [
        {
            "id": "native_pytest",
            "framework": "pytest",
            "entrypoint": "evals/test_tickets.py",
            "command": {"argv": ["python", "-m", "pytest", "evals/test_tickets.py"], "cwd": "."},
            "evidence": [
                {"path": "evals/test_tickets.py", "line": 10, "detail": "Existing test entrypoint"}
            ],
        }
    ]
    scoring = {
        "mode": "primary",
        "primary": "success",
        "target": 0.95,
        "metrics": proposal["metrics"],
        "behaviors": [
            {
                key: value
                for key, value in proposal["behaviors"][0].items()
                if key not in {"evidence", "prerequisites"}
            }
        ],
    }
    fixture.frozen_evaluators = [
        {"evaluation_id": "eval_reviewed", "goal": "Reviewed correctness", "scoring": scoring}
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=design")
    page.get_by_label("Evaluator approach", exact=True).select_option("reuse")
    page.get_by_label("Existing evaluator", exact=True).select_option("native:native_pytest")
    playwright.expect(page.get_by_label("Executable", exact=True)).to_have_value("python")
    playwright.expect(page.get_by_label("Command arguments", exact=True)).to_have_value(
        "-m\npytest\nevals/test_tickets.py"
    )
    playwright.expect(page.get_by_text("evals/test_tickets.py:10", exact=False)).to_be_visible()
    page.get_by_text("Native metric output mapping", exact=True).click()
    page.get_by_label("Output field for success", exact=True).fill("summary.pass_rate")
    page.get_by_role("button", name="Save draft", exact=True).click()
    playwright.expect(page.get_by_role("button", name="Accept plan", exact=True)).to_be_enabled()
    evaluation = fixture.mutations[-1]["payload"]["evaluation"]
    assert fixture.mutations[-1]["payload"]["scoring"]["target"] == 0.95
    assert evaluation["candidate_id"] == "native_pytest"
    assert evaluation["command"]["argv"] == ["python", "-m", "pytest", "evals/test_tickets.py"]
    assert evaluation["output_mapping"] == {"success": "summary.pass_rate"}
    assert "evaluation_id" not in evaluation
    page.get_by_label("Existing evaluator", exact=True).select_option("frozen:eval_reviewed")
    playwright.expect(page.get_by_label("Metric key", exact=True)).to_be_disabled()
    playwright.expect(page.get_by_label("Behavior 1", exact=True)).to_be_disabled()
    playwright.expect(
        page.get_by_text("This evaluator's scoring is frozen.", exact=False)
    ).to_be_visible()
    page.get_by_role("button", name="Save draft", exact=True).click()
    playwright.expect(page.get_by_role("button", name="Accept plan", exact=True)).to_be_enabled()
    saved = fixture.mutations[-1]["payload"]
    assert saved["metrics"] == scoring["metrics"]
    assert saved["behaviors"] == scoring["behaviors"]
    assert saved["scoring"] == {"mode": "primary", "primary": "success", "target": 0.95}
    assert saved["evaluation"] == {"mode": "reuse", "evaluation_id": "eval_reviewed"}
    page.get_by_label("Evaluator approach", exact=True).select_option("create")
    playwright.expect(page.get_by_label("Metric key", exact=True)).to_be_enabled()


def test_manual_plan_and_setup_remain_usable_without_traces_on_mobile(webapp_page):
    page, fixture = webapp_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("http://127.0.0.1:8765/")
    playwright.expect(page.get_by_role("region", name="Setup checklist")).to_contain_text(
        "Traces · optional"
    )
    page.get_by_role("button", name="Open agent", exact=True).click()
    page.get_by_role("button", name="Design measurement", exact=True).click()
    page.get_by_role("button", name="Write a plan", exact=True).click()
    page.get_by_role("button", name="Add behavior", exact=True).click()
    page.get_by_label("Behavior 1", exact=True).fill("A retry does not create a second ticket")
    page.get_by_label("Check name", exact=True).fill("unique_ticket")
    page.get_by_role("button", name="Add metric", exact=True).click()
    page.get_by_label("Metric key", exact=True).fill("latency")
    page.get_by_label("Direction", exact=True).select_option("min")
    page.get_by_label("Unit", exact=True).fill("ms")
    page.get_by_label("Primary metric", exact=True).select_option("latency")
    page.get_by_role("button", name="Save draft", exact=True).click()
    playwright.expect(page.get_by_role("button", name="Accept plan", exact=True)).to_be_enabled()
    payload = fixture.mutations[-1]["payload"]
    assert payload["expected_revision"] == 0
    assert payload["metrics"]["latency"]["direction"] == "min"
    assert "dataset_snapshot_id" not in payload["evaluation"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path="/private/tmp/agentagon-goal-mobile.png")
    page.get_by_label("Behavior 1", exact=True).scroll_into_view_if_needed()
    page.screenshot(path="/private/tmp/agentagon-goal-mobile-editor.png")
    page.set_viewport_size({"width": 1450, "height": 1000})
    page.get_by_role("button", name="Coding agent", exact=True).click()
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path="/private/tmp/agentagon-goal-desktop.png")
    page.get_by_label("Metric key", exact=True).scroll_into_view_if_needed()
    page.screenshot(path="/private/tmp/agentagon-goal-desktop-editor.png")


def test_dataset_export_and_publication_require_separate_reviewed_actions(webapp_page):
    page, fixture = webapp_page
    fixture.datasets = [
        {"id": "snapshot_cases", "kind": "dataset", "name": "Ticket cases", "count": 2}
    ]
    fixture.connections = [
        {
            "id": "source_bt",
            "provider": "braintrust",
            "name": "Team Braintrust",
            "project": "Support",
            "project_ids": ["project_alpha"],
        }
    ]
    publications = []

    def dataset_actions(route):
        request = route.request
        payload = request.post_data_json
        publications.append((urlsplit(request.url).path, payload))
        if request.url.endswith("/export"):
            route.fulfill(
                json={
                    "state": "draft",
                    "count": 2,
                    "missing_expectations": 1,
                    "framework": payload["framework"],
                    "artifact_urls": {
                        "dataset.jsonl": "/api/projects/project_alpha/datasets/snapshot_cases/exports/export_one/dataset.jsonl"
                    },
                }
            )
        elif request.url.endswith("/publish-preview"):
            route.fulfill(
                json={
                    "preview_id": "preview_exact",
                    "destination": {"project": "Support", "name": "Ticket cases · snapshot-1234"},
                    "count": 2,
                    "missing_expectations": 1,
                    "events": [
                        {"input": "<script>window.compromised=true</script>", "output": "observed"}
                    ],
                }
            )
        elif len([item for item in publications if item[0].endswith("/publish")]) == 1:
            route.fulfill(
                status=503,
                json={"error": "Connection interrupted. Retry this reviewed publication."},
            )
        else:
            route.fulfill(
                json={
                    "state": "completed",
                    "receipt": {
                        "count": 2,
                        "project": "Support",
                        "name": "Ticket cases · snapshot-1234",
                        "dataset_id": "remote_dataset",
                    },
                }
            )

    page.route("**/datasets/snapshot_cases/*", dataset_actions)
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=eval&tab=datasets")
    page.get_by_role("button", name="Export", exact=True).click()
    assert publications == []
    page.get_by_label("Export framework", exact=True).select_option("deepeval")
    page.get_by_role("button", name="Create export", exact=True).click()
    playwright.expect(
        page.get_by_role("link", name="Download dataset.jsonl", exact=True)
    ).to_be_visible()
    playwright.expect(
        page.get_by_text("1 examples have no accepted expectation.", exact=False)
    ).to_be_visible()
    assert publications[-1][1] == {"framework": "deepeval"}
    page.get_by_role("button", name="Close dialog", exact=True).click()
    page.get_by_role("button", name="Publish to Braintrust", exact=True).click()
    page.get_by_role("button", name="Preview publication", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).to_contain_text("Ticket cases · snapshot-1234")
    playwright.expect(
        page.get_by_text("Those expected values will remain missing.", exact=False)
    ).to_be_visible()
    assert not any(path.endswith("/publish") for path, _ in publications)
    assert page.evaluate("window.compromised || false") is False
    page.get_by_role("button", name="Publish reviewed dataset", exact=True).click()
    playwright.expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text(
        "Connection interrupted"
    )
    page.get_by_role("button", name="Publish reviewed dataset", exact=True).click()
    playwright.expect(page.get_by_role("dialog")).to_contain_text("Published 2 examples")
    attempts = [payload for path, payload in publications if path.endswith("/publish")]
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert attempts[0]["preview_id"] == "preview_exact"
    assert attempts[0]["connection_id"] == "source_bt"


def test_each_verified_finalist_keeps_its_own_goal_measurements_and_behaviors(webapp_page):
    page, fixture = webapp_page
    baseline = {
        "id": "candidate_base",
        "state": "verified",
        "hypothesis": "Original agent",
        "metrics": {"latency": 1000},
        "score": {"score": -1000},
    }
    candidates = [
        {
            "id": f"candidate_{index}",
            "state": "verified",
            "hypothesis": f"Finalist {index + 1}",
            "metrics": {"latency": 500 + index * 10},
            "score": {
                "score": -(500 + index * 10),
                "components": [
                    {
                        "id": "latency",
                        "value": 500 + index * 10,
                        "unit": "ms",
                        "direction": "min",
                        "weight": 1,
                    }
                ],
                "behaviors": [{"id": "unique_ticket", "required": True, "passed": True}],
            },
            "review_verdict": "pass",
        }
        for index in range(4)
    ]
    members = [
        {
            "focus_id": "focus_quality",
            "name": "Keep answer quality",
            "primary_metric": "quality",
            "metrics": {"quality": {"unit": "ratio", "direction": "max"}},
        }
    ]
    finalists = {
        candidate["id"]: {
            "state": "completed",
            "result": {
                "passed": True,
                "members": [
                    {
                        "focus_id": "focus_quality",
                        "reference": {"quality": 0.9},
                        "finalist": {"quality": 0.91 + index / 100},
                        "score": {
                            "value": 0.91 + index / 100,
                            "behaviors": [
                                {
                                    "id": "grounded_answer",
                                    "description": "Answers cite retained evidence",
                                    "required": True,
                                    "passed": True,
                                }
                            ],
                        },
                        "checks": [{"id": "citations_exist", "passed": True}],
                        "guardrails": [
                            {
                                "metric": "quality",
                                "reference": "baseline_delta",
                                "baseline": "accepted",
                                "actual": 0.01 + index / 100,
                                "threshold": 0,
                                "op": "gte",
                                "passed": True,
                            }
                        ],
                        "passed": True,
                    }
                ],
            },
        }
        for index, candidate in enumerate(candidates)
    }
    fixture.runs = [
        {
            "run_id": "run_finalists",
            "goal": "Choose a verified improvement",
            "baseline_id": "candidate_base",
            "candidates": [baseline, *candidates],
            "frontier": [candidates[0]["id"]],
            "comparisons": {"result": "verified_improvement", "alternatives": candidates},
            "measurement_suite": {
                "state": "completed",
                "members": members,
                "result": finalists["candidate_0"]["result"],
                "finalists": finalists,
            },
        }
    ]
    page.goto("http://127.0.0.1:8765/?agent=agent_support&view=fix")
    page.get_by_role("button", name="Choose a verified improvement", exact=True).click()
    cards = page.locator(".comparison-grid > article")
    playwright.expect(cards).to_have_count(5)
    first = cards.filter(has=page.get_by_role("heading", name="Finalist 1", exact=True))
    fourth = cards.filter(has=page.get_by_role("heading", name="Finalist 4", exact=True))
    # Agreed scores allow choosing verified alternatives outside the Pareto frontier.
    playwright.expect(
        fourth.get_by_role("button", name="Select candidate", exact=True)
    ).to_be_enabled()
    for card in (first, fourth):
        card.get_by_text("Signals, behaviors and required goals", exact=True).click()
    playwright.expect(
        first.get_by_role("table", name="Finalist 1 · Keep answer quality", exact=True)
    ).to_contain_text("0.91")
    playwright.expect(
        fourth.get_by_role("table", name="Finalist 4 · Keep answer quality", exact=True)
    ).to_contain_text("0.94")
    playwright.expect(
        fourth.get_by_role("table", name="Finalist 4 signals", exact=True)
    ).to_contain_text("530 ms")
    playwright.expect(fourth).to_contain_text("unique_ticket · required · passed")
    playwright.expect(fourth).to_contain_text("Answers cite retained evidence · required · passed")
    playwright.expect(fourth).to_contain_text("quality · baseline delta · accepted")
    assert fixture.mutations == []
