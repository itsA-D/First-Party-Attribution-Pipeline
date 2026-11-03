from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.attribution import TouchpointCandidate, compute_roas, select_last_click

CONVERSION = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def candidate(name: str, days_before: float, channel: str = "Google Ads") -> TouchpointCandidate:
    return TouchpointCandidate(
        touchpoint_id=f"tp-{name}",
        stable_event_id=name,
        occurred_at=CONVERSION - timedelta(days=days_before),
        channel=channel,
    )


def test_exact_30_day_lookback_boundary_is_inclusive():
    selected = select_last_click([candidate("boundary", 30)], CONVERSION, 30)
    assert selected is not None
    assert selected.stable_event_id == "boundary"


def test_just_outside_lookback_is_excluded():
    assert select_last_click([candidate("old", 30.0001)], CONVERSION, 30) is None


def test_touchpoint_after_conversion_is_excluded():
    assert select_last_click([candidate("after", -0.1)], CONVERSION, 30) is None


def test_last_click_uses_latest_occurrence():
    selected = select_last_click(
        [candidate("early", 10, "Email"), candidate("late", 1, "Meta Ads")],
        CONVERSION,
        30,
    )
    assert selected is not None
    assert selected.channel == "Meta Ads"


def test_same_timestamp_uses_stable_event_tie_break():
    selected = select_last_click([candidate("b", 1), candidate("a", 1)], CONVERSION, 30)
    assert selected is not None
    assert selected.stable_event_id == "b"


def test_no_eligible_candidate_returns_none():
    assert select_last_click([], CONVERSION, 30) is None


def test_roas_is_null_when_spend_is_missing():
    assert compute_roas(Decimal("100"), None, False) is None


def test_roas_is_null_when_spend_is_zero():
    assert compute_roas(Decimal("100"), Decimal("0"), True) is None


def test_roas_is_zero_when_revenue_is_zero_and_spend_is_positive():
    assert compute_roas(Decimal("0"), Decimal("50"), True) == Decimal("0")


def test_roas_uses_weighted_amounts():
    revenue = Decimal("150.00")
    spend = Decimal("50.00")
    assert compute_roas(revenue, spend, True) == Decimal("3")
