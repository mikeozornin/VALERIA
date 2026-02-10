from dataclasses import replace
from pathlib import Path

import pytest

from youtrack_valeria_watcher.config import Settings
from youtrack_valeria_watcher.errors import ExternalServiceError
from youtrack_valeria_watcher.models import Comment, Issue, TaskRun, hash_text
from youtrack_valeria_watcher.service import Watcher
from youtrack_valeria_watcher.store import Store


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "state.db",
        lock_path=tmp_path / "lock",
        opencode_config=tmp_path / "opencode.jsonc",
        youtrack_mcp_name="youtrack.example.com",
        youtrack_authors=frozenset({"valeria bot"}),
        mention="@Valeria",
        valeria_base_url="http://valeria",
        valeria_login="watcher",
        valeria_password="secret",
        valeria_task_id="task-1",
        ca_bundle=None,
        request_timeout_seconds=60,
        max_issue_pages=10,
        max_comment_pages=10,
        mcp_concurrency=2,
    )


def comment(number: int, text: str, *, author: str = "User", issue: str = "DEMO-1"):
    return Comment(
        comment_id=f"Comments-1-{number}.0-0",
        issue_id=issue,
        issue_url=f"https://youtrack.example/issue/{issue}",
        author=author,
        created_at_ms=2_000 + number,
        updated_at_ms=None,
        text=text,
        text_hash=hash_text(text),
    )


class FakeYouTrack:
    def __init__(self, comments):
        self.items = comments
        self.lookbacks = []

    async def recent_issues(self, lookback_hours):
        self.lookbacks.append(lookback_hours)
        issue_ids = dict.fromkeys(item.issue_id for item in self.items)
        return [
            Issue(issue_id=value, url=f"https://youtrack.example/issue/{value}")
            for value in issue_ids
        ]

    async def recent_comments(self, issue, cutoff_ms):
        return [
            item
            for item in self.items
            if item.issue_id == issue.issue_id and item.created_at_ms >= cutoff_ms
        ]


class FakeValeria:
    def __init__(self):
        self.submissions = []
        self.statuses = {}
        self.failures = 0

    async def submit(self, issue_url, comment_id):
        self.submissions.append((issue_url, comment_id))
        if self.failures:
            self.failures -= 1
            raise ExternalServiceError("temporary submission failure")
        run_id = f"run-{len(self.submissions)}"
        self.statuses[run_id] = "queued"
        return TaskRun(run_id, "queued")

    async def get_run(self, run_id):
        return TaskRun(run_id, self.statuses[run_id])


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "state.db")
    settings = make_settings(tmp_path)
    yield settings, store
    store.close()


@pytest.mark.asyncio
async def test_first_run_only_sets_baseline(setup):
    settings, store = setup
    watcher = Watcher(settings, store, None, None)

    summary = watcher.bootstrap(1_000)

    assert summary.bootstrapped
    assert store.get_meta_int("initialized_at_ms") == 1_000
    assert watcher.bootstrap(2_000) is None


@pytest.mark.asyncio
async def test_multiple_mentions_in_same_issue_create_one_run(setup):
    settings, store = setup
    yt = FakeYouTrack([comment(1, "@Valeria first"), comment(2, "@Valeria second")])
    valeria = FakeValeria()
    watcher = Watcher(settings, store, yt, valeria)
    watcher.bootstrap(1_000)

    summary = await watcher.run_cycle(10_000)

    assert len(valeria.submissions) == 1
    assert summary.enqueued == 1
    assert summary.coalesced == 1
    assert store.get_comment("Comments-1-1.0-0")["status"] == "enqueued"
    second = store.get_comment("Comments-1-2.0-0")
    assert second["status"] == "handled_by_active_run"
    assert second["valeria_run_id"] == "run-1"


@pytest.mark.asyncio
async def test_coalesced_comment_stays_handled_after_run_fails(setup):
    settings, store = setup
    first = comment(1, "@Valeria first")
    second = comment(2, "@Valeria while running")
    yt = FakeYouTrack([first, second])
    valeria = FakeValeria()
    watcher = Watcher(settings, store, yt, valeria)
    watcher.bootstrap(1_000)
    await watcher.run_cycle(10_000)

    valeria.statuses["run-1"] = "failed"
    yt.items.append(comment(3, "ordinary comment"))
    await watcher.run_cycle(20_000)

    assert len(valeria.submissions) == 1
    assert store.get_comment(second.comment_id)["status"] == "handled_by_active_run"


@pytest.mark.asyncio
async def test_new_mention_after_terminal_run_creates_new_run(setup):
    settings, store = setup
    first = comment(1, "@Valeria first")
    yt = FakeYouTrack([first])
    valeria = FakeValeria()
    watcher = Watcher(settings, store, yt, valeria)
    watcher.bootstrap(1_000)
    await watcher.run_cycle(10_000)

    valeria.statuses["run-1"] = "completed"
    yt.items.append(comment(2, "@Valeria after completion"))
    summary = await watcher.run_cycle(20_000)

    assert len(valeria.submissions) == 2
    assert summary.enqueued == 1


@pytest.mark.asyncio
async def test_submission_failure_is_retried_without_rescanning_dependency(setup):
    settings, store = setup
    item = comment(1, "@Valeria retry")
    yt = FakeYouTrack([item])
    valeria = FakeValeria()
    valeria.failures = 1
    watcher = Watcher(settings, store, yt, valeria)
    watcher.bootstrap(1_000)

    first = await watcher.run_cycle(10_000)
    second = await watcher.run_cycle(20_000)

    assert first.retries == 1
    assert second.enqueued == 1
    assert [value[1] for value in valeria.submissions] == [item.comment_id, item.comment_id]
    assert store.get_comment(item.comment_id)["status"] == "enqueued"


@pytest.mark.asyncio
async def test_own_comment_is_ignored(setup):
    settings, store = setup
    item = comment(1, "@Valeria quoted", author="Valeria Bot")
    yt = FakeYouTrack([item])
    valeria = FakeValeria()
    watcher = Watcher(settings, store, yt, valeria)
    watcher.bootstrap(1_000)

    await watcher.run_cycle(10_000)

    assert not valeria.submissions
    assert store.get_comment(item.comment_id)["status"] == "ignored_own"


@pytest.mark.asyncio
async def test_editing_ignored_comment_to_add_mention_enqueues(setup):
    settings, store = setup
    original = comment(1, "ordinary")
    yt = FakeYouTrack([original])
    valeria = FakeValeria()
    watcher = Watcher(settings, store, yt, valeria)
    watcher.bootstrap(1_000)
    await watcher.run_cycle(10_000)

    edited = replace(
        original,
        text="please @Valeria",
        text_hash=hash_text("please @Valeria"),
        updated_at_ms=15_000,
    )
    yt.items = [edited]
    await watcher.run_cycle(20_000)

    assert len(valeria.submissions) == 1
    assert store.get_comment(original.comment_id)["status"] == "enqueued"


@pytest.mark.asyncio
async def test_only_comments_from_last_hour_are_considered(setup):
    settings, store = setup
    yt = FakeYouTrack([comment(1, "@Valeria old")])
    valeria = FakeValeria()
    watcher = Watcher(settings, store, yt, valeria)
    watcher.bootstrap(1_000)

    now_ms = 4 * 3_600_000
    await watcher.run_cycle(now_ms)

    assert not valeria.submissions
    assert yt.lookbacks == [1]
    assert store.get_meta_int("last_success_at_ms") == now_ms
