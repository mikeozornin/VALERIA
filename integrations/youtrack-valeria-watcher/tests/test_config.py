from pathlib import Path

import pytest

from youtrack_valeria_watcher.config import load_mcp_connection, load_settings
from youtrack_valeria_watcher.errors import ConfigError


def base_env(tmp_path: Path) -> dict[str, str]:
    ca = tmp_path / "ca.pem"
    ca.write_text("test")
    return {
        "WATCHER_DB_PATH": str(tmp_path / "state.db"),
        "WATCHER_LOCK_PATH": str(tmp_path / "watcher.lock"),
        "OPENCODE_CONFIG": str(tmp_path / "opencode.jsonc"),
        "YOUTRACK_VALERIA_AUTHORS": "Valeria Bot, valeria",
        "VALERIA_BASE_URL": "http://127.0.0.1:8080/",
        "VALERIA_LOGIN": "watcher",
        "VALERIA_PASSWORD": "secret",
        "VALERIA_TASK_ID": "task-1",
        "TLS_CA_BUNDLE": str(ca),
    }


def test_loads_jsonc_mcp_and_resolves_environment_reference(tmp_path: Path):
    env = base_env(tmp_path)
    Path(env["OPENCODE_CONFIG"]).write_text(
        """
        {
          // OpenCode remote integration
          mcp: {
            "youtrack.example.com": {
              type: "remote",
              url: "https://youtrack.example/mcp",
              headers: {Authorization: "{env:YOUTRACK_AUTHORIZATION}"},
              enabled: true,
            },
          },
        }
        """
    )
    env["YOUTRACK_AUTHORIZATION"] = "Bearer hidden"

    settings = load_settings(env)
    connection = load_mcp_connection(settings, env)

    assert settings.valeria_base_url == "http://127.0.0.1:8080"
    assert settings.youtrack_authors == frozenset({"valeria bot", "valeria"})
    assert connection.url == "https://youtrack.example/mcp"
    assert connection.headers == {"Authorization": "Bearer hidden"}
    assert "hidden" not in repr(connection)
    assert "secret" not in repr(settings)
    assert settings.mcp_concurrency == 8


def test_missing_referenced_mcp_secret_is_rejected(tmp_path: Path):
    env = base_env(tmp_path)
    Path(env["OPENCODE_CONFIG"]).write_text(
        '{mcp: {"youtrack.example.com": {type: "remote", '
        'url: "https://youtrack.example/mcp", '
        'headers: {Authorization: "{env:MISSING_TOKEN}"}}}}'
    )

    with pytest.raises(ConfigError, match="MISSING_TOKEN"):
        load_mcp_connection(load_settings(env), env)


def test_valeria_password_is_required(tmp_path: Path):
    env = base_env(tmp_path)
    del env["VALERIA_PASSWORD"]

    with pytest.raises(ConfigError, match="VALERIA_PASSWORD"):
        load_settings(env)
