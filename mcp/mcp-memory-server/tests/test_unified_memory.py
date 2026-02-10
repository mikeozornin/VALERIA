import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
import migrate
import models


class UnifiedSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.temp.name) / 'memory.db'
        database.init_db()
        self.both = models.add_note('PostgreSQL решение', ['architecture', 'component:example-backend'])
        self.arch = models.add_note('PostgreSQL другое решение', ['architecture'])
        self.component = models.add_note('PostgreSQL стиль', ['component:example-backend'])

    def tearDown(self):
        database.DATABASE_PATH = self.old_path
        self.temp.cleanup()

    def test_global_search_and_intersection_before_limit(self):
        self.assertEqual(len(models.get_context(query='PostgreSQL')), 3)
        result = models.get_context(query='PostgreSQL', tags=['architecture', 'component:example-backend'], limit=1)
        self.assertEqual([n['id'] for n in result], [self.both['id']])
        self.assertEqual(models.get_context(tags=['missing']), [])
        self.assertEqual(len(models.get_context(tags=[])), 3)
        self.assertNotIn('project_id', result[0])
        self.assertNotIn('project_name', result[0])

    def test_tag_any_all_duplicates_unknown_and_pagination(self):
        tags = ['architecture', 'component:example-backend']
        self.assertEqual(models.get_notes_by_tags(tags)['total'], 1)
        self.assertEqual(models.get_notes_by_tags(tags + tags)['total'], 1)
        result = models.get_notes_by_tags(tags, match_all_tags=False, limit=2)
        self.assertEqual((result['total'], result['total_pages']), (3, 2))
        second = models.get_notes_by_tags(tags, match_all_tags=False, limit=2, page=2)
        self.assertEqual(len(second['notes']), 1)
        self.assertEqual(len({n['id'] for n in result['notes'] + second['notes']}), 3)
        self.assertEqual(models.get_notes_by_tags(['architecture', 'missing'])['total'], 0)
        self.assertEqual(models.get_notes_by_tags(['architecture', 'missing'], match_all_tags=False)['total'], 2)
        self.assertEqual(models.get_notes_by_tags([])['total'], 0)
        self.assertEqual(len(models.get_context(query='PostgreSQL', tags=tags, match_all_tags=False)), 3)

    def test_tag_update_restore_delete_and_status(self):
        models.update_note(self.both['id'], 1, tags=['new-tag'])
        self.assertEqual(models.get_notes_by_tags(['architecture'])['total'], 1)
        self.assertEqual(models.get_context(query='new-tag')[0]['id'], self.both['id'])
        models.restore_note_version(self.both['id'], 1, 2)
        self.assertEqual(models.get_context(query='new-tag'), [])
        models.mark_note_superseded(self.both['id'], self.arch['id'], 3)
        self.assertEqual(models.get_context(query='решение', tags=['architecture', 'component:example-backend']), [])
        self.assertEqual(len(models.get_context(query='решение', tags=['architecture', 'component:example-backend'], include_superseded=True)), 1)
        models.delete_note(self.arch['id'])
        self.assertEqual(models.get_note_by_id(self.both['id'])['superseded_by_note_id'], None)
        self.assertEqual(models.get_context(query='другое'), [])

    def test_invalid_filters(self):
        for call in (
            lambda: models.get_context(tags='architecture'),
            lambda: models.get_context(match_all_tags='true'),
            lambda: models.get_notes_by_tags([], match_all_tags=1),
            lambda: models.get_context(query='***'),
            lambda: models.get_context(include_superseded=1),
        ):
            with self.subTest(call=call), self.assertRaises(ValueError):
                call()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'memory.db'
        self.backup = Path(self.temp.name) / 'backup.db'
        self.old_path = database.DATABASE_PATH
        database.DATABASE_PATH = self.path
        with sqlite3.connect(self.path) as conn:
            conn.executescript((Path(__file__).parent / 'fixtures/schema_v2.sql').read_text())
            conn.execute("INSERT INTO projects(id, name) VALUES (1, 'first'), (2, 'second')")
            conn.execute("INSERT INTO tags(id, name) VALUES (1, 'architecture')")
            conn.execute("""INSERT INTO notes(id, project_id, content, version) VALUES
                (4, 1, 'PostgreSQL old', 1), (9, 1, 'PostgreSQL new', 1),
                (12, 2, 'PostgreSQL other', 1), (100, 1, 'deleted', 1)""")
            conn.execute('DELETE FROM notes WHERE id = 100')
            conn.execute("UPDATE notes SET status = 'superseded', superseded_by_note_id = 9 WHERE id = 4")
            conn.execute('INSERT INTO note_tags(note_id, tag_id) VALUES (4, 1), (9, 1), (12, 1)')
            conn.execute("""INSERT INTO note_versions(note_id, version, content, tags_json, status,
                superseded_by_note_id, change_type)
                SELECT id, version, content, '["architecture"]', status, superseded_by_note_id, 'created'
                FROM notes""")
            self.original_notes = conn.execute(f'SELECT {migrate.NOTE_COLUMNS} FROM notes ORDER BY id').fetchall()
            self.original_history = conn.execute('SELECT * FROM note_versions ORDER BY id').fetchall()

    def tearDown(self):
        database.DATABASE_PATH = self.old_path
        self.temp.cleanup()

    def test_migration_preserves_data_backup_history_and_identifiers(self):
        with self.assertRaisesRegex(RuntimeError, 'migrate.py'):
            database.init_db()
        result = migrate.migrate(self.path, self.backup)
        self.assertEqual(result['notes'], 3)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.backup) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT * FROM note_versions ORDER BY id').fetchall(), self.original_history)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            history = conn.execute('SELECT * FROM note_versions WHERE version = 1 ORDER BY id').fetchall()
            self.assertEqual(history, self.original_history)
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE name = 'projects'").fetchall(), [])
            migrated = conn.execute(f'SELECT {migrate.NOTE_COLUMNS} FROM notes ORDER BY id').fetchall()
            for before, after in zip(self.original_notes, migrated):
                self.assertEqual(after[:4], before[:4])
                self.assertEqual(after[4], before[4] + 1)
                self.assertEqual(after[5:], before[5:])
        self.assertEqual(models.get_notes_by_tags(['legacy-project:first'], include_superseded=True)['total'], 2)
        self.assertEqual(len(models.get_context(query='PostgreSQL')), 2)
        self.assertEqual(len(models.get_context(query='PostgreSQL', include_superseded=True)), 3)
        self.assertEqual(models.update_note(9, 1, content='stale')['status'], 'conflict')
        self.assertEqual(models.mark_note_superseded(9, 12, 2)['status'], 'superseded')
        self.assertGreater(models.add_note('new', [])['id'], 100)
        restored = models.restore_note_version(4, 1, 2)['note']
        self.assertEqual(restored['superseded_by_note_id'], 9)
        # Старые снимки восстанавливают именно прежние теги, без миграционного тега.
        self.assertEqual([t['name'] for t in restored['tags']], ['architecture'])
        self.assertEqual(migrate.migrate(self.path, self.backup)['status'], 'already_current')
        database.init_db()

    def test_failure_rolls_back_schema_and_data(self):
        original = migrate._check_integrity
        calls = 0
        def fail_after_migration(conn):
            nonlocal calls
            calls += 1
            original(conn)
            if calls == 3:
                raise RuntimeError('injected validation failure')
        with patch.object(migrate, '_check_integrity', side_effect=fail_after_migration):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                migrate.migrate(self.path, self.backup)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT * FROM note_versions ORDER BY id').fetchall(), self.original_history)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 2)
            self.assertEqual(conn.execute(f'SELECT {migrate.NOTE_COLUMNS} FROM notes ORDER BY id').fetchall(), self.original_notes)

    def test_existing_backup_is_never_overwritten(self):
        self.backup.write_text('keep')
        with self.assertRaises(FileExistsError):
            migrate.migrate(self.path, self.backup)
        self.assertEqual(self.backup.read_text(), 'keep')
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_unknown_schema_is_rejected(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute('PRAGMA user_version = 1')
        with self.assertRaisesRegex(RuntimeError, 'Only schema 2'):
            migrate.migrate(self.path, self.backup)
        self.assertFalse(self.backup.exists())


if __name__ == '__main__':
    unittest.main()
