from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class RawEvent(Base):
    __tablename__ = "raw_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(128))
    dedupe_key: Mapped[str] = mapped_column(String(191), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_row: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    raw_event_id: Mapped[str] = mapped_column(String(36), ForeignKey("raw_events.id"), unique=True)
    external_event_id: Mapped[str | None] = mapped_column(String(128))
    event_name: Mapped[str] = mapped_column(String(32), nullable=False)
    anonymous_id: Mapped[str | None] = mapped_column(String(64))
    customer_id: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    page_url: Mapped[str | None] = mapped_column(Text)
    referrer: Mapped[str | None] = mapped_column(Text)
    utm_source: Mapped[str | None] = mapped_column(String(64))
    utm_medium: Mapped[str | None] = mapped_column(String(64))
    utm_campaign: Mapped[str | None] = mapped_column(String(128))
    gclid: Mapped[str | None] = mapped_column(String(128))
    fbclid: Mapped[str | None] = mapped_column(String(128))
    ttclid: Mapped[str | None] = mapped_column(String(128))
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str | None] = mapped_column(String(8))
    order_id: Mapped[str | None] = mapped_column(String(64))
    data_quality_status: Mapped[str] = mapped_column(String(16), default="ok")
    quarantined_reason: Mapped[str | None] = mapped_column(Text)
    touchpoint_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    channel: Mapped[str | None] = mapped_column(String(32))
    session_id: Mapped[str | None] = mapped_column(String(36))


class RawOrderWebhook(Base):
    __tablename__ = "raw_order_webhooks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_key: Mapped[str] = mapped_column(String(191), unique=True, nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_row: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    financial_status: Mapped[str] = mapped_column(String(16), nullable=False)
    conversion_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_version: Mapped[int] = mapped_column(Integer, default=0)


class IdentityLink(Base):
    __tablename__ = "identity_links"
    __table_args__ = (UniqueConstraint("customer_id", "anonymous_id", name="uq_identity_link"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    anonymous_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(128))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    backfilled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    anonymous_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_index: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)


class Touchpoint(Base):
    __tablename__ = "touchpoints"
    __table_args__ = (UniqueConstraint("event_id", name="uq_touchpoint_event"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    anonymous_id: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(128))
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("events.id"))
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="SET NULL")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    is_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(64))
    medium: Mapped[str | None] = mapped_column(String(64))
    campaign: Mapped[str | None] = mapped_column(String(128))
    utm_source: Mapped[str | None] = mapped_column(String(64))
    utm_medium: Mapped[str | None] = mapped_column(String(64))
    utm_campaign: Mapped[str | None] = mapped_column(String(128))
    referrer: Mapped[str | None] = mapped_column(Text)
    click_id_type: Mapped[str | None] = mapped_column(String(16))
    page_url: Mapped[str | None] = mapped_column(Text)
    direct_evidence: Mapped[bool] = mapped_column(Boolean, default=False)


class RawAdSpend(Base):
    __tablename__ = "raw_ad_spend"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(191), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_row: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdSpend(Base):
    __tablename__ = "ad_spend"
    __table_args__ = (
        UniqueConstraint("spend_date", "platform", "campaign", "currency", name="uq_spend_natural"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    spend_date: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    campaign: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    source_raw_id: Mapped[str | None] = mapped_column(String(36))
    latest_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attribution(Base):
    __tablename__ = "attributions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_row_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id"))
    customer_id: Mapped[str | None] = mapped_column(String(128))
    touchpoint_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("touchpoints.id", ondelete="SET NULL")
    )
    event_id: Mapped[str | None] = mapped_column(String(36))
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(16), nullable=False)
    lookback_days: Mapped[int] = mapped_column(Integer, nullable=False)
    conversion_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    touchpoint_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revenue_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(191), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    __table_args__ = (UniqueConstraint("source_type", "source_ref", "rule", name="uq_dq_issue"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(128))
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload_excerpt: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index("idx_events_anon_time", Event.anonymous_id, Event.occurred_at)
Index("idx_events_customer_time", Event.customer_id, Event.occurred_at)
Index("idx_events_order_id", Event.order_id)
Index("idx_sessions_anon", Session.anonymous_id)
Index("idx_touchpoints_anon_time", Touchpoint.anonymous_id, Touchpoint.occurred_at)
Index("idx_touchpoints_customer_time", Touchpoint.customer_id, Touchpoint.occurred_at)
Index("idx_orders_order_id", Order.order_id)
Index("idx_orders_paid", Order.paid_eligible, Order.conversion_at)
Index("idx_identity_links_customer", IdentityLink.customer_id)
Index("idx_identity_links_anon", IdentityLink.anonymous_id)
Index("idx_attributions_order_current", Attribution.order_id, Attribution.is_current)
Index("idx_jobs_claim", ProcessingJob.status, ProcessingJob.available_at)
Index("idx_ad_spend_date_channel", AdSpend.spend_date, AdSpend.channel, AdSpend.currency)
Index("idx_raw_events_external", RawEvent.external_event_id, unique=True)
Index(
    "uq_attribution_current",
    Attribution.order_id,
    Attribution.model,
    unique=True,
    postgresql_where=text("is_current"),
)
