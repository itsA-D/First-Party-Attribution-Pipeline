from datetime import datetime, timedelta, timezone

from app.sessions import EventPoint, build_sessions

BASE = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def point(name: str, minutes: float, second: int = 0) -> EventPoint:
    return EventPoint(
        event_id=f"row-{name}",
        external_event_id=name,
        occurred_at=BASE + timedelta(minutes=minutes, seconds=second),
    )


def test_empty_visitor_has_no_sessions():
    assert build_sessions([]) == []


def test_29_minute_gap_stays_in_session():
    windows = build_sessions([point("a", 0), point("b", 29)])
    assert len(windows) == 1
    assert windows[0].duration_seconds == 29 * 60


def test_exactly_30_minute_gap_stays_in_session():
    windows = build_sessions([point("a", 0), point("b", 30)])
    assert len(windows) == 1
    assert windows[0].duration_seconds == 30 * 60


def test_31_minute_gap_starts_new_session():
    windows = build_sessions([point("a", 0), point("b", 31)])
    assert len(windows) == 2
    assert windows[0].event_count == 1
    assert windows[0].duration_seconds == 0


def test_single_event_session_has_zero_duration():
    [window] = build_sessions([point("only", 15)])
    assert window.started_at == window.ended_at
    assert window.duration_seconds == 0


def test_session_crossing_midnight_remains_one_session():
    start = datetime(2026, 9, 3, 23, 50, tzinfo=timezone.utc)
    end = datetime(2026, 9, 4, 0, 10, tzinfo=timezone.utc)
    windows = build_sessions(
        [
            EventPoint("a", start, "a"),
            EventPoint("b", end, "b"),
        ]
    )
    assert len(windows) == 1
    assert windows[0].started_at.date().isoformat() == "2026-09-03"
    assert windows[0].duration_seconds == 20 * 60


def test_late_event_can_bridge_existing_sessions():
    windows = build_sessions([point("a", 0), point("c", 40), point("b", 20)])
    assert len(windows) == 1
    assert windows[0].event_ids == ("row-a", "row-b", "row-c")


def test_same_timestamp_uses_stable_external_id_tie_break():
    windows = build_sessions([point("b", 0), point("a", 0)])
    assert windows[0].event_ids == ("row-a", "row-b")


def test_ordering_uses_occurrence_time_not_input_order():
    windows = build_sessions([point("c", 60), point("a", 0), point("b", 20)])
    assert [window.event_count for window in windows] == [2, 1]
