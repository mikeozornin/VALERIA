import json
from types import SimpleNamespace

import pytest

from youtrack_valeria_watcher.config import Settings
from youtrack_valeria_watcher.mcp_client import YouTrackSession
from youtrack_valeria_watcher.models import Issue


def result(payload):
    return SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "search_issues":
            offset = arguments["offset"]
            if offset == 0:
                return result(
                    {
                        "issuesPage": [
                            {
                                "id": f"DEMO-{index}",
                                "url": f"https://youtrack.example/issue/DEMO-{index}",
                            }
                            for index in range(20)
                        ],
                        "hasNextPage": True,
                        "nextPageOffset": 20,
                    }
                )
            return result(
                {
                    "issuesPage": [
                        {"id": "DEMO-20", "url": "https://youtrack.example/issue/DEMO-20"}
                    ],
                    "hasNextPage": False,
                    "nextPageOffset": 21,
                }
            )
        offset = arguments["offset"]
        count = 10 if offset == 0 else 1
        return result(
            [
                {
                    "author": "User",
                    "createdAt": 1000 + offset + index,
                    "updatedAt": None,
                    "text": "hello",
                    "url": "https://youtrack.example/issue/DEMO-0"
                    f"#focus=Comments-1-{offset + index}.0-0",
                }
                for index in range(count)
            ]
        )


def settings(tmp_path):
    return Settings(
        database_path=tmp_path / "db",
        lock_path=tmp_path / "lock",
        opencode_config=tmp_path / "config",
        youtrack_mcp_name="youtrack.example.com",
        youtrack_authors=frozenset({"valeria"}),
        mention="@Valeria",
        valeria_base_url="http://valeria",
        valeria_login="login",
        valeria_password="password",
        valeria_task_id="task",
        ca_bundle=None,
        request_timeout_seconds=60,
        max_issue_pages=5,
        max_comment_pages=5,
        mcp_concurrency=2,
    )


@pytest.mark.asyncio
async def test_mcp_paginates_issues_and_comments(tmp_path):
    fake = FakeSession()
    api = YouTrackSession(fake, settings(tmp_path), "youtrack.example")

    issues = await api.recent_issues(2)
    comments = await api.recent_comments(issues[0], 1008)

    assert len(issues) == 21
    assert len(comments) == 3
    assert comments[-1].comment_id == "Comments-1-10.0-0"
    assert fake.calls[0][1]["query"].startswith("commented: {minus 2h}")
    assert [call[1]["offset"] for call in fake.calls if call[0] == "get_issue_comments"] == [
        0,
        10,
    ]


@pytest.mark.asyncio
async def test_recent_comments_seeks_to_tail_without_reading_every_page(tmp_path):
    class LongCommentSession:
        def __init__(self):
            self.offsets = []

        async def call_tool(self, name, arguments):
            assert name == "get_issue_comments"
            offset = arguments["offset"]
            self.offsets.append(offset)
            return result(
                [
                    {
                        "author": "User",
                        "createdAt": 1000 + number,
                        "updatedAt": None,
                        "text": "hello",
                        "url": "https://youtrack.example/issue/DEMO-1"
                        f"#focus=Comments-1-{number}.0-0",
                    }
                    for number in range(offset, min(offset + 10, 35))
                ]
            )

    fake = LongCommentSession()
    api = YouTrackSession(fake, settings(tmp_path), "youtrack.example")
    issue = Issue("DEMO-1", "https://youtrack.example/issue/DEMO-1")

    comments = await api.recent_comments(issue, 1032)

    assert [item.created_at_ms for item in comments] == [1032, 1033, 1034]
    assert fake.offsets == [0, 10, 20, 40, 30]
