from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class Acquisition:
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    gclid: str | None = None
    fbclid: str | None = None
    ttclid: str | None = None

    @property
    def click_id(self) -> tuple[str, str] | None:
        for name, value in (("gclid", self.gclid), ("fbclid", self.fbclid), ("ttclid", self.ttclid)):
            if value:
                return name, value
        return None

    @property
    def has_any(self) -> bool:
        return any(
            value
            for value in (
                self.utm_source,
                self.utm_medium,
                self.utm_campaign,
                self.gclid,
                self.fbclid,
                self.ttclid,
            )
        )


def parse_timestamp(value: object) -> datetime | None:
    """Parse ISO-8601 or Unix timestamps. Unix values are treated as milliseconds when large."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            number = float(value)
            seconds = number / 1000.0 if abs(number) >= 100_000_000_000 else number
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        else:
            text = str(value).strip()
            if not text:
                return None
            if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
                number = float(text)
                seconds = number / 1000.0 if abs(number) >= 100_000_000_000 else number
                parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
            else:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_event_time(
    payload: dict, server_received_at: datetime, historical_validity_days: int
) -> tuple[datetime | None, str, str | None]:
    occurred_at = parse_timestamp(payload.get("ts"))
    received_at = parse_timestamp(payload.get("received_at")) or server_received_at
    if occurred_at is None:
        return None, "quarantined", "device timestamp missing or malformed; server receipt time was not substituted"
    if occurred_at > received_at + timedelta(hours=24):
        return occurred_at, "quarantined", "device timestamp is more than 24 hours in the future relative to receipt"
    if occurred_at < received_at - timedelta(days=historical_validity_days):
        return (
            occurred_at,
            "quarantined",
            f"device timestamp is more than {historical_validity_days} days before receipt",
        )
    return occurred_at, "ok", None


def extract_acquisition(page_url: str | None, referrer: str | None = None) -> Acquisition:
    query: dict[str, list[str]] = {}
    if page_url:
        try:
            query = parse_qs(urlsplit(page_url).query, keep_blank_values=False)
        except ValueError:
            query = {}
    lowered = {key.lower(): values for key, values in query.items()}

    def first(name: str) -> str | None:
        values = lowered.get(name)
        if not values:
            return None
        value = values[0].strip()
        return value or None

    return Acquisition(
        utm_source=first("utm_source"),
        utm_medium=first("utm_medium"),
        utm_campaign=first("utm_campaign"),
        gclid=first("gclid"),
        fbclid=first("fbclid"),
        ttclid=first("ttclid"),
    )


def _host(value: str | None) -> str:
    if not value:
        return ""
    candidate = value.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate.lstrip("/")
    try:
        return urlsplit(candidate).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _same_site(first: str, second: str) -> bool:
    if not first or not second:
        return False
    return first == second or first.endswith(f".{second}") or second.endswith(f".{first}")


def is_external_referrer(referrer: str | None, page_url: str | None) -> bool:
    referrer_host = _host(referrer)
    if not referrer_host:
        return False
    return not _same_site(referrer_host, _host(page_url))


def _utm_paid_search(source: str, medium: str) -> bool:
    return source == "google" and medium in {"cpc", "ppc", "paid", "paid_search", "paidsearch"}


def _utm_paid_social(source: str, medium: str) -> bool:
    meta = source in {"fb", "facebook", "meta", "ig", "instagram"}
    tiktok = source in {"tiktok", "tik_tok"}
    paid_medium = medium in {"cpc", "paid", "paid_social", "paidsocial"}
    return (meta or tiktok) and paid_medium


def classify_channel(
    acquisition: Acquisition,
    referrer: str | None,
    page_url: str | None,
    is_session_start_page_view: bool,
) -> str | None:
    click_id = acquisition.click_id
    if click_id:
        return {
            "gclid": "Google Ads",
            "fbclid": "Meta Ads",
            "ttclid": "TikTok Ads",
        }[click_id[0]]

    source = (acquisition.utm_source or "").strip().lower()
    medium = (acquisition.utm_medium or "").strip().lower()
    campaign = (acquisition.utm_campaign or "").strip().lower()
    has_utm = bool(source or medium or campaign)

    if _utm_paid_search(source, medium):
        return "Google Ads"
    if _utm_paid_social(source, medium):
        return "TikTok Ads" if source in {"tiktok", "tik_tok"} else "Meta Ads"
    if medium == "email" or source in {"klaviyo", "newsletter", "mailchimp", "email"}:
        return "Email"
    if medium == "organic" or source in {"organic", "seo"}:
        if source in {"fb", "facebook", "meta", "ig", "instagram", "tiktok"}:
            return "Organic Social"
        if source in {"google", "bing", "duckduckgo", "yahoo"}:
            return "Organic Search"
        return "Organic Search"
    if has_utm:
        return "Unknown"

    referrer_host = _host(referrer)
    if referrer_host:
        if not is_external_referrer(referrer, page_url):
            return None
        if any(host in referrer_host for host in ("google.", "bing.", "duckduckgo.", "yahoo.")):
            return "Organic Search"
        if any(host in referrer_host for host in ("facebook.", "instagram.", "tiktok.")):
            return "Organic Social"
        return "Referral"

    if is_session_start_page_view:
        return "Direct"
    return None


def platform_channel(platform: str) -> str:
    value = platform.strip().lower()
    if value == "google":
        return "Google Ads"
    if value in {"meta", "facebook", "instagram"}:
        return "Meta Ads"
    if value == "tiktok":
        return "TikTok Ads"
    return "Unknown"


def safe_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount


def parse_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def is_finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
