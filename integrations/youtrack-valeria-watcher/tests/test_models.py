import pytest

from youtrack_valeria_watcher.errors import ExternalServiceError
from youtrack_valeria_watcher.models import canonical_issue_url, comment_id_from_url


def test_canonical_issue_url_removes_query_and_fragment():
    assert (
        canonical_issue_url(
            "https://youtrack.example/issue/DEMO-42/?x=1#focus=Comments-1", "youtrack.example"
        )
        == "https://youtrack.example/issue/DEMO-42"
    )


def test_canonical_issue_url_rejects_another_host():
    with pytest.raises(ExternalServiceError, match="unexpected"):
        canonical_issue_url("https://attacker.example/issue/DEMO-42", "youtrack.example")


def test_comment_id_comes_from_focus_fragment():
    assert (
        comment_id_from_url(
            "https://youtrack.example/issue/DEMO-42#focus=Comments-6-123.0-0",
            issue_id="DEMO-42",
            author="User",
            created_at_ms=1,
        )
        == "Comments-6-123.0-0"
    )
