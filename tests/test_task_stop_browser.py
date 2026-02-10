"""Owner cancellation stays usable across live updates and API errors."""

from pathlib import Path
from urllib.parse import urlparse

import pytest


@pytest.mark.parametrize("tasks_only", [False, True])
def test_owner_task_stop_ui(tasks_only):
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).parents[1] / "src/opencode_web/static"
    runs = [
        {
            "id": status, "task_id": "task", "title": f"Проверка {status}",
            "status": status, "created_at": "2026-10-04", "finished_at": None,
        }
        for status in ("queued", "running", "cancelling", "completed", "failed", "cancelled")
    ]
    stops = []
    fail_stop = True

    def route_handler(route):
        nonlocal fail_stop
        path = urlparse(route.request.url).path
        if path == "/" or path.startswith("/static/"):
            file = static / (path.removeprefix("/static/") if path != "/" else "index.html")
            mime = {
                ".js": "text/javascript", ".mjs": "text/javascript",
                ".css": "text/css", ".html": "text/html",
            }
            route.fulfill(body=file.read_text(), content_type=mime[file.suffix])
            return
        payload = []
        if path == "/api/auth/me":
            payload = {
                "user": {
                    "id": "worker", "login": "worker", "role": "user",
                    "tasks_only": tasks_only, "workspace_path": "/workspace",
                },
                "csrf_token": "csrf",
            }
        elif path == "/api/tasks":
            payload = [{"id": "task", "title": "Проверка", "description": "Проверка CI"}]
        elif path == "/api/task-runs":
            payload = runs
        elif path.endswith("/stop"):
            assert route.request.method == "POST"
            assert route.request.headers["x-csrf-token"] == "csrf"
            stops.append(path)
            if fail_stop:
                fail_stop = False
                route.fulfill(status=503, json={"detail": "Временная ошибка"})
                return
            run_id = path.split("/")[-2]
            run = next(run for run in runs if run["id"] == run_id)
            run["status"] = "cancelled" if run["status"] == "queued" else "cancelling"
            payload = run
        route.fulfill(json=payload)

    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
            window.EventSource = class {
                constructor() { window.taskEvents = this; }
                close() {}
            };
        """)
        page.route("**/*", route_handler)
        page.goto("http://tasks.test/")
        if not tasks_only:
            page.locator('[data-route="tasks"]').click()
        draft = page.locator("#task-user-input")
        draft.fill("Не терять этот черновик")
        for status in ("completed", "failed", "cancelled"):
            playwright.expect(page.locator(f'[data-task-run="{status}"] button')).to_have_count(0)
        playwright.expect(page.locator('[data-stop-task-run="cancelling"]')).to_be_disabled()
        running = page.locator('[data-stop-task-run="running"]')
        page.once("dialog", lambda dialog: dialog.dismiss())
        running.click()
        assert stops == []
        page.on("dialog", lambda dialog: dialog.accept())
        running.click()
        playwright.expect(page.locator("#toast")).to_have_text("Временная ошибка")
        playwright.expect(running).to_be_enabled()
        running.click()
        playwright.expect(running).to_be_disabled()
        playwright.expect(running).to_have_text("Останавливается…")
        playwright.expect(page.locator('[data-task-run="running"] .badge')).to_have_text(
            "Останавливается…"
        )
        # A duplicate DOM click cannot submit another stop request.
        running.evaluate("button => button.click()")
        assert stops == ["/api/task-runs/running/stop"] * 2
        run = next(run for run in runs if run["id"] == "running")
        run["status"] = "cancelled"
        page.evaluate(
            "task => taskEvents.onmessage({data: JSON.stringify({type: 'task.updated', task})})",
            run,
        )
        playwright.expect(page.locator('[data-task-run="running"] .badge')).to_have_text(
            "Отменена"
        )
        playwright.expect(running).to_have_count(0)
        page.locator('[data-stop-task-run="queued"]').click()
        playwright.expect(page.locator('[data-task-run="queued"] .badge')).to_have_text(
            "Отменена"
        )
        playwright.expect(page.locator('[data-stop-task-run="queued"]')).to_have_count(0)
        playwright.expect(draft).to_have_value("Не терять этот черновик")
        assert not errors
        browser.close()
