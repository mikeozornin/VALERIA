from __future__ import annotations

from test_chat_actions import ui_app as ui_app

from opencode_web.app import PACKAGE_DIR

INDEX = (PACKAGE_DIR / "static" / "index.html").read_bytes()


async def test_page_urls_serve_the_single_page_app(ui_app):
    t = ui_app
    t.client.cookies.clear()  # pages load before signing in
    for path in (
        "/", "/questions/chat-1", "/tasks/runs/run-1", "/folder/chat-2",
        "/admin/users?status=blocked", "/apiary", "/staticky/page",
    ):
        response = await t.client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html"), path
        assert response.content == INDEX, path


async def test_unknown_api_and_static_paths_stay_not_found(ui_app):
    t = ui_app
    for method, path in (
        ("GET", "/api/nope"), ("GET", "/api"), ("GET", "/api/"), ("POST", "/api/nope"),
        ("DELETE", "/api/admin/nope"), ("GET", "/static/"),
    ):
        response = await t.client.request(method, path)
        assert response.status_code == 404, (method, path)
        assert response.json() == {"detail": "Not Found"}, (method, path)
    response = await t.client.get("/static/missing.js")
    assert response.status_code == 404 and response.content != INDEX
    # Real routes keep their own answers.
    assert (await t.client.get("/api/health")).json()["status"] == "ok"
    assert (await t.client.post("/api/health")).status_code == 405
