import json

import httpx
import pytest

from youtrack_valeria_watcher.config import Settings
from youtrack_valeria_watcher.valeria_client import ValeriaClient


def settings(tmp_path):
    return Settings(
        database_path=tmp_path / "db",
        lock_path=tmp_path / "lock",
        opencode_config=tmp_path / "config",
        youtrack_mcp_name="youtrack.example.com",
        youtrack_authors=frozenset({"valeria"}),
        mention="@Valeria",
        valeria_base_url="http://valeria.test",
        valeria_login="watcher",
        valeria_password="password",
        valeria_task_id="task-1",
        ca_bundle=None,
        request_timeout_seconds=60,
        max_issue_pages=5,
        max_comment_pages=5,
        mcp_concurrency=2,
    )


@pytest.mark.asyncio
async def test_submit_logs_in_and_uses_csrf_and_stable_idempotency_key(tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            assert json.loads(request.content) == {"login": "watcher", "password": "password"}
            return httpx.Response(
                200,
                json={"csrf_token": "csrf-value"},
                headers={"set-cookie": "ocw_session=session; Path=/; HttpOnly"},
            )
        assert request.url.path == "/api/tasks/task-1/runs"
        assert request.headers["x-csrf-token"] == "csrf-value"
        assert request.headers["idempotency-key"] == "youtrack-comment:Comments-1-2.0-0"
        assert json.loads(request.content) == {
            "user_input": "https://youtrack.example/issue/DEMO-1"
        }
        assert request.headers["cookie"] == "ocw_session=session"
        return httpx.Response(202, json={"id": "run-1", "status": "queued"})

    client = ValeriaClient(settings(tmp_path))
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="http://valeria.test", transport=httpx.MockTransport(handler)
    )
    try:
        run = await client.submit("https://youtrack.example/issue/DEMO-1", "Comments-1-2.0-0")
    finally:
        await client.client.aclose()

    assert run.run_id == "run-1"
    assert run.status == "queued"
    assert len(requests) == 2
