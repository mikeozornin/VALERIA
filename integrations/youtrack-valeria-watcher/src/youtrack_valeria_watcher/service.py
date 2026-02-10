from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from .config import Settings
from .errors import ExternalServiceError
from .models import Comment, Issue, TaskRun
from .store import Store, row_to_comment

LOGGER = logging.getLogger(__name__)
HANDLED_COMMENT_STATUSES = frozenset({"enqueued", "handled_by_active_run"})


class YouTrackApi(Protocol):
    async def recent_issues(self, lookback_hours: int) -> list[Issue]: ...

    async def recent_comments(self, issue: Issue, cutoff_ms: int) -> list[Comment]: ...


class ValeriaApi(Protocol):
    async def submit(self, issue_url: str, comment_id: str) -> TaskRun: ...

    async def get_run(self, run_id: str) -> TaskRun: ...


@dataclass
class CycleSummary:
    bootstrapped: bool = False
    issues: int = 0
    comments: int = 0
    enqueued: int = 0
    coalesced: int = 0
    ignored: int = 0
    retries: int = 0


class Watcher:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        youtrack: YouTrackApi | None,
        valeria: ValeriaApi | None,
    ):
        self.settings = settings
        self.store = store
        self.youtrack = youtrack
        self.valeria = valeria
        escaped = re.escape(settings.mention)
        self.mention_pattern = re.compile(rf"(?<![\w.-]){escaped}(?![\w.-])", re.IGNORECASE)

    def bootstrap(self, now_ms: int) -> CycleSummary | None:
        _, created = self.store.initialize_baseline(now_ms)
        if not created:
            return None
        return CycleSummary(bootstrapped=True)

    async def run_cycle(self, now_ms: int) -> CycleSummary:
        if self.youtrack is None or self.valeria is None:
            raise RuntimeError("Remote clients are required after bootstrap")
        summary = CycleSummary()
        await self._refresh_runs(now_ms)
        await self._retry_submissions(now_ms, summary)

        initialized = self.store.get_meta_int("initialized_at_ms")
        if initialized is None:
            raise RuntimeError("Watcher must be bootstrapped before a cycle")
        cutoff_ms = max(initialized, now_ms - 3_600_000)

        issues = await self.youtrack.recent_issues(1)
        summary.issues = len(issues)
        semaphore = asyncio.Semaphore(self.settings.mcp_concurrency)

        async def fetch_comments(issue: Issue) -> list[Comment]:
            async with semaphore:
                return await self.youtrack.recent_comments(issue, cutoff_ms)

        comment_pages = await asyncio.gather(*(fetch_comments(issue) for issue in issues))
        for comments in comment_pages:
            for comment in sorted(comments, key=lambda item: (item.created_at_ms, item.comment_id)):
                summary.comments += 1
                await self._process_comment(comment, now_ms, summary)
        self.store.set_meta_int("last_success_at_ms", now_ms)
        return summary

    async def _refresh_runs(self, now_ms: int) -> None:
        assert self.valeria is not None
        for row in self.store.active_runs():
            try:
                run = await self.valeria.get_run(row["run_id"])
            except ExternalServiceError as exc:
                LOGGER.warning("Could not refresh Valeria run %s: %s", row["run_id"], exc)
                continue
            self.store.update_run(run, now_ms=now_ms)

    async def _retry_submissions(self, now_ms: int, summary: CycleSummary) -> None:
        for row in self.store.pending_comments():
            await self._submit_or_coalesce(row_to_comment(row), now_ms, summary)

    async def _process_comment(self, comment: Comment, now_ms: int, summary: CycleSummary) -> None:
        existing = self.store.get_comment(comment.comment_id)
        if existing and existing["status"] in HANDLED_COMMENT_STATUSES:
            return
        if existing and existing["status"] == "submission_retry":
            return
        if existing and existing["text_hash"] == comment.text_hash:
            return

        has_mention = bool(self.mention_pattern.search(comment.text))
        if not has_mention:
            self.store.save_comment(comment, has_mention=False, status="ignored", now_ms=now_ms)
            summary.ignored += 1
            return
        if comment.author.strip().casefold() in self.settings.youtrack_authors:
            self.store.save_comment(comment, has_mention=True, status="ignored_own", now_ms=now_ms)
            summary.ignored += 1
            return
        await self._submit_or_coalesce(comment, now_ms, summary)

    async def _submit_or_coalesce(
        self, comment: Comment, now_ms: int, summary: CycleSummary
    ) -> None:
        assert self.valeria is not None
        active = self.store.active_run(comment.issue_id)
        if active:
            self.store.save_comment(
                comment,
                has_mention=True,
                status="handled_by_active_run",
                run_id=active["run_id"],
                now_ms=now_ms,
            )
            summary.coalesced += 1
            return
        try:
            run = await self.valeria.submit(comment.issue_url, comment.comment_id)
        except ExternalServiceError as exc:
            self.store.save_comment(
                comment,
                has_mention=True,
                status="submission_retry",
                error=str(exc)[:500],
                processed=False,
                now_ms=now_ms,
            )
            summary.retries += 1
            return
        self.store.save_comment(
            comment,
            has_mention=True,
            status="enqueued",
            run_id=run.run_id,
            now_ms=now_ms,
        )
        self.store.add_run(
            run,
            issue_id=comment.issue_id,
            issue_url=comment.issue_url,
            comment_id=comment.comment_id,
            now_ms=now_ms,
        )
        summary.enqueued += 1
