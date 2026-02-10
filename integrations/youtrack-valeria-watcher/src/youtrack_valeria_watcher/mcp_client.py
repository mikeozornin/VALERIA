from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .config import McpConnection, Settings
from .errors import ExternalServiceError
from .models import Comment, Issue, canonical_issue_url, comment_id_from_url, hash_text


def _httpx_factory(verify: bool | str) -> Callable[..., httpx.AsyncClient]:
    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            auth=auth,
            verify=verify,
            follow_redirects=False,
        )

    return factory


def _tool_json(result: Any, tool_name: str) -> Any:
    if getattr(result, "isError", False):
        raise ExternalServiceError(f"YouTrack MCP tool failed: {tool_name}")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for item in getattr(result, "content", []):
        if getattr(item, "type", None) == "text":
            try:
                return json.loads(item.text)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ExternalServiceError(
                    f"YouTrack MCP returned invalid JSON from {tool_name}"
                ) from exc
    raise ExternalServiceError(f"YouTrack MCP returned no data from {tool_name}")


class YouTrackSession:
    def __init__(self, session: ClientSession, settings: Settings, host: str):
        self.session = session
        self.settings = settings
        self.host = host

    async def _call_json(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        try:
            result = await self.session.call_tool(tool_name, arguments)
        except Exception as exc:
            raise ExternalServiceError(f"YouTrack MCP call failed: {tool_name}") from exc
        return _tool_json(result, tool_name)

    async def recent_issues(self, lookback_hours: int) -> list[Issue]:
        query = f"commented: {{minus {lookback_hours}h}} .. * sort by: updated desc"
        offset = 0
        issues: list[Issue] = []
        for _ in range(self.settings.max_issue_pages):
            result = await self._call_json(
                "search_issues",
                {
                    "query": query,
                    "limit": 20,
                    "offset": offset,
                    "customFieldsToReturn": [],
                },
            )
            if not isinstance(result, dict) or not isinstance(result.get("issuesPage"), list):
                raise ExternalServiceError("YouTrack MCP returned an invalid issue page")
            for raw in result["issuesPage"]:
                if not isinstance(raw, dict) or not raw.get("id") or not raw.get("url"):
                    raise ExternalServiceError("YouTrack MCP returned an invalid issue")
                issues.append(
                    Issue(
                        issue_id=str(raw["id"]),
                        url=canonical_issue_url(str(raw["url"]), self.host),
                    )
                )
            if not result.get("hasNextPage"):
                return issues
            next_offset = result.get("nextPageOffset")
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise ExternalServiceError("YouTrack MCP returned an invalid issue offset")
            offset = next_offset
        raise ExternalServiceError("YouTrack MCP issue pagination limit was exceeded")

    async def recent_comments(self, issue: Issue, cutoff_ms: int) -> list[Comment]:
        """Return comments created at or after cutoff without scanning full history."""
        limit = 10
        page_cache: dict[int, list[Any]] = {}

        async def page(index: int) -> list[Any]:
            if index < 0 or index >= self.settings.max_comment_pages:
                raise ExternalServiceError("YouTrack MCP comment pagination limit was exceeded")
            if index in page_cache:
                return page_cache[index]
            result = await self._call_json(
                "get_issue_comments",
                {"issueId": issue.issue_id, "limit": limit, "offset": index * limit},
            )
            if not isinstance(result, list):
                raise ExternalServiceError("YouTrack MCP returned an invalid comment page")
            page_cache[index] = result
            return result

        first = await page(0)
        if not first:
            return []

        if len(first) < limit:
            last_page = 0
        else:
            low = 0
            high = 1
            while True:
                if high >= self.settings.max_comment_pages:
                    high = self.settings.max_comment_pages - 1
                if high == low:
                    raise ExternalServiceError("YouTrack MCP comment pagination limit was exceeded")

                candidate = await page(high)
                if not candidate:
                    while high - low > 1:
                        middle = (low + high) // 2
                        if await page(middle):
                            low = middle
                        else:
                            high = middle
                    last_page = low
                    break
                if len(candidate) < limit:
                    last_page = high
                    break
                if high == self.settings.max_comment_pages - 1:
                    raise ExternalServiceError("YouTrack MCP comment pagination limit was exceeded")
                low = high
                high *= 2

        comments: list[Comment] = []
        for index in range(last_page, -1, -1):
            parsed = [self._comment(raw, issue) for raw in await page(index)]
            comments.extend(item for item in parsed if item.created_at_ms >= cutoff_ms)
            if parsed and min(item.created_at_ms for item in parsed) < cutoff_ms:
                break
        return sorted(comments, key=lambda item: (item.created_at_ms, item.comment_id))

    def _comment(self, raw: Any, issue: Issue) -> Comment:
        if not isinstance(raw, dict):
            raise ExternalServiceError("YouTrack MCP returned an invalid comment")
        try:
            author = str(raw["author"])
            created_at = int(raw["createdAt"])
            updated_raw = raw.get("updatedAt")
            updated_at = int(updated_raw) if updated_raw is not None else None
            text = str(raw["text"])
            url = str(raw["url"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExternalServiceError("YouTrack MCP returned an incomplete comment") from exc
        return Comment(
            comment_id=comment_id_from_url(
                url, issue_id=issue.issue_id, author=author, created_at_ms=created_at
            ),
            issue_id=issue.issue_id,
            issue_url=issue.url,
            author=author,
            created_at_ms=created_at,
            updated_at_ms=updated_at,
            text=text,
            text_hash=hash_text(text),
        )


@asynccontextmanager
async def connect_youtrack(
    connection: McpConnection, settings: Settings
) -> AsyncIterator[YouTrackSession]:
    verify: bool | str = str(settings.ca_bundle) if settings.ca_bundle else True
    timeout = settings.request_timeout_seconds
    try:
        async with (
            streamablehttp_client(
                connection.url,
                headers=connection.headers,
                timeout=timeout,
                sse_read_timeout=timeout,
                httpx_client_factory=_httpx_factory(verify),
            ) as (read, write, _),
            ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=timeout),
            ) as session,
        ):
            await session.initialize()
            yield YouTrackSession(session, settings, connection.host)
    except ExternalServiceError:
        raise
    except Exception as exc:
        raise ExternalServiceError("Cannot connect to YouTrack MCP") from exc
