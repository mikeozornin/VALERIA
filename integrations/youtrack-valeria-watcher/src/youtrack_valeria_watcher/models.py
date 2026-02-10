from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .errors import ExternalServiceError


@dataclass(frozen=True)
class Issue:
    issue_id: str
    url: str


@dataclass(frozen=True)
class Comment:
    comment_id: str
    issue_id: str
    issue_url: str
    author: str
    created_at_ms: int
    updated_at_ms: int | None
    text: str
    text_hash: str


@dataclass(frozen=True)
class TaskRun:
    run_id: str
    status: str


def canonical_issue_url(raw_url: str, expected_host: str) -> str:
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme != "https" or parsed.hostname != expected_host:
        raise ExternalServiceError("YouTrack MCP returned an unexpected issue URL")
    if not parsed.path.startswith("/issue/"):
        raise ExternalServiceError("YouTrack MCP returned a non-issue URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def comment_id_from_url(raw_url: str, *, issue_id: str, author: str, created_at_ms: int) -> str:
    fragment = parse_qs(urlsplit(raw_url).fragment).get("focus", [""])[0]
    if fragment.casefold().startswith("comments-"):
        return fragment
    fallback = f"{issue_id}\0{author}\0{created_at_ms}".encode()
    return "fallback-" + hashlib.sha256(fallback).hexdigest()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
