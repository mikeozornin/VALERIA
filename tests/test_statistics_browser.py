from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


def test_statistics_navigation_date_and_refresh():
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).parents[1] / "src/opencode_web/static"
    requests = []
    empty = False

    def handle(route):
        parsed = urlparse(route.request.url)
        if parsed.path == "/" or parsed.path.startswith("/static/"):
            file = static / (parsed.path.removeprefix("/static/")
                             if parsed.path != "/" else "index.html")
            mime = {".js": "text/javascript", ".mjs": "text/javascript",
                    ".css": "text/css", ".html": "text/html"}
            route.fulfill(body=file.read_text(), content_type=mime[file.suffix])
            return
        payload = []
        if parsed.path == "/api/auth/me":
            payload = {"user": {"id": "admin", "login": "admin", "role": "admin"},
                       "csrf_token": "test"}
        elif parsed.path == "/api/config/public":
            payload = {"agents": ["build"], "default_agent": "build", "workspace": "/test"}
        elif parsed.path == "/api/options":
            payload = {"models": [], "agents": ["build"]}
        elif parsed.path == "/api/admin/statistics":
            query = parse_qs(parsed.query)
            requests.append(query)
            payload = {
                "day": query.get("day", ["2026-10-04"])[0],
                "timezone": query["timezone"][0], "completed_all_time": 600,
                "completed_day": 0 if empty else 12,
                "average_execution_seconds": None if empty else 90,
                "average_total_seconds": None if empty else 3700,
                "execution_samples": 0 if empty else 12, "total_samples": 0 if empty else 12,
            }
        route.fulfill(json=payload)

    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page(timezone_id="UTC")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
            window.EventSource = class {
                constructor() { window.statisticsEvents = this; }
                close() {}
            };
        """)
        page.route("**/*", handle)
        page.goto("http://statistics.test/")
        page.locator('[data-route="statistics"]').click()
        values = page.locator(".statistics-cards .stat strong")
        playwright.expect(values).to_have_text(["1 мин 30 с", "1 ч 1 мин", "12", "600"])
        page.locator("#statistics-day").fill("2026-10-03")
        with page.expect_response("**/api/admin/statistics?*day=2026-10-03"):
            page.locator("#statistics-form .btn-primary").click()
        playwright.expect(page.locator("#statistics-day")).to_have_value("2026-10-03")
        page.locator("#statistics-today").click()
        playwright.expect(page.locator("#statistics-day")).to_have_value("2026-10-04")
        assert any(query.get("day") == ["2026-10-03"] for query in requests)
        empty = True
        page.evaluate("""statisticsEvents.onmessage({data: JSON.stringify({
            type: 'task.updated', task: {id: 'completed-task'}
        })})""")
        playwright.expect(values).to_have_text(["Нет данных", "Нет данных", "0", "600"])
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        browser.close()
