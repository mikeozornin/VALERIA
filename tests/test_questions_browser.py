"""Optional browser regression: pip install playwright; playwright install chromium."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import pytest
from test_questions import REQUEST


@pytest.mark.parametrize(
    "viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}]
)
def test_question_flow_in_chat(viewport):
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).parents[1] / "src/opencode_web/static"
    pending = [deepcopy(REQUEST)]
    replies = []
    question_reads = []
    failures = {"reply": False, "read": False}
    chat = {
        "id": "chat",
        "title": "Question test",
        "opencode_session_id": "root",
        "workspace_snapshot": "/workspace",
        "status": "running",
    }
    options = {"agents": ["build"], "models": [], "default_agent": "build"}

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
        status = 200
        if path == "/api/auth/me":
            payload = {
                "user": {"id": "user", "login": "owner", "role": "user"},
                "csrf_token": "test-csrf",
            }
        elif path == "/api/events":
            route.fulfill(body=": connected\n\n", content_type="text/event-stream")
            return
        elif path == "/api/chats":
            payload = [chat]
        elif path == "/api/chats/chat":
            payload = chat
        elif path == "/api/config/public":
            payload = {**options, "workspace": "/workspace", "max_active_sessions": 1}
        elif path == "/api/options":
            payload = options
        elif path == "/api/chats/chat/mcp":
            payload = {"servers": []}
        elif path == "/api/chats/chat/questions":
            question_reads.append(True)
            payload = pending
            if failures["read"]:
                status, payload = 503, {"detail": "offline"}
        elif path == "/api/chats/chat/questions/que-root/reply":
            assert route.request.headers["x-csrf-token"] == "test-csrf"
            replies.append(route.request.post_data_json)
            if failures["reply"]:
                status, payload = 503, {"detail": "Temporary reply failure"}
            else:
                pending.clear()
                payload = {"accepted": True}
        route.fulfill(status=status, json=payload)

    with playwright.sync_playwright() as engine:
        try:
            browser = engine.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f"Install Playwright chromium: {exc}")
        page = browser.new_page(viewport=viewport)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", route_handler)
        page.goto("http://question.test/")
        page.locator('[data-chat="chat"]').click()
        form = page.locator(".question-card")
        playwright.expect(form).to_be_visible()
        playwright.expect(page.locator("#chat-status")).to_have_text("Ожидает вашего ответа")
        form.get_by_role("button", name="Отправить ответ").click()
        playwright.expect(form.locator(".error")).to_contain_text("Ответьте на каждый вопрос")
        form.get_by_role("radio", name="Yes").check()
        form.get_by_role("checkbox", name="A First").check()
        custom = form.get_by_role("textbox", name="Свой ответ: Targets")
        custom.fill("My custom target")
        # Real timer poll must preserve both focused text and selected options.
        count = len(question_reads)
        with page.expect_response(lambda response: response.url.endswith("/questions")):
            pass
        assert len(question_reads) > count
        playwright.expect(custom).to_have_value("My custom target")
        playwright.expect(form.get_by_role("radio", name="Yes")).to_be_checked()
        failures["read"] = True
        playwright.expect(page.locator(".question-error")).to_contain_text("offline", timeout=6000)
        playwright.expect(custom).to_have_value("My custom target")
        failures["read"] = False
        failures["reply"] = True
        form.get_by_role("button", name="Отправить ответ").click()
        playwright.expect(form.locator(".error")).to_have_text("Temporary reply failure")
        playwright.expect(custom).to_have_value("My custom target")
        failures["reply"] = False
        # Two synchronous submits while awaiting HTTP must produce one request.
        form.evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
        playwright.expect(form).to_have_count(0)
        assert len(replies) == 2  # One failed attempt and one explicit retry.
        assert replies[-1] == {"answers": [["Yes"], ["A", "My custom target"]]}
        playwright.expect(page.locator("#chat-status")).to_have_text("running")
        playwright.expect(page.locator("#question-waiting")).to_be_hidden()
        # Reload and reopen must fetch a pending question independently of SSE history.
        pending.append(deepcopy(REQUEST))
        page.reload()
        page.locator('[data-chat="chat"]').click()
        playwright.expect(page.locator(".question-card")).to_be_visible()
        assert not errors, json.dumps(errors)
        browser.close()
