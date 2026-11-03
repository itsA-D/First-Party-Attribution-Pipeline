from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.normalization import (
    Acquisition,
    classify_channel,
    extract_acquisition,
    is_external_referrer,
    normalize_event_time,
    parse_date,
    parse_timestamp,
    platform_channel,
    safe_decimal,
)


def test_parse_timestamp_iso_offset_to_utc():
    parsed = parse_timestamp("2026-09-03T10:00:00+05:30")
    assert parsed == datetime(2026, 9, 3, 4, 30, tzinfo=timezone.utc)


def test_parse_timestamp_epoch_milliseconds():
    assert parse_timestamp(1_788_000_000_000) == datetime.fromtimestamp(
        1_788_000_000, tz=timezone.utc
    )


def test_parse_timestamp_naive_is_utc():
    assert parse_timestamp("2026-09-03T10:00:00") == datetime(
        2026, 9, 3, 10, 0, tzinfo=timezone.utc
    )


def test_invalid_timestamp_is_quarantined_without_substitution():
    received = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    occurred, status, reason = normalize_event_time(
        {"ts": "not-a-timestamp"}, received, historical_validity_days=365
    )
    assert occurred is None
    assert status == "quarantined"
    assert "not substituted" in reason


def test_future_timestamp_is_quarantined():
    received = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    future = received + timedelta(hours=25)
    occurred, status, _ = normalize_event_time(
        {"ts": future.isoformat(), "received_at": received.isoformat()},
        received,
        historical_validity_days=365,
    )
    assert occurred == future
    assert status == "quarantined"


def test_historical_validity_boundary():
    received = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    exactly = received - timedelta(days=365)
    too_old = received - timedelta(days=366)
    assert normalize_event_time(
        {"ts": exactly.isoformat()}, received, 365
    )[1] == "ok"
    assert normalize_event_time(
        {"ts": too_old.isoformat()}, received, 365
    )[1] == "quarantined"


def test_extract_acquisition_is_case_insensitive_and_keeps_first_value():
    acquisition = extract_acquisition(
        "https://urbanthread.com/products/t?UTM_Source=Google&utm_medium=cpc"
        "&utm_campaign=brand&gclid=abc&gclid=def"
    )
    assert acquisition.utm_source == "Google"
    assert acquisition.utm_medium == "cpc"
    assert acquisition.utm_campaign == "brand"
    assert acquisition.gclid == "abc"


def test_click_id_precedence():
    acquisition = Acquisition(gclid="g", fbclid="f", ttclid="t")
    assert acquisition.click_id == ("gclid", "g")


def test_classify_click_ids_and_utm_families():
    assert classify_channel(Acquisition(gclid="x"), None, None, False) == "Google Ads"
    assert classify_channel(Acquisition(fbclid="x"), None, None, False) == "Meta Ads"
    assert classify_channel(Acquisition(ttclid="x"), None, None, False) == "TikTok Ads"
    assert (
        classify_channel(Acquisition(utm_source="google", utm_medium="cpc"), None, None, False)
        == "Google Ads"
    )
    assert (
        classify_channel(Acquisition(utm_source="meta", utm_medium="paid_social"), None, None, False)
        == "Meta Ads"
    )
    assert (
        classify_channel(Acquisition(utm_source="newsletter", utm_medium="email"), None, None, False)
        == "Email"
    )
    assert (
        classify_channel(Acquisition(utm_source="google", utm_medium="organic"), None, None, False)
        == "Organic Search"
    )


def test_classify_external_referrer_and_direct():
    page = "https://urbanthread.com/products/tee"
    assert classify_channel(Acquisition(), "https://www.google.com/search?q=tee", page, True) == "Organic Search"
    assert classify_channel(Acquisition(), "https://www.instagram.com/", page, True) == "Organic Social"
    assert classify_channel(Acquisition(), "https://blog.example.com/post", page, True) == "Referral"
    assert classify_channel(Acquisition(), f"{page}?variant=1", page, True) is None
    assert classify_channel(Acquisition(), None, page, True) == "Direct"
    assert classify_channel(Acquisition(), None, page, False) is None


def test_unknown_utm_and_platform_mapping():
    assert (
        classify_channel(Acquisition(utm_source="mystery", utm_medium="display"), None, None, True)
        == "Unknown"
    )
    assert platform_channel("Google") == "Google Ads"
    assert platform_channel("facebook") == "Meta Ads"
    assert platform_channel("tiktok") == "TikTok Ads"
    assert platform_channel("snapchat") == "Unknown"


def test_external_referrer_comparison_ignores_www_and_subdomains():
    assert is_external_referrer("https://www.google.com/", "https://urbanthread.com/x")
    assert not is_external_referrer(
        "https://shop.urbanthread.com/x", "https://urbanthread.com/y"
    )


def test_decimal_and_date_parsing_do_not_use_float():
    assert safe_decimal("19.99") == Decimal("19.99")
    assert safe_decimal("NaN") is None
    assert safe_decimal(True) is None
    assert parse_date("2026-09-03T00:00:00+05:30") == date(2026, 9, 3)
    assert parse_date("not-a-date") is None
