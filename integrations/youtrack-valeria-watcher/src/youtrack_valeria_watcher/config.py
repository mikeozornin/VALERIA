from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import json5

from .errors import ConfigError

ENV_REFERENCE = re.compile(r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class Settings:
    database_path: Path
    lock_path: Path
    opencode_config: Path
    youtrack_mcp_name: str
    youtrack_authors: frozenset[str]
    mention: str
    valeria_base_url: str
    valeria_login: str
    valeria_password: str = field(repr=False)
    valeria_task_id: str
    ca_bundle: Path | None
    request_timeout_seconds: float
    max_issue_pages: int
    max_comment_pages: int
    mcp_concurrency: int


@dataclass(frozen=True)
class McpConnection:
    url: str
    headers: dict[str, str] = field(repr=False)
    host: str


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Required environment variable is missing: {name}")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < 1:
        raise ConfigError(f"{name} must be positive")
    return value


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    values = os.environ if env is None else env
    base_url = _required(values, "VALERIA_BASE_URL").rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("VALERIA_BASE_URL must be an HTTP(S) URL")

    raw_authors = _required(values, "YOUTRACK_VALERIA_AUTHORS")
    authors = frozenset(item.strip().casefold() for item in raw_authors.split(",") if item.strip())
    if not authors:
        raise ConfigError("YOUTRACK_VALERIA_AUTHORS must contain at least one author")

    ca_raw = values.get("TLS_CA_BUNDLE", "").strip()
    ca_bundle = Path(ca_raw).expanduser().resolve() if ca_raw else None
    if ca_bundle is not None and not ca_bundle.is_file():
        raise ConfigError("TLS_CA_BUNDLE does not point to a readable file")

    return Settings(
        database_path=Path(
            values.get("WATCHER_DB_PATH", "/var/lib/youtrack-valeria-watcher/state.db")
        ).expanduser(),
        lock_path=Path(
            values.get("WATCHER_LOCK_PATH", "/run/youtrack-valeria-watcher/watcher.lock")
        ).expanduser(),
        opencode_config=Path(
            values.get("OPENCODE_CONFIG", "/root/.config/opencode/opencode.jsonc")
        ).expanduser(),
        youtrack_mcp_name=values.get("YOUTRACK_MCP_NAME", "youtrack.example.com").strip(),
        youtrack_authors=authors,
        mention=values.get("YOUTRACK_MENTION", "@Valeria").strip() or "@Valeria",
        valeria_base_url=base_url,
        valeria_login=_required(values, "VALERIA_LOGIN"),
        valeria_password=_required(values, "VALERIA_PASSWORD"),
        valeria_task_id=_required(values, "VALERIA_TASK_ID"),
        ca_bundle=ca_bundle,
        request_timeout_seconds=_positive_float(values, "WATCHER_REQUEST_TIMEOUT_SECONDS", 60),
        max_issue_pages=_positive_int(values, "WATCHER_MAX_ISSUE_PAGES", 500),
        max_comment_pages=_positive_int(values, "WATCHER_MAX_COMMENT_PAGES", 1000),
        mcp_concurrency=_positive_int(values, "WATCHER_MCP_CONCURRENCY", 8),
    )


def _resolve_reference(value: str, env: Mapping[str, str]) -> str:
    match = ENV_REFERENCE.fullmatch(value.strip())
    if not match:
        return value
    variable = match.group(1)
    resolved = env.get(variable, "")
    if not resolved:
        raise ConfigError(f"MCP header references missing environment variable: {variable}")
    return resolved


def load_mcp_connection(settings: Settings, env: Mapping[str, str] | None = None) -> McpConnection:
    values = os.environ if env is None else env
    try:
        raw: Any = json5.loads(settings.opencode_config.read_text())
    except OSError as exc:
        raise ConfigError("Cannot read OPENCODE_CONFIG") from exc
    except ValueError as exc:
        raise ConfigError("OPENCODE_CONFIG is not valid JSONC") from exc

    entry = raw.get("mcp", {}).get(settings.youtrack_mcp_name) if isinstance(raw, dict) else None
    if not isinstance(entry, dict):
        raise ConfigError("Configured YouTrack MCP entry was not found")
    if entry.get("enabled", True) is False or entry.get("type") != "remote":
        raise ConfigError("YouTrack MCP must be an enabled remote connection")
    url = str(entry.get("url", "")).strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ConfigError("YouTrack MCP URL must use HTTPS")
    raw_headers = entry.get("headers", {})
    if not isinstance(raw_headers, dict):
        raise ConfigError("YouTrack MCP headers must be an object")
    headers = {
        str(name): _resolve_reference(str(value), values) for name, value in raw_headers.items()
    }
    if not headers.get("Authorization", "").strip():
        raise ConfigError("YouTrack MCP Authorization header is missing")
    return McpConnection(url=url, headers=headers, host=parsed.hostname)
