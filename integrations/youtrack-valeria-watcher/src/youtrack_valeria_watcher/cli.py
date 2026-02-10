from __future__ import annotations

import asyncio
import fcntl
import logging
import sys
import time
from pathlib import Path

from .config import load_mcp_connection, load_settings
from .errors import WatcherError
from .mcp_client import connect_youtrack
from .service import Watcher
from .valeria_client import ValeriaClient
from .store import Store

LOGGER = logging.getLogger(__name__)


def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


async def _run() -> int:
    settings = load_settings()
    lock = _lock(settings.lock_path)
    if lock is None:
        LOGGER.info("Another watcher instance is already running")
        return 0
    try:
        store = Store(settings.database_path)
        try:
            now_ms = int(time.time() * 1000)
            watcher = Watcher(settings, store, None, None)
            summary = watcher.bootstrap(now_ms)
            if summary is not None:
                LOGGER.info("Watcher initialized; historical comments will not be enqueued")
                return 0

            connection = load_mcp_connection(settings)
            async with (
                connect_youtrack(connection, settings) as youtrack,
                ValeriaClient(settings) as valeria,
            ):
                watcher = Watcher(settings, store, youtrack, valeria)
                summary = await watcher.run_cycle(now_ms)
            LOGGER.info(
                "Cycle complete: issues=%d comments=%d enqueued=%d coalesced=%d "
                "ignored=%d retries=%d totals=%s",
                summary.issues,
                summary.comments,
                summary.enqueued,
                summary.coalesced,
                summary.ignored,
                summary.retries,
                store.counts(),
            )
            return 0
        finally:
            store.close()
    finally:
        lock.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_run())
    except WatcherError as exc:
        LOGGER.error("Watcher failed: %s", exc)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
