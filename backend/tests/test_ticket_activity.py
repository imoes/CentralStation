from datetime import datetime, timezone

from app.services.ticket_activity import (
    build_ticket_activity_prompt,
    build_ticket_snapshot,
    diff_ticket_activity,
)


def _detail(*, updated="2026-09-17T08:00:00+00:00", status="Open", comments=None):
    return {
        "id": "10001",
        "key": "OPS-7",
        "summary": "Router prüfen",
        "description": "Bestehende Beschreibung",
        "status": status,
        "priority": "High",
        "assignee": "Thomas",
        "updated": updated,
        "comments": comments or [],
    }


def _comment(comment_id, body, created, *, updated=None, author="Anna"):
    return {
        "id": str(comment_id),
        "author": author,
        "body": body,
        "created": created,
        "updated": updated or created,
    }


def test_structured_snapshot_detects_new_edited_deleted_comments_and_fields():
    before = _detail(comments=[
        _comment("1", "bleibt", "2026-09-17T07:00:00+00:00"),
        _comment("2", "alte Fassung", "2026-09-17T07:10:00+00:00"),
        _comment("3", "wird entfernt", "2026-09-17T07:20:00+00:00"),
    ])
    previous = build_ticket_snapshot(before)
    current_detail = _detail(
        updated="2026-09-17T09:00:00+00:00",
        status="In Progress",
        comments=[
            _comment("1", "bleibt", "2026-09-17T07:00:00+00:00"),
            _comment(
                "2", "neue Fassung", "2026-09-17T07:10:00+00:00",
                updated="2026-09-17T08:30:00+00:00",
            ),
            _comment("4", "neu", "2026-09-17T08:45:00+00:00"),
        ],
    )
    current = build_ticket_snapshot(current_detail)

    activity = diff_ticket_activity(previous, current, current_detail)

    assert activity["state"] == "changed"
    assert [comment["id"] for comment in activity["new_comments"]] == ["4"]
    assert [comment["id"] for comment in activity["edited_comments"]] == ["2"]
    assert activity["deleted_comment_ids"] == ["3"]
    assert activity["comment_change_count"] == 3
    assert activity["field_changes"] == [{
        "field": "status",
        "label": "Status",
        "before": "Open",
        "after": "In Progress",
    }]


def test_legacy_session_uses_sync_time_instead_of_marking_history_unread():
    detail = _detail(comments=[
        _comment("1", "bereits bekannt", "2026-09-17T07:00:00+00:00"),
        _comment("2", "neu", "2026-09-17T09:00:00+00:00"),
    ])

    activity = diff_ticket_activity(
        None,
        build_ticket_snapshot(detail),
        detail,
        synced_at=datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc),
    )

    assert [comment["id"] for comment in activity["new_comments"]] == ["2"]
    assert activity["comment_change_count"] == 1


def test_acknowledged_observation_does_not_hide_a_later_comment():
    observed_detail = _detail(comments=[
        _comment("1", "beobachtet", "2026-09-17T08:30:00+00:00"),
    ])
    acknowledged_snapshot = build_ticket_snapshot(observed_detail)
    later_detail = _detail(
        updated="2026-09-17T09:05:00+00:00",
        comments=[
            _comment("1", "beobachtet", "2026-09-17T08:30:00+00:00"),
            _comment("2", "während der KI-Antwort", "2026-09-17T09:05:00+00:00"),
        ],
    )

    activity = diff_ticket_activity(
        acknowledged_snapshot,
        build_ticket_snapshot(later_detail),
        later_detail,
    )

    assert activity["state"] == "changed"
    assert [comment["id"] for comment in activity["new_comments"]] == ["2"]


def test_delta_prompt_contains_changes_but_omits_unchanged_description():
    previous_detail = _detail()
    current_detail = _detail(comments=[
        _comment("1", "Bitte Logdatei nachreichen", "2026-09-17T09:00:00+00:00"),
    ])
    activity = diff_ticket_activity(
        build_ticket_snapshot(previous_detail),
        build_ticket_snapshot(current_detail),
        current_detail,
    )

    prompt = build_ticket_activity_prompt(current_detail, activity)

    assert "Bitte Logdatei nachreichen" in prompt
    assert "Bestehende Beschreibung" not in prompt
    assert "Wiederhole nicht den bereits bekannten Ticketkontext" in prompt
