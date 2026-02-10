"""Derived SQLite index with a durable retry queue and optimistic publication."""
from contextlib import asynccontextmanager
import hashlib
import logging
import struct
import threading
import time

from embeddings import EmbeddingClient, EmbeddingError, Settings

log = logging.getLogger('memory.search')


def ensure_schema(cursor):
    # Additive, disposable index extension; the authoritative note schema stays v3.
    cursor.execute('''CREATE TABLE IF NOT EXISTS embedding_sources (
        note_id INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
        content_hash TEXT NOT NULL, document TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS note_embeddings (
        note_id INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
        content_hash TEXT NOT NULL, profile TEXT NOT NULL, model TEXT NOT NULL,
        dimensions INTEGER NOT NULL, vector BLOB NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')


def enqueue(cursor, note_id, content, tags):
    # Safe also immediately after the legacy v2 -> v3 migration.
    ensure_schema(cursor)
    document = content + '\n\nTags: ' + ', '.join(sorted(set(tags)))
    digest = hashlib.sha256(document.encode()).hexdigest()
    cursor.execute('''INSERT INTO embedding_sources(note_id, content_hash, document)
        VALUES (?, ?, ?) ON CONFLICT(note_id) DO UPDATE SET
        content_hash=excluded.content_hash, document=excluded.document,
        attempts=0, next_attempt=0
        WHERE embedding_sources.content_hash != excluded.content_hash''', (note_id, digest, document))


def initialize(cursor):
    ensure_schema(cursor)
    for note in cursor.execute('SELECT id, content FROM notes').fetchall():
        tags = [row[0] for row in cursor.execute('''SELECT t.name FROM tags t
            JOIN note_tags nt ON nt.tag_id=t.id WHERE nt.note_id=?''', (note[0],))]
        enqueue(cursor, note[0], note[1], tags)


def pending(settings, *, due_only=True):
    from database import connection
    with connection() as conn:
        return [dict(row) for row in conn.execute('''SELECT s.* FROM embedding_sources s
            LEFT JOIN note_embeddings e ON e.note_id=s.note_id
                AND e.content_hash=s.content_hash AND e.profile=?
            WHERE e.note_id IS NULL AND (?=0 OR s.next_attempt <= ?)
            ORDER BY s.next_attempt, s.note_id LIMIT ?''',
            (settings.profile, int(due_only), time.time(), settings.batch_size))]


def publish(settings, rows, vectors):
    from database import connection
    stored = 0
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for row, vector in zip(rows, vectors, strict=True):
            current = conn.execute('SELECT content_hash FROM embedding_sources WHERE note_id=?',
                                   (row['note_id'],)).fetchone()
            if not current or current[0] != row['content_hash']:
                continue
            blob = struct.pack(f'<{settings.dimensions}f', *vector)
            conn.execute('''INSERT INTO note_embeddings
                (note_id, content_hash, profile, model, dimensions, vector)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(note_id) DO UPDATE SET
                content_hash=excluded.content_hash, profile=excluded.profile, model=excluded.model,
                dimensions=excluded.dimensions, vector=excluded.vector, updated_at=CURRENT_TIMESTAMP''',
                (row['note_id'], row['content_hash'], settings.profile, settings.model, settings.dimensions, blob))
            conn.execute('UPDATE embedding_sources SET attempts=0, next_attempt=0 WHERE note_id=?', (row['note_id'],))
            stored += 1
    return stored


def defer(rows):
    from database import connection
    with connection() as conn:
        for row in rows:
            delay = min(300, 5 * 2 ** min(row['attempts'], 6))
            conn.execute('''UPDATE embedding_sources SET attempts=attempts+1, next_attempt=?
                WHERE note_id=? AND content_hash=?''',
                (time.time() + delay, row['note_id'], row['content_hash']))


def index_batch(settings, client=None):
    rows = pending(settings)
    if not rows:
        return {'selected': 0, 'stored': 0, 'failed': 0}
    client = client or EmbeddingClient(settings)
    valid, failed = [], []
    # One oversized document must not prevent indexing its batch neighbours.
    for row in rows:
        try:
            client.validate_lengths([row['document']])
            valid.append(row)
        except EmbeddingError:
            failed.append(row)
    stored = 0
    if valid:
        try:
            vectors = client.embed_sync([row['document'] for row in valid])
            stored = publish(settings, valid, vectors)
        except EmbeddingError as exc:
            log.warning('embedding_index_deferred count=%s reason=%s', len(valid), exc)
            failed.extend(valid)
    if failed:
        defer(failed)
        log.warning('embedding_index_pending count=%s', len(failed))
    return {'selected': len(rows), 'stored': stored, 'failed': len(failed)}


def coverage(settings):
    from database import connection
    with connection() as conn:
        total, ready = conn.execute('''SELECT COUNT(*), COUNT(e.note_id) FROM notes n
            LEFT JOIN embedding_sources s ON s.note_id=n.id
            LEFT JOIN note_embeddings e ON e.note_id=s.note_id
                AND e.content_hash=s.content_hash AND e.profile=?''', (settings.profile,)).fetchone()
    return {'total': total, 'ready': ready, 'pending': total-ready}


@asynccontextmanager
async def lifespan(_server):
    settings = Settings.from_env()
    stop = threading.Event()
    def work():
        client = EmbeddingClient(settings)
        while not stop.is_set():
            try:
                result = index_batch(settings, client)
                delay = 0.1 if result['stored'] else 5
            except Exception:
                # Do not log unexpected exception bodies; upstream might echo secrets/text.
                log.error('embedding_worker_failed; retrying')
                delay = 5
            stop.wait(delay)
    thread = None
    if settings.enabled:
        thread = threading.Thread(target=work, name='memory-embeddings', daemon=True)
        thread.start()
    try:
        yield {}
    finally:
        stop.set()
        if thread:
            import asyncio
            await asyncio.to_thread(thread.join, settings.timeout + 2)
