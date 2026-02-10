from unittest.mock import AsyncMock

import pytest
from test_admin_stop import make_dispatcher, settle
from test_task_database import start_task
from test_tasks_api import create_task, login
from test_tasks_api import task_app as task_app

from opencode_web.app import event_for_user, public_task_run


@pytest.mark.parametrize("username", ["worker", "tasker"])
@pytest.mark.parametrize(
    "status", ["queued", "dispatching", "running", "waiting_permission", "waiting_question"]
)
async def test_owner_can_stop_task_run(task_app, username, status):
    t = task_app
    task = await create_task(t.client)
    user = t.users[username]
    job = start_task(t.db, user, task, "target-session")
    if status != "queued":
        t.db.claim_next_job(1)
        t.db.set_job_status(job["id"], status)
    dispatcher, client = make_dispatcher(t.db)
    t.app.state.dispatcher = dispatcher
    await login(t.client, username)
    endpoint = f"/api/task-runs/{job['id']}/stop"
    response = await t.client.post(endpoint)
    assert response.status_code == 202
    assert response.json()["status"] == ("cancelled" if status == "queued" else "cancelling")
    assert set(response.json()) == set(public_task_run(job))
    assert "SECRET ADMIN INSTRUCTIONS" not in response.text
    await settle(dispatcher)
    stopped = t.db.get_job(job["id"])
    assert stopped["status"] == "cancelled"
    assert stopped["finished_at"]
    assert t.db.get_chat(job["chat_id"])["status"] == "idle"
    assert t.db.active_jobs() == []
    if status == "queued":
        client.abort.assert_not_awaited()
    else:
        client.abort.assert_awaited_once_with("target-session")
    assert (await t.client.post(endpoint)).json() == public_task_run(stopped)
    assert client.abort.await_count == (0 if status == "queued" else 1)
    requests = [entry for entry in t.db.list_audit() if entry["action"] == "job.stop.request"]
    assert requests
    assert all(entry["actor_user_id"] == user["id"] for entry in requests)
    assert all(entry["effective_user_id"] == user["id"] for entry in requests)
    updates = [
        event_for_user(t.db, user, call.args[0])
        for call in dispatcher.events.publish.await_args_list
    ]
    assert {"type": "task.updated", "task": public_task_run(stopped)} in updates


async def test_task_stop_requires_owner_authentication_and_csrf(task_app):
    t = task_app
    task = await create_task(t.client)
    job = start_task(t.db, t.users["worker"], task, "target-session")
    endpoint = f"/api/task-runs/{job['id']}/stop"
    stop = AsyncMock()
    t.app.state.dispatcher.stop_job = stop
    t.client.cookies.clear()
    assert (await t.client.post(endpoint)).status_code == 401
    for username in ("stranger", "tasker", "admin"):
        await login(t.client, username)
        assert (await t.client.post(endpoint)).status_code == 404
    await login(t.client, "worker")
    assert (await t.client.post(endpoint, headers={"x-csrf-token": "wrong"})).status_code == 403
    assert (await t.client.post("/api/task-runs/missing/stop")).status_code == 404
    user = t.users["worker"]
    chat = t.db.create_chat(user["id"], "chat-session", "Chat", user["workspace_path"])
    chat_job = t.db.create_job(
        chat=chat, effective_user_id=user["id"], actor_user_id=user["id"],
        content="Chat message", agent="build", provider_id=None,
        model_id=None, model_variant=None,
    )
    assert (await t.client.post(f"/api/task-runs/{chat_job['id']}/stop")).status_code == 404
    stop.assert_not_awaited()
    assert t.db.get_job(job["id"])["status"] == "queued"
    assert not [entry for entry in t.db.list_audit() if entry["action"] == "job.stop.request"]


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_owner_stop_preserves_terminal_task(task_app, status):
    t = task_app
    task = await create_task(t.client)
    job = start_task(t.db, t.users["worker"], task, "target-session")
    t.db.set_job_status(job["id"], status)
    before = t.db.get_task_run(job["id"])
    dispatcher, client = make_dispatcher(t.db)
    t.app.state.dispatcher = dispatcher
    await login(t.client, "worker")
    response = await t.client.post(f"/api/task-runs/{job['id']}/stop")
    assert response.status_code == 202
    assert response.json() == public_task_run(before)
    assert t.db.get_task_run(job["id"]) == before
    client.abort.assert_not_awaited()
