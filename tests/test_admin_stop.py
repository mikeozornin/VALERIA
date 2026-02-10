from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_task_database import start_task
from test_task_database import task_database as task_database
from test_task_pipeline import pipeline
from test_tasks_api import create_task, login
from test_tasks_api import task_app as task_app

from opencode_web.app import event_for_user
from opencode_web.dispatcher import QueueDispatcher
from opencode_web.opencode_client import OpenCodeError


def make_dispatcher(db):
    client = SimpleNamespace(
        abort=AsyncMock(return_value=True),
        session_statuses=AsyncMock(return_value={}),
        send_prompt_async=AsyncMock(),
        create_session=AsyncMock(return_value={"id": "new-session"}),
        delete_session=AsyncMock(return_value=True),
    )
    manager = SimpleNamespace(ensure=AsyncMock(return_value=client))
    events = SimpleNamespace(publish=AsyncMock())
    return QueueDispatcher(db, manager, events, 1), client


async def settle(dispatcher):
    pending = list(dispatcher._cancellations.values())
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending), timeout=2)


@pytest.mark.parametrize("kind", ["task", "chat"])
@pytest.mark.parametrize(
    "status", ["queued", "dispatching", "running", "waiting_permission", "waiting_question"]
)
async def test_admin_stops_any_users_job(task_app, kind, status):
    t = task_app
    task = await create_task(t.client)
    user = t.users["tasker" if kind == "task" else "worker"]
    if kind == "task":
        job = start_task(t.db, user, task, "target-session")
    else:
        chat = t.db.create_chat(user["id"], "target-session", "Chat", user["workspace_path"])
        job = t.db.create_job(
            chat=chat, effective_user_id=user["id"], actor_user_id=user["id"],
            content="Iteration", agent="build", provider_id=None,
            model_id=None, model_variant=None,
        )
    if status != "queued":
        t.db.claim_next_job(1)
        t.db.set_job_status(job["id"], status)
    dispatcher, client = make_dispatcher(t.db)
    t.app.state.dispatcher = dispatcher
    admin = await login(t.client)
    response = await t.client.post(f"/api/admin/jobs/{job['id']}/stop")
    assert response.status_code == 202
    assert response.json()["status"] == ("cancelled" if status == "queued" else "cancelling")
    await settle(dispatcher)
    stopped = t.db.get_job(job["id"])
    assert stopped["status"] == "cancelled"
    assert stopped["finished_at"]
    assert t.db.get_chat(job["chat_id"])["status"] == "idle"
    assert t.db.active_jobs() == []
    if status == "queued":
        client.abort.assert_not_awaited()
        dispatcher.manager.ensure.assert_not_awaited()
    else:
        client.abort.assert_awaited_once_with("target-session")
    repeat = await t.client.post(f"/api/admin/jobs/{job['id']}/stop")
    assert repeat.json()["status"] == "cancelled"
    assert client.abort.await_count == (0 if status == "queued" else 1)
    entries = [entry for entry in t.db.list_audit() if entry["object_id"] == job["id"]]
    requests = [entry for entry in entries if entry["action"] == "job.stop.request"]
    assert all(entry["actor_user_id"] == admin["id"] for entry in requests)
    assert all(entry["effective_user_id"] == user["id"] for entry in entries)
    assert any(entry["action"] == "job.stop.completed" for entry in entries)
    updates = [call.args[0] for call in dispatcher.events.publish.await_args_list]
    assert any(event["job"]["status"] == "cancelled" for event in updates)
    if kind == "task":
        event = event_for_user(t.db, user, {"type": "job.updated", "job": stopped})
        assert event["task"]["status"] == "cancelled"


async def test_stop_requires_admin_and_csrf(task_app):
    t = task_app
    task = await create_task(t.client)
    job = start_task(t.db, t.users["worker"], task, "target")
    endpoint = f"/api/admin/jobs/{job['id']}/stop"
    for name in ("worker", "tasker", "stranger"):
        await login(t.client, name)
        assert (await t.client.post(endpoint)).status_code == 403
    await login(t.client)
    assert (await t.client.post(endpoint, headers={"x-csrf-token": "wrong"})).status_code == 403
    assert (await t.client.post("/api/admin/jobs/missing/stop")).status_code == 404
    assert t.db.get_job(job["id"])["status"] == "queued"


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_stop_terminal_job_is_noop(task_database, status):
    db, _, user, task = task_database
    job = start_task(db, user, task, "target")
    db.set_job_status(job["id"], status)
    before = db.get_job(job["id"])
    dispatcher, client = make_dispatcher(db)
    assert await dispatcher.stop_job(job["id"]) == before
    client.abort.assert_not_awaited()


async def test_stop_before_worker_starts_does_not_send_prompt(task_database):
    db, _, user, task = task_database
    job = start_task(db, user, task, "target")
    claimed = db.claim_next_job(1)
    dispatcher, client = make_dispatcher(db)
    await dispatcher.stop_job(job["id"])
    await dispatcher._execute(claimed)
    await settle(dispatcher)
    client.send_prompt_async.assert_not_awaited()
    assert db.get_job(job["id"])["status"] == "cancelled"


async def test_stop_waits_for_inflight_prompt_before_abort(task_database):
    db, admin, user, task = task_database
    task, *_ = pipeline(db, admin, task)
    job = start_task(db, user, task, "target")
    other = start_task(db, user, task, "other", user_input="Independent run")
    claimed = db.claim_next_job(1)
    dispatcher, client = make_dispatcher(db)
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(*args, **kwargs):
        entered.set()
        await release.wait()

    client.send_prompt_async.side_effect = send
    worker = asyncio.create_task(dispatcher._execute(claimed))
    await asyncio.wait_for(entered.wait(), 2)
    await dispatcher.stop_job(job["id"])
    await dispatcher.stop_job(job["id"])
    assert len(dispatcher._cancellations) == 1
    assert db.claim_next_job(1) is None
    client.abort.assert_not_awaited()
    db.set_job_status(job["id"], "completed")
    db.set_job_status(job["id"], "failed", "late_error")
    db.set_job_status(job["id"], "running")
    db.requeue_interrupted_job(job["id"])
    assert db.get_job(job["id"])["status"] == "cancelling"
    release.set()
    await asyncio.wait_for(worker, 2)
    await settle(dispatcher)
    client.abort.assert_awaited_once_with("target")
    assert db.get_job(job["id"])["status"] == "cancelled"
    assert db.pipeline_children(job["id"]) == []
    assert db.get_job(other["id"])["status"] == "queued"
    assert db.claim_next_job(1)["id"] == other["id"]


@pytest.mark.parametrize("during_create", [False, True])
async def test_cancel_pending_pipeline_session(task_database, during_create):
    db, admin, user, task = task_database
    task, *_ = pipeline(db, admin, task)
    parent = start_task(db, user, task, "parent")
    db.set_job_status(parent["id"], "completed")
    job = db.claim_next_job(1)
    assert job["session_pending"]
    dispatcher, client = make_dispatcher(db)
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(**kwargs):
        entered.set()
        await release.wait()
        return {"id": "new-session"}

    client.create_session.side_effect = create
    worker = None
    if during_create:
        worker = asyncio.create_task(dispatcher._execute(job))
        await asyncio.wait_for(entered.wait(), 2)
    await dispatcher.stop_job(job["id"])
    release.set()
    if worker:
        await asyncio.wait_for(worker, 2)
        client.delete_session.assert_awaited_once_with("new-session")
    await settle(dispatcher)
    client.send_prompt_async.assert_not_awaited()
    client.abort.assert_not_awaited()
    assert db.get_job(job["id"])["status"] == "cancelled"
    assert db.pipeline_children(job["id"]) == []


@pytest.mark.parametrize("failure", ["offline", "rejected", "busy"])
async def test_stop_retry_holds_capacity_until_confirmed(task_database, monkeypatch, failure):
    monkeypatch.setattr("opencode_web.dispatcher.STOP_RETRY_SECONDS", 0.01)
    db, _, user, task = task_database
    job = start_task(db, user, task, "target")
    db.claim_next_job(1)
    dispatcher, client = make_dispatcher(db)
    second_attempt, release = asyncio.Event(), asyncio.Event()
    attempts = 0

    async def abort(session_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if failure == "offline":
                raise OpenCodeError("unavailable")
            return failure != "rejected"
        second_attempt.set()
        await release.wait()
        return True

    client.abort.side_effect = abort
    if failure == "busy":
        client.session_statuses.side_effect = [{"target": {"type": "busy"}}, {}]
    await dispatcher.stop_job(job["id"])
    await asyncio.wait_for(second_attempt.wait(), 2)
    assert db.get_job(job["id"])["status"] == "cancelling"
    assert db.get_job(job["id"])["error_code"] == "stop_unconfirmed"
    assert len(db.active_jobs()) == 1
    assert any(
        call.args[0]["job"].get("error_code") == "stop_unconfirmed"
        for call in dispatcher.events.publish.await_args_list
    )
    release.set()
    await settle(dispatcher)
    assert db.get_job(job["id"])["status"] == "cancelled"
    assert db.get_job(job["id"])["error_code"] is None
    assert db.active_jobs() == []


async def test_restart_resumes_unconfirmed_stop(task_database):
    db, _, user, task = task_database
    job = start_task(db, user, task, "target")
    db.claim_next_job(1)
    first, client = make_dispatcher(db)
    entered = asyncio.Event()

    async def unavailable(session_id):
        entered.set()
        await asyncio.Event().wait()

    client.abort.side_effect = unavailable
    await first.stop_job(job["id"])
    await asyncio.wait_for(entered.wait(), 2)
    await first.stop()
    assert db.get_job(job["id"])["status"] == "cancelling"
    recovered, recovered_client = make_dispatcher(db)
    await recovered.recover()
    await settle(recovered)
    recovered_client.send_prompt_async.assert_not_awaited()
    recovered_client.abort.assert_awaited_once_with("target")
    assert db.get_job(job["id"])["status"] == "cancelled"


async def test_stop_waits_for_automatic_answer(task_database):
    db, _, user, task = task_database
    job = start_task(db, user, task, "target")
    db.claim_next_job(1)
    db.set_job_status(job["id"], "running")
    dispatcher, client = make_dispatcher(db)
    entered, release = asyncio.Event(), asyncio.Event()

    async def answer(*args):
        entered.set()
        await release.wait()
        return True

    client.questions = AsyncMock(return_value=[{
        "id": "question", "sessionID": "target",
        "questions": [{"options": [{"label": "Yes"}]}],
    }])
    client.respond_question = AsyncMock(side_effect=answer)
    client.permissions = AsyncMock(return_value=[])
    worker = asyncio.create_task(dispatcher._monitor(job, client))
    await asyncio.wait_for(entered.wait(), 2)
    await dispatcher.stop_job(job["id"])
    client.abort.assert_not_awaited()
    release.set()
    await asyncio.wait_for(worker, 2)
    await settle(dispatcher)
    client.abort.assert_awaited_once_with("target")
    assert db.get_job(job["id"])["status"] == "cancelled"
