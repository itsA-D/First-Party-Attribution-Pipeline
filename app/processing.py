from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import delete, distinct, func, or_, select, update

from app.attribution import TouchpointCandidate, select_last_click
from app.config import settings, utcnow
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
    gen_uuid,
)
from app.normalization import (
    Acquisition,
    classify_channel,
    extract_acquisition,
    normalize_event_time,
    parse_date,
    parse_timestamp,
    platform_channel,
    safe_decimal,
)
from app.sessions import EventPoint, build_sessions

POLICY_VERSION = "last_click_v1"


def _issue(
    db,
    source_type: str,
    source_ref: str | None,
    rule: str,
    severity: str,
    message: str,
    payload_excerpt: dict | None = None,
) -> None:
    existing = db.scalar(
        select(DataQualityIssue.id).where(
            DataQualityIssue.source_type == source_type,
            DataQualityIssue.source_ref == source_ref,
            DataQualityIssue.rule == rule,
        )
    )
    if existing:
        return
    db.add(
        DataQualityIssue(
            source_type=source_type,
            source_ref=source_ref,
            rule=rule,
            severity=severity,
            message=message,
            payload_excerpt=payload_excerpt,
        )
    )


def enqueue_job(db, job_type: str, entity_type: str, entity_id: str, dedupe_key: str) -> None:
    if db.scalar(select(ProcessingJob.id).where(ProcessingJob.dedupe_key == dedupe_key)):
        return
    db.add(
        ProcessingJob(
            job_type=job_type,
            entity_type=entity_type,
            entity_id=entity_id,
            dedupe_key=dedupe_key,
            status="pending",
            max_attempts=settings.worker_max_attempts,
            available_at=utcnow(),
        )
    )


def process_event(db, raw_id: str) -> None:
    raw = db.get(RawEvent, raw_id)
    if raw is None:
        raise ValueError(f"raw event {raw_id} not found")
    if raw.processed_at is not None:
        return

    payload = raw.payload or {}
    event_name = str(payload.get("event_name") or "").strip()
    if not event_name:
        raise ValueError("event_name is missing")

    occurred_at, quality_status, quarantine_reason = normalize_event_time(
        payload, raw.received_at, settings.historical_validity_days
    )
    source_received_at = parse_timestamp(payload.get("received_at")) or raw.received_at
    anonymous_id = str(payload.get("anonymous_id") or "").strip() or None
    customer_id = str(payload.get("email_hash") or "").strip().lower() or None
    acquisition = extract_acquisition(payload.get("page_url"), payload.get("referrer"))

    event = db.scalar(select(Event).where(Event.raw_event_id == raw.id))
    if event is None:
        event = Event(
            raw_event_id=raw.id,
            event_name=event_name,
            received_at=source_received_at,
        )
        db.add(event)

    event.external_event_id = raw.external_event_id
    event.event_name = event_name
    event.anonymous_id = anonymous_id
    event.customer_id = customer_id
    event.occurred_at = occurred_at
    event.received_at = source_received_at
    event.page_url = payload.get("page_url")
    event.referrer = payload.get("referrer")
    event.utm_source = acquisition.utm_source
    event.utm_medium = acquisition.utm_medium
    event.utm_campaign = acquisition.utm_campaign
    event.gclid = acquisition.gclid
    event.fbclid = acquisition.fbclid
    event.ttclid = acquisition.ttclid
    event.value = safe_decimal(payload.get("value"))
    event.currency = payload.get("currency")
    event.order_id = payload.get("order_id")
    event.data_quality_status = quality_status
    event.quarantined_reason = quarantine_reason
    event.touchpoint_eligible = False
    event.channel = None
    event.session_id = None
    db.flush()

    if quality_status != "ok":
        _issue(
            db,
            "event",
            raw.external_event_id or raw.id,
            "quarantined_timestamp",
            "warning",
            quarantine_reason or "event timestamp quarantined",
            {"ts": payload.get("ts"), "received_at": payload.get("received_at")},
        )

    if anonymous_id and customer_id:
        evidence_at = occurred_at if quality_status == "ok" and occurred_at else source_received_at
        _link_identity(
            db,
            visitor_id=anonymous_id,
            customer_id=customer_id,
            evidence_source="event_email_hash",
            evidence_ref=raw.external_event_id or raw.id,
            evidence_at=evidence_at,
        )

    if anonymous_id and quality_status == "ok":
        _rebuild_visitor(db, anonymous_id)

    if anonymous_id:
        customers = db.scalars(
            select(IdentityLink.customer_id)
            .where(IdentityLink.anonymous_id == anonymous_id)
            .distinct()
        ).all()
        for customer in customers:
            _reconcile_customer(db, customer)

    raw.processed_at = utcnow()


def _link_identity(
    db,
    *,
    visitor_id: str,
    customer_id: str,
    evidence_source: str,
    evidence_ref: str | None,
    evidence_at,
) -> None:
    valid_from = evidence_at - timedelta(days=settings.identity_backfill_days)
    link = db.scalar(
        select(IdentityLink).where(
            IdentityLink.anonymous_id == visitor_id,
            IdentityLink.customer_id == customer_id,
        )
    )
    if link is None:
        db.add(
            IdentityLink(
                customer_id=customer_id,
                anonymous_id=visitor_id,
                evidence_source=evidence_source,
                evidence_ref=evidence_ref,
                valid_from=valid_from,
                backfilled=True,
            )
        )
    else:
        if valid_from < link.valid_from:
            link.valid_from = valid_from
        if link.evidence_ref is None:
            link.evidence_ref = evidence_ref
        link.backfilled = True
    db.flush()

    customers = db.scalars(
        select(distinct(IdentityLink.customer_id)).where(IdentityLink.anonymous_id == visitor_id)
    ).all()
    if len(customers) > 1:
        db.execute(
            update(IdentityLink)
            .where(IdentityLink.anonymous_id == visitor_id)
            .values(conflict=True)
        )
        _issue(
            db,
            "identity",
            visitor_id,
            "conflicting_customer_identity",
            "warning",
            "visitor is linked to more than one customer; ambiguous visitors are excluded from attribution",
            {"customers": sorted(customers)},
        )


def _rebuild_visitor(db, visitor_id: str) -> None:
    events = list(
        db.scalars(
            select(Event).where(
                Event.anonymous_id == visitor_id,
                Event.data_quality_status == "ok",
                Event.occurred_at.is_not(None),
            )
        ).all()
    )
    all_events = list(db.scalars(select(Event).where(Event.anonymous_id == visitor_id)).all())
    event_by_id = {event.id: event for event in all_events}
    points = [
        EventPoint(
            event_id=event.id,
            occurred_at=event.occurred_at,
            external_event_id=event.external_event_id,
        )
        for event in events
        if event.occurred_at is not None
    ]
    windows = build_sessions(points, settings.session_timeout_minutes)

    db.execute(delete(Session).where(Session.anonymous_id == visitor_id))
    db.flush()
    for event in all_events:
        event.session_id = None

    session_start_page_views: set[str] = set()
    for window in windows:
        session_id = gen_uuid()
        db.add(
            Session(
                id=session_id,
                anonymous_id=visitor_id,
                session_index=window.session_index,
                started_at=window.started_at,
                ended_at=window.ended_at,
                duration_seconds=window.duration_seconds,
                event_count=window.event_count,
            )
        )
        first_event = event_by_id.get(window.event_ids[0])
        if first_event is not None and first_event.event_name == "page_view":
            session_start_page_views.add(first_event.id)
        for event_id in window.event_ids:
            event = event_by_id.get(event_id)
            if event is not None:
                event.session_id = session_id
    db.flush()

    page_views = [event for event in events if event.event_name == "page_view"]
    page_view_ids = {event.id for event in page_views}
    existing = {
        touchpoint.event_id: touchpoint
        for touchpoint in db.scalars(select(Touchpoint).where(Touchpoint.anonymous_id == visitor_id)).all()
    }

    for event in page_views:
        acquisition = Acquisition(
            utm_source=event.utm_source,
            utm_medium=event.utm_medium,
            utm_campaign=event.utm_campaign,
            gclid=event.gclid,
            fbclid=event.fbclid,
            ttclid=event.ttclid,
        )
        is_start = event.id in session_start_page_views
        channel = classify_channel(acquisition, event.referrer, event.page_url, is_start)
        event.touchpoint_eligible = channel is not None
        event.channel = channel
        touchpoint = existing.get(event.id)
        if channel is None:
            if touchpoint is not None:
                touchpoint.is_eligible = False
            continue
        click_id = acquisition.click_id
        if touchpoint is None:
            touchpoint = Touchpoint(
                anonymous_id=visitor_id,
                event_id=event.id,
                occurred_at=event.occurred_at,
                channel=channel,
            )
            db.add(touchpoint)
            existing[event.id] = touchpoint
        touchpoint.customer_id = event.customer_id
        touchpoint.session_id = event.session_id
        touchpoint.occurred_at = event.occurred_at
        touchpoint.channel = channel
        touchpoint.source = acquisition.utm_source
        touchpoint.medium = acquisition.utm_medium
        touchpoint.campaign = acquisition.utm_campaign
        touchpoint.utm_source = acquisition.utm_source
        touchpoint.utm_medium = acquisition.utm_medium
        touchpoint.utm_campaign = acquisition.utm_campaign
        touchpoint.referrer = event.referrer
        touchpoint.click_id_type = click_id[0] if click_id else None
        touchpoint.page_url = event.page_url
        touchpoint.direct_evidence = channel == "Direct"
        touchpoint.is_eligible = True

    for event_id, touchpoint in existing.items():
        if event_id not in page_view_ids:
            touchpoint.is_eligible = False
    db.flush()


def _linked_visitors(db, customer_id: str, at) -> set[str]:
    candidates = set(
        db.scalars(
            select(IdentityLink.anonymous_id)
            .where(
                IdentityLink.customer_id == customer_id,
                IdentityLink.valid_from <= at,
                or_(IdentityLink.valid_to.is_(None), IdentityLink.valid_to > at),
            )
            .distinct()
        ).all()
    )
    if not candidates:
        return set()
    conflicted = set(
        db.scalars(
            select(IdentityLink.anonymous_id)
            .where(IdentityLink.anonymous_id.in_(candidates))
            .group_by(IdentityLink.anonymous_id)
            .having(func.count(distinct(IdentityLink.customer_id)) > 1)
        ).all()
    )
    return candidates - conflicted


def _reconcile_customer(db, customer_id: str) -> None:
    orders = list(
        db.scalars(
            select(Order).where(
                Order.customer_id == customer_id,
                Order.paid_eligible.is_(True),
            )
        ).all()
    )
    for order in orders:
        _reconcile_order(db, order)


def _reconcile_order(db, order: Order) -> None:
    if not order.paid_eligible:
        _deactivate_current(db, order)
        return

    linked = _linked_visitors(db, order.customer_id, order.conversion_at)
    selected = None
    selected_event = None
    reason = "unattributed: no reliable visitor identity for the order customer"
    if linked:
        lower_bound = order.conversion_at - timedelta(days=settings.attribution_lookback_days)
        rows = db.execute(
            select(Touchpoint, Event)
            .join(Event, Event.id == Touchpoint.event_id)
            .where(
                Touchpoint.anonymous_id.in_(linked),
                Touchpoint.is_eligible.is_(True),
                Touchpoint.occurred_at >= lower_bound,
                Touchpoint.occurred_at <= order.conversion_at,
            )
        ).all()
        candidates = [
            TouchpointCandidate(
                touchpoint_id=touchpoint.id,
                occurred_at=touchpoint.occurred_at,
                channel=touchpoint.channel,
                stable_event_id=event.external_event_id or event.id,
            )
            for touchpoint, event in rows
        ]
        selected = select_last_click(
            candidates, order.conversion_at, settings.attribution_lookback_days
        )
        if selected is not None:
            selected_event = next(
                (event for touchpoint, event in rows if touchpoint.id == selected.touchpoint_id),
                None,
            )
            reason = "last_click: selected the latest eligible touchpoint within the lookback window"
        else:
            reason = "unattributed: no eligible marketing touchpoint in the lookback window"

    channel = selected.channel if selected else "Unattributed"
    touchpoint_id = selected.touchpoint_id if selected else None
    event_external_id = (
        (selected_event.external_event_id or selected_event.id) if selected_event else None
    )
    current = db.scalar(
        select(Attribution).where(
            Attribution.order_row_id == order.id,
            Attribution.is_current.is_(True),
        )
    )
    desired = (
        touchpoint_id,
        channel,
        order.total,
        order.currency,
        reason,
        order.conversion_at,
        settings.attribution_lookback_days,
        POLICY_VERSION,
        settings.attribution_model,
    )
    if current is not None:
        actual = (
            current.touchpoint_id,
            current.channel,
            current.revenue_amount,
            current.currency,
            current.reason,
            current.conversion_at,
            current.lookback_days,
            current.policy_version,
            current.model,
        )
        if actual == desired:
            return
        current.is_current = False
        db.flush()

    db.add(
        Attribution(
            order_id=order.order_id,
            order_row_id=order.id,
            customer_id=order.customer_id,
            touchpoint_id=touchpoint_id,
            event_id=event_external_id,
            channel=channel,
            model=settings.attribution_model,
            policy_version=POLICY_VERSION,
            lookback_days=settings.attribution_lookback_days,
            conversion_at=order.conversion_at,
            touchpoint_occurred_at=selected.occurred_at if selected else None,
            revenue_amount=order.total,
            currency=order.currency,
            reason=reason,
            calculated_at=utcnow(),
            is_current=True,
        )
    )


def _deactivate_current(db, order: Order) -> None:
    db.execute(
        update(Attribution)
        .where(
            Attribution.order_row_id == order.id,
            Attribution.is_current.is_(True),
        )
        .values(is_current=False)
    )


def process_order_webhook(db, raw_id: str) -> None:
    raw = db.get(RawOrderWebhook, raw_id)
    if raw is None:
        raise ValueError(f"raw order webhook {raw_id} not found")
    payload = raw.payload or {}
    order_id = str(payload.get("order_id") or "").strip()
    email_hash = str(payload.get("email_hash") or "").strip().lower()
    status = str(payload.get("financial_status") or "").strip().lower()
    created_at = parse_timestamp(payload.get("created_at"))
    updated_at = parse_timestamp(payload.get("updated_at"))
    total = safe_decimal(payload.get("total"))
    currency = str(payload.get("currency") or "").strip().upper()

    if not order_id or created_at is None or updated_at is None:
        _issue(
            db,
            "order_webhook",
            raw.delivery_key,
            "invalid_order_payload",
            "error",
            "order webhook has missing identifiers or invalid timestamps",
        )
        raise ValueError("invalid order webhook timestamps or identifiers")
    if total is None or total < 0 or not currency:
        _issue(
            db,
            "order_webhook",
            raw.delivery_key,
            "invalid_order_amount",
            "error",
            "order webhook has invalid total or currency",
        )
        raise ValueError("invalid order amount or currency")
    if status not in {"paid", "refunded", "cancelled"}:
        _issue(
            db,
            "order_webhook",
            raw.delivery_key,
            "unknown_financial_status",
            "warning",
            f"unrecognized financial_status: {status!r}",
        )

    order = db.scalar(select(Order).where(Order.order_id == order_id))
    if order is None:
        order = Order(
            id=gen_uuid(),
            order_id=order_id,
            email_hash=email_hash,
            customer_id=email_hash,
            created_at=created_at,
            updated_at=updated_at,
            total=total,
            currency=currency,
            financial_status=status,
            conversion_at=created_at,
            paid_eligible=status == "paid",
            updated_version=1,
        )
        db.add(order)
        db.flush()
    elif updated_at > order.updated_at:
        order.email_hash = email_hash or order.email_hash
        order.customer_id = email_hash or order.customer_id
        order.created_at = created_at
        order.updated_at = updated_at
        order.total = total
        order.currency = currency
        order.financial_status = status
        order.conversion_at = created_at
        order.paid_eligible = status == "paid"
        order.updated_version += 1
    elif updated_at == order.updated_at:
        if (
            order.total != total
            or order.currency != currency
            or order.financial_status != status
            or order.email_hash != email_hash
        ):
            _issue(
                db,
                "order_webhook",
                raw.delivery_key,
                "conflicting_webhook_same_timestamp",
                "warning",
                "a webhook with the same updated_at conflicts with the canonical order; the first state was kept",
            )
    else:
        _issue(
            db,
            "order_webhook",
            raw.delivery_key,
            "older_order_update_ignored",
            "info",
            "an older order update arrived after a newer canonical state",
        )

    if order.paid_eligible and not order.email_hash:
        _issue(
            db,
            "order",
            order.order_id,
            "missing_customer_identity",
            "warning",
            "paid order has no email_hash and will remain unattributed",
        )

    if order.paid_eligible:
        _reconcile_order(db, order)
    else:
        _deactivate_current(db, order)


def process_spend(db, raw_id: str) -> None:
    raw = db.get(RawAdSpend, raw_id)
    if raw is None:
        raise ValueError(f"raw spend record {raw_id} not found")
    payload = raw.payload or {}
    spend_date = parse_date(payload.get("date"))
    amount = safe_decimal(payload.get("spend"))
    platform = str(payload.get("platform") or "").strip().lower()
    campaign = str(payload.get("campaign") or "").strip()
    currency = str(payload.get("currency") or "").strip().upper()
    pulled_at_value = payload.get("pulled_at")
    pulled_at = parse_timestamp(pulled_at_value)
    if pulled_at_value is not None and pulled_at is None:
        _issue(
            db,
            "ad_spend",
            raw.dedupe_key,
            "invalid_pulled_at",
            "warning",
            "pulled_at was malformed; arrival time is used only as a snapshot-order fallback",
        )
    pulled_at = pulled_at or raw.received_at
    raw.pulled_at = pulled_at

    if spend_date is None or amount is None or amount < 0 or not platform or not campaign or not currency:
        _issue(
            db,
            "ad_spend",
            raw.dedupe_key,
            "invalid_spend_record",
            "error",
            "spend record has invalid date, amount, platform, campaign or currency",
            {"date": payload.get("date"), "spend": payload.get("spend")},
        )
        raise ValueError("invalid ad spend record")

    channel = platform_channel(platform)
    if channel == "Unknown":
        _issue(
            db,
            "ad_spend",
            raw.dedupe_key,
            "unknown_spend_platform",
            "warning",
            f"unrecognized spend platform: {platform!r}",
        )

    existing = db.scalar(
        select(AdSpend).where(
            AdSpend.spend_date == spend_date,
            AdSpend.platform == platform,
            AdSpend.campaign == campaign,
            AdSpend.currency == currency,
        )
    )
    if existing is None:
        db.add(
            AdSpend(
                spend_date=spend_date,
                platform=platform,
                campaign=campaign,
                channel=channel,
                currency=currency,
                amount=amount,
                source_raw_id=raw.id,
                latest_pulled_at=pulled_at,
            )
        )
        return

    existing_raw = db.get(RawAdSpend, existing.source_raw_id) if existing.source_raw_id else None
    existing_key = existing_raw.dedupe_key if existing_raw else ""
    is_newer = existing.latest_pulled_at is None or pulled_at > existing.latest_pulled_at
    same_time_tie_break = (
        existing.latest_pulled_at is not None
        and pulled_at == existing.latest_pulled_at
        and raw.dedupe_key > existing_key
    )

    if same_time_tie_break and amount != existing.amount:
        _issue(
            db,
            "ad_spend",
            raw.dedupe_key,
            "conflicting_spend_snapshot",
            "warning",
            "two spend snapshots share the same pulled_at but disagree; the deterministic delivery-key tie-break was used",
        )
    elif (
        existing.latest_pulled_at is not None
        and pulled_at == existing.latest_pulled_at
        and amount != existing.amount
    ):
        _issue(
            db,
            "ad_spend",
            raw.dedupe_key,
            "conflicting_spend_snapshot",
            "warning",
            "two spend snapshots share the same pulled_at but disagree; the existing deterministic snapshot was kept",
        )

    if is_newer or same_time_tie_break:
        existing.channel = channel
        existing.amount = amount
        existing.source_raw_id = raw.id
        existing.latest_pulled_at = pulled_at


def process_job(db, job_type: str, entity_id: str) -> None:
    handlers: dict[str, Callable[[object, str], None]] = {
        "process_event": process_event,
        "process_order_webhook": process_order_webhook,
        "process_spend": process_spend,
    }
    handler = handlers.get(job_type)
    if handler is None:
        raise ValueError(f"unknown job type: {job_type}")
    handler(db, entity_id)
