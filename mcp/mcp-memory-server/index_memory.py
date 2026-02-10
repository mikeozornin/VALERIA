"""Back up a schema-3 database and resume indexing all current notes."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time

import database
from embeddings import EmbeddingClient, Settings
import vector_index


def backup(path):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    target = path.with_name(path.name + '.pre-embeddings-' + stamp + '.bak')
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--retry-now', action='store_true', help='Reset retry deadlines for this index profile')
    args = parser.parse_args()
    if args.database:
        database.DATABASE_PATH = args.database.expanduser().resolve(strict=True)
    settings = Settings.from_env()
    if args.status:
        print(json.dumps(vector_index.coverage(settings)))
        return 0
    if database.DATABASE_PATH.exists():
        print(json.dumps({'backup': str(backup(database.DATABASE_PATH))}), flush=True)
    database.init_db()
    if args.retry_now:
        with database.connection() as conn:
            conn.execute('UPDATE embedding_sources SET next_attempt=0, attempts=0')
    client = EmbeddingClient(settings)
    started = time.perf_counter()
    while True:
        result = vector_index.index_batch(settings, client)
        if not result['selected']:
            break
        print(json.dumps({**result, **vector_index.coverage(settings)}), flush=True)
        # Do not keep hammering a broken API; work remains durable for the next run.
        if result['failed']:
            break
    result = vector_index.coverage(settings)
    print(json.dumps({**result, 'seconds': round(time.perf_counter()-started, 3)}), flush=True)
    return 1 if result['pending'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
