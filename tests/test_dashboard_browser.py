"""Optional Chromium checks for dashboard navigation and accessible empty states."""

import json

import pytest
from support.dashboard import make_audit, running
from support.evaluation import draft

from agentagon.experiments import engine, preparation

playwright = pytest.importorskip("playwright.sync_api", reason="install the browser extra")


@pytest.mark.parametrize("width", [360, 1280])
def test_saved_issue_handoff_copies_a_scoped_request_without_starting_work(
    workspace, imported, width
):
    from support.audit import finish

    from agentagon.storage.issues import list_issues

    finish(workspace, imported)
    issue_id = list_issues(workspace)[0]["issue_id"]
    before = {
        p.relative_to(workspace.state): p.read_bytes()
        for p in workspace.state.rglob("*")
        if p.is_file()
    }
    with running(workspace) as server, playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": width, "height": 1000},
                permissions=["clipboard-read", "clipboard-write"],
            )
            methods = []
            page.on("request", lambda request: methods.append(request.method))
            page.goto(f"http://127.0.0.1:{server.server_port}/?audit={imported}")
            page.locator(".issue > summary").click()
            page.get_by_text("Fix this issue", exact=True).click()
            prompt = page.get_by_role("textbox", name="Fix request for Weather requests time out")
            playwright.expect(prompt).to_be_visible()
            value = prompt.input_value()
            assert "Use ag:fix to fix saved issue" in value
            assert f"--audit {imported} --issue {issue_id}" in value
            assert "do not treat observed outputs as ground truth" in value
            assert "ordinary successes" in value
            page.get_by_role("button", name="Copy request", exact=True).click()
            playwright.expect(page.get_by_role("status").filter(has_text="Copied.")).to_be_visible()
            assert page.evaluate("navigator.clipboard.readText()") == value
            assert set(methods) == {"GET"}
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        finally:
            browser.close()
    assert {
        p.relative_to(workspace.state): p.read_bytes()
        for p in workspace.state.rglob("*")
        if p.is_file()
    } == before


@pytest.mark.parametrize("view", ["audit", "eval", "fix"])
def test_workflow_dashboard_opens_before_work_and_reuses_server(
    workspace, application, specification, view
):
    work = workspace if view == "audit" else application
    selection, selector, empty = {
        "audit": ("audit", "audit-select", "empty"),
        "eval": ("evaluation", "eval-select", "eval-empty"),
        "fix": ("run", "run-select", "fix-empty"),
    }[view]

    def start_record():
        if view == "audit":
            return make_audit(work)
        if view == "eval":
            return draft(work, specification)[0]["evaluation_id"]
        return engine.start(work, specification, "local")["run_id"]

    with running(work) as server, playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            page = browser.new_page()
            url = f"http://127.0.0.1:{server.server_port}/"
            page.goto(f"{url}?view={view}")
            playwright.expect(page.locator(f"#{empty}")).to_be_visible()
            playwright.expect(page.locator(f"#{selector}")).to_be_disabled()

            active_id = start_record()
            start_record()
            # The host selects its active record, even with another saved result.
            page.goto(f"{url}?{selection}={active_id}")
            playwright.expect(page.locator(f"#{selector}")).to_have_value(active_id)
            playwright.expect(page.locator(f"#view-{view}")).to_have_attribute(
                "aria-pressed", "true"
            )
            playwright.expect(page.locator("#workspace-path")).to_have_text(str(work.root))
            page.get_by_role("button", name="Refresh", exact=True).click()
            playwright.expect(page.locator(f"#{selector}")).to_have_value(active_id)
            playwright.expect(page.locator("#error")).to_be_hidden()

            # An explicit invalid selection must not silently show another result.
            page.goto(f"{url}?{selection}=missing")
            playwright.expect(page.locator("#error")).to_contain_text("not found")
        finally:
            browser.close()


@pytest.mark.parametrize("width", [360, 1280])
def test_empty_views_refresh_and_themes(workspace, width):
    make_audit(workspace, goal="Browser smoke fixture")
    with running(workspace) as server, playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: errors.append(message.text) if message.type == "error" else None,
            )
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            announcement = page.locator("#announcement")
            playwright.expect(announcement).to_contain_text("Code audit")
            page.get_by_role("button", name="Fix runs", exact=True).click()
            playwright.expect(announcement).to_have_text("No fix runs yet.")
            playwright.expect(page.locator("#run-select")).to_have_text("No fix runs yet")
            page.get_by_role("button", name="Evaluations", exact=True).click()
            for _ in range(2):
                playwright.expect(page.locator("#eval-empty")).to_be_visible()
                playwright.expect(page.locator("#eval-select")).to_be_disabled()
                playwright.expect(page.locator("#eval-select")).to_have_text("No evaluations yet")
                playwright.expect(announcement).to_have_text("No evaluations yet.")
                page.get_by_role("button", name="Refresh", exact=True).click()
            page.get_by_role("button", name="Switch to dark theme", exact=True).click()
            playwright.expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            page.reload()
            playwright.expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            playwright.expect(announcement).to_have_text("No evaluations yet.")
            page.get_by_role("button", name="Switch to light theme", exact=True).click()
            playwright.expect(page.locator("html")).to_have_attribute("data-theme", "light")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert errors == []
        finally:
            browser.close()


def test_failed_preflight_shows_skipped_benchmark(application, specification):
    specification["checks"][0].update(preflight=True, baseline_expected="fail")
    started = engine.start(application, specification, "local")
    failed = engine.run(application, started["run_id"])
    assert failed["candidate"]["state"] == "failed"
    with (
        running(application, run_id=started["run_id"]) as server,
        playwright.sync_playwright() as driver,
    ):
        browser = driver.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            inspector = page.locator("#candidate-inspector")
            playwright.expect(inspector).to_contain_text("preflight check failed")
            playwright.expect(inspector).to_contain_text(
                "benchmark · skipped: preflight quality-control failed"
            )
            playwright.expect(inspector).to_contain_text("quality-control · exit 0")
        finally:
            browser.close()


@pytest.mark.parametrize("min_delta", [10, 21])
def test_metric_comparison_distinguishes_correct_cases_from_negative_controls(
    application, specification, min_delta
):
    specification.update(repetitions=1, seeds=[0])
    started, plan = draft(application, specification)
    source = application.state / "known-correct.json"
    source.write_text(json.dumps({"latency": 80, "quality": 0.9, "variant": "correct"}))
    plan["metric_cases"] = [
        {
            "id": "known-correct",
            "description": "A known correct faster variant",
            "mutations": [
                {"source": str(source.relative_to(application.root)), "path": "app.json"}
            ],
        }
    ]
    plan["metric_comparisons"] = [
        {
            "metric": "latency",
            "better": "known-correct",
            "worse": "baseline",
            "min_delta": min_delta,
        }
    ]
    preparation.check(application, started["evaluation_id"], plan)
    with running(application) as server, playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 360, "height": 900})
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.get_by_role("button", name="Evaluations", exact=True).click()
            content = page.locator("#eval-content")
            playwright.expect(content).to_contain_text(
                "known-correct · repetition 1 · correctness checks passed"
            )
            playwright.expect(content).to_contain_text(
                "low-quality · repetition 1 · expected rejection confirmed"
            )
            playwright.expect(content).to_contain_text(
                f"latency comparison · {'passed' if min_delta == 10 else 'failed'}"
            )
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        finally:
            browser.close()


@pytest.mark.parametrize("width", [360, 1280])
def test_benchmark_and_unmeasured_patch_journeys_show_limits_without_running_work(
    application, width
):
    from pathlib import Path

    from agentagon.experiments import benchmarks, delivery, patches

    assessment = {
        "goal": "Audit existing retry eval coverage",
        "author": "audit-host",
        "dataset_paths": ["app.json"],
        "entrypoint_paths": ["benchmark.py"],
        "assessments": {
            topic: {
                "status": "unknown",
                "rationale": "Sensitivity needs validation.",
                "evidence": [],
            }
            for topic in benchmarks.ASSESSMENTS
        },
        "proposed_cases": ["Retry after a timeout."],
    }
    benchmarks.draft(application, assessment)
    patch = patches.start(
        application,
        {
            "editable_paths": ["app.json"],
            "checks": [],
            "no_checks_reason": "No meaningful executable checks for this fixture change.",
            "timeout_seconds": 5,
            "max_attempts": 1,
        },
        goal="Clarify the retry variant",
        author="patch-author",
        reason_no_comparison="Baseline credentials unavailable.",
    )
    (application.root / patch["worktree"] / "app.json").write_text(
        json.dumps({"latency": 100, "quality": 0.8, "variant": "reviewed"})
    )
    checked = patches.check(application, patch["patch_id"])
    review = checked["checks"][-1]["review_template"]
    review.update(
        reviewer="independent-reviewer",
        verdict="pass",
        rationale="Reviewed exact diff and limitations.",
    )
    review["assessments"] = dict.fromkeys(review["assessments"], True)
    patches.review(application, patch["patch_id"], review)
    shipped = delivery.deliver(application, patch_id=patch["patch_id"])
    before = {path: path.read_bytes() for path in application.state.rglob("*") if path.is_file()}
    with running(application) as server, playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 1000})
            methods, errors = [], []
            page.on("request", lambda request: methods.append(request.method))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/?view=audit")
            playwright.expect(
                page.get_by_role("heading", name="Benchmark readiness")
            ).to_be_visible()
            page.locator("#journey-items summary").click()
            playwright.expect(page.get_by_text("Draft; not measured", exact=False)).to_be_visible()
            playwright.expect(
                page.get_by_text("Declare a bounded preparation budget.")
            ).to_be_visible()
            playwright.expect(page.locator("#empty")).to_be_hidden()
            page.locator("#view-fix").click()
            playwright.expect(page.get_by_role("heading", name="Reviewed patches")).to_be_visible()
            page.locator("#journey-items summary").click()
            playwright.expect(
                page.get_by_text("No executable checks run", exact=True)
            ).to_be_visible()
            playwright.expect(page.get_by_text("Baseline credentials unavailable.")).to_be_visible()
            playwright.expect(page.get_by_text("Independent review: pass")).to_be_visible()
            playwright.expect(page.locator("#fix-empty")).to_be_hidden()
            with page.expect_download() as download:
                page.locator("#journey-items").get_by_role("link", name="diff", exact=True).click()
            downloaded = download.value
            assert (
                Path(downloaded.path()).read_bytes()
                == Path(shipped["artifacts"]["diff"]).read_bytes()
            )
            page.get_by_role("button", name="Refresh", exact=True).click()
            playwright.expect(page.get_by_role("heading", name="Reviewed patches")).to_be_visible()
            assert not errors
            assert set(methods) == {"GET"}
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        finally:
            browser.close()
    assert {
        path: path.read_bytes() for path in application.state.rglob("*") if path.is_file()
    } == before
