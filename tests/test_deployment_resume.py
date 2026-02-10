"""Explicit offline continuation keeps identity, phase and original user input."""

import pytest
from test_consultations import dispatcher_case as dispatcher_case
from test_plan_first import phases as phases
from test_task_database import task_database as task_database


@pytest.mark.parametrize("phase", ["plan", "build"])
async def test_resume_task_once_in_original_phase(phases, phase):
    db, dispatcher, client, job = phases
    db.claim_next_job(1)
    db.set_job_status(job["id"], "running")
    if phase == "build":
        db.reserve_build_phase(job["id"])
    before = db.get_job(job["id"])
    old_id = before["phase2_message_id"] or before["message_id"]
    assert not db.resume_interrupted_job(job["id"], "wrong-id", "Continue safely")
    assert db.resume_interrupted_job(job["id"], old_id, "Continue safely")
    assert not db.resume_interrupted_job(job["id"], old_id, "Duplicate")
    queued = db.get_job(job["id"])
    for key in ("content", "started_at", "session_id", "pipeline_json", "plan_first"):
        assert queued[key] == before[key]
    assert queued["resume_from_message_id"] == old_id
    await dispatcher._execute(db.claim_next_job(1))
    after = db.get_job(job["id"])
    assert after["status"] == "completed"
    assert after["started_at"] == before["started_at"]
    assert client.prompts[0]["content"] == "Continue safely"
    assert client.prompts[0]["agent"] == phase
    assert client.prompts[0]["message_id"] != old_id
    assert client.prompts[0]["session_id"] == job["session_id"]
    assert len(client.prompts) == (2 if phase == "plan" else 1)
    assert len(db.pipeline_children(job["id"])) == 1


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "cancelling"])
def test_resume_rejects_terminal_or_cancelled_runs(phases, status):
    db, _, _, job = phases
    db.set_job_status(job["id"], status)
    assert not db.resume_interrupted_job(job["id"], job["message_id"], "Continue")
    assert db.get_job(job["id"])["status"] == status


async def test_resume_consultation_keeps_question_and_saves_new_answer(dispatcher_case):
    db, dispatcher, client, job = dispatcher_case
    db.set_job_status(job["id"], "running")
    assert db.resume_interrupted_job(job["id"], job["message_id"], "Continue safely")
    current = db.claim_next_job(1)
    for message in client.message_list:
        info = message.get("info", {})
        if info.get("id") == job["message_id"]:
            info["id"] = current["message_id"]
        if info.get("parentID") == job["message_id"]:
            info["parentID"] = current["message_id"]
    await dispatcher._execute(current)
    assert db.get_job(job["id"])["status"] == "completed"
    assert db.get_job(job["id"])["content"] == "Question"
    assert db.consultation_jobs(job["chat_id"])[0]["answer"] == "Final answer"
    assert client.prompts[0]["content"] == "Continue safely"
    assert client.prompts[0]["agent"] == "build"
