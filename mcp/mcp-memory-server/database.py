"""
Модуль для работы с базой данных SQLite.
Создание таблиц и управление подключением.
"""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from config import DATABASE_PATH


SCHEMA_VERSION = 3


def get_connection() -> sqlite3.Connection:
    """
    Получить подключение к базе данных.
    """
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    # SQLite не включает проверку FK для новых соединений автоматически.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Выдать соединение и гарантировать commit/rollback и close."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """
    Инициализация базы данных: создание таблиц.
    """
    with connection() as conn:
        cursor = conn.cursor()
        # WAL позволяет читателям не блокировать единственного писателя.
        cursor.execute("PRAGMA journal_mode = WAL")

        existing_tables = {
            row[0]
            for row in cursor.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """).fetchall()
        }
        current_version = cursor.execute("PRAGMA user_version").fetchone()[0]
        if existing_tables and current_version != SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported memory database schema version {current_version}; "
                f"expected {SCHEMA_VERSION}. For schema 2, stop all memory servers "
                "and run migrate.py --database PATH."
            )

        conn.execute("BEGIN IMMEDIATE")

        # Таблица тегов
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица заметок
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notes (
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

        # Таблица связи заметок и тегов (многие-ко-многим)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (note_id, tag_id),
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS note_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                status TEXT NOT NULL,
                superseded_by_note_id INTEGER,
                change_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(note_id, version),
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
                FOREIGN KEY (superseded_by_note_id) REFERENCES notes(id) ON DELETE SET NULL
            )
        """)

        # FTS5 индексирует текст и теги. rowid равен notes.id.
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                note_id UNINDEXED,
                content,
                tags,
                tokenize='unicode61'
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_notes_created
            ON notes(created_at DESC, id DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_notes_status_updated
            ON notes(status, updated_at DESC, id DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_note_tags_tag_note
            ON note_tags(tag_id, note_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_note_versions_note_version
            ON note_versions(note_id, version DESC)
        """)

        # Пересборка восстанавливает FTS-индекс актуальной схемы.
        cursor.execute("DELETE FROM notes_fts")
        cursor.execute("""
            INSERT INTO notes_fts(rowid, note_id, content, tags)
            SELECT n.id, n.id, n.content, COALESCE(group_concat(t.name, ' '), '')
            FROM notes n
            LEFT JOIN note_tags nt ON nt.note_id = n.id
            LEFT JOIN tags t ON t.id = nt.tag_id
            GROUP BY n.id
        """)
        import vector_index
        vector_index.initialize(cursor)
        cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


if __name__ == "__main__":
    init_db()
    print(f"База данных инициализирована: {DATABASE_PATH}")
