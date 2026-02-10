"""Compare BM25, vectors and RRF on one SQLite snapshot and fixed known targets."""
import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import tempfile
import time
from unittest.mock import patch

import database
from embeddings import EmbeddingClient, EmbeddingError, Settings
import hybrid
import models
import vector_index


def metrics(results, mode):
    hits, reciprocals = [], []
    for result in results:
        ranking = result[mode]['ids']
        positions = [i for i, note_id in enumerate(ranking, 1) if note_id in result['expected']]
        hits.append(int(bool(positions)))
        reciprocals.append(1/min(positions) if positions else 0)
    latencies = sorted(r[mode]['seconds'] for r in results)
    return {'queries':len(results), 'hits_at_5':sum(hits),
            'hit_at_5':statistics.mean(hits), 'mrr_at_5':statistics.mean(reciprocals),
            'latency_mean_seconds':statistics.mean(latencies),
            'latency_median_seconds':statistics.median(latencies),
            'latency_p95_seconds':latencies[math.ceil(len(latencies)*.95)-1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--queries', type=Path, default=Path(__file__).parent/'eval/queries.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.database:
        database.DATABASE_PATH=args.database.expanduser().resolve(strict=True)
    settings = Settings.from_env()
    dataset = json.loads(args.queries.read_text())
    cases = dataset['cases']
    results=[]
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory)/'memory.db'
        with sqlite3.connect(database.DATABASE_PATH.resolve().as_uri()+'?mode=ro',uri=True) as src:
            with sqlite3.connect(snapshot) as dst:
                src.backup(dst)
        database.DATABASE_PATH = snapshot
        coverage = vector_index.coverage(settings)
        if coverage['pending']:
            raise RuntimeError('Complete indexing before evaluation')
        with database.connection() as conn:
            corpus = [tuple(r) for r in conn.execute('''SELECT n.id,n.version,n.status,n.updated_at,s.content_hash
                FROM notes n LEFT JOIN embedding_sources s ON s.note_id=n.id ORDER BY n.id''')]
            for case in cases:
                filt, params = models._tag_filter(case.get('tags',[]),case.get('match_all_tags',True))
                allowed={r[0] for r in conn.execute(f"SELECT n.id FROM notes n WHERE n.status='active' {filt}",params)}
                if not case['expected'] or not set(case['expected']) <= allowed:
                    raise ValueError('Expected targets missing or excluded by filters')
        client=EmbeddingClient(settings)
        token_counts=client.token_counts([r['document'] for r in _sources()])
        for i,case in enumerate(cases,1):
            params = {k:case[k] for k in ('query','tags','match_all_tags') if k in case}
            started=time.perf_counter()
            with patch.object(models.Settings,'from_env',return_value=replace(settings,enabled=False)):
                lexical=models.get_context(**params,limit=5)
            lexical_seconds=time.perf_counter()-started
            filt,tag_params=models._tag_filter(case.get('tags',[]),case.get('match_all_tags',True))
            started=time.perf_counter()
            error=None
            try:
                ranked=hybrid.rankings(case['query'],models._fts_match_expression(case['query']),
                    filt,tag_params,"AND n.status='active'",settings,5,client=client)
                fused=hybrid.fuse(ranked['lexical'],ranked['semantic'],ranked['notes'],5,
                                  identifier_hits=ranked['identifier_hits'])
                semantic_ids=ranked['semantic'][:5]
            except EmbeddingError as exc:
                # Production search would fall back to BM25, not fail the MCP call.
                error=str(exc)
                fused=lexical
                semantic_ids=[]
            elapsed=time.perf_counter()-started
            result={**case, 'embedding_error':error,
                'bm25':{'ids':[n['id'] for n in lexical], 'seconds':lexical_seconds},
                'vector':{'ids':semantic_ids, 'seconds':elapsed},
                'hybrid':{'ids':[n['id'] for n in fused], 'seconds':elapsed}}
            results.append(result)
            print(json.dumps({'completed':i,'total':len(cases),
                'hits':{m:bool(set(result[m]['ids']) & set(case['expected'])) for m in ('bm25','vector','hybrid')}}),flush=True)
    report={
        'methodology':dataset['description'],
        'latency_note':'One request per query; vector/hybrid share the same query embedding and candidate computation. Network latency is included. Not a load test.',
        'corpus_sha256':hashlib.sha256(json.dumps(corpus).encode()).hexdigest(),
        'queries_sha256':hashlib.sha256(args.queries.read_bytes()).hexdigest(),
        'embedding_errors':sum(r['embedding_error'] is not None for r in results),
        'timeout_seconds':settings.timeout,
        'model':settings.model,'dimensions':settings.dimensions,'candidates':settings.candidates,'rrf_k':60,
        'coverage':coverage, 'identifier_policy':'Exact issue-style identifiers get +1 per matched distinct identifier before final sort',
        'document_tokens':{'min':min(token_counts),'max':max(token_counts),'mean':statistics.mean(token_counts)},
        'summary':{m:metrics(results,m) for m in ('bm25','vector','hybrid')},
        'by_kind':{k:{m:metrics([r for r in results if r['kind']==k],m) for m in ('bm25','vector','hybrid')}
                   for k in sorted({r['kind'] for r in results})},
        'results':results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report['summary'],ensure_ascii=False,indent=2))


def _sources():
    with database.connection() as conn:
        return [dict(r) for r in conn.execute('SELECT document FROM embedding_sources')]


if __name__ == '__main__':
    main()
