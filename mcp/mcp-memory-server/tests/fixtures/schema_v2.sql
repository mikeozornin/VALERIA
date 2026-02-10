-- Schema 2 fixture for data-preserving migration tests.
CREATE TABLE note_tags (
                note_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (note_id, tag_id),
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
CREATE TABLE note_versions (
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
            );
CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'superseded')),
                superseded_by_note_id INTEGER,
                superseded_at TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY (superseded_by_note_id) REFERENCES notes(id) ON DELETE SET NULL
            );
CREATE VIRTUAL TABLE notes_fts USING fts5(
                note_id UNINDEXED,
                content,
                tags,
                tokenize='unicode61'
            );
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
CREATE INDEX idx_note_tags_tag_note
            ON note_tags(tag_id, note_id)
        ;
CREATE INDEX idx_note_versions_note_version
            ON note_versions(note_id, version DESC)
        ;
CREATE INDEX idx_notes_project_created
            ON notes(project_id, created_at DESC, id DESC)
        ;
CREATE INDEX idx_notes_project_status_updated
            ON notes(project_id, status, updated_at DESC, id DESC)
        ;
PRAGMA user_version = 2;
