from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from .store import Store

LOGGER = logging.getLogger(__name__)


def record_baseline(database_path: Path, now_ms: int | None = None) -> tuple[int, bool]:
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    store = Store(database_path)
    try:
        return store.initialize_baseline(timestamp)
    finally:
        store.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    path = Path(
        os.environ.get("WATCHER_DB_PATH", "/var/lib/youtrack-valeria-watcher/state.db")
    ).expanduser()
    baseline_ms, created = record_baseline(path)
    baseline = datetime.fromtimestamp(baseline_ms / 1000, tz=UTC).isoformat()
    if created:
        LOGGER.info("Baseline recorded at %s; older comments cannot enqueue tasks", baseline)
    else:
        LOGGER.info("Baseline already exists at %s; it was not changed", baseline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
