"""Task result selection, durable completion, and owner/admin access."""

from unittest.mock import AsyncMock

import pytest
from test_plan_first import phases as phases
from test_task_database import start_task
from test_task_database import task_database as task_database
from test_tasks_api import attach_run, create_task, login
from test_tasks_api import task_app as task_app

from opencode_web.database import Database
from opencode_web.opencode_client import OpenCodeError
from opencode_web.task_results import task_final_answer


def user(mid):
    return {"info": {"id": mid, "role": "user"}}


def answer(mid, text="Итог", **info):
    return {
        "info": {"id": mid + "-answer", "parentID": mid, "role": "assistant",
                 "finish": "stop", **info},
        "parts": [{"type": "text", "text": text}],
    }


def test_final_answer_selects_build_after_compaction_and_only_visible_text():
    final = answer("continuation", "**Готово**")
    final["parts"] += [
        {"type": "reasoning", "text": "private reasoning"},
        {"type": "tool", "text": "private tool output"},
        {"type": "text", "text": "hidden", "ignored": True},
        {"type": "text", "text": "injected", "synthetic": True},
        {"type": "text", "text": "Отчёт"},
    ]
    history = [user("plan"), answer("plan", "PLAN"), user("build"),
               answer("build", "working", finish="tool-calls"),
               answer("build", "summary", summary=True), user("continuation"), final,
               answer("foreign", "unrelated")]
    assert task_final_answer(history, "build") == {
        "message_id": "continuation-answer", "text": "**Готово**\n\nОтчёт",
    }
    assert task_final_answer(history, "missing") is None


@pytest.mark.parametrize("info", [
    {"finish": "tool-calls"}, {"finish": "unknown"}, {"summary": True},
    {"error": {}}, {"finish": "error"}, {"finish": "content-filter"},
    {"finish": None},
])
def test_final_answer_does_not_fall_back_to_earlier_text(info):
    assert task_final_answer([
        user("run"), answer("run", "intermediate"), answer("run", **info),
    ], "run") is None


def test_final_answer_empty_or_unfinished_continuation():
    assert task_final_answer([user("run"), answer("run", "  ")], "run") is None
    assert task_final_answer([
        user("run"), answer("run"), user("continuation"),
    ], "run") is None
    assert task_final_answer([
        user("run"), answer("run", finish=None, time={"completed": 1}),
    ], "run")["text"] == "Итог"


def test_result_persists_across_reopen_and_repeated_completion(task_database):
    db, _, owner, task = task_database
    run = start_task(db, owner, task, "session")
    db.complete_task_run(run["id"], {"message_id": "final", "text": "Saved"})
    db.complete_task_run(run["id"], {"message_id": "other", "text": "Wrong"})
    reopened = Database(db.path, db.session_secret)
    try:
        reopened.initialize()
        assert reopened.get_task_result(run["id"])["text"] == "Saved"
        assert reopened.get_job(run["id"])["status"] == "completed"
    finally:
        reopened.close()


@pytest.mark.parametrize("status", ["cancelling", "cancelled", "failed"])
def test_late_completion_does_not_save_result(task_database, status):
    db, _, owner, task = task_database
    run = start_task(db, owner, task, "session")
    db.set_job_status(run["id"], status)
    db.complete_task_run(run["id"], {"text": "Late response"})
    assert db.get_job(run["id"])["status"] == status
    assert db.get_task_result(run["id"]) is None


def test_result_save_failure_rolls_back_completion_and_pipeline(phases, monkeypatch):
    db, _, _, run = phases
    db.set_job_status(run["id"], "running")

    def fail(*args):
        raise RuntimeError("disk failure")

    monkeypatch.setattr(db, "save_task_result", fail)
    with pytest.raises(RuntimeError, match="disk failure"):
        db.complete_task_run(run["id"], {"text": "Done"})
    assert db.get_job(run["id"])["status"] == "running"
    assert db.pipeline_children(run["id"]) == []


@pytest.mark.parametrize("recover", [False, True])
async def test_dispatcher_saves_build_answer_on_execution_and_recovery(phases, recover):
    db, dispatcher, client, run = phases
    response = client.response

    def response_with_text(mid, **kwargs):
        response(mid, **kwargs)
        client.history[-1]["parts"] = [{"type": "text", "text": mid + " result"}]

    client.response = response_with_text
    job = db.claim_next_job(1)
    if recover:
        db.set_job_status(run["id"], "running")
        client.response(run["message_id"])
        mid = db.reserve_build_phase(run["id"])
        client.response(mid)
        await dispatcher.recover()
    else:
        await dispatcher._execute(job)
    current = db.get_job(run["id"])
    assert current["status"] == "completed"
    assert db.get_task_result(run["id"])["text"] == current["phase2_message_id"] + " result"


@pytest.mark.parametrize("username", ["worker", "tasker"])
async def test_api_saved_result_access_and_no_opencode_wakeup(task_app, username):
    t = task_app
    task = await create_task(t.client)
    await login(t.client, username)
    run = (await t.client.post(f"/api/tasks/{task['id']}/runs", json={})).json()
    t.db.complete_task_run(run["id"], {"text": "## Result", "message_id": "final"})
    t.manager.ensure = AsyncMock(side_effect=AssertionError("Must not wake OpenCode"))
    path = f"/api/task-runs/{run['id']}/result"
    response = await t.client.get(path)
    assert response.status_code == 200
    assert response.json() == {"run_id": run["id"], "status": "available", "text": "## Result"}
    assert "## Result" not in (await t.client.get("/api/task-runs")).text
    await login(t.client, "stranger")
    assert (await t.client.get(path)).status_code == 404
    await login(t.client)
    assert (await t.client.get(path)).json() == response.json()
    t.client.cookies.clear()
    assert (await t.client.get(path)).status_code == 401
    t.manager.ensure.assert_not_called()


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled", "completed"])
async def test_api_noncompleted_and_empty_results(task_app, status):
    t = task_app
    task = await create_task(t.client)
    await login(t.client, "tasker")
    run = (await t.client.post(f"/api/tasks/{task['id']}/runs", json={})).json()
    if status == "completed":
        t.db.complete_task_run(run["id"], None)
    else:
        t.db.set_job_status(run["id"], status)
    t.manager.ensure = AsyncMock(side_effect=AssertionError("Unexpected wakeup"))
    response = await t.client.get(f"/api/task-runs/{run['id']}/result")
    assert response.status_code == 200
    assert response.json() == {
        "run_id": run["id"], "text": None,
        "status": "unavailable" if status == "completed" else "not_completed",
    }
    t.manager.ensure.assert_not_called()


async def test_api_legacy_result_retries_then_caches_build_answer(task_app):
    t = task_app
    task = await create_task(t.client, plan_first=True)
    await login(t.client, "worker")
    run = (await t.client.post(f"/api/tasks/{task['id']}/runs", json={})).json()
    job = await attach_run(t, run)
    t.db.set_job_status(run["id"], "running")
    mid = t.db.reserve_build_phase(run["id"])
    t.db.set_job_status(run["id"], "completed")
    path = f"/api/task-runs/{run['id']}/result"
    history = [user(job["message_id"]), answer(job["message_id"], "PLAN"),
               user(mid), answer(mid, "BUILD RESULT")]
    t.manager.client.messages = AsyncMock(side_effect=[OpenCodeError("secret"), history])
    response = await t.client.get(path)
    assert response.status_code == 503
    assert "secret" not in response.text
    assert t.db.get_task_result(run["id"]) is None
    for _ in range(2):
        response = await t.client.get(path)
        assert response.status_code == 200
        assert response.json()["text"] == "BUILD RESULT"
    assert t.manager.client.messages.await_count == 2


async def test_api_legacy_missing_history_is_unavailable(task_app):
    t = task_app
    task = await create_task(t.client)
    await login(t.client, "worker")
    run = (await t.client.post(f"/api/tasks/{task['id']}/runs", json={})).json()
    await attach_run(t, run)
    t.db.set_job_status(run["id"], "completed")
    t.manager.client.messages = AsyncMock(return_value=[])
    response = await t.client.get(f"/api/task-runs/{run['id']}/result")
    assert response.json()["status"] == "unavailable"
    assert response.json()["text"] is None
