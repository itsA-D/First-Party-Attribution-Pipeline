from __future__ import annotations

import base64
import binascii
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, distinct, func, or_, select

from app.attribution import compute_roas
from app.config import settings
from app.models import (
    AdSpend,
    Attribution,
    DataQualityIssue,
    Event,
    IdentityLink,
    Order,
    ProcessingJob,
    RawAdSpend,
    RawEvent,
    RawOrderWebhook,
    Session,
    Touchpoint,
)


def _bounds(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    tz = settings.tz
    start = datetime.combine(start_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, end


def _money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(Decimal(value).quantize(Decimal("0.01")))


def _roas(revenue: Decimal, spend: Decimal | None, spend_records: int) -> float | None:
    value = compute_roas(revenue, spend, spend_records > 0)
    return float(value) if value is not None else None


def _spend_status(records: int, spend: Decimal) -> str:
    if records == 0:
        return "missing"
    return "zero" if spend == 0 else "available"


def _channel_expr():
    return func.coalesce(Attribution.channel, "Unattributed")


def _current_attribution_join():
    return and_(
        Attribution.order_row_id == Order.id,
        Attribution.is_current.is_(True),
        Attribution.model == settings.attribution_model,
    )


def channel_report(db, start_date: date, end_date: date) -> dict:
    start, end = _bounds(start_date, end_date)
    channel = _channel_expr()
    revenue_rows = db.execute(
        select(
            channel.label("channel"),
            Order.currency,
            func.count(distinct(Order.id)).label("orders"),
            func.sum(Order.total).label("revenue"),
        )
        .select_from(Order)
        .outerjoin(Attribution, _current_attribution_join())
        .where(
            Order.paid_eligible.is_(True),
            Order.conversion_at >= start,
            Order.conversion_at < end,
        )
        .group_by(channel, Order.currency)
    ).all()

    spend_rows = db.execute(
        select(
            AdSpend.channel,
            AdSpend.currency,
            func.count().label("records"),
            func.sum(AdSpend.amount).label("spend"),
        )
        .where(AdSpend.spend_date >= start_date, AdSpend.spend_date <= end_date)
        .group_by(AdSpend.channel, AdSpend.currency)
    ).all()

    combined: dict[tuple[str, str], dict] = {}
    for row in revenue_rows:
        key = (row.currency, row.channel)
        combined[key] = {
            "orders": int(row.orders or 0),
            "revenue": Decimal(row.revenue or 0),
            "spend": Decimal(0),
            "spend_records": 0,
        }
    for row in spend_rows:
        key = (row.currency, row.channel)
        item = combined.setdefault(
            key,
            {"orders": 0, "revenue": Decimal(0), "spend": Decimal(0), "spend_records": 0},
        )
        item["spend"] = Decimal(row.spend or 0)
        item["spend_records"] = int(row.records or 0)

    currencies: dict[str, list[dict]] = {}
    for (currency, channel_name), item in sorted(combined.items()):
        status = _spend_status(item["spend_records"], item["spend"])
        currencies.setdefault(currency, []).append(
            {
                "channel": channel_name,
                "orders": item["orders"],
                "revenue": _money_str(item["revenue"]),
                "ad_spend": _money_str(item["spend"]) if item["spend_records"] else None,
                "roas": _roas(item["revenue"], item["spend"], item["spend_records"]),
                "spend_status": status,
            }
        )

    result_currencies: list[dict] = []
    for currency, channels in sorted(currencies.items()):
        channels.sort(key=lambda row: (-Decimal(row["revenue"] or 0), row["channel"]))
        revenue = sum((Decimal(row["revenue"] or 0) for row in channels), Decimal(0))
        spend = sum((Decimal(row["ad_spend"] or 0) for row in channels), Decimal(0))
        records = sum(1 for row in channels if row["spend_status"] != "missing")
        result_currencies.append(
            {
                "currency": currency,
                "channels": channels,
                "totals": {
                    "orders": sum(row["orders"] for row in channels),
                    "revenue": _money_str(revenue),
                    "ad_spend": _money_str(spend) if records else None,
                    "roas": _roas(revenue, spend, records),
                    "spend_status": _spend_status(records, spend),
                },
            }
        )

    return {
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "timezone": settings.reporting_timezone,
        },
        "attribution_model": settings.attribution_model,
        "lookback_days": settings.attribution_lookback_days,
        "revenue_date_basis": "orders.conversion_at",
        "spend_date_basis": "ad_spend.spend_date",
        "currencies": result_currencies,
    }


def daily_report(db, start_date: date, end_date: date) -> dict:
    start, end = _bounds(start_date, end_date)
    channel = _channel_expr()
    local_day = func.date(func.timezone(settings.reporting_timezone, Order.conversion_at))
    revenue_rows = db.execute(
        select(
            local_day.label("day"),
            channel.label("channel"),
            Order.currency,
            func.count(distinct(Order.id)).label("orders"),
            func.sum(Order.total).label("revenue"),
        )
        .select_from(Order)
        .outerjoin(Attribution, _current_attribution_join())
        .where(
            Order.paid_eligible.is_(True),
            Order.conversion_at >= start,
            Order.conversion_at < end,
        )
        .group_by(local_day, channel, Order.currency)
    ).all()
    spend_rows = db.execute(
        select(
            AdSpend.spend_date.label("day"),
            AdSpend.channel,
            AdSpend.currency,
            func.count().label("records"),
            func.sum(AdSpend.amount).label("spend"),
        )
        .where(AdSpend.spend_date >= start_date, AdSpend.spend_date <= end_date)
        .group_by(AdSpend.spend_date, AdSpend.channel, AdSpend.currency)
    ).all()

    combined: dict[tuple[str, str, str], dict] = {}
    for row in revenue_rows:
        key = (str(row.day), row.channel, row.currency)
        combined[key] = {
            "orders": int(row.orders or 0),
            "revenue": Decimal(row.revenue or 0),
            "spend": Decimal(0),
            "spend_records": 0,
        }
    for row in spend_rows:
        key = (str(row.day), row.channel, row.currency)
        item = combined.setdefault(
            key,
            {"orders": 0, "revenue": Decimal(0), "spend": Decimal(0), "spend_records": 0},
        )
        item["spend"] = Decimal(row.spend or 0)
        item["spend_records"] = int(row.records or 0)

    rows = []
    for (day, channel_name, currency), item in sorted(combined.items()):
        status = _spend_status(item["spend_records"], item["spend"])
        rows.append(
            {
                "date": day,
                "channel": channel_name,
                "currency": currency,
                "orders": item["orders"],
                "revenue": _money_str(item["revenue"]),
                "ad_spend": _money_str(item["spend"]) if item["spend_records"] else None,
                "roas": _roas(item["revenue"], item["spend"], item["spend_records"]),
                "spend_status": status,
            }
        )

    return {
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "timezone": settings.reporting_timezone,
        },
        "attribution_model": settings.attribution_model,
        "lookback_days": settings.attribution_lookback_days,
        "rows": rows,
    }


def _encode_cursor(paid_at: datetime, order_id: str) -> str:
    raw = json.dumps({"paid_at": paid_at.isoformat(), "id": order_id}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(value: str) -> tuple[datetime, str] | None:
    try:
        raw = json.loads(base64.urlsafe_b64decode(value.encode()))
        paid_at = datetime.fromisoformat(raw["paid_at"])
        if paid_at.tzinfo is None:
            return None
        return paid_at, str(raw["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error):
        return None


def order_ledger(
    db,
    start_date: date,
    end_date: date,
    channel_filter: str | None,
    limit: int,
    cursor: str | None,
) -> dict:
    start, end = _bounds(start_date, end_date)
    channel = _channel_expr()
    statement = (
        select(Order, Attribution)
        .outerjoin(Attribution, _current_attribution_join())
        .where(
            Order.paid_eligible.is_(True),
            Order.conversion_at >= start,
            Order.conversion_at < end,
        )
        .order_by(Order.conversion_at.desc(), Order.id.desc())
        .limit(limit + 1)
    )
    if channel_filter:
        statement = statement.where(channel == channel_filter)
    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded is None:
            raise ValueError("invalid cursor")
        cursor_paid_at, cursor_id = decoded
        statement = statement.where(
            or_(
                Order.conversion_at < cursor_paid_at,
                and_(Order.conversion_at == cursor_paid_at, Order.id < cursor_id),
            )
        )
    rows = db.execute(statement).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for order, attribution in rows:
        items.append(
            {
                "order_id": order.order_id,
                "paid_at": order.conversion_at.isoformat(),
                "currency": order.currency,
                "revenue": _money_str(order.total),
                "status": order.financial_status,
                "channel": attribution.channel if attribution else "Unattributed",
                "attribution_reason": attribution.reason if attribution else None,
                "attribution_calculated_at": (
                    attribution.calculated_at.isoformat() if attribution else None
                ),
            }
        )
    next_cursor = None
    if has_more and rows:
        last_order, _ = rows[-1]
        next_cursor = _encode_cursor(last_order.conversion_at, last_order.id)
    return {"items": items, "next_cursor": next_cursor}


def order_detail(db, external_order_id: str) -> dict | None:
    order = db.scalar(select(Order).where(Order.order_id == external_order_id))
    if order is None:
        return None
    current = db.scalar(
        select(Attribution).where(
            Attribution.order_row_id == order.id,
            Attribution.model == settings.attribution_model,
            Attribution.is_current.is_(True),
        )
    )
    history = list(
        db.scalars(
            select(Attribution)
            .where(Attribution.order_row_id == order.id)
            .order_by(Attribution.calculated_at.desc())
        ).all()
    )
    webhook_count = db.scalar(
        select(func.count()).select_from(RawOrderWebhook).where(RawOrderWebhook.order_id == order.order_id)
    )
    links = list(
        db.scalars(
            select(IdentityLink).where(IdentityLink.customer_id == order.customer_id)
        ).all()
    )
    touchpoint = db.get(Touchpoint, current.touchpoint_id) if current and current.touchpoint_id else None
    return {
        "order": {
            "order_id": order.order_id,
            "email_hash": order.email_hash,
            "customer_id": order.customer_id,
            "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat(),
            "conversion_at": order.conversion_at.isoformat(),
            "total": _money_str(order.total),
            "currency": order.currency,
            "financial_status": order.financial_status,
            "paid_eligible": order.paid_eligible,
            "webhook_deliveries": int(webhook_count or 0),
        },
        "attribution": (
            {
                "channel": current.channel,
                "model": current.model,
                "policy_version": current.policy_version,
                "lookback_days": current.lookback_days,
                "conversion_at": current.conversion_at.isoformat(),
                "touchpoint_occurred_at": (
                    current.touchpoint_occurred_at.isoformat()
                    if current.touchpoint_occurred_at
                    else None
                ),
                "revenue": _money_str(current.revenue_amount),
                "currency": current.currency,
                "reason": current.reason,
                "calculated_at": current.calculated_at.isoformat(),
                "is_current": current.is_current,
                "touchpoint": (
                    {
                        "id": touchpoint.id,
                        "event_id": touchpoint.event_id,
                        "channel": touchpoint.channel,
                        "occurred_at": touchpoint.occurred_at.isoformat(),
                        "utm_source": touchpoint.utm_source,
                        "utm_medium": touchpoint.utm_medium,
                        "utm_campaign": touchpoint.utm_campaign,
                        "referrer": touchpoint.referrer,
                        "direct_evidence": touchpoint.direct_evidence,
                    }
                    if touchpoint
                    else None
                ),
            }
            if current
            else None
        ),
        "attribution_history": [
            {
                "channel": row.channel,
                "touchpoint_id": row.touchpoint_id,
                "revenue": _money_str(row.revenue_amount),
                "currency": row.currency,
                "reason": row.reason,
                "calculated_at": row.calculated_at.isoformat(),
                "is_current": row.is_current,
            }
            for row in history
        ],
        "identity_links": [
            {
                "anonymous_id": link.anonymous_id,
                "evidence_source": link.evidence_source,
                "evidence_ref": link.evidence_ref,
                "valid_from": link.valid_from.isoformat(),
                "conflict": link.conflict,
            }
            for link in links
        ],
    }


def status_report(db) -> dict:
    latest_raw_event = db.scalar(select(func.max(RawEvent.received_at)))
    latest_raw_order = db.scalar(select(func.max(RawOrderWebhook.received_at)))
    latest_raw_spend = db.scalar(select(func.max(RawAdSpend.received_at)))
    latest_completed = db.scalar(
        select(func.max(ProcessingJob.completed_at)).where(ProcessingJob.status == "completed")
    )
    job_counts = {
        status: int(count)
        for status, count in db.execute(
            select(ProcessingJob.status, func.count()).group_by(ProcessingJob.status)
        ).all()
    }
    issue_counts = [
        {"rule": rule, "severity": severity, "count": int(count)}
        for rule, severity, count in db.execute(
            select(DataQualityIssue.rule, DataQualityIssue.severity, func.count())
            .group_by(DataQualityIssue.rule, DataQualityIssue.severity)
            .order_by(func.count().desc())
        ).all()
    ]
    return {
        "freshness": {
            "latest_raw_event_received_at": _iso(latest_raw_event),
            "latest_raw_order_received_at": _iso(latest_raw_order),
            "latest_raw_spend_received_at": _iso(latest_raw_spend),
            "latest_completed_job_at": _iso(latest_completed),
        },
        "jobs": job_counts,
        "data_quality": issue_counts,
        "counts": {
            "raw_events": int(db.scalar(select(func.count()).select_from(RawEvent)) or 0),
            "events": int(db.scalar(select(func.count()).select_from(Event)) or 0),
            "orders": int(db.scalar(select(func.count()).select_from(Order)) or 0),
            "paid_orders": int(
                db.scalar(
                    select(func.count()).select_from(Order).where(Order.paid_eligible.is_(True))
                )
                or 0
            ),
            "sessions": int(db.scalar(select(func.count()).select_from(Session)) or 0),
            "touchpoints": int(db.scalar(select(func.count()).select_from(Touchpoint)) or 0),
            "current_attributions": int(
                db.scalar(
                    select(func.count())
                    .select_from(Attribution)
                    .where(Attribution.is_current.is_(True))
                )
                or 0
            ),
            "spend_records": int(db.scalar(select(func.count()).select_from(AdSpend)) or 0),
        },
        "reporting_timezone": settings.reporting_timezone,
        "attribution_model": settings.attribution_model,
        "lookback_days": settings.attribution_lookback_days,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
