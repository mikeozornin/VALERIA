"""A lost acceptance response is safely retried, and pending chat history stays usable."""

from pathlib import Path
from urllib.parse import urlparse

import pytest


def test_pending_chat_retry_history_and_preparation_status():
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).parents[1] / "src/opencode_web/static"
    chat = {
        "id": "chat", "title": "Новый диалог", "status": "queued",
        "opencode_session_id": "pending_chat", "workspace_snapshot": "/workspace",
    }
    job = {"id": "job", "chat_id": "chat", "status": "queued"}
    messages = [{
        "info": {"id": "msg_one", "role": "user"},
        "parts": [{"type": "text", "text": "Первый запрос"}], "submission_status": "queued",
    }]
    options = {"agents": ["build"], "models": [], "default_agent": "build"}
    request_keys = []
    sends = []

    def route_handler(route):
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
                "user": {"id": "user", "login": "owner", "role": "user"},
                "csrf_token": "csrf",
            }
        elif path == "/api/chats":
            if route.request.method == "POST":
                request_keys.append(route.request.headers.get("idempotency-key"))
                if len(request_keys) == 1:
                    route.abort()
                    return
                payload = {"chat": chat, "job": job}
        elif path == "/api/chats/chat":
            payload = chat
        elif path == "/api/chats/chat/messages":
            if route.request.method == "POST":
                sends.append(route.request.post_data_json)
                payload = job
            else:
                payload = messages
        elif path == "/api/queue/me":
            payload = [job]
        elif path == "/api/config/public":
            payload = {**options, "workspace": "/workspace", "max_active_sessions": 1}
        elif path == "/api/options":
            payload = options
        elif path == "/api/chats/chat/mcp":
            payload = {"servers": [], "pending": True}
        route.fulfill(json=payload)

    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
            window.EventSource = class {
                constructor() { window.queueEvents = this; }
                close() {}
            };
        """)
        page.route("**/*", route_handler)
        page.goto("http://queued.test/")
        form = page.locator("#new-chat")
        form.locator("textarea").fill("Первый запрос")
        form.evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
        playwright.expect(page.locator("#toast")).to_contain_text("Failed to fetch")
        form.locator("button[type=submit]").click()
        playwright.expect(page.locator("#message-list")).to_contain_text("Первый запрос")
        playwright.expect(page.locator("#chat-status")).to_have_text("В очереди")
        assert len(request_keys) == 2 and request_keys[0] == request_keys[1]
        assert request_keys[0]
        composer = page.locator("#composer")
        composer.locator("textarea").fill("Следующий запрос")
        chat["status"] = job["status"] = messages[0]["submission_status"] = "dispatching"
        page.evaluate(
            "job => queueEvents.onmessage({data: JSON.stringify({type: 'job.updated', job})})", job,
        )
        playwright.expect(page.locator("#chat-status")).to_have_text("Подготовка окружения")
        playwright.expect(composer.locator("textarea")).to_have_value("Следующий запрос")
        composer.evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
        playwright.expect(composer.locator("textarea")).to_have_value("")
        assert len(sends) == 1
        assert not errors
        browser.close()
