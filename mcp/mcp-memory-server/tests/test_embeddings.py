import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
import embeddings
import hybrid
import models
import vector_index


class ClientTests(unittest.TestCase):
    def client(self, handler, **kwargs):
        client = embeddings.EmbeddingClient(embeddings.Settings(dimensions=32, **kwargs),
                                            transport=httpx.MockTransport(handler))
        client.validate_lengths = lambda texts: [len(t) for t in texts]
        return client

    def test_order_normalization_auth_and_payload(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload['input'], ['one', 'two'])
            self.assertEqual(payload['dimensions'], 32)
            self.assertEqual(request.headers['Authorization'], 'Bearer secret')
            self.assertEqual(request.headers['Accept-Encoding'], 'identity')
            self.assertTrue(str(request.url).endswith('/v1/embeddings'))
            return httpx.Response(200, json={'data': [
                {'index': 1, 'embedding': [0, 2] + [0]*30},
                {'index': 0, 'embedding': [3, 0] + [0]*30}]})
        vectors = self.client(handler, api_key='secret').embed_sync(['one', 'two'])
        self.assertEqual(vectors[0], [1, 0]+[0]*30)
        self.assertEqual(vectors[1], [0, 1]+[0]*30)

    def test_native_dimensions_omit_unsupported_parameter(self):
        def handler(request):
            self.assertNotIn('dimensions', json.loads(request.content))
            return httpx.Response(200,json={'data':[{'index':0,'embedding':[1]*2560}]})
        client=embeddings.EmbeddingClient(embeddings.Settings(), transport=httpx.MockTransport(handler))
        client.validate_lengths=lambda texts: [1]
        self.assertEqual(len(client.embed_sync(['test'])[0]),2560)

    def test_bad_responses_and_no_upstream_body_exposure(self):
        bodies = [
            {'data': []},
            {'data': [{'index': 1, 'embedding': [1]*32}]},
            {'data': [{'index': True, 'embedding': [1]*32}]},
            {'data': [{'index': 0, 'embedding': [1]*31}]},
            {'data': [{'index': 0, 'embedding': [0]*32}]},
            {'data': [{'index': 0, 'embedding': [True]*32}]},
            {'data': [{'index': 0, 'embedding': ['secret']*32}]},
            {'model': 'wrong', 'data': [{'index': 0, 'embedding': [1]*32}]},
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(embeddings.EmbeddingError):
                self.client(lambda req: httpx.Response(200, json=body)).embed_sync(['one'])
        with self.assertRaisesRegex(embeddings.EmbeddingError, '^Embedding API HTTP 401$'):
            self.client(lambda req: httpx.Response(401, text='SECRET')).embed_sync(['one'])
        with self.assertRaises(embeddings.EmbeddingError):
            self.client(lambda req: httpx.Response(200, content=b'{bad')).embed_sync(['one'])

    def test_duplicate_indices_and_nonfinite(self):
        body = {'data': [{'index': 0, 'embedding': [1]*32}]*2}
        with self.assertRaises(embeddings.EmbeddingError):
            self.client(lambda req: httpx.Response(200, json=body)).embed_sync(['one', 'two'])
        for value in (float('nan'), float('inf')):
            with self.assertRaises(embeddings.EmbeddingError):
                embeddings.normalize([value]*32, 32)

    def test_retry_and_total_deadline(self):
        calls = []
        def handler(req):
            calls.append(1)
            if len(calls) < 2:
                return httpx.Response(503)
            return httpx.Response(200, json={'data':[{'index':0,'embedding':[1]*32}]})
        self.client(handler).embed_sync(['one'])
        self.assertEqual(len(calls), 2)
        async def slow(req):
            await asyncio.sleep(1)
        with self.assertRaisesRegex(embeddings.EmbeddingError, 'timed out'):
            self.client(slow, timeout=0.01).embed_sync(['one'])

    def test_token_limit_never_truncates_and_query_instruction(self):
        client = embeddings.EmbeddingClient(embeddings.Settings())
        client.token_counts = lambda texts: [32000]
        with self.assertRaisesRegex(embeddings.EmbeddingError, 'token limit'):
            client.validate_lengths(['too long'])
        with patch.object(client, 'embed_sync', return_value=[[1]]) as call:
            client.query('question')
        text = call.call_args.args[0][0]
        self.assertTrue(text.startswith('Instruct: '))
        self.assertTrue(text.endswith('\nQuery: question'))


class FakeClient:
    def validate_lengths(self, texts):
        return [10]*len(texts)

    def embed_sync(self, texts):
        return [[1.0]+[0.0]*31 for _ in texts]

    def query(self, text):
        return [1.0]+[0.0]*31


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.tmp.name)/'memory.db'
        database.init_db()
        self.settings = embeddings.Settings(enabled=True, dimensions=32, batch_size=8)
        self.client = FakeClient()
        self.env = patch.dict(os.environ, {'MCP_MEMORY_HYBRID':'false'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        database.DATABASE_PATH = self.previous
        self.tmp.cleanup()

    def note(self, content='old content', tags=None):
        return models.add_note(content, tags or ['architecture'])

    def run_batch(self):
        return vector_index.index_batch(self.settings, self.client)

    def test_update_tags_restore_delete_noop_and_restart(self):
        n = self.note()
        self.assertEqual(self.run_batch()['stored'], 1)
        models.update_note(n['id'], 1, content='old content')
        self.assertEqual(self.run_batch()['selected'], 0)
        models.update_note(n['id'], 2, content='new content')
        self.assertEqual(vector_index.coverage(self.settings)['ready'], 0)
        database.init_db()  # Pending work survives restart and FTS rebuild.
        self.assertEqual(self.run_batch()['stored'], 1)
        models.update_note(n['id'], 3, tags=['other'])
        self.assertEqual(vector_index.coverage(self.settings)['pending'], 1)
        self.run_batch()
        models.restore_note_version(n['id'], 1, 4)
        self.assertEqual(vector_index.coverage(self.settings)['pending'], 1)
        self.run_batch()
        models.delete_note(n['id'])
        self.assertEqual(vector_index.coverage(self.settings)['total'], 0)
        with database.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM note_embeddings').fetchone()[0], 0)

    def test_concurrent_edit_and_delete_cannot_publish_stale_vector(self):
        n = self.note()
        rows = vector_index.pending(self.settings)
        models.update_note(n['id'], 1, content='new')
        self.assertEqual(vector_index.publish(self.settings, rows, self.client.embed_sync(['old'])), 0)
        rows = vector_index.pending(self.settings)
        models.delete_note(n['id'])
        self.assertEqual(vector_index.publish(self.settings, rows, self.client.embed_sync(['new'])), 0)

    def test_failure_durable_retry_and_profile_invalidation(self):
        n = self.note()
        with patch.object(self.client, 'embed_sync', side_effect=embeddings.EmbeddingError('unavailable')):
            self.assertEqual(self.run_batch()['failed'], 1)
        self.assertEqual(models.get_note_by_id(n['id'])['content'], 'old content')
        self.assertEqual(len(vector_index.pending(self.settings)), 0)
        self.assertEqual(len(vector_index.pending(self.settings, due_only=False)), 1)
        with database.connection() as conn:
            conn.execute('UPDATE embedding_sources SET next_attempt=0')
        self.assertEqual(self.run_batch()['stored'], 1)
        changed = replace(self.settings, model='another-model')
        self.assertEqual(vector_index.coverage(changed)['ready'], 0)
        self.assertEqual(vector_index.index_batch(changed, self.client)['stored'], 1)

    def test_oversized_note_does_not_block_neighbours(self):
        self.note('oversized')
        self.note('valid')
        def check(texts):
            if texts[0].startswith('oversized'):
                raise embeddings.EmbeddingError('too long')
        self.client.validate_lengths = check
        self.assertEqual(self.run_batch(), {'selected':2, 'stored':1, 'failed':1})

    def test_filtering_stale_vectors_and_superseded(self):
        a = self.note('alpha', ['x','y'])
        b = self.note('beta', ['x'])
        c = self.note('gamma', ['y'])
        self.run_batch()
        def rank(tags, all_tags=True, superseded=False):
            filt, params = models._tag_filter(tags, all_tags)
            return hybrid.rankings('question', '"question"', filt, params,
                '' if superseded else "AND n.status='active'", self.settings, 1, client=self.client)
        self.assertEqual(rank(['x','y'])['semantic'], [a['id']])
        self.assertEqual(len(rank(['x','y'],False)['semantic']), 3)
        models.update_note(a['id'], 1, content='new')
        self.assertEqual(rank(['x','y'])['semantic'], [])
        self.run_batch()
        models.mark_note_superseded(a['id'], b['id'], 2)
        self.assertEqual(rank(['x','y'])['semantic'], [])
        self.assertEqual(rank(['x','y'],superseded=True)['semantic'], [a['id']])

    def test_rrf_order_dedup_and_ties(self):
        notes = {i:{'id':i,'updated_at':'2026'} for i in range(1,5)}
        result = hybrid.fuse([1,2,3], [3,2,4], notes, 5)
        self.assertEqual([n['id'] for n in result], [3,2,1,4])
        self.assertEqual(len({n['id'] for n in result}), 4)
        self.assertAlmostEqual(result[0]['relevance_score'], 1/61+1/63)
        self.assertEqual(hybrid.fuse([1],[2], notes, 1)[0]['id'], 2)

    def test_exact_identifiers_survive_candidate_cutoff_and_respect_boundaries(self):
        wanted = self.note('Fix PROJ-42', ['x'])
        other = self.note('Unrelated PROJ-420', ['x'])
        self.run_batch()
        settings = replace(self.settings, candidates=1)
        result = hybrid.rankings('PROJ-42', models._fts_match_expression('PROJ-42'),
            '', [], "AND n.status='active'", settings, 1, client=self.client)
        self.assertEqual(result['identifier_hits'], {wanted['id']:1})
        fused = hybrid.fuse(result['lexical'], result['semantic'], result['notes'], 1,
                            identifier_hits=result['identifier_hits'])
        self.assertEqual(fused[0]['id'], wanted['id'])
        self.assertGreaterEqual(fused[0]['relevance_score'], 1)
        self.assertIn('tags', fused[0])

    def test_public_contract_and_fallback(self):
        n=self.note('lexical needle')
        expected=models.get_context(query='needle')
        with patch.object(models.Settings, 'from_env', return_value=self.settings):
            with patch.object(embeddings.EmbeddingClient, 'query', side_effect=embeddings.EmbeddingError('down')):
                self.assertEqual(models.get_context(query='needle'), expected)
            self.run_batch()
            with patch.object(embeddings.EmbeddingClient, 'query', return_value=self.client.query('x')):
                result=models.get_context(query='semantic question')
                self.assertEqual(result[0]['id'], n['id'])
                self.assertEqual(set(result[0]), set(expected[0]))
                self.assertIsNone(models.get_context()[0]['relevance_score'])
                self.assertEqual(models.get_notes_by_tags(['architecture'])['total'],1)


if __name__ == '__main__':
    unittest.main()
