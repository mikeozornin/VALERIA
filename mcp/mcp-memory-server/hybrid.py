"""Exact semantic retrieval and reciprocal-rank fusion over filtered notes."""
import logging
import math
import re
import struct
import time

from embeddings import EmbeddingClient, EmbeddingError
from database import connection

log = logging.getLogger('memory.search')


def fuse(lexical_ids, semantic_ids, notes, limit, k=60, identifier_hits=None):
    scores = {}
    for ranking in (lexical_ids, semantic_ids):
        for rank, note_id in enumerate(ranking, 1):
            scores[note_id] = scores.get(note_id, 0.0) + 1 / (k + rank)
    # An explicitly requested issue ID outweighs fuzzy similarity. RRF's maximum
    # is < 1, so one exact identifier match is sufficient; no corpus-specific IDs.
    for note_id, count in (identifier_hits or {}).items():
        scores[note_id] = scores.get(note_id, 0.0) + count
    ordered = sorted(scores, key=lambda i: (scores[i], notes[i]['updated_at'], i), reverse=True)
    return [dict(notes[i], relevance_score=scores[i]) for i in ordered[:limit]]


def rankings(query, match_expression, tag_filter, tag_params, status_filter, settings,
             limit, *, client=None, query_vector=None):
    """One query embedding, then one consistent SQLite snapshot for both branches."""
    from models import NOTE_FIELDS, _get_note_tags
    started = time.perf_counter()
    if query_vector is None:
        query_vector = (client or EmbeddingClient(settings)).query(query)
    if len(query_vector) != settings.dimensions:
        raise EmbeddingError('Invalid query dimensions')
    candidates = max(settings.candidates, limit)
    identifiers = set(re.findall(r"\b[A-Za-z][A-Za-z0-9_]{1,31}-\d+\b", query))
    exact = re.compile(r"(?<![\w-])(?:" + '|'.join(re.escape(i) for i in identifiers) + r")(?![\w-])", re.I) if identifiers else None
    with connection() as conn:
        conn.execute('BEGIN')
        cursor = conn.cursor()
        lexical = cursor.execute(f'''SELECT n.id, -bm25(notes_fts) AS score
            FROM notes_fts JOIN notes n ON n.id=notes_fts.note_id
            WHERE notes_fts MATCH ? {status_filter} {tag_filter}
            ORDER BY bm25(notes_fts), n.updated_at DESC, n.id DESC LIMIT ?''',
            [match_expression, *tag_params, candidates]).fetchall()
        rows = cursor.execute(f'''SELECT {NOTE_FIELDS}, e.vector, s.document AS search_document
            FROM notes n LEFT JOIN embedding_sources s ON s.note_id=n.id
            LEFT JOIN note_embeddings e ON e.note_id=s.note_id
                AND e.content_hash=s.content_hash AND e.profile=? AND e.dimensions=?
            WHERE 1=1 {status_filter} {tag_filter}''',
            [settings.profile, settings.dimensions, *tag_params]).fetchall()
        notes, similarities, identifier_hits = {}, {}, {}
        for row in rows:
            note = dict(row)
            blob = note.pop('vector')
            document = note.pop('search_document') or note['content']
            if exact:
                matches = {m.group().lower() for m in exact.finditer(document)}
                if matches:
                    identifier_hits[note['id']] = len(matches)
            notes[note['id']] = note
            if blob is not None:
                if len(blob) != settings.dimensions * 4:
                    raise EmbeddingError('Corrupt stored vector')
                vector = struct.unpack(f'<{settings.dimensions}f', blob)
                score = math.fsum(x * y for x, y in zip(vector, query_vector, strict=True))
                if not math.isfinite(score):
                    raise EmbeddingError('Corrupt stored vector values')
                similarities[note['id']] = score
        semantic = sorted(similarities, key=lambda i: (similarities[i], notes[i]['updated_at'], i),
                          reverse=True)[:candidates]
        # Fetch tags only for candidates, retaining the same snapshot.
        for i in set(semantic) | {r['id'] for r in lexical} | set(identifier_hits):
            notes[i]['tags'] = _get_note_tags(cursor, i)
    return {'notes': notes, 'lexical': [r['id'] for r in lexical], 'semantic': semantic,
            'bm25_scores': {r['id']: r['score'] for r in lexical},
            'cosine_scores': similarities, 'identifier_hits': identifier_hits, 'indexed': len(similarities), 'eligible': len(notes),
            'seconds': time.perf_counter() - started}


def search(query, match_expression, tag_filter, tag_params, status_filter, settings, limit):
    try:
        result = rankings(query, match_expression, tag_filter, tag_params, status_filter, settings, limit)
    except EmbeddingError as exc:
        log.warning('search_mode=bm25 fallback=%s', exc)
        return None
    if not result['semantic']:
        log.warning('search_mode=bm25 fallback=no_current_vectors')
        return None
    log.info('search_mode=hybrid indexed=%s eligible=%s seconds=%.3f',
             result['indexed'], result['eligible'], result['seconds'])
    return fuse(result['lexical'], result['semantic'], result['notes'], limit,
                identifier_hits=result['identifier_hits'])
