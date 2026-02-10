from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from opencode_web.app import create_app


@pytest.fixture
def statistics_app(settings, workspace):
    app = create_app(settings, start_dispatcher=False)
    with TestClient(app) as client:
        db = app.state.database
        user = db.create_user("worker", "long-user-password", "user", str(workspace))
        task = db.create_task(
            user["id"], "Test", "", "Instructions", "build", None, None, None, True
        )
        response = client.post("/api/auth/login", json={
            "login": "admin", "password": "correct-horse-battery-staple",
        })
        assert response.status_code == 200
        yield client, db, user, task


def add_run(db, user, task, finished, *, status="completed", execution=60, total=180):
    run = db.create_task_job(
        task=task, user=user, actor_user_id=user["id"], session_id=str(uuid4()),
        user_input=str(uuid4()), max_queued=1000,
    )
    with db.transaction() as connection:
        connection.execute(
            """UPDATE queue_jobs SET status=?, created_at=?, started_at=?, finished_at=?
               WHERE id=?""",
            (status, (finished - timedelta(seconds=total)).isoformat(),
             (finished - timedelta(seconds=execution)).isoformat() if execution is not None
             else None, finished.isoformat(), run["id"]),
        )
    return run


def test_statistics_empty_validation_and_permissions(statistics_app):
    client, _, _, _ = statistics_app
    response = client.get("/api/admin/statistics?day=2026-10-04&timezone=UTC")
    assert response.status_code == 200
    assert response.json() == {
        "day": "2026-10-04", "timezone": "UTC", "completed_all_time": 0,
        "completed_day": 0, "average_execution_seconds": None, "average_total_seconds": None,
        "execution_samples": 0, "total_samples": 0,
    }
    for query in ("day=invalid", "timezone=Invalid/Timezone", "day=9999-12-31"):
        assert client.get(f"/api/admin/statistics?{query}").status_code == 422
    client.post("/api/auth/login", json={"login": "worker", "password": "long-user-password"})
    assert client.get("/api/admin/statistics").status_code == 403
    client.cookies.clear()
    assert client.get("/api/admin/statistics").status_code == 401


def test_statistics_dates_statuses_and_missing_times(statistics_app):
    client, db, user, task = statistics_app
    start = datetime(2026, 10, 4, 0, tzinfo=UTC)
    add_run(db, user, task, start - timedelta(seconds=1))
    add_run(db, user, task, start, execution=60, total=180)
    add_run(db, user, task, start + timedelta(hours=23), execution=120, total=300)
    add_run(db, user, task, start + timedelta(hours=24))
    for status in ("failed", "cancelled", "running", "queued"):
        add_run(db, user, task, start + timedelta(hours=1), status=status)
    ordinary = add_run(db, user, task, start + timedelta(hours=2))
    with db.transaction() as connection:
        connection.execute("UPDATE queue_jobs SET task_id=NULL WHERE id=?", (ordinary["id"],))
    add_run(db, user, task, start + timedelta(hours=3), execution=None, total=240)
    add_run(db, user, task, start + timedelta(hours=4), execution=-10, total=-10)
    data = client.get(
        "/api/admin/statistics?day=2026-10-04&timezone=UTC"
    ).json()
    assert data["completed_all_time"] == 6
    assert data["completed_day"] == 4
    assert data["execution_samples"] == 2
    assert data["total_samples"] == 3
    assert data["average_execution_seconds"] == pytest.approx(90, abs=0.001)
    assert data["average_total_seconds"] == pytest.approx(240, abs=0.001)


@pytest.mark.parametrize("day,start,end", [
    ("2026-03-08", "2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00"),
    ("2026-11-01", "2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00"),
])
def test_statistics_daylight_saving(statistics_app, monkeypatch, day, start, end):
    client, db, _, _ = statistics_app
    boundaries = []
    monkeypatch.setattr(db, "task_statistics", lambda a, b: boundaries.append((a, b)) or {})
    assert client.get(
        f"/api/admin/statistics?day={day}&timezone=EST5EDT"
    ).status_code == 200
    assert boundaries == [(start, end)]


def test_statistics_full_history_and_all_users(statistics_app):
    client, db, user, task = statistics_app
    other = db.create_user("other", "long-user-password", "user", user["workspace_path"] + "-other")
    for index in range(502):
        add_run(db, user if index % 2 else other, task,
                datetime(2026, 10, 4, 10, tzinfo=UTC) + timedelta(seconds=index))
    data = client.get("/api/admin/statistics?day=2026-10-04").json()
    assert data["completed_all_time"] == data["completed_day"] == 502
