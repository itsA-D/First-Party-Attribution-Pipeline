"""initial attribution schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_event_id", sa.String(length=128), nullable=True),
        sa.Column("dedupe_key", sa.String(length=191), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_row", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_table(
        "raw_order_webhooks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("delivery_key", sa.String(length=191), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_row", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_key"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("raw_event_id", sa.String(length=36), nullable=False),
        sa.Column("external_event_id", sa.String(length=128), nullable=True),
        sa.Column("event_name", sa.String(length=32), nullable=False),
        sa.Column("anonymous_id", sa.String(length=64), nullable=True),
        sa.Column("customer_id", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column("utm_source", sa.String(length=64), nullable=True),
        sa.Column("utm_medium", sa.String(length=64), nullable=True),
        sa.Column("utm_campaign", sa.String(length=128), nullable=True),
        sa.Column("gclid", sa.String(length=128), nullable=True),
        sa.Column("fbclid", sa.String(length=128), nullable=True),
        sa.Column("ttclid", sa.String(length=128), nullable=True),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("order_id", sa.String(length=64), nullable=True),
        sa.Column("data_quality_status", sa.String(length=16), nullable=False),
        sa.Column("quarantined_reason", sa.Text(), nullable=True),
        sa.Column("touchpoint_eligible", sa.Boolean(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["raw_event_id"], ["raw_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("raw_event_id"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("email_hash", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("financial_status", sa.String(length=16), nullable=False),
        sa.Column("conversion_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_eligible", sa.Boolean(), nullable=False),
        sa.Column("updated_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )
    op.create_table(
        "identity_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("anonymous_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_source", sa.String(length=32), nullable=False),
        sa.Column("evidence_ref", sa.String(length=128), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conflict", sa.Boolean(), nullable=False),
        sa.Column("backfilled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", "anonymous_id", name="uq_identity_link"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("anonymous_id", sa.String(length=64), nullable=False),
        sa.Column("session_index", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "touchpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("anonymous_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=True),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("is_eligible", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("medium", sa.String(length=64), nullable=True),
        sa.Column("campaign", sa.String(length=128), nullable=True),
        sa.Column("utm_source", sa.String(length=64), nullable=True),
        sa.Column("utm_medium", sa.String(length=64), nullable=True),
        sa.Column("utm_campaign", sa.String(length=128), nullable=True),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column("click_id_type", sa.String(length=16), nullable=True),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.Column("direct_evidence", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_touchpoint_event"),
    )
    op.create_table(
        "raw_ad_spend",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=191), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_row", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_table(
        "ad_spend",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("spend_date", sa.Date(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("campaign", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("source_raw_id", sa.String(length=36), nullable=True),
        sa.Column("latest_pulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "spend_date", "platform", "campaign", "currency", name="uq_spend_natural"
        ),
    )
    op.create_table(
        "attributions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("order_row_id", sa.String(length=36), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=True),
        sa.Column("touchpoint_id", sa.String(length=36), nullable=True),
        sa.Column("event_id", sa.String(length=36), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=16), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("conversion_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("touchpoint_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revenue_amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["order_row_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["touchpoint_id"], ["touchpoints.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("dedupe_key", sa.String(length=191), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_table(
        "data_quality_issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=128), nullable=True),
        sa.Column("rule", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_excerpt", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_ref", "rule", name="uq_dq_issue"),
    )

    op.create_index("idx_events_anon_time", "events", ["anonymous_id", "occurred_at"])
    op.create_index("idx_events_customer_time", "events", ["customer_id", "occurred_at"])
    op.create_index("idx_events_order_id", "events", ["order_id"])
    op.create_index("idx_sessions_anon", "sessions", ["anonymous_id"])
    op.create_index("idx_touchpoints_anon_time", "touchpoints", ["anonymous_id", "occurred_at"])
    op.create_index("idx_touchpoints_customer_time", "touchpoints", ["customer_id", "occurred_at"])
    op.create_index("idx_orders_order_id", "orders", ["order_id"])
    op.create_index("idx_orders_paid", "orders", ["paid_eligible", "conversion_at"])
    op.create_index("idx_identity_links_customer", "identity_links", ["customer_id"])
    op.create_index("idx_identity_links_anon", "identity_links", ["anonymous_id"])
    op.create_index("idx_attributions_order_current", "attributions", ["order_id", "is_current"])
    op.create_index("idx_jobs_claim", "processing_jobs", ["status", "available_at"])
    op.create_index("idx_ad_spend_date_channel", "ad_spend", ["spend_date", "channel", "currency"])
    op.create_index(
        "idx_raw_events_external", "raw_events", ["external_event_id"], unique=True
    )
    op.create_index(
        "uq_attribution_current",
        "attributions",
        ["order_id", "model"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    op.drop_index("uq_attribution_current", table_name="attributions")
    op.drop_index("idx_raw_events_external", table_name="raw_events")
    op.drop_index("idx_ad_spend_date_channel", table_name="ad_spend")
    op.drop_index("idx_jobs_claim", table_name="processing_jobs")
    op.drop_index("idx_attributions_order_current", table_name="attributions")
    op.drop_index("idx_identity_links_anon", table_name="identity_links")
    op.drop_index("idx_identity_links_customer", table_name="identity_links")
    op.drop_index("idx_orders_paid", table_name="orders")
    op.drop_index("idx_orders_order_id", table_name="orders")
    op.drop_index("idx_touchpoints_customer_time", table_name="touchpoints")
    op.drop_index("idx_touchpoints_anon_time", table_name="touchpoints")
    op.drop_index("idx_sessions_anon", table_name="sessions")
    op.drop_index("idx_events_order_id", table_name="events")
    op.drop_index("idx_events_customer_time", table_name="events")
    op.drop_index("idx_events_anon_time", table_name="events")
    op.drop_table("data_quality_issues")
    op.drop_table("processing_jobs")
    op.drop_table("attributions")
    op.drop_table("ad_spend")
    op.drop_table("raw_ad_spend")
    op.drop_table("touchpoints")
    op.drop_table("sessions")
    op.drop_table("identity_links")
    op.drop_table("orders")
    op.drop_table("events")
    op.drop_table("raw_order_webhooks")
    op.drop_table("raw_events")
