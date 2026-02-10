"""Real stdio MCP, HTTP fixture and background indexing; never touches live notes."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

SERVER_DIR = Path(__file__).resolve().parents[1]


class HybridMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_reindexes_and_hybrid_calls_over_stdio(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.extend(body['input'])
                vectors=[]
                for i,text in enumerate(body['input']):
                    # Semantic synonyms not present in the document's FTS tokens.
                    first = any(word in text for word in ('alpha','first','restored'))
                    vector=([1,0] if first else [0,1])+[0]*30
                    vectors.append({'index':i,'embedding':vector})
                encoded=json.dumps({'data':vectors}).encode()
                self.send_response(200)
                self.send_header('Content-Length',str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                db=Path(directory)/'memory.db'
                tokenizer=Tokenizer(WordLevel({'[UNK]':0},unk_token='[UNK]'))
                tokenizer.pre_tokenizer=Whitespace()
                tokenizer_path=Path(directory)/'tokenizer.json';tokenizer.save(str(tokenizer_path))
                params=StdioServerParameters(command=sys.executable,args=[str(SERVER_DIR/'server.py')],
                    cwd=str(SERVER_DIR),env={**os.environ,'MCP_MEMORY_DB_PATH':str(db),
                    'MCP_MEMORY_HYBRID':'true','MCP_MEMORY_EMBEDDING_DIMENSIONS':'32',
                    'MCP_MEMORY_TOKENIZER_PATH':str(tokenizer_path),
                    'MCP_MEMORY_EMBEDDING_BASE_URL':f'http://127.0.0.1:{http.server_port}/v1',
                    'MCP_MEMORY_EMBEDDING_API_KEY':'','MCP_MEMORY_EMBEDDING_TIMEOUT':'2'})
                async with stdio_client(params) as (read,write):
                    async with ClientSession(read,write) as session:
                        await session.initialize()
                        tools=(await session.list_tools()).tools
                        self.assertEqual(len(tools),11)
                        context=next(t for t in tools if t.name=='get_context')
                        self.assertEqual(set(context.inputSchema['properties']),
                            {'query','tags','match_all_tags','limit','include_superseded'})
                        async def call(name,arguments):
                            result=await session.call_tool(name,arguments)
                            self.assertFalse(result.isError,result.content)
                            return result.structuredContent if result.structuredContent is not None else json.loads(result.content[0].text)
                        async def ready():
                            deadline=time.monotonic()+12
                            while time.monotonic()<deadline:
                                with sqlite3.connect(db) as conn:
                                    missing=conn.execute('''SELECT count(*) FROM embedding_sources s
                                        LEFT JOIN note_embeddings e ON s.note_id=e.note_id AND s.content_hash=e.content_hash
                                        WHERE e.note_id IS NULL''').fetchone()[0]
                                if not missing:
                                    return
                                await asyncio.sleep(.1)
                            self.fail('Worker did not index queued changes')
                        first=await call('add_note',{'content':'alpha','tags':['x']})
                        second=await call('add_note',{'content':'beta','tags':['y']})
                        await ready()
                        result=await call('get_context',{'query':'first','limit':1})
                        self.assertEqual(result['result'][0]['id'],first['id'])
                        await call('update_note',{'note_id':first['id'],'expected_version':1,'content':'beta changed'})
                        await ready()
                        with sqlite3.connect(db) as conn:
                            self.assertEqual(conn.execute('SELECT count(*) FROM note_embeddings').fetchone()[0],2)
                        await call('restore_note_version',{'note_id':first['id'],'expected_version':2,'version':1})
                        await ready()
                        result=await call('get_context',{'query':'restored','tags':['x'],'limit':1})
                        self.assertEqual(result['result'][0]['id'],first['id'])
                        await call('delete_note',{'note_id':second['id']})
                        self.assertTrue(any(t.startswith('Instruct: ') for t in requests))
                        self.assertTrue(any(t=='alpha\n\nTags: x' for t in requests))
                        self.assertTrue(any(t.startswith('beta changed') for t in requests))
        finally:
            await asyncio.to_thread(http.shutdown)
            http.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
