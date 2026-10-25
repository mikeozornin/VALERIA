from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from test_admin_stop import make_dispatcher, settle
from test_consultations import ConsultationClient
from test_tasks_api import create_task, login

from opencode_web.app import create_app
from opencode_web.database import Database


class UiManager:
    def __init__(self):
        self.client = ConsultationClient()

    @asynccontextmanager
    async def use(self, workspace, *, wake=True, activity=True):
        yield self.client

    async def ensure(self, workspace):
        return self.client

    def instances(self):
        return []

    async def close(self):
        pass


@pytest.fixture
async def ui_app(settings, workspace):
    app = create_app(settings, start_dispatcher=False)
    async with app.router.lifespan_context(app):
        manager = UiManager()
        app.state.manager = manager
        db = app.state.database
        users = {}
        for name, only in (("worker", False), ("tasker", True), ("stranger", False)):
            path = workspace / name
            path.mkdir()
            users[name] = db.create_user(
                name, "a-strong-password", "user", str(path), tasks_only=only
            )
        users["admin"] = db.get_user_by_login("admin")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            t = SimpleNamespace(app=app, client=client, db=db, users=users, manager=manager)
            await login(client)
            response = await client.put(
                "/api/admin/settings/consultations", json={"model": "test/coder"}
            )
            assert response.status_code == 200, response.text
            t.task = await create_task(client)
            yield t


async def make_question(t, name, message="Как настроить CI?"):
    await login(t.client, name)
    response = await t.client.post("/api/consultations", json={"message": message})
    assert response.status_code == 201, response.text
    chat_id = response.json()["id"]
    return {"chat_id": chat_id, "id": chat_id, "kind": "question"}


async def make_folder(t, name, message="Запусти тесты"):
    await login(t.client, name)
    response = await t.client.post("/api/chats", json={"message": message})
    assert response.status_code == 201, response.text
    chat_id = response.json()["chat"]["id"]
    return {"chat_id": chat_id, "id": chat_id, "kind": "folder"}


async def make_run(t, name):
    await login(t.client, name)
    response = await t.client.post(f"/api/tasks/{t.task['id']}/runs", json={})
    assert response.status_code == 202, response.text
    run = response.json()
    return {"chat_id": t.db.get_job(run["id"])["chat_id"], "id": run["id"], "kind": "task"}


MAKERS = {"question": make_question, "folder": make_folder, "task": make_run}
KIND_CASES = [
    ("worker", "question"), ("worker", "folder"), ("worker", "task"),
    ("tasker", "question"), ("tasker", "task"),
]


def future(hours=1):
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def audit_actions(db, chat_id):
    return [
        entry["action"] for entry in reversed(db.list_audit())
        if entry["object_type"] == "chat" and entry["object_id"] == chat_id
    ]


async def user_lists(t):
    lists = [(await t.client.get("/api/consultations")).json()]
    lists.append((await t.client.get("/api/task-runs")).json())
    return {entry["id"] for entries in lists for entry in entries}


@pytest.mark.parametrize(("username", "kind"), KIND_CASES)
async def test_archive_snooze_and_delete_for_every_chat_kind(ui_app, username, kind):
    t = ui_app
    made = await MAKERS[kind](t, username)
    chat_id = made["chat_id"]
    base = f"/api/chats/{chat_id}"

    archived = await t.client.post(f"{base}/archive")
    assert archived.status_code == 200, archived.text
    item = archived.json()
    assert set(item) == {
        "kind", "id", "chat_id", "title", "status", "phase", "task_id", "created_at",
        "updated_at", "archived_at", "snoozed_until",
    }
    assert (item["kind"], item["id"], item["chat_id"]) == (kind, made["id"], chat_id)
    assert item["status"] == "queued"
    assert item["archived_at"] and item["snoozed_until"] is None
    assert item["task_id"] == (t.task["id"] if kind == "task" else None)
    assert item["phase"] == ("build" if kind == "task" else None)
    # Archiving keeps the work going.
    assert [job["status"] for job in t.db.unfinished_chat_jobs(chat_id)] == ["queued"]
    assert (await t.client.post(f"{base}/archive")).json()["archived_at"] == item["archived_at"]
    snoozed_archived = await t.client.post(f"{base}/snooze", json={"until": future()})
    assert snoozed_archived.status_code == 409

    restored = await t.client.post(f"{base}/unarchive")
    assert restored.status_code == 200 and restored.json()["archived_at"] is None
    until = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=2)
    snoozed = await t.client.post(
        f"{base}/snooze", json={"until": until.astimezone().isoformat()}
    )
    assert snoozed.status_code == 200, snoozed.text
    assert datetime.fromisoformat(snoozed.json()["snoozed_until"]) == until
    assert snoozed.json()["snoozed_until"].endswith("+00:00")
    # Archiving a snoozed chat clears the snooze.
    archived_snoozed = (await t.client.post(f"{base}/archive")).json()
    assert archived_snoozed["archived_at"] and archived_snoozed["snoozed_until"] is None
    await t.client.post(f"{base}/unarchive")
    await t.client.post(f"{base}/snooze", json={"until": future()})
    unsnoozed = await t.client.post(f"{base}/unsnooze")
    assert unsnoozed.status_code == 200 and unsnoozed.json()["snoozed_until"] is None

    if kind != "folder":
        assert made["id"] in await user_lists(t)
    deleted = await t.client.delete(base)
    assert deleted.status_code == 204 and deleted.content == b""
    assert t.db.get_chat(chat_id)["deleted_at"]
    assert t.db.unfinished_chat_jobs(chat_id) == []
    for action in ("archive", "unarchive", "unsnooze"):
        assert (await t.client.post(f"{base}/{action}")).status_code == 404
    assert (await t.client.delete(base)).status_code == 404
    assert made["id"] not in await user_lists(t)
    detail_path = {
        "question": f"/api/consultations/{chat_id}",
        "task": f"/api/task-runs/{made['id']}",
        "folder": f"/api/chats/{chat_id}",
    }[kind]
    assert (await t.client.get(detail_path)).status_code == 404
    if kind == "folder":
        assert (await t.client.get("/api/chats")).json() == []
        assert (await t.client.post(f"{base}/messages", json={"message": "x"})).status_code == 404
    assert audit_actions(t.db, chat_id) == [
        "chat.archive", "chat.unarchive", "chat.snooze", "chat.archive", "chat.unarchive",
        "chat.snooze", "chat.unsnooze", "chat.delete",
    ]
    entry = next(e for e in t.db.list_audit() if e["action"] == "chat.delete")
    assert entry["actor_user_id"] == entry["effective_user_id"] == t.users[username]["id"]

    # Administrators still see soft-deleted chats in their views.
    await login(t.client)
    owner_id = t.users[username]["id"]
    if kind == "question":
        listed = (await t.client.get("/api/admin/consultations")).json()
        assert chat_id in {entry["id"] for entry in listed}
        assert (await t.client.get(f"/api/admin/consultations/{chat_id}")).status_code == 200
    elif kind == "task":
        listed = (await t.client.get("/api/admin/task-runs")).json()
        assert made["id"] in {entry["id"] for entry in listed}
        listed = (await t.client.get(f"/api/admin/users/{owner_id}/task-runs")).json()
        assert made["id"] in {entry["id"] for entry in listed}
    else:
        listed = (await t.client.get(f"/api/admin/users/{owner_id}/chats")).json()
        assert chat_id in {entry["id"] for entry in listed}


async def test_chat_actions_are_owner_only_and_need_csrf(ui_app):
    t = ui_app
    made = [
        await make_question(t, "worker"), await make_folder(t, "worker"),
        await make_run(t, "worker"),
    ]
    for name in ("stranger", "admin"):
        await login(t.client, name)
        for entry in made:
            base = f"/api/chats/{entry['chat_id']}"
            for action in ("archive", "unarchive", "unsnooze"):
                assert (await t.client.post(f"{base}/{action}")).status_code == 404
            snooze = await t.client.post(f"{base}/snooze", json={"until": future()})
            assert snooze.status_code == 404
            assert (await t.client.delete(base)).status_code == 404
    await login(t.client, "worker")
    base = f"/api/chats/{made[0]['chat_id']}"
    csrf = t.client.headers.pop("x-csrf-token")
    assert (await t.client.post(f"{base}/archive")).status_code == 403
    assert (await t.client.delete(base)).status_code == 403
    t.client.headers["x-csrf-token"] = csrf
    t.client.cookies.clear()
    assert (await t.client.post(f"{base}/archive")).status_code == 401
    assert all(t.db.get_chat(entry["chat_id"])["archived_at"] is None for entry in made)
    assert all(t.db.get_chat(entry["chat_id"])["deleted_at"] is None for entry in made)


async def test_tasks_only_reaches_only_chat_actions(ui_app):
    t = ui_app
    tasker = t.users["tasker"]
    legacy = t.db.create_chat(tasker["id"], None, "Legacy", tasker["workspace_path"])
    question = await make_question(t, "tasker")
    base = f"/api/chats/{legacy['id']}"
    for action in ("archive", "unarchive", "unsnooze"):
        assert (await t.client.post(f"{base}/{action}")).status_code == 404
    assert (await t.client.post(f"{base}/snooze", json={"until": future()})).status_code == 404
    assert (await t.client.delete(base)).status_code == 404
    for method, path in (
        ("GET", base), ("GET", f"{base}/messages"), ("POST", f"{base}/abort"),
        ("GET", f"/api/chats/{question['chat_id']}"), ("GET", "/api/chats"),
        ("POST", "/api/chats/title-sync"), ("GET", "/api/queue/me"),
    ):
        assert (await t.client.request(method, path)).status_code == 403, (method, path)
    archived = await t.client.post(f"/api/chats/{question['chat_id']}/archive")
    assert archived.status_code == 200


@pytest.mark.parametrize("until", [
    "2099-01-01T10:00:00",
    "not a date",
    None,
    (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
])
async def test_snooze_requires_future_time_with_offset(ui_app, until):
    t = ui_app
    made = await make_question(t, "worker")
    response = await t.client.post(f"/api/chats/{made['chat_id']}/snooze", json={"until": until})
    assert response.status_code == 422
    extra = await t.client.post(
        f"/api/chats/{made['chat_id']}/snooze", json={"until": future(), "note": "x"}
    )
    assert extra.status_code == 422
    assert t.db.get_chat(made["chat_id"])["snoozed_until"] is None
    assert "chat.snooze" not in audit_actions(t.db, made["chat_id"])


@pytest.mark.parametrize(("kind", "status"), [
    ("question", "waiting_question"), ("folder", "waiting_permission"),
])
async def test_waiting_for_the_user_clears_snooze(ui_app, kind, status):
    t = ui_app
    made = await MAKERS[kind](t, "worker")
    chat_id = made["chat_id"]
    assert (await t.client.post(f"/api/chats/{chat_id}/snooze", json={"until": future()})).json()[
        "snoozed_until"
    ]
    job = t.db.unfinished_chat_jobs(chat_id)[0]
    assert t.db.claim_next_job(5)["id"] == job["id"]
    assert t.db.set_job_status(job["id"], "running") is False
    assert t.db.get_chat(chat_id)["snoozed_until"]
    assert t.db.set_job_status(job["id"], status) is True
    assert t.db.get_chat(chat_id)["snoozed_until"] is None
    assert t.db.set_job_status(job["id"], status) is False


@pytest.mark.parametrize("kind", ["question", "folder", "task"])
@pytest.mark.parametrize("status", ["queued", "running", "waiting_question"])
async def test_deleting_unfinished_chat_stops_its_job_first(ui_app, kind, status):
    t = ui_app
    made = await MAKERS[kind](t, "worker")
    job = t.db.unfinished_chat_jobs(made["chat_id"])[0]
    if status != "queued":
        assert t.db.claim_next_job(5)["id"] == job["id"]
        with t.db.transaction() as connection:
            connection.execute(
                "UPDATE queue_jobs SET session_pending = 0, session_id = ? WHERE id = ?",
                (f"session-{job['id']}", job["id"]),
            )
        t.db.set_job_status(job["id"], status)
    dispatcher, client = make_dispatcher(t.db)
    t.app.state.dispatcher = dispatcher
    response = await t.client.delete(f"/api/chats/{made['chat_id']}")
    assert response.status_code == 204
    await settle(dispatcher)
    stopped = t.db.get_job(job["id"])
    assert stopped["status"] == "cancelled"
    assert t.db.active_jobs() == []
    if status == "queued":
        client.abort.assert_not_awaited()
    else:
        client.abort.assert_awaited_once_with(f"session-{job['id']}")
    requests = [e for e in t.db.list_audit() if e["action"] == "job.stop.request"]
    assert [e["object_id"] for e in requests] == [job["id"]]
    assert '"reason": "chat.delete"' in requests[0]["metadata_json"]
    deleted = next(e for e in t.db.list_audit() if e["action"] == "chat.delete")
    assert job["id"] in deleted["metadata_json"]
    # The admin queue keeps the cancelled job of the deleted chat.
    await login(t.client)
    assert (await t.client.get(f"/api/jobs/{job['id']}")).status_code == 200


async def test_workspace_change_archive_sets_archived_at_and_cannot_be_undone(
    ui_app, workspace
):
    t = ui_app
    run = await make_run(t, "worker")
    assert (await t.client.post(f"/api/chats/{run['chat_id']}/snooze", json={
        "until": future(),
    })).status_code == 200
    moved = workspace / "moved"
    moved.mkdir()
    t.db.archive_chats_outside_workspace(t.users["worker"]["id"], str(moved))
    chat = t.db.get_chat(run["chat_id"])
    assert chat["archived_reason"] == "workspace_changed"
    assert chat["archived_at"] and chat["snoozed_until"] is None
    response = await t.client.post(f"/api/chats/{run['chat_id']}/unarchive")
    assert response.status_code == 409
    assert t.db.get_chat(run["chat_id"])["archived_at"] == chat["archived_at"]


def test_existing_database_gets_chat_state_columns(tmp_path):
    path = tmp_path / "legacy.db"
    db = Database(path, "s" * 32)
    db.initialize()
    user = db.create_user("legacy", "a-strong-password", "user", str(tmp_path))
    chat = db.create_chat(user["id"], None, "Old", str(tmp_path))
    with db.transaction() as connection:
        connection.execute(
            "UPDATE chats SET archived_reason = 'workspace_changed', updated_at = ? WHERE id = ?",
            ("2025-01-02T03:04:05+00:00", chat["id"]),
        )
        connection.execute("DROP INDEX idx_queue_chat")
        for column in ("archived_at", "snoozed_until", "deleted_at"):
            connection.execute(f"ALTER TABLE chats DROP COLUMN {column}")
    db.close()
    db = Database(path, "s" * 32)
    db.initialize()
    migrated = db.get_chat(chat["id"])
    assert migrated["archived_at"] == "2025-01-02T03:04:05+00:00"
    assert migrated["snoozed_until"] is None and migrated["deleted_at"] is None
    indexes = {row["name"] for row in db._all("PRAGMA index_list(queue_jobs)")}
    assert "idx_queue_chat" in indexes
    db.close()
