from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from .models import Comment, TaskRun

ACTIVE_RUN_STATUSES = frozenset({"queued", "dispatching", "running", "cancelling"})
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS comments (
                comment_id TEXT PRIMARY KEY,
                issue_id TEXT NOT NULL,
                issue_url TEXT NOT NULL,
                author TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER,
                text_hash TEXT NOT NULL,
                has_mention INTEGER NOT NULL CHECK (has_mention IN (0, 1)),
                status TEXT NOT NULL,
                valeria_run_id TEXT,
                first_seen_at_ms INTEGER NOT NULL,
                last_seen_at_ms INTEGER NOT NULL,
                processed_at_ms INTEGER,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS issue_runs (
                run_id TEXT PRIMARY KEY,
                issue_id TEXT NOT NULL,
                issue_url TEXT NOT NULL,
                trigger_comment_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                finished_at_ms INTEGER,
                FOREIGN KEY (trigger_comment_id) REFERENCES comments(comment_id)
            );

            CREATE INDEX IF NOT EXISTS issue_runs_issue_status
                ON issue_runs(issue_id, status);
            CREATE INDEX IF NOT EXISTS comments_status
                ON comments(status, first_seen_at_ms);
            """
        )
        self.connection.commit()

    def get_meta_int(self, key: str) -> int | None:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return int(row["value"]) if row else None

    def set_meta_int(self, key: str, value: int) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def initialize_baseline(self, now_ms: int) -> tuple[int, bool]:
        """Set the immutable first-run watermark and cursor in one transaction."""
        with self.connection:
            row = self.connection.execute(
                "SELECT value FROM meta WHERE key = 'initialized_at_ms'"
            ).fetchone()
            if row:
                return int(row["value"]), False
            self.connection.execute(
                "INSERT INTO meta(key, value) VALUES ('initialized_at_ms', ?)",
                (str(now_ms),),
            )
            self.connection.execute(
                "INSERT INTO meta(key, value) VALUES ('last_success_at_ms', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(now_ms),),
            )
        return now_ms, True

    def get_comment(self, comment_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM comments WHERE comment_id = ?", (comment_id,)
        ).fetchone()

    def save_comment(
        self,
        comment: Comment,
        *,
        has_mention: bool,
        status: str,
        run_id: str | None = None,
        error: str | None = None,
        processed: bool = True,
        now_ms: int | None = None,
    ) -> None:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        processed_at = now if processed else None
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO comments (
                    comment_id, issue_id, issue_url, author, created_at_ms, updated_at_ms,
                    text_hash, has_mention, status, valeria_run_id, first_seen_at_ms,
                    last_seen_at_ms, processed_at_ms, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(comment_id) DO UPDATE SET
                    issue_id = excluded.issue_id,
                    issue_url = excluded.issue_url,
                    author = excluded.author,
                    created_at_ms = excluded.created_at_ms,
                    updated_at_ms = excluded.updated_at_ms,
                    text_hash = excluded.text_hash,
                    has_mention = excluded.has_mention,
                    status = excluded.status,
                    valeria_run_id = excluded.valeria_run_id,
                    last_seen_at_ms = excluded.last_seen_at_ms,
                    processed_at_ms = excluded.processed_at_ms,
                    last_error = excluded.last_error
                """,
                (
                    comment.comment_id,
                    comment.issue_id,
                    comment.issue_url,
                    comment.author,
                    comment.created_at_ms,
                    comment.updated_at_ms,
                    comment.text_hash,
                    int(has_mention),
                    status,
                    run_id,
                    now,
                    now,
                    processed_at,
                    error,
                ),
            )

    def pending_comments(self) -> Iterable[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM comments WHERE status = 'submission_retry' "
            "ORDER BY first_seen_at_ms, comment_id"
        ).fetchall()

    def active_run(self, issue_id: str) -> sqlite3.Row | None:
        placeholders = ",".join("?" for _ in ACTIVE_RUN_STATUSES)
        return self.connection.execute(
            f"SELECT * FROM issue_runs WHERE issue_id = ? AND status IN ({placeholders}) "
            "ORDER BY created_at_ms DESC LIMIT 1",
            (issue_id, *sorted(ACTIVE_RUN_STATUSES)),
        ).fetchone()

    def active_runs(self) -> Iterable[sqlite3.Row]:
        placeholders = ",".join("?" for _ in ACTIVE_RUN_STATUSES)
        return self.connection.execute(
            f"SELECT * FROM issue_runs WHERE status IN ({placeholders}) ORDER BY created_at_ms",
            tuple(sorted(ACTIVE_RUN_STATUSES)),
        ).fetchall()

    def add_run(
        self, run: TaskRun, *, issue_id: str, issue_url: str, comment_id: str, now_ms: int
    ) -> None:
        finished = now_ms if run.status in TERMINAL_RUN_STATUSES else None
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO issue_runs (
                    run_id, issue_id, issue_url, trigger_comment_id, status,
                    created_at_ms, updated_at_ms, finished_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at_ms = excluded.updated_at_ms,
                    finished_at_ms = excluded.finished_at_ms
                """,
                (
                    run.run_id,
                    issue_id,
                    issue_url,
                    comment_id,
                    run.status,
                    now_ms,
                    now_ms,
                    finished,
                ),
            )

    def update_run(self, run: TaskRun, *, now_ms: int) -> None:
        finished = now_ms if run.status in TERMINAL_RUN_STATUSES else None
        with self.connection:
            self.connection.execute(
                "UPDATE issue_runs SET status = ?, updated_at_ms = ?, "
                "finished_at_ms = COALESCE(finished_at_ms, ?) WHERE run_id = ?",
                (run.status, now_ms, finished, run.run_id),
            )

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for row in self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM comments GROUP BY status"
        ):
            result[str(row["status"])] = int(row["count"])
        return result


def row_to_comment(row: sqlite3.Row) -> Comment:
    return Comment(
        comment_id=row["comment_id"],
        issue_id=row["issue_id"],
        issue_url=row["issue_url"],
        author=row["author"],
        created_at_ms=row["created_at_ms"],
        updated_at_ms=row["updated_at_ms"],
        text="",
        text_hash=row["text_hash"],
    )
