from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class EventPoint:
    event_id: str
    occurred_at: datetime
    external_event_id: str | None = None

    @property
    def stable_id(self) -> str:
        return self.external_event_id or self.event_id


@dataclass(frozen=True)
class SessionWindow:
    session_index: int
    started_at: datetime
    ended_at: datetime
    duration_seconds: int
    event_count: int
    event_ids: tuple[str, ...]


def build_sessions(events: list[EventPoint], timeout_minutes: int = 30) -> list[SessionWindow]:
    """Sessionize one visitor. A new session starts only when the gap is strictly greater."""
    if not events:
        return []
    ordered = sorted(events, key=lambda event: (event.occurred_at, event.stable_id))
    timeout = timedelta(minutes=timeout_minutes)
    windows: list[SessionWindow] = []
    current: list[EventPoint] = [ordered[0]]

    for event in ordered[1:]:
        if event.occurred_at - current[-1].occurred_at > timeout:
            windows.append(_window(len(windows) + 1, current))
            current = [event]
        else:
            current.append(event)
    windows.append(_window(len(windows) + 1, current))
    return windows


def _window(index: int, events: list[EventPoint]) -> SessionWindow:
    started = events[0].occurred_at
    ended = events[-1].occurred_at
    return SessionWindow(
        session_index=index,
        started_at=started,
        ended_at=ended,
        duration_seconds=int((ended - started).total_seconds()),
        event_count=len(events),
        event_ids=tuple(event.event_id for event in events),
    )
