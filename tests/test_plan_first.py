"""Durable task phase transitions, cancellation, and restart recovery."""

import asyncio
from types import SimpleNamespace

import pytest
from test_task_database import start_task
from test_task_database import task_database as task_database
from test_tasks_api import create_task, login
from test_tasks_api import task_app as task_app

from opencode_web.dispatcher import CONTINUE_PROMPT, QueueDispatcher
from opencode_web.events import EventBroker
from opencode_web.opencode_client import OpenCodeError


class PhaseClient:
    def __init__(self):
        self.prompts = []
        self.history = []
        self.aborted = []
        self.build_started = asyncio.Event()
        self.finish_build = True
        self.plan_error = False
        self.build_error = False
        self.lost_response = False
        self.reject_build = False
        self.on_plan_read = None

    def response(self, message_id, *, error=False, complete=True):
        self.history.append({"info": {"id": message_id, "role": "user"}})
        if complete:
            info = {
                "id": message_id + "-answer",
                "role": "assistant",
                "parentID": message_id,
                "finish": "stop",
            }
            if error:
                info["error"] = {"name": "ProviderError"}
            self.history.append({"info": info})

    async def send_prompt_async(self, session_id, **payload):
        self.prompts.append({"session_id": session_id, **payload})
        build = payload["agent"] == "build"
        if build and self.reject_build:
            raise OpenCodeError("request outcome unknown")
        self.response(payload["message_id"], error=self.build_error if build else self.plan_error)
        if build:
            self.build_started.set()
            if self.lost_response:
                raise OpenCodeError("accepted, response lost")

    async def session_statuses(self):
        return (
            {"session": {"type": "busy"}}
            if self.build_started.is_set() and not self.finish_build
            else {}
        )

    async def messages(self, session_id):
        if self.on_plan_read and not self.build_started.is_set():
            self.on_plan_read()
        return self.history

    async def questions(self):
        return []

    async def permissions(self):
        return []

    async def abort(self, session_id):
        self.aborted.append(session_id)
        self.finish_build = True
        return True


@pytest.fixture
def phases(task_database, monkeypatch):
    db, admin, user, task = task_database
    child = db.create_task(
        admin["id"], "After build", "", "Next prompt", "build", "p", "m", None, False
    )
    task = db.update_task(task["id"], {"plan_first": True, "next_task_ids": [child["id"]]})
    job = start_task(db, user, task, "session")
    client = PhaseClient()
    original_sleep = asyncio.sleep

    async def fast_poll(delay):
        await original_sleep(0)

    async def ensure(workspace):
        return client

    monkeypatch.setattr("opencode_web.dispatcher.asyncio.sleep", fast_poll)
    dispatcher = QueueDispatcher(db, SimpleNamespace(ensure=ensure), EventBroker(), 1)
    return db, dispatcher, client, job


async def test_two_phases_keep_slot_and_delay_pipeline_until_build_finishes(phases):
    db, dispatcher, client, job = phases
    client.finish_build = False
    worker = asyncio.create_task(dispatcher._execute(db.claim_next_job(1)))
    await asyncio.wait_for(client.build_started.wait(), 2)
    current = db.get_job(job["id"])
    assert current["status"] == "running"
    assert current["finished_at"] is None
    assert db.claim_next_job(1) is None
    assert db.pipeline_children(job["id"]) == []
    assert [p["agent"] for p in client.prompts] == ["plan", "build"]
    assert len({p["session_id"] for p in client.prompts}) == 1
    assert client.prompts[1]["message_id"] == current["phase2_message_id"]
    assert client.prompts[1]["content"] == CONTINUE_PROMPT
    for key in ("provider_id", "model_id", "model_variant"):
        assert client.prompts[0][key] == client.prompts[1][key] == job[key]
    client.finish_build = True
    await asyncio.wait_for(worker, 2)
    assert db.get_job(job["id"])["status"] == "completed"
    assert len(db.pipeline_children(job["id"])) == 1


@pytest.mark.parametrize("phase", ["plan", "build"])
async def test_error_in_either_phase_fails_the_run(phases, phase):
    db, dispatcher, client, job = phases
    setattr(client, phase + "_error", True)
    await dispatcher._execute(db.claim_next_job(1))
    assert db.get_job(job["id"])["status"] == "failed"
    assert db.get_job(job["id"])["error_code"] == "assistant_error"
    assert len(client.prompts) == (1 if phase == "plan" else 2)
    assert client.aborted == ["session"]


async def test_cancel_at_phase_boundary_never_submits_build(phases):
    db, dispatcher, client, job = phases
    client.on_plan_read = lambda: db.set_job_status(job["id"], "cancelling")
    await dispatcher._execute(db.claim_next_job(1))
    await dispatcher._cancel_job(job["id"])
    assert [p["agent"] for p in client.prompts] == ["plan"]
    assert db.get_job(job["id"])["status"] == "cancelled"
    assert db.get_job(job["id"])["phase2_message_id"] is None


async def test_cancel_while_build_submission_is_in_flight(phases):
    db, dispatcher, client, job = phases
    original_send = client.send_prompt_async
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_send(session_id, **payload):
        if payload["agent"] == "build":
            entered.set()
            await release.wait()
        await original_send(session_id, **payload)

    client.send_prompt_async = blocked_send
    worker = asyncio.create_task(dispatcher._execute(db.claim_next_job(1)))
    await asyncio.wait_for(entered.wait(), 2)
    await dispatcher.stop_job(job["id"])
    assert db.get_job(job["id"])["status"] == "cancelling"
    assert not client.aborted
    release.set()
    await asyncio.wait_for(worker, 2)
    await asyncio.gather(*tuple(dispatcher._workers))
    assert db.get_job(job["id"])["status"] == "cancelled"
    assert client.aborted == ["session"]


@pytest.mark.parametrize(
    "state", ["plan_done", "build_done", "build_interrupted", "uncertain", "build_busy"]
)
async def test_restart_never_resends_an_accepted_build(phases, state):
    db, dispatcher, client, job = phases
    db.claim_next_job(1)
    db.set_job_status(job["id"], "running")
    client.response(job["message_id"])
    if state != "plan_done":
        mid = db.reserve_build_phase(job["id"])
        if state != "uncertain":
            client.response(mid, complete=state != "build_interrupted")
        if state == "build_busy":
            client.build_started.set()
            client.finish_build = False
    await dispatcher.recover()
    client.finish_build = True
    await asyncio.gather(*tuple(dispatcher._workers))
    assert [p["agent"] for p in client.prompts] == (["build"] if state == "plan_done" else [])
    current = db.get_job(job["id"])
    assert current["status"] == (
        "failed" if state in ("uncertain", "build_interrupted") else "completed"
    )
    if state == "uncertain":
        assert current["error_code"] == "build_submission_uncertain"


@pytest.mark.parametrize("accepted", [True, False])
async def test_uncertain_http_result_is_reconciled_without_retry(phases, accepted):
    db, dispatcher, client, job = phases
    client.lost_response = accepted
    client.reject_build = not accepted
    await dispatcher._execute(db.claim_next_job(1))
    assert len(client.prompts) == 2
    assert db.get_job(job["id"])["status"] == ("completed" if accepted else "failed")


def test_snapshot_migration_and_atomic_phase_reservation(task_database):
    db, admin, user, task = task_database
    assert task["plan_first"] == 0
    task = db.update_task(task["id"], {"plan_first": True})
    run = start_task(db, user, task, "session")
    db.update_task(task["id"], {"plan_first": False})
    db.initialize()
    assert db.get_job(run["id"])["plan_first"] == 1
    assert db.reserve_build_phase(run["id"]) is None
    db.set_job_status(run["id"], "running")
    mid = db.reserve_build_phase(run["id"])
    assert mid
    assert db.reserve_build_phase(run["id"]) is None
    assert db.get_job(run["id"])["phase2_message_id"] == mid


def test_pipeline_children_use_snapshotted_flag(task_database):
    db, admin, user, root = task_database
    child = db.create_task(
        admin["id"], "Child", "", "Prompt", "build", "p", "m", None, False, plan_first=True
    )
    root = db.update_task(root["id"], {"next_task_ids": [child["id"]]})
    run = start_task(db, user, root, "session")
    db.update_task(child["id"], {"plan_first": False})
    db.set_job_status(run["id"], "completed")
    children = db.pipeline_children(run["id"])
    assert len(children) == 1
    assert children[0]["plan_first"] == 1


async def test_api_flag_validation_snapshot_and_public_events(task_app):
    t = task_app
    task = await create_task(t.client, plan_first=True)
    assert task["plan_first"] == 1
    for patch in ({"agent": "plan"}, {"plan_first": None}):
        response = await t.client.patch("/api/admin/tasks/" + task["id"], json=patch)
        assert response.status_code == 422
    response = await t.client.post(
        "/api/admin/tasks",
        json={
            "title": "Invalid",
            "content": "Prompt",
            "agent": "plan",
            "model": "provider/coder",
            "plan_first": True,
        },
    )
    assert response.status_code == 422
    await login(t.client, "worker")
    response = await t.client.patch("/api/admin/tasks/" + task["id"], json={"plan_first": False})
    assert response.status_code == 403
    run = (await t.client.post("/api/tasks/" + task["id"] + "/runs", json={})).json()
    assert run["plan_first"] is True and run["phase"] == "plan"
    t.db.set_job_status(run["id"], "running")
    t.db.reserve_build_phase(run["id"])
    detail = (await t.client.get("/api/task-runs/" + run["id"])).json()
    assert detail["phase"] == "build"
    assert "phase2_message_id" not in detail
    await login(t.client)
    response = await t.client.patch(
        "/api/admin/tasks/" + task["id"], json={"plan_first": False, "agent": "plan"}
    )
    assert response.status_code == 200
    assert t.db.get_job(run["id"])["plan_first"] == 1


def test_existing_database_migrates_without_changing_old_runs(task_database):
    db, admin, user, task = task_database
    run = start_task(db, user, task, "legacy-session")
    with db.transaction() as connection:
        connection.execute("ALTER TABLE tasks DROP COLUMN plan_first")
        connection.execute("ALTER TABLE queue_jobs DROP COLUMN plan_first")
        connection.execute("ALTER TABLE queue_jobs DROP COLUMN phase2_message_id")
    db.initialize()
    db.initialize()
    assert db.get_task(task["id"])["plan_first"] == 0
    current = db.get_job(run["id"])
    assert current["plan_first"] == 0
    assert current["phase2_message_id"] is None
    assert current["status"] == "queued"
    assert current["content"] == run["content"]
