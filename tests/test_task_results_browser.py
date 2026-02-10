"""Lazy task results stay open through live updates and do not open admin history."""

from pathlib import Path
from urllib.parse import urlparse

import pytest


def test_task_result_deep_link_opens_saved_result():
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).parents[1] / "src/opencode_web/static"

    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/" or path.startswith("/static/"):
            file = static / (path.removeprefix("/static/") if path != "/" else "index.html")
            mime = {".js": "text/javascript", ".mjs": "text/javascript",
                    ".css": "text/css", ".html": "text/html"}
            route.fulfill(body=file.read_text(), content_type=mime[file.suffix])
            return
        if path == "/api/auth/me":
            payload = {"user": {"id": "worker", "login": "worker", "role": "user",
                                "tasks_only": True, "workspace_path": "/workspace"},
                       "csrf_token": "csrf"}
        elif path == "/api/tasks":
            payload = [{"id": "task", "title": "Проверка", "description": ""}]
        elif path == "/api/task-runs":
            payload = [{"id": "run", "task_id": "task", "title": "Проверка",
                        "status": "completed", "created_at": "2026-10-09",
                        "finished_at": "2026-10-09"}]
        elif path == "/api/task-runs/run/result":
            payload = {"run_id": "run", "status": "available", "text": "**Готово**"}
        else:
            payload = []
        route.fulfill(json=payload)

    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        page.add_init_script("window.EventSource = class { close() {} };")
        page.route("**/*", handle)
        page.goto("http://tasks.test/#/tasks/runs/run")
        details = page.locator('[data-task-result="run"]')
        playwright.expect(details).to_be_visible()
        assert details.evaluate("element => element.open")
        playwright.expect(details.locator("strong")).to_have_text("Готово")
        assert page.url.endswith("#/tasks/runs/run")
        browser.close()


@pytest.mark.parametrize("view", ["user", "admin", "admin-user"])
def test_task_result_disclosure_live_updates_and_retry(view):
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).parents[1] / "src/opencode_web/static"
    run = {"id": "run", "task_id": "task", "title": "Проверка", "status": "running",
           "created_at": "2026-10-06", "finished_at": None, "owner_login": "worker"}
    owner = {"id": "worker", "login": "worker", "role": "user", "tasks_only": True,
             "workspace_path": "/workspace", "status": "active"}
    empty_run = {**run, "id": "empty", "status": "completed"}
    requests, errors = [], []
    result_error = True

    def handle(route):
        path = urlparse(route.request.url).path
        requests.append(path)
        if path == "/" or path.startswith("/static/"):
            file = static / (path.removeprefix("/static/") if path != "/" else "index.html")
            mime = {".js": "text/javascript", ".mjs": "text/javascript",
                    ".css": "text/css", ".html": "text/html"}
            route.fulfill(body=file.read_text(), content_type=mime[file.suffix])
            return
        payload = []
        if path == "/api/auth/me":
            payload = {"user": owner if view == "user" else {
                "id": "admin", "login": "admin", "role": "admin",
            }, "csrf_token": "csrf"}
        elif path == "/api/tasks":
            payload = [{"id": "task", "title": "Проверка", "description": "Проверяет CI"}]
        elif path == "/api/admin/users":
            payload = [owner]
        elif path in ("/api/task-runs", "/api/admin/task-runs",
                      "/api/admin/users/worker/task-runs"):
            payload = [run, empty_run]
        elif path in ("/api/config/public", "/api/options"):
            payload = {"agents": ["build"], "models": [], "default_agent": "build"}
        elif path == "/api/task-runs/empty/result":
            payload = {"run_id": "empty", "status": "unavailable", "text": None}
        elif path == "/api/task-runs/run/result":
            if result_error:
                route.fulfill(status=503, json={"detail": "Unavailable"})
                return
            payload = {"run_id": "run", "status": "available", "text": (
                "## Готово\n\n**Успешно**\n\n[Отчёт](https://example.test/report)\n\n"
                "<script>window.resultXss = true</script>\n\n"
                + "\n\n".join(f"Строка {i}" for i in range(80))
            )}
        route.fulfill(json=payload)

    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
            window.EventSource = class {
                constructor() { window.taskEvents = this; }
                close() {}
            };
        """)
        page.route("**/*", handle)
        page.goto("http://tasks.test/")
        if view == "admin":
            page.locator('[data-route="admin-tasks"]').click()
        elif view == "admin-user":
            page.locator('[data-route="users"]').click()
            page.locator('[data-user-task-runs="worker"]').click()
        row = page.locator('[data-task-run="run"]')
        playwright.expect(row).to_be_visible()
        playwright.expect(row.locator("details")).to_have_count(0)
        run.update(status="completed", finished_at="2026-10-06")
        page.evaluate("taskEvents.onopen()")
        details = row.locator("details")
        playwright.expect(details).to_be_visible()
        assert not details.evaluate("el => el.open")
        assert "/api/task-runs/run/result" not in requests
        details.locator("summary").focus()
        page.keyboard.press("Enter")
        retry = details.get_by_role("button", name="Повторить", exact=True)
        playwright.expect(retry).to_be_visible()
        result_error = False
        retry.click()
        playwright.expect(details.get_by_role("heading", name="Готово")).to_be_visible()
        playwright.expect(details.locator("strong")).to_have_text("Успешно")
        assert not page.evaluate("Boolean(window.resultXss)")
        assert requests.count("/api/task-runs/run/result") == 2
        link = details.get_by_role("link", name="Отчёт")
        playwright.expect(link).to_have_attribute("rel", "noopener noreferrer")
        with page.expect_popup() as popup:
            link.click()
        popup.value.close()
        body = details.locator("[data-task-result-body]")
        body.evaluate("el => el.scrollTop = 200")
        run["title"] = "Новое название"
        page.evaluate(
            "task => taskEvents.onmessage({data: JSON.stringify({"
            "type: 'task.updated', task, owner_user_id: 'worker'})})",
            run,
        )
        playwright.expect(row.locator("[data-task-run-title]")).to_have_text("Новое название")
        assert details.evaluate("el => el.open")
        assert body.evaluate("el => el.scrollTop") == 200
        page.get_by_role("button", name="Обновить", exact=True).click()
        playwright.expect(details.get_by_role("heading", name="Готово")).to_be_attached()
        details.locator("summary").click()
        details.locator("summary").click()
        assert details.evaluate("el => el.open")
        assert requests.count("/api/task-runs/run/result") == 2
        empty = page.locator('[data-task-run="empty"] details')
        empty.locator("summary").click()
        playwright.expect(empty.get_by_text("Итоговое сообщение недоступно")).to_be_visible()
        assert not any(path.endswith("/messages") for path in requests)
        assert not errors
        browser.close()
