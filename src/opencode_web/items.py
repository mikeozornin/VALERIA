"""One projection of consultations, folder chats and task runs for the new UI."""

from __future__ import annotations

from typing import Any

from .database import UNFINISHED_JOB_STATUSES

ITEM_KINDS = ("question", "task", "folder")
ITEM_STATUSES = frozenset(
    {*UNFINISHED_JOB_STATUSES, "completed", "idle", "failed", "cancelled"}
)


def task_run_display_title(job: dict[str, Any]) -> str:
    template_title = job["task_title"]
    title = job.get("chat_title") or template_title
    if title == template_title or title.startswith(f"{template_title}: "):
        return title
    return f"{template_title}: {title}"


def task_phase(job: dict[str, Any]) -> str:
    if not job.get("plan_first"):
        return "build"
    return "build" if job.get("phase2_message_id") else "plan"


def item_kind(row: dict[str, Any]) -> str:
    kind = row.get("chat_kind", row.get("kind"))
    if kind == "task":
        return "task"
    return "question" if row.get("consultation") else "folder"


def item_status(kind: str, job_status: str | None) -> str:
    if kind == "folder":
        # A folder chat is "idle" whenever its agent has nothing unfinished.
        return job_status if job_status in UNFINISHED_JOB_STATUSES else "idle"
    if job_status is None:
        return "idle"
    return job_status if job_status in ITEM_STATUSES else "running"


def public_item(row: dict[str, Any]) -> dict[str, Any]:
    kind = item_kind(row)
    is_task = kind == "task" and row.get("task_id") is not None
    return {
        "kind": kind,
        "id": row["job_id"] if is_task else row["chat_id"],
        "chat_id": row["chat_id"],
        "title": task_run_display_title(row) if is_task else row["chat_title"],
        "status": item_status(kind, row.get("job_status")),
        "phase": task_phase(row) if is_task else None,
        "task_id": row.get("task_id") if is_task else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived_at": row.get("archived_at"),
        "snoozed_until": row.get("snoozed_until"),
    }


def allowed_item_kinds(user: dict[str, Any]) -> tuple[str, ...]:
    return ("question", "task") if user.get("tasks_only") else ITEM_KINDS


CHAT_EVENT_TYPES = frozenset({"chat.updated", "chat.deleted", "task.title.updated"})


def chat_item_row(database: Any, chat_id: str) -> dict[str, Any] | None:
    rows = database.list_items(chat_id=chat_id, include_deleted=True)
    return rows[0] if rows else None


async def publish_chat_updated(
    database: Any, events: Any, chat_id: str, *, title_changed: bool = False
) -> None:
    """Tell the shell that a chat row changed (archive, snooze, title)."""
    row = chat_item_row(database, chat_id)
    if row is None:
        return
    await events.publish({"type": "chat.updated", "item": row})
    if title_changed and item_kind(row) == "task" and row.get("task_id") is not None:
        await events.publish({"type": "task.title.updated", "item": row})


async def publish_chat_deleted(database: Any, events: Any, chat_id: str) -> None:
    if row := chat_item_row(database, chat_id):
        await events.publish({"type": "chat.deleted", "item": row})


def chat_event_for_user(user: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
    """Only the owner (in the scope where they can open the chat) and admins see these."""
    row = event.get("item") or {}
    kind = item_kind(row)
    admin = user["role"] == "admin"
    if not admin:
        if row.get("owner_user_id") != user["id"] or kind not in allowed_item_kinds(user):
            return None
        workspace = user.get("workspace_path")
        if kind != "task" and workspace and row.get("workspace_snapshot") != workspace:
            return None
        if row.get("deleted_at") and event["type"] != "chat.deleted":
            return None
    item = public_item(row)
    if event["type"] == "chat.deleted":
        visible: dict[str, Any] = {"type": "chat.deleted", "chat_id": item["chat_id"]}
    elif event["type"] == "task.title.updated":
        visible = {
            "type": "task.title.updated",
            "run_id": item["id"],
            "chat_id": item["chat_id"],
            "title": item["title"],
            # The legacy UI patches titles from `task`.
            "task": {"id": item["id"], "title": item["title"]},
        }
    else:
        visible = {"type": "chat.updated", **item}
    if admin:
        visible["owner_user_id"] = row.get("owner_user_id")
    return visible
