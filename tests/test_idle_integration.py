from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest

from opencode_web.opencode_client import OpenCodeManager


@pytest.mark.skipif(
    os.getenv("RUN_OPENCODE_IDLE_INTEGRATION") != "1",
    reason="Set RUN_OPENCODE_IDLE_INTEGRATION=1 to exercise real idle-stop/recovery",
)
async def test_real_idle_stop_preserves_session(settings, workspace):
    manager = OpenCodeManager(replace(
        settings,
        opencode_bin=Path(os.environ.get("OPENCODE_TEST_BIN", "/usr/bin/opencode")),
        opencode_idle_timeout_seconds=1,
        opencode_start_timeout=120,
    ))
    session_id = None
    try:
        client = await manager.ensure(str(workspace))
        session = await client.create_session(title="Idle-stop integration probe")
        session_id = session["id"]
        history = await client.messages(session_id)
        before_process = manager.instances()[0].process
        before_pid = before_process.pid
        manager.start_idle_reaper(lambda _: False)
        async with asyncio.timeout(30):
            while manager.instances() or before_process.returncode is None:
                await asyncio.sleep(0.1)
        assert not Path(f"/proc/{before_pid}").exists()
        recovered = await client.session(session_id)
        assert recovered["id"] == session_id
        assert recovered["title"] == session["title"]
        assert await client.messages(session_id) == history
        assert manager.instances()[0].process.pid != before_pid
    finally:
        if session_id:
            client = await manager.ensure(str(workspace))
            await client.delete_session(session_id)
        await manager.close()
