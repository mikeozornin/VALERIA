"""Select the final visible answer for one task phase, including compaction."""

from typing import Any


def task_final_answer(
    messages: list[dict[str, Any]], message_id: str
) -> dict[str, str | None] | None:
    parent_id = None
    latest = None
    for message in messages:
        info = message.get("info", message)
        if parent_id is None:
            if info.get("id") == message_id and info.get("role") == "user":
                parent_id = message_id
            continue
        if info.get("role") == "user":
            # Task sessions are dedicated to a run. OpenCode may introduce
            # continuation prompts when compacting the current phase.
            parent_id = info.get("id")
            latest = None
            continue
        if info.get("role") != "assistant" or info.get("parentID", parent_id) != parent_id:
            continue
        if info.get("error") is not None or info.get("finish") in {"error", "content-filter"}:
            return None
        latest = message
    if latest is None:
        return None
    info = latest.get("info", latest)
    finish = info.get("finish")
    if info.get("summary") or finish in {"tool-calls", "unknown"} or not (
        finish or (info.get("time") or {}).get("completed")
    ):
        return None
    text = "\n\n".join(
        part["text"] for part in latest.get("parts", [])
        if part.get("type") == "text" and isinstance(part.get("text"), str)
        and not part.get("synthetic") and not part.get("ignored") and part["text"].strip()
    )
    return {"message_id": info.get("id"), "text": text} if text else None
