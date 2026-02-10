from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from opencode_web.config import Settings
from opencode_web.opencode_client import OpenCodeManager


@pytest.mark.skipif(
    os.getenv("RUN_OPENCODE_INTEGRATION") != "1",
    reason="Set RUN_OPENCODE_INTEGRATION=1 to start a real OpenCode server",
)
async def test_managed_opencode_health_path_and_agents(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings(
        host="127.0.0.1",
        port=8080,
        base_url="http://127.0.0.1:8080",
        database_path=tmp_path / "test.db",
        cookie_secure=False,
        max_active_sessions=1,
        max_active_sessions_hard_limit=2,
        max_active_sessions_per_user=0,
        max_active_sessions_per_user_hard_limit=2,
        max_queued_messages=10,
        allowed_workspace_roots=(workspace,),
        allow_shared_workspaces=False,
        opencode_bin=Path("/usr/bin/opencode"),
        opencode_host="127.0.0.1",
        opencode_port_start=45991,
        opencode_port_end=45995,
        opencode_username="opencode",
        opencode_password="integration-test-only",
        default_agent="build",
        allowed_agents=("build", "plan"),
        default_model=None,
        allowed_models=("*",),
        opencode_start_timeout=120,
        session_secret="i" * 32,
        auth_session_ttl_hours=1,
        bootstrap_admin_login=None,
        bootstrap_admin_password_file=None,
        log_level="INFO",
    )
    received: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    async def event_sink(event: dict[str, object]) -> None:
        await received.put(event)

    manager = OpenCodeManager(settings, event_sink)
    session_id: str | None = None
    try:
        client, concurrent_client = await asyncio.gather(
            manager.ensure(str(workspace)), manager.ensure(str(workspace))
        )
        assert concurrent_client is client
        health = await client.health()
        agents = await client.agents()
        path_info = await client.path()
        assert health["healthy"] is True
        assert {item["name"] for item in agents if item["mode"] == "primary"} >= {
            "build",
            "plan",
        }
        assert Path(path_info["directory"]).resolve() == workspace
        while True:
            connected = await asyncio.wait_for(received.get(), 10)
            payload = connected.get("event", {}).get("payload", {})  # type: ignore[union-attr]
            if payload.get("type") == "server.connected":
                break

        session = await client.create_session("Integration event probe")
        session_id = session["id"]
        while True:
            event = await asyncio.wait_for(received.get(), 10)
            payload = event.get("event", {}).get("payload", {})  # type: ignore[union-attr]
            if payload.get("type") == "session.created":
                assert payload["properties"]["sessionID"] == session_id
                break

        assert isinstance(await client.mcp_statuses(), dict)
    finally:
        if session_id:
            await client.delete_session(session_id)
        await manager.close()
