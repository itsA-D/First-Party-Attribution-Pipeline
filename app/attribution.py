from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class TouchpointCandidate:
    touchpoint_id: str
    occurred_at: datetime
    channel: str
    stable_event_id: str


def select_last_click(
    touchpoints: list[TouchpointCandidate],
    conversion_at: datetime,
    lookback_days: int,
) -> TouchpointCandidate | None:
    lower_bound = conversion_at - timedelta(days=lookback_days)
    eligible = [
        touchpoint
        for touchpoint in touchpoints
        if lower_bound <= touchpoint.occurred_at <= conversion_at
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda item: (item.occurred_at, item.stable_event_id))


def compute_roas(revenue: Decimal, spend: Decimal | None, spend_available: bool) -> Decimal | None:
    if not spend_available or spend is None or spend == 0:
        return None
    return revenue / spend
