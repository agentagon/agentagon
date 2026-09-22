import os

import pytest
from test_webapp_browser import WorkspaceFixture


class OperationFixture(WorkspaceFixture):
    def respond(self, path, query, method, payload):
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[:2] == ["api", "projects"]:
            project_id, resource = parts[2:4]
            if resource == "tasks" and len(parts) == 4 and method == "POST":
                task = {
                    "id": "task_started",
                    "project_id": project_id,
                    "workflow": payload["workflow"],
                    "workflow_version": 1,
                    "agent_id": payload.get("agent_id"),
                    "title": "New workflow task",
                    "state": "running",
                    "needs_attention": False,
                    "conversation": [],
                    "events": [],
                    "question": None,
                    "available_actions": ["pause", "cancel", "message"],
                }
                self.tasks[project_id] = [
                    item for item in self.tasks[project_id] if item["id"] != task["id"]
                ]
                self.tasks[project_id].insert(0, task)
                return {"task_id": task["id"]}
            if resource == "issues":
                return {"issues": []}
            if resource == "traces":
                if len(parts) == 4:
                    return {"traces": []}
                if parts[4] == "preview" and method == "POST":
                    return {
                        "preview_id": "preview_imported",
                        "state": "ready",
                        "diagnosis_ready": True,
                        "provider": "otlp",
                        "provider_detected": True,
                        "provider_candidates": ["otlp"],
                        "trace_count": 1,
                        "normalized_spans": 1,
                        "inspected_records": 1,
                        "unusable_records": 0,
                        "incomplete_traces": 0,
                        "redaction": "Secrets are removed before storage.",
                        "blockers": [],
                        "limitations": [],
                        "source_complete": True,
                    }
                return {
                    "id": parts[4],
                    "name": "Imported trace",
                    "provider": "otlp",
                    "count": 0,
                    "summary": {},
                    "spans": [],
                    "issues": [],
                }
            if resource == "imports" and method == "POST":
                return {"id": "trace_imported"}
        return super().respond(path, query, method, payload)


class DeliveryFixture(OperationFixture):
    def respond(self, path, query, method, payload):
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[:2] == ["api", "projects"]:
            _, resource = parts[2:4]
            if resource == "results":
                return {
                    "result_kind": "optimize",
                    "run_id": "run_choice",
                    "revision": 8,
                    "state": "completed",
                    "baseline_id": "candidate_baseline",
                    "candidates": [
                        {
                            "id": "candidate_baseline",
                            "state": "verified",
                            "source_revision": "0" * 40,
                        },
                        {
                            "id": "candidate_old",
                            "state": "verified",
                            "source_revision": "1" * 40,
                        },
                        {
                            "id": "candidate_current",
                            "state": "verified",
                            "source_revision": "2" * 40,
                            "hypothesis": "Use the safer tool retry",
                            "review_verdict": "pass",
                            "feasible": True,
                        },
                    ],
                    "comparisons": {
                        "alternatives": [
                            {
                                "id": "candidate_current",
                                "state": "verified",
                                "source_revision": "2" * 40,
                                "hypothesis": "Use the safer tool retry",
                                "review_verdict": "pass",
                                "feasible": True,
                            }
                        ],
                        "result": "verified_improvement",
                    },
                    "selection": {
                        "decision": {
                            "id": "decision_choice",
                            "revision": 2,
                            "decision": "select_candidate",
                            "candidate_id": "candidate_current",
                            "source_revision": "2" * 40,
                            "current": True,
                        },
                        "expected_revision": 2,
                        "recommended_candidate_id": "candidate_current",
                        "engine_selected_candidate_id": "candidate_current",
                        "allowed_actions": [
                            "prepare_local_delivery",
                            "select_candidate",
                            "keep_current",
                        ],
                    },
                    "deliveries": [
                        {
                            "delivery_id": "delivery_old",
                            "state": "prepared",
                            "candidate_id": "candidate_old",
                            "source_revision": "1" * 40,
                            "user_decision_id": "decision_choice",
                            "user_decision_revision": 1,
                            "artifact_urls": {"diff": "/old.patch"},
                        }
                    ],
                }
            if resource == "runs":
                return {"run_id": "run_choice", "candidate": {}, "diff": {"text": ""}}
            if resource == "deliveries" and method == "POST":
                return {
                    "delivery_id": "delivery_current",
                    "state": "prepared",
                    "candidate_id": "candidate_current",
                    "source_revision": "2" * 40,
                    "user_decision_id": "decision_choice",
                    "user_decision_revision": 2,
                    "artifact_urls": {"diff": "/current.patch"},
                }
        return super().respond(path, query, method, payload)


def _draft(page, prefix):
    return page.evaluate(
        """prefix => {
          const item = Object.entries(sessionStorage).find(([key]) => key.startsWith(prefix));
          return item ? { key: item[0], value: JSON.parse(item[1]) } : null;
        }""",
        prefix,
    )


def test_operation_ids_survive_reload_and_clear_after_confirmed_success():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as value:
        browser = value.chromium.launch(
            headless=True, executable_path=os.environ.get("AGENTAGON_TEST_CHROMIUM")
        )
        page = browser.new_page()
        page.set_default_timeout(8_000)
        fixture = OperationFixture()
        page.route("**/*", fixture.route)

        # Goal Go retains its exact request across reload and rotates after admission.
        goal_url = (
            "http://127.0.0.1/projects/project_alpha/agents/agent_support/goals/goal_correctness"
        )
        page.goto(goal_url)
        page.get_by_label("Details Optional").fill("Keep retries idempotent.")
        goal_key = "agentagon.goal-run:project_alpha:agent_support:goal_correctness"
        page.wait_for_function(
            "key => JSON.parse(sessionStorage.getItem(key) || '{}').details === 'Keep retries idempotent.'",
            arg=goal_key,
        )
        goal_draft = _draft(page, goal_key)
        page.reload()
        assert page.get_by_label("Details Optional").input_value() == "Keep retries idempotent."
        page.get_by_role("button", name="Go", exact=True).click()
        page.wait_for_url("**/goals/goal_correctness?run=goalrun_started")
        goal_start = next(item for item in fixture.mutations if item["path"].endswith("/runs"))
        assert goal_start["payload"]["operation_id"] == goal_draft["value"]["operationId"]
        page.wait_for_function(
            "({ key, prior }) => JSON.parse(sessionStorage.getItem(key) || '{}').operationId !== prior",
            arg={"key": goal_key, "prior": goal_draft["value"]["operationId"]},
        )

        # Assessment: its exact scope and id survive reload, then clear on task receipt.
        page.goto("http://127.0.0.1/projects/project_alpha/onboarding")
        page.get_by_text("Analyze code and traces", exact=True).click()
        page.get_by_label("Environment").fill("staging")
        page.get_by_label("Environment").blur()
        page.wait_for_function(
            "() => sessionStorage.getItem('agentagon.operation-draft:project_alpha:assess') !== null"
        )
        assessment_draft = _draft(page, "agentagon.operation-draft:project_alpha:assess")
        page.reload()
        page.get_by_text("Analyze code and traces", exact=True).click()
        assert page.get_by_label("Environment").input_value() == "staging"
        page.get_by_role("button", name="Analyze project").click()
        page.wait_for_url("**/tasks/task_started?view=running")
        assessment_start = next(
            item
            for item in reversed(fixture.mutations)
            if item["path"] == "/api/projects/project_alpha/tasks"
            and item["payload"]["workflow"] == "assess"
        )
        assert (
            assessment_start["payload"]["operation_id"] == assessment_draft["value"]["operationId"]
        )
        assert _draft(page, "agentagon.operation-draft:project_alpha:assess") is None

        # Trace import: edits rotate the payload binding; reload/retry keeps the new id.
        page.goto("http://127.0.0.1/projects/project_alpha/issues")
        trace_input = page.get_by_label("JSON or JSONL")
        trace_input.fill('{"resourceSpans": []}')
        page.wait_for_function(
            "() => sessionStorage.getItem('agentagon.operation-draft:project_alpha:trace-import') !== null"
        )
        first_trace_draft = _draft(page, "agentagon.operation-draft:project_alpha:trace-import")
        trace_input.fill('{"resourceSpans": [{"scopeSpans": []}]}')
        page.wait_for_function(
            "previous => JSON.parse(sessionStorage.getItem('agentagon.operation-draft:project_alpha:trace-import')).operationId !== previous",
            arg=first_trace_draft["value"]["operationId"],
        )
        trace_draft = _draft(page, "agentagon.operation-draft:project_alpha:trace-import")
        page.reload()
        assert page.get_by_label("JSON or JSONL").input_value() == (
            '{"resourceSpans": [{"scopeSpans": []}]}'
        )
        page.get_by_role("button", name="Check trace", exact=True).click()
        page.get_by_role("button", name="Import and investigate", exact=True).click()
        page.wait_for_url("**/traces/trace_imported?intent=discover")
        trace_import = next(
            item
            for item in reversed(fixture.mutations)
            if item["path"] == "/api/projects/project_alpha/imports"
        )
        assert trace_import["payload"]["operation_id"] == trace_draft["value"]["operationId"]
        assert _draft(page, "agentagon.operation-draft:project_alpha:trace-import") is None
        browser.close()


def test_trace_preview_response_cannot_reenable_import_after_an_edit():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as value:
        browser = value.chromium.launch(
            headless=True, executable_path=os.environ.get("AGENTAGON_TEST_CHROMIUM")
        )
        page = browser.new_page()
        page.set_default_timeout(8_000)
        fixture = OperationFixture()
        page.route("**/*", fixture.route)
        page.goto("http://127.0.0.1/projects/project_alpha/issues")
        page.evaluate(
            """() => {
              const nativeFetch = window.fetch.bind(window);
              let holdFirstPreview = true;
              window.fetch = (input, init) => {
                const path = typeof input === 'string' ? input : input.url;
                if (holdFirstPreview && path.endsWith('/traces/preview')) {
                  holdFirstPreview = false;
                  return new Promise(resolve => {
                    window.__releaseTracePreview = () => nativeFetch(input, init).then(resolve);
                  });
                }
                return nativeFetch(input, init);
              };
            }"""
        )

        trace_input = page.get_by_label("JSON or JSONL")
        trace_input.fill('{"resourceSpans": []}')
        page.get_by_role("button", name="Check trace", exact=True).click()
        page.wait_for_function("() => typeof window.__releaseTracePreview === 'function'")
        trace_input.fill('{"resourceSpans": [{"scopeSpans": []}]}')
        page.evaluate("window.__releaseTracePreview()")
        page.wait_for_timeout(100)
        assert page.locator(".trace-preview").count() == 0
        assert page.get_by_role("button", name="Import and investigate", exact=True).count() == 0

        page.get_by_role("button", name="Check trace", exact=True).click()
        page.get_by_role("heading", name="Trace evidence is ready", exact=True).wait_for()
        assert page.get_by_role("button", name="Import and investigate", exact=True).is_visible()
        browser.close()


def test_old_delivery_stays_history_and_does_not_block_current_candidate():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as value:
        browser = value.chromium.launch(
            headless=True, executable_path=os.environ.get("AGENTAGON_TEST_CHROMIUM")
        )
        page = browser.new_page()
        page.set_default_timeout(8_000)
        fixture = DeliveryFixture()
        page.route("**/*", fixture.route)
        page.goto("http://127.0.0.1/projects/project_alpha/results/optimize/run_choice")

        page.get_by_role("button", name="Prepare local delivery", exact=True).wait_for()
        assert page.get_by_text("Earlier local packages (1)", exact=True).is_visible()
        assert page.get_by_role("link", name="Download patch", exact=True).count() == 0
        page.get_by_role("button", name="Prepare local delivery", exact=True).click()
        page.get_by_role("link", name="Download patch", exact=True).wait_for()
        prepared = next(
            item
            for item in fixture.mutations
            if item["path"] == "/api/projects/project_alpha/deliveries"
        )
        assert prepared["payload"] == {
            "kind": "optimize",
            "source_id": "run_choice",
            "publish": False,
        }
        browser.close()
