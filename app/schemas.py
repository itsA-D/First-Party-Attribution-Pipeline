from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str | None = None
    event_name: str = Field(min_length=1)
    anonymous_id: str | None = None
    email_hash: str | None = None
    ts: Any = None
    received_at: Any = None
    page_url: str | None = None
    referrer: str | None = None
    value: Any = None
    currency: str | None = None
    order_id: str | None = None


class OrderWebhookIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    order_id: str = Field(min_length=1)
    email_hash: str | None = None
    created_at: Any
    updated_at: Any
    total: Any
    currency: str = Field(min_length=1, max_length=8)
    financial_status: str = Field(min_length=1)


class AdSpendIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    date: Any
    platform: str = Field(min_length=1)
    campaign: str = Field(min_length=1)
    spend: Any
    currency: str = Field(min_length=1, max_length=8)
    pulled_at: Any = None
