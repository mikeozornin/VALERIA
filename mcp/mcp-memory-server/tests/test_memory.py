import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import database  # noqa: E402
import models  # noqa: E402
from config import MAX_LIMIT  # noqa: E402


class MemoryDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.temp_dir.name) / "memory.db"
        database.init_db()

    def tearDown(self) -> None:
        database.DATABASE_PATH = self.old_path
        self.temp_dir.cleanup()

    def test_foreign_keys_are_enabled_and_delete_cascades(self) -> None:
        note = models.add_note("content", ["tag"])

        with database.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

        self.assertTrue(models.delete_note(note["id"]))
        with database.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM note_tags").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_invalid_pagination_is_rejected(self) -> None:
        models.add_note("content", ["tag"])

        invalid_calls = (
            lambda: models.get_context(limit=0),
            lambda: models.get_context(limit=-1),
            lambda: models.get_context(limit=MAX_LIMIT + 1),
            lambda: models.get_notes_by_tags(["tag"], page=0),
            lambda: models.get_notes_by_tags(["tag"], limit=0),
        )
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises(ValueError):
                call()

    def test_context_order_is_stable_when_timestamps_match(self) -> None:
        first = models.add_note("first", [])
        second = models.add_note("second", [])

        notes = models.get_context(limit=2)

        self.assertEqual([note["id"] for note in notes], [second["id"], first["id"]])

    def test_context_searches_by_relevance_and_updates_fts_index(self) -> None:
        relevant = models.add_note(
            "PostgreSQL выбран для платежных транзакций",
            ["architecture"],
        )
        models.add_note("Redis используется для кэша", ["architecture"])

        matches = models.get_context(query="платежи PostgreSQL")

        self.assertEqual([note["id"] for note in matches], [relevant["id"]])
        self.assertGreater(matches[0]["relevance_score"], 0)

        models.update_note(
            relevant["id"],
            expected_version=1,
            content="ClickHouse выбран для аналитики",
        )
        self.assertEqual(models.get_context(query="PostgreSQL"), [])
        self.assertEqual(
            models.get_context(query="ClickHouse")[0]["id"],
            relevant["id"],
        )

    def test_versions_conflicts_and_explicit_resolution(self) -> None:
        note = models.add_note("version one", ["context"])
        updated = models.update_note(
            note["id"],
            expected_version=1,
            content="version two",
            tags=["context", "decisions"],
        )
        self.assertEqual(updated["status"], "updated")
        self.assertEqual(updated["note"]["version"], 2)

        conflict = models.update_note(
            note["id"],
            expected_version=1,
            content="stale overwrite",
        )
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(conflict["current_version"], 2)
        self.assertEqual(models.get_note_by_id(note["id"])["content"], "version two")

        resolved = models.resolve_note_conflict(
            note["id"],
            expected_current_version=2,
            merged_content="merged version",
            merged_tags=["context", "decisions"],
        )
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["note"]["version"], 3)

        history = models.get_note_history(note["id"])
        self.assertEqual([item["version"] for item in history], [3, 2, 1])
        self.assertEqual(
            [item["change_type"] for item in history],
            ["conflict_resolution", "updated", "created"],
        )

    def test_parallel_updates_preserve_exactly_one_writer(self) -> None:
        note = models.add_note("initial", ["context"])

        def update(index: int) -> dict:
            return models.update_note(
                note["id"],
                expected_version=1,
                content=f"writer {index}",
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(update, range(8)))

        statuses = [result["status"] for result in results]
        self.assertEqual(statuses.count("updated"), 1)
        self.assertEqual(statuses.count("conflict"), 7)
        self.assertEqual(models.get_note_by_id(note["id"])["version"], 2)
        self.assertEqual(
            [item["version"] for item in models.get_note_history(note["id"])],
            [2, 1],
        )

    def test_restore_creates_a_new_version(self) -> None:
        note = models.add_note("original", ["one"])
        models.update_note(
            note["id"],
            expected_version=1,
            content="changed",
            tags=["two"],
        )

        restored = models.restore_note_version(
            note["id"],
            version=1,
            expected_version=2,
        )

        self.assertEqual(restored["status"], "restored")
        self.assertEqual(restored["note"]["version"], 3)
        self.assertEqual(restored["note"]["content"], "original")
        self.assertEqual([tag["name"] for tag in restored["note"]["tags"]], ["one"])

    def test_superseded_notes_are_hidden_by_default(self) -> None:
        old = models.add_note("old architecture", ["architecture"])
        replacement = models.add_note("new architecture", ["architecture"])

        result = models.mark_note_superseded(
            old["id"],
            replacement["id"],
            expected_version=1,
        )

        self.assertEqual(result["status"], "superseded")
        self.assertEqual(result["note"]["superseded_by_note_id"], replacement["id"])
        self.assertEqual(
            [note["id"] for note in models.get_context()],
            [replacement["id"]],
        )
        self.assertEqual(
            models.get_notes_by_tags(["architecture"])["total"],
            1,
        )
        self.assertEqual(
            models.get_notes_by_tags(
                    ["architecture"],
                include_superseded=True,
            )["total"],
            2,
        )

    def test_legacy_database_is_rejected(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        database.DATABASE_PATH = legacy_path
        conn = sqlite3.connect(legacy_path)
        conn.executescript("""
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE note_tags (
                note_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (note_id, tag_id),
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
            INSERT INTO projects(id, name) VALUES (1, 'legacy');
            INSERT INTO tags(id, name) VALUES (1, 'architecture');
            INSERT INTO notes(id, project_id, content) VALUES (1, 1, 'legacy PostgreSQL decision');
            INSERT INTO note_tags(note_id, tag_id) VALUES (1, 1);
        """)
        conn.close()

        with self.assertRaisesRegex(RuntimeError, "Unsupported memory database schema"):
            database.init_db()


if __name__ == "__main__":
    unittest.main()
