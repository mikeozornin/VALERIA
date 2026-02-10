"""Операции с единой памятью, версионируемыми заметками и тегами.
"""
import json
import re
import sqlite3
from typing import Optional

from config import MAX_LIMIT, MAX_TAGS_PER_QUERY
from database import connection
from embeddings import Settings
import vector_index


NOTE_FIELDS = """
    n.id, n.content, n.created_at, n.updated_at,
    n.version, n.status, n.superseded_by_note_id, n.superseded_at
"""


def _validate_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_positive_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be an integer greater than or equal to 1")


def _validate_limit(limit: int) -> None:
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= MAX_LIMIT
    ):
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}")


def _validate_pagination(page: int, limit: int) -> None:
    _validate_positive_int(page, "page")
    _validate_limit(limit)


def _validate_tags(tags: list[str]) -> None:
    if not isinstance(tags, list):
        raise ValueError("tags must be a list")
    if len(tags) > MAX_TAGS_PER_QUERY:
        raise ValueError(f"tags must contain at most {MAX_TAGS_PER_QUERY} items")
    for tag in tags:
        _validate_text(tag, "tag")


def _get_or_create_tag(cursor: sqlite3.Cursor, name: str) -> dict:
    cursor.execute(
        "INSERT INTO tags (name) VALUES (?) ON CONFLICT(name) DO NOTHING",
        (name,),
    )
    cursor.execute("SELECT id, name, created_at FROM tags WHERE name = ?", (name,))
    return dict(cursor.fetchone())


def _get_note_tags(cursor: sqlite3.Cursor, note_id: int) -> list[dict]:
    rows = cursor.execute("""
        SELECT t.id, t.name
        FROM tags t
        JOIN note_tags nt ON t.id = nt.tag_id
        WHERE nt.note_id = ?
        ORDER BY t.name
    """, (note_id,)).fetchall()
    return [dict(row) for row in rows]


def _fetch_note(cursor: sqlite3.Cursor, note_id: int) -> Optional[dict]:
    row = cursor.execute(f"""
        SELECT {NOTE_FIELDS}
        FROM notes n
        WHERE n.id = ?
    """, (note_id,)).fetchone()
    if not row:
        return None

    note = dict(row)
    note["tags"] = _get_note_tags(cursor, note_id)
    return note


def _set_note_tags(cursor: sqlite3.Cursor, note_id: int, tags: list[str]) -> None:
    cursor.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
    for tag_name in dict.fromkeys(tags):
        tag = _get_or_create_tag(cursor, tag_name)
        cursor.execute(
            "INSERT INTO note_tags (note_id, tag_id) VALUES (?, ?)",
            (note_id, tag["id"]),
        )


def _sync_note_fts(cursor: sqlite3.Cursor, note_id: int) -> None:
    cursor.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
    note = cursor.execute(
        "SELECT content FROM notes WHERE id = ?",
        (note_id,),
    ).fetchone()
    if not note:
        return

    tag_names = [tag["name"] for tag in _get_note_tags(cursor, note_id)]
    vector_index.enqueue(cursor, note_id, note["content"], tag_names)
    cursor.execute(
        "INSERT INTO notes_fts(rowid, note_id, content, tags) VALUES (?, ?, ?, ?)",
        (note_id, note_id, note["content"], " ".join(tag_names)),
    )


def _record_version(cursor: sqlite3.Cursor, note_id: int, change_type: str) -> None:
    note = _fetch_note(cursor, note_id)
    if not note:
        raise ValueError(f"note {note_id} not found")

    cursor.execute("""
        INSERT INTO note_versions (
            note_id, version, content, tags_json, status,
            superseded_by_note_id, change_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        note_id,
        note["version"],
        note["content"],
        json.dumps([tag["name"] for tag in note["tags"]], ensure_ascii=False),
        note["status"],
        note["superseded_by_note_id"],
        change_type,
    ))


def _conflict_result(note: dict, expected_version: int) -> dict:
    return {
        "status": "conflict",
        "expected_version": expected_version,
        "current_version": note["version"],
        "current_note": note,
    }


def _fts_match_expression(query: str) -> str:
    # Цитированные токены не дают тексту стать FTS-синтаксисом.
    tokens = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))
    if not tokens:
        raise ValueError("query must contain at least one searchable word")
    return " OR ".join(f'"{token}"' for token in tokens)


def get_all_tags() -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM tags ORDER BY name"
        ).fetchall()
        return [dict(row) for row in rows]


def add_note(content: str, tags: list[str]) -> dict:
    _validate_text(content, "content")
    _validate_tags(tags)

    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO notes (content) VALUES (?)",
            (content,),
        )
        note_id = cursor.lastrowid
        _set_note_tags(cursor, note_id, tags)
        _sync_note_fts(cursor, note_id)
        _record_version(cursor, note_id, "created")
        return _fetch_note(cursor, note_id)


def _tag_filter(tags: list[str], match_all_tags: bool) -> tuple[str, list]:
    """Сформировать фильтр без дублирования заметок при нескольких тегах."""
    _validate_tags(tags)
    if not isinstance(match_all_tags, bool):
        raise ValueError("match_all_tags must be a boolean")
    unique_tags = list(dict.fromkeys(tags))
    if not unique_tags:
        return "", []
    placeholders = ",".join("?" * len(unique_tags))
    having = "HAVING COUNT(DISTINCT t.name) = ?" if match_all_tags else ""
    parameters = [*unique_tags, len(unique_tags)] if match_all_tags else unique_tags
    return f"""
        AND n.id IN (
            SELECT nt.note_id
            FROM note_tags nt
            JOIN tags t ON t.id = nt.tag_id
            WHERE t.name IN ({placeholders})
            GROUP BY nt.note_id
            {having}
        )
    """, parameters


def get_context(
    query: Optional[str] = None,
    tags: Optional[list[str]] = None,
    match_all_tags: bool = True,
    limit: int = 5,
    include_superseded: bool = False,
) -> list[dict]:
    """Искать по всей памяти; теги сужают выборку до ранжирования и LIMIT."""
    _validate_limit(limit)
    if not isinstance(include_superseded, bool):
        raise ValueError("include_superseded must be a boolean")
    tag_filter, tag_params = _tag_filter([] if tags is None else tags, match_all_tags)
    status_filter = "" if include_superseded else "AND n.status = 'active'"

    if query is not None:
        _validate_text(query, "query")
        match_expression = _fts_match_expression(query)
        settings = Settings.from_env()
        if settings.enabled:
            import hybrid
            result = hybrid.search(query, match_expression, tag_filter, tag_params,
                                   status_filter, settings, limit)
            if result is not None:
                return result

    with connection() as conn:
        cursor = conn.cursor()
        if query is not None:
            _validate_text(query, "query")
            match_expression = _fts_match_expression(query)
            rows = cursor.execute(f"""
                SELECT {NOTE_FIELDS}, -bm25(notes_fts) AS relevance_score
                FROM notes_fts
                JOIN notes n ON n.id = notes_fts.note_id
                WHERE notes_fts MATCH ? {status_filter} {tag_filter}
                ORDER BY bm25(notes_fts), n.updated_at DESC, n.id DESC
                LIMIT ?
            """, [match_expression, *tag_params, limit]).fetchall()
        else:
            rows = cursor.execute(f"""
                SELECT {NOTE_FIELDS}, NULL AS relevance_score
                FROM notes n
                WHERE 1 = 1 {status_filter} {tag_filter}
                ORDER BY n.updated_at DESC, n.id DESC
                LIMIT ?
            """, [*tag_params, limit]).fetchall()
        notes = []
        for row in rows:
            note = dict(row)
            note["tags"] = _get_note_tags(cursor, note["id"])
            notes.append(note)
        return notes


def get_notes_by_tags(
    tags: list[str],
    match_all_tags: bool = True,
    page: int = 1,
    limit: int = 5,
    include_superseded: bool = False,
) -> dict:
    _validate_pagination(page, limit)
    if not isinstance(include_superseded, bool):
        raise ValueError("include_superseded must be a boolean")
    tag_filter, tag_params = _tag_filter(tags, match_all_tags)
    if not tags:
        return {"notes": [], "total": 0, "page": page, "total_pages": 0}
    status_filter = "" if include_superseded else "AND n.status = 'active'"

    with connection() as conn:
        cursor = conn.cursor()
        total = cursor.execute(f"""
            SELECT COUNT(*) AS total FROM notes n
            WHERE 1 = 1 {status_filter} {tag_filter}
        """, tag_params).fetchone()["total"]
        rows = cursor.execute(f"""
            SELECT {NOTE_FIELDS} FROM notes n
            WHERE 1 = 1 {status_filter} {tag_filter}
            ORDER BY n.updated_at DESC, n.id DESC
            LIMIT ? OFFSET ?
        """, [*tag_params, limit, (page - 1) * limit]).fetchall()
        notes = []
        for row in rows:
            note = dict(row)
            note["tags"] = _get_note_tags(cursor, note["id"])
            notes.append(note)
        return {
            "notes": notes,
            "total": total,
            "page": page,
            "total_pages": (total + limit - 1) // limit if total else 0,
        }


def get_note_by_id(note_id: int) -> Optional[dict]:
    _validate_positive_int(note_id, "note_id")
    with connection() as conn:
        return _fetch_note(conn.cursor(), note_id)


def _update_note(
    note_id: int,
    expected_version: int,
    content: Optional[str],
    tags: Optional[list[str]],
    change_type: str,
) -> dict:
    _validate_positive_int(note_id, "note_id")
    _validate_positive_int(expected_version, "expected_version")
    if content is None and tags is None:
        raise ValueError("content or tags must be provided")
    if content is not None:
        _validate_text(content, "content")
    if tags is not None:
        _validate_tags(tags)

    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        current = _fetch_note(cursor, note_id)
        if not current:
            return {"status": "not_found", "note_id": note_id}
        if current["version"] != expected_version:
            return _conflict_result(current, expected_version)
        if current["status"] == "superseded":
            return {"status": "superseded", "current_note": current}

        next_content = content if content is not None else current["content"]
        cursor.execute("""
            UPDATE notes
            SET content = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
        """, (next_content, note_id, expected_version))
        if tags is not None:
            _set_note_tags(cursor, note_id, tags)
        _sync_note_fts(cursor, note_id)
        _record_version(cursor, note_id, change_type)
        success_status = "resolved" if change_type == "conflict_resolution" else "updated"
        return {"status": success_status, "note": _fetch_note(cursor, note_id)}


def update_note(
    note_id: int,
    expected_version: int,
    content: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> dict:
    """Обновить заметку, если expected_version совпадает с текущей."""
    return _update_note(note_id, expected_version, content, tags, "updated")


def resolve_note_conflict(
    note_id: int,
    expected_current_version: int,
    merged_content: str,
    merged_tags: list[str],
) -> dict:
    """Сохранить явно объединенную версию после конфликта."""
    return _update_note(
        note_id,
        expected_current_version,
        merged_content,
        merged_tags,
        "conflict_resolution",
    )


def get_note_history(note_id: int, limit: int = 20) -> list[dict]:
    _validate_positive_int(note_id, "note_id")
    _validate_limit(limit)
    with connection() as conn:
        rows = conn.execute("""
            SELECT version, content, tags_json, status, superseded_by_note_id,
                   change_type, created_at
            FROM note_versions
            WHERE note_id = ?
            ORDER BY version DESC
            LIMIT ?
        """, (note_id, limit)).fetchall()
        history = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json"))
            history.append(item)
        return history


def restore_note_version(
    note_id: int,
    version: int,
    expected_version: int,
) -> dict:
    _validate_positive_int(note_id, "note_id")
    _validate_positive_int(version, "version")
    _validate_positive_int(expected_version, "expected_version")

    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        current = _fetch_note(cursor, note_id)
        if not current:
            return {"status": "not_found", "note_id": note_id}
        if current["version"] != expected_version:
            return _conflict_result(current, expected_version)

        snapshot = cursor.execute("""
            SELECT content, tags_json, status, superseded_by_note_id
            FROM note_versions
            WHERE note_id = ? AND version = ?
        """, (note_id, version)).fetchone()
        if not snapshot:
            return {"status": "version_not_found", "note_id": note_id, "version": version}

        cursor.execute("""
            UPDATE notes
            SET content = ?, status = ?, superseded_by_note_id = ?,
                superseded_at = CASE WHEN ? = 'superseded' THEN CURRENT_TIMESTAMP ELSE NULL END,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
        """, (
            snapshot["content"],
            snapshot["status"],
            snapshot["superseded_by_note_id"],
            snapshot["status"],
            note_id,
            expected_version,
        ))
        _set_note_tags(cursor, note_id, json.loads(snapshot["tags_json"]))
        _sync_note_fts(cursor, note_id)
        _record_version(cursor, note_id, f"restored_from_{version}")
        return {
            "status": "restored",
            "restored_from_version": version,
            "note": _fetch_note(cursor, note_id),
        }


def mark_note_superseded(
    note_id: int,
    replacement_note_id: int,
    expected_version: int,
) -> dict:
    _validate_positive_int(note_id, "note_id")
    _validate_positive_int(replacement_note_id, "replacement_note_id")
    _validate_positive_int(expected_version, "expected_version")
    if note_id == replacement_note_id:
        raise ValueError("a note cannot supersede itself")

    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        current = _fetch_note(cursor, note_id)
        replacement = _fetch_note(cursor, replacement_note_id)
        if not current:
            return {"status": "not_found", "note_id": note_id}
        if not replacement:
            return {"status": "replacement_not_found", "replacement_note_id": replacement_note_id}
        if current["version"] != expected_version:
            return _conflict_result(current, expected_version)
        if replacement["status"] != "active":
            raise ValueError("replacement note must be active")

        cursor.execute("""
            UPDATE notes
            SET status = 'superseded', superseded_by_note_id = ?,
                superseded_at = CURRENT_TIMESTAMP,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
        """, (replacement_note_id, note_id, expected_version))
        _record_version(cursor, note_id, "superseded")
        return {
            "status": "superseded",
            "note": _fetch_note(cursor, note_id),
            "replacement_note": replacement,
        }


def delete_note(note_id: int) -> bool:
    _validate_positive_int(note_id, "note_id")
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
        cursor = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cursor.rowcount > 0
