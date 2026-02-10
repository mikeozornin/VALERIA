from __future__ import annotations

from pathlib import Path

import pytest

from opencode_web.config import ConfigError, Settings


def test_workspace_must_be_inside_allowed_root(
    settings: Settings, workspace: Path, tmp_path: Path
) -> None:
    project = workspace / "relay"
    project.mkdir()
    assert settings.validate_workspace(str(project)) == project

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ConfigError, match="outside"):
        settings.validate_workspace(str(outside))


def test_ensure_workspace_creates_nested_directory_inside_allowed_root(
    settings: Settings, workspace: Path
) -> None:
    project = workspace / "team" / "relay"
    assert not project.exists()

    assert settings.ensure_workspace(project) == project
    assert project.is_dir()

    file_path = workspace / "not-a-directory"
    file_path.write_text("content", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a directory"):
        settings.ensure_workspace(file_path)


def test_ensure_workspace_does_not_create_outside_allowed_root(
    settings: Settings, workspace: Path
) -> None:
    outside = workspace.parent / "outside" / "relay"

    with pytest.raises(ConfigError, match="outside"):
        settings.ensure_workspace(outside)

    traversal = Path("../outside/relay")
    with pytest.raises(ConfigError, match="outside"):
        settings.ensure_workspace(traversal)

    assert not outside.exists()


def test_relative_workspace_is_created_under_single_allowed_root(
    settings: Settings, workspace: Path
) -> None:
    expected = workspace / "relative" / "project"

    assert settings.ensure_workspace("relative/project") == expected
    assert expected.is_dir()


def test_settings_rejects_short_session_secret(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    monkeypatch.setenv("ALLOWED_WORKSPACE_ROOTS", str(workspace))
    monkeypatch.setenv("SESSION_SECRET", "short")
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        Settings.from_env()


@pytest.mark.parametrize("raw, expected", [("900", 900), ("0", 0), ("12", 12)])
def test_idle_timeout_configuration(monkeypatch, workspace, raw, expected):
    monkeypatch.setenv("ALLOWED_WORKSPACE_ROOTS", str(workspace))
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("OPENCODE_IDLE_TIMEOUT_SECONDS", raw)
    assert Settings.from_env().opencode_idle_timeout_seconds == expected


@pytest.mark.parametrize("raw", ["-1", "invalid"])
def test_idle_timeout_rejects_invalid_value(monkeypatch, workspace, raw):
    monkeypatch.setenv("ALLOWED_WORKSPACE_ROOTS", str(workspace))
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("OPENCODE_IDLE_TIMEOUT_SECONDS", raw)
    with pytest.raises(ConfigError, match="OPENCODE_IDLE_TIMEOUT_SECONDS"):
        Settings.from_env()
