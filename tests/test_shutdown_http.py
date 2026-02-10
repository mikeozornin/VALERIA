from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
from pathlib import Path

import httpx
import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="SIGTERM shutdown")
async def test_sigterm_with_open_sse_reaches_lifespan_cleanup(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    password = tmp_path / "admin-password"
    password.write_text("test-only-password")
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "SESSION_SECRET": "test-only-session-secret-" * 3,
        "APP_HOST": "127.0.0.1",
        "APP_PORT": str(port),
        "APP_COOKIE_SECURE": "false",
        "APP_DATABASE_PATH": str(tmp_path / "app.db"),
        "ALLOWED_WORKSPACE_ROOTS": str(tmp_path),
        "BOOTSTRAP_ADMIN_LOGIN": "admin",
        "BOOTSTRAP_ADMIN_PASSWORD_FILE": str(password),
    }
    # Exercise the real CLI/uvicorn settings without loading a deployment .env.
    command = "import opencode_web.cli as cli; cli.load_dotenv = lambda: None; cli.main()"
    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", command, cwd=tmp_path, env=env,
            stdout=log, stderr=log,
        )
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                async with asyncio.timeout(15):
                    while True:
                        assert process.returncode is None, log_path.read_text()
                        try:
                            if (await client.get("/api/health")).status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(0.05)
                response = await client.post(
                    "/api/auth/login", json={"login": "admin", "password": "test-only-password"}
                )
                assert response.status_code == 200
                async with client.stream("GET", "/api/events", timeout=None) as events:
                    assert events.status_code == 200
                    process.terminate()
                    # Keep the browser connection open until the server exits.
                    await asyncio.wait_for(process.wait(), 20)
                assert process.returncode in (0, -signal.SIGTERM)
                assert "Application shutdown complete" in log_path.read_text()
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
