from __future__ import annotations

from pathlib import Path

import pytest

from opencode_web.config import Settings


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspaces"
    root.mkdir()
    return root


@pytest.fixture
def settings(tmp_path: Path, workspace: Path) -> Settings:
    password_file = tmp_path / "admin-password"
    password_file.write_text("correct-horse-battery-staple", encoding="utf-8")
    return Settings(
        host="127.0.0.1",
        port=8080,
        base_url="http://127.0.0.1:8080",
        database_path=tmp_path / "test.db",
        cookie_secure=False,
        max_active_sessions=1,
        max_active_sessions_hard_limit=5,
        max_active_sessions_per_user=0,
        max_active_sessions_per_user_hard_limit=5,
        max_queued_messages=100,
        allowed_workspace_roots=(workspace,),
        allow_shared_workspaces=False,
        opencode_bin=Path("/usr/bin/opencode"),
        opencode_host="127.0.0.1",
        opencode_port_start=45100,
        opencode_port_end=45120,
        opencode_username="opencode",
        opencode_password="test-opencode-password",
        default_agent="build",
        allowed_agents=("build", "plan"),
        default_model=None,
        allowed_models=("*",),
        opencode_start_timeout=2,
        session_secret="s" * 32,
        auth_session_ttl_hours=24,
        bootstrap_admin_login="admin",
        bootstrap_admin_password_file=password_file,
        log_level="INFO",
    )
