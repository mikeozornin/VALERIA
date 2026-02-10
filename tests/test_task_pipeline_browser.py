"""Optional browser regression: pip install playwright; playwright install chromium."""

from pathlib import Path
from urllib.parse import urlparse

import pytest


def test_admin_can_save_multiple_pipeline_successors():
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).parents[1] / "src/opencode_web/static"
    tasks = [
        {
            "id": key,
            "title": key,
            "content": f"Prompt {key}",
            "description": "",
            "agent": "build",
            "model": "provider/model",
            "queue_id": "default",
            "is_published": key == "A",
            "next_task_ids": ["B"] if key == "A" else [],
        }
        for key in ("A", "B", "C")
    ]
    options = {
        "agents": ["build", "plan"],
        "default_agent": "build",
        "default_model": "provider/model",
        "models": [{"id": "provider/model"}],
    }
    saved = []

    def route_handler(route):
        path = urlparse(route.request.url).path
        if path == "/" or path.startswith("/static/"):
            file = static / (path.removeprefix("/static/") if path != "/" else "index.html")
            mime = {
                ".js": "text/javascript",
                ".mjs": "text/javascript",
                ".css": "text/css",
                ".html": "text/html",
            }
            route.fulfill(body=file.read_text(), content_type=mime[file.suffix])
            return
        payload = []
        if path == "/api/auth/me":
            payload = {
                "user": {"id": "admin", "login": "admin", "role": "admin"},
                "csrf_token": "csrf",
            }
        elif path == "/api/events":
            route.fulfill(body=": connected\n\n", content_type="text/event-stream")
            return
        elif path in ("/api/config/public", "/api/options"):
            payload = options
        elif path == "/api/admin/tasks":
            payload = tasks
        elif path == "/api/admin/queues":
            payload = [
                {"id": "default", "name": "Основная", "status": "active"},
                {"id": "ci", "name": "CI", "status": "active"},
            ]
        elif path == "/api/admin/tasks/A" and route.request.method == "PATCH":
            assert route.request.headers["x-csrf-token"] == "csrf"
            saved.append(route.request.post_data_json)
            tasks[0].update(saved[-1])
            payload = tasks[0]
        route.fulfill(json=payload)

    with playwright.sync_playwright() as engine:
        try:
            browser = engine.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f"Install Playwright chromium: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", route_handler)
        page.goto("http://pipeline.test/")
        page.locator('[data-route="admin-tasks"]').click()
        page.locator('[data-admin-task="A"]').click()
        field = page.locator("#task-successors")
        playwright.expect(field.locator("input")).to_have_count(2)
        playwright.expect(field.locator('[value="B"]')).to_be_checked()
        page.locator("#task-queue").select_option("ci")
        field.locator('[value="C"]').check()
        page.locator('[name="plan_first"]').check()
        playwright.expect(page.locator("#task-unsaved")).to_be_visible()
        page.get_by_role("button", name="Сохранить", exact=True).click()
        playwright.expect(page.locator("#toast")).to_have_text("Таска сохранена")
        assert saved[-1]["next_task_ids"] == ["B", "C"]
        assert saved[-1]["plan_first"] is True
        assert saved[-1]["queue_id"] == "ci"
        playwright.expect(page.locator('[name="plan_first"]')).to_be_checked()
        page.locator("#task-agent").select_option("plan")
        playwright.expect(page.locator('[name="plan_first"]')).not_to_be_checked()
        playwright.expect(page.locator('[name="plan_first"]')).to_be_disabled()
        playwright.expect(page.locator('#task-successors [value="C"]')).to_be_checked()
        page.locator('#task-successors [value="B"]').uncheck()
        page.locator('#task-successors [value="C"]').uncheck()
        with page.expect_response(lambda response: response.request.method == "PATCH"):
            page.get_by_role("button", name="Сохранить", exact=True).click()
        assert saved[-1]["next_task_ids"] == []
        assert saved[-1]["plan_first"] is False
        assert not errors
        browser.close()
