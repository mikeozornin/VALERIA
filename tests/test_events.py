from __future__ import annotations

from typing import Any

from opencode_web.app import can_receive_event


class Ownership:
    def user_owns_session(self, user_id: str, workspace: str, session_id: str) -> bool:
        return (user_id, workspace, session_id) == ("user-1", "/one", "session-1")


def test_event_scope_keeps_full_payload_inside_owned_session() -> None:
    database: Any = Ownership()
    owner = {"id": "user-1", "role": "user", "workspace_path": "/one"}
    stranger = {"id": "user-2", "role": "user", "workspace_path": "/two"}
    admin = {"id": "admin", "role": "admin", "workspace_path": None}
    event = {
        "type": "opencode.event",
        "workspace": "/one",
        "session_id": "session-1",
        "event": {
            "payload": {
                "type": "message.part.updated",
                "properties": {
                    "sessionID": "session-1",
                    "part": {"type": "reasoning", "text": "unredacted"},
                },
            }
        },
    }

    assert can_receive_event(database, owner, event)
    assert can_receive_event(database, admin, event)
    assert not can_receive_event(database, stranger, event)
    assert event["event"]["payload"]["properties"]["part"]["text"] == "unredacted"
