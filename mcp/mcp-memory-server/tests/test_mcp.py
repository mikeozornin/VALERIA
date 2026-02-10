"""Проверка контракта через реальный stdio MCP на временной БД."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SERVER_DIR = Path(__file__).resolve().parents[1]


class MemoryMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_unified_tools_over_stdio(self):
        with tempfile.TemporaryDirectory() as directory:
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(SERVER_DIR / 'server.py')],
                cwd=str(SERVER_DIR),
                env={**os.environ, 'MCP_MEMORY_DB_PATH': str(Path(directory) / 'memory.db')},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tool_list = await session.list_tools()
                    tools = {tool.name: tool for tool in tool_list.tools}
                    self.assertEqual(len(tools), 11)
                    self.assertNotIn('get_project_list', tools)
                    self.assertNotIn('create_project', tools)
                    for tool in tools.values():
                        self.assertNotIn('project_name', tool.inputSchema.get('properties', {}))

                    async def call(name, args):
                        result = await session.call_tool(name, args)
                        self.assertFalse(result.isError, result.content)
                        return result.structuredContent if result.structuredContent is not None else json.loads(result.content[0].text)

                    note = await call('add_note', {'content': 'MCP PostgreSQL decision', 'tags': ['architecture', 'component:one']})
                    other = await call('add_note', {'content': 'MCP PostgreSQL replacement', 'tags': ['architecture', 'component:two']})
                    result = await call('get_context', {'query': 'PostgreSQL'})
                    self.assertEqual(len(result['result']), 2)
                    result = await call('get_context', {'query': 'PostgreSQL', 'tags': ['architecture', 'component:one'], 'limit': 1})
                    self.assertEqual(result['result'][0]['id'], note['id'])
                    result = await call('get_notes_by_tags', {'tags': ['component:one', 'component:two'], 'match_all_tags': False})
                    self.assertEqual(result['total'], 2)
                    updated = await call('update_note', {'note_id': note['id'], 'expected_version': 1, 'content': 'MCP Redis'})
                    self.assertEqual(updated['note']['version'], 2)
                    conflict = await call('update_note', {'note_id': note['id'], 'expected_version': 1, 'content': 'stale'})
                    self.assertEqual(conflict['status'], 'conflict')
                    resolved = await call('resolve_note_conflict', {'note_id': note['id'], 'expected_current_version': 2, 'merged_content': 'MCP merged', 'merged_tags': ['architecture']})
                    self.assertEqual(resolved['status'], 'resolved')
                    await call('restore_note_version', {'note_id': note['id'], 'expected_version': 3, 'version': 1})
                    history = await call('get_note_history', {'note_id': note['id']})
                    self.assertEqual(len(history['result']), 4)
                    await call('mark_note_superseded', {'note_id': note['id'], 'replacement_note_id': other['id'], 'expected_version': 4})
                    result = await call('get_context', {'query': 'PostgreSQL'})
                    self.assertEqual([n['id'] for n in result['result']], [other['id']])
                    await call('delete_note', {'note_id': other['id']})
                    result = await call('get_note_by_id', {'note_id': note['id']})
                    self.assertIsNone(result['result']['superseded_by_note_id'])
                    result = await call('get_all_tags', {})
                    self.assertGreater(len(result['result']), 0)


if __name__ == '__main__':
    unittest.main()
