"""Явная миграция схемы 2 в единую память (схема 3).

Перед запуском остановите все процессы Memory MCP, использующие эту БД.
Резервная копия создаётся SQLite backup API под блокировкой писателя.
"""
import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from database import SCHEMA_VERSION


NOTE_COLUMNS = (
    "id, content, created_at, updated_at, version, status, "
    "superseded_by_note_id, superseded_at"
)


def _check_integrity(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RuntimeError("Database integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Database foreign key check failed")


def _migrate(conn: sqlite3.Connection) -> int:
    """Выполнить DDL и перенос внутри транзакции вызывающей функции."""
    note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    history_count = conn.execute("SELECT COUNT(*) FROM note_versions").fetchone()[0]
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'notes'").fetchone()
    conn.execute("""
        CREATE TABLE notes_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'superseded')),
            superseded_by_note_id INTEGER,
            superseded_at TIMESTAMP,
            FOREIGN KEY (superseded_by_note_id) REFERENCES notes(id) ON DELETE SET NULL
        )
    """)
    conn.execute(f"INSERT INTO notes_new ({NOTE_COLUMNS}) SELECT {NOTE_COLUMNS} FROM notes")
    conn.execute("""
        INSERT INTO tags(name)
        SELECT 'legacy-project:' || name FROM projects WHERE 1
        ON CONFLICT(name) DO NOTHING
    """)
    conn.execute("""
        INSERT OR IGNORE INTO note_tags(note_id, tag_id)
        SELECT n.id, t.id FROM notes n
        JOIN projects p ON p.id = n.project_id
        JOIN tags t ON t.name = 'legacy-project:' || p.name
    """)
    # FK выключены ДО BEGIN: дочерние строки и ссылки не удаляются каскадом.
    conn.execute("DROP TABLE notes")
    conn.execute("ALTER TABLE notes_new RENAME TO notes")
    if sequence:
        conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'notes'", sequence)
    conn.execute("DROP TABLE projects")
    conn.execute("CREATE INDEX idx_notes_created ON notes(created_at DESC, id DESC)")
    conn.execute("CREATE INDEX idx_notes_status_updated ON notes(status, updated_at DESC, id DESC)")

    # Старые снимки неизменяемы; перенос принадлежности в тег — новая версия.
    # updated_at сохраняется, чтобы миграция не меняла порядок свежести заметок.
    conn.execute("UPDATE notes SET version = version + 1")
    for note in conn.execute(
        "SELECT id, version, content, status, superseded_by_note_id FROM notes"
    ).fetchall():
        tags = [row[0] for row in conn.execute("""
            SELECT t.name FROM tags t JOIN note_tags nt ON t.id = nt.tag_id
            WHERE nt.note_id = ? ORDER BY t.name
        """, (note[0],))]
        conn.execute("""
            INSERT INTO note_versions (
                note_id, version, content, status, superseded_by_note_id, tags_json, change_type
            ) VALUES (?, ?, ?, ?, ?, ?, 'migrated_to_unified_memory')
        """, (*note, json.dumps(tags, ensure_ascii=False)))
    conn.execute("DELETE FROM notes_fts")
    conn.execute("""
        INSERT INTO notes_fts(rowid, note_id, content, tags)
        SELECT n.id, n.id, n.content, COALESCE(group_concat(t.name, ' '), '')
        FROM notes n LEFT JOIN note_tags nt ON nt.note_id = n.id
        LEFT JOIN tags t ON t.id = nt.tag_id GROUP BY n.id
    """)
    if conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] != note_count:
        raise RuntimeError("Note count changed during migration")
    if conn.execute("SELECT COUNT(*) FROM note_versions").fetchone()[0] != history_count + note_count:
        raise RuntimeError("Unexpected history count after migration")
    _check_integrity(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return note_count


def migrate(database_path: Path, backup_path: Path | None = None) -> dict:
    database_path = database_path.expanduser().resolve(strict=True)
    with closing(sqlite3.connect(database_path.as_uri() + '?mode=rw', uri=True, timeout=30)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                _check_integrity(conn)
                conn.rollback()
                return {"status": "already_current", "database": str(database_path)}
            if version != 2:
                raise RuntimeError(f"Only schema 2 can be migrated; found {version}")
            _check_integrity(conn)
            if backup_path is None:
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
                backup_path = database_path.with_name(f'{database_path.name}.v2-{stamp}.bak')
            backup_path = backup_path.expanduser().absolute()
            # O_EXCL запрещает перезапись существующей копии, включая симлинки.
            fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(database_path.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(backup_path)) as target:
                    source.backup(target)
                    _check_integrity(target)
            note_count = _migrate(conn)
            conn.commit()
            return {
                "status": "migrated", "database": str(database_path),
                "backup": str(backup_path), "notes": note_count, "schema_version": SCHEMA_VERSION,
            }
        except Exception:
            conn.rollback()
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.database, args.backup), ensure_ascii=False, indent=2))
