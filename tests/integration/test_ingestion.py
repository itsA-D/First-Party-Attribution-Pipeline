import hashlib
import hmac
import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Attribution, Order, ProcessingJob, RawEvent, RawOrderWebhook
from app.processing import process_job

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    yield test_engine
    Base.metadata.drop_all(test_engine)
    test_engine.dispose()


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def client(session_factory):
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def event_payload(event_id: str = "evt-1") -> dict:
    return {
        "event_id": event_id,
        "event_name": "page_view",
        "ts": "2026-09-03T10:00:00Z",
        "received_at": "2026-09-03T10:00:01Z",
        "anonymous_id": "visitor-1",
        "page_url": "https://urbanthread.com/?utm_source=google&utm_medium=cpc",
    }


def order_payload(status: str = "paid", updated_at: str = "2026-09-03T10:05:00Z") -> dict:
    return {
        "order_id": "#1001",
        "email_hash": "customer@example.com",
        "created_at": "2026-09-03T10:00:00Z",
        "updated_at": updated_at,
        "total": "1250.00",
        "currency": "INR",
        "financial_status": status,
    }


def post_order(client: TestClient, payload: dict, delivery_id: str):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Delivery-Id": delivery_id}
    if settings.order_webhook_secret:
        digest = hmac.new(
            settings.order_webhook_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        headers["X-Signature"] = f"sha256={digest}"
    return client.post("/webhooks/orders", content=body, headers=headers)


@pytest.mark.integration
def test_duplicate_event_is_rejected_without_duplicate_raw_or_job(client, session_factory):
    first = client.post("/collect", json=event_payload())
    second = client.post("/collect", json=event_payload())
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    with session_factory() as db:
        raw = db.scalar(select(RawEvent).where(RawEvent.dedupe_key == "event:evt-1"))
        assert db.scalar(select(func.count()).select_from(RawEvent)) == 1
        assert raw is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(ProcessingJob.entity_id == raw.id)
            )
            == 1
        )


@pytest.mark.integration
def test_duplicate_order_delivery_is_idempotent(client, session_factory):
    first = post_order(client, order_payload(), "delivery-1")
    second = post_order(client, order_payload(), "delivery-1")
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    with session_factory() as db:
        raw = db.scalar(
            select(RawOrderWebhook).where(RawOrderWebhook.delivery_key == "delivery-1")
        )
        assert db.scalar(select(func.count()).select_from(RawOrderWebhook)) == 1
        assert raw is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(ProcessingJob.entity_id == raw.id)
            )
            == 1
        )


@pytest.mark.integration
def test_order_state_reduction_and_refund_deactivation(client, session_factory):
    paid = post_order(client, order_payload(), "paid-1")
    assert paid.status_code == 202
    with session_factory() as db:
        raw_id = db.scalar(
            select(RawOrderWebhook.id).where(RawOrderWebhook.delivery_key == "paid-1")
        )
        process_job(db, "process_order_webhook", raw_id)
        db.commit()
        order = db.scalar(select(Order).where(Order.order_id == "#1001"))
        assert order is not None
        assert order.paid_eligible is True
        assert order.updated_version == 1
        assert db.scalar(
            select(func.count()).select_from(Attribution).where(Attribution.is_current.is_(True))
        ) == 1

        process_job(db, "process_order_webhook", raw_id)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Attribution)) == 1

    refunded = post_order(client, order_payload("refunded", "2026-09-04T10:05:00Z"), "refund-1")
    assert refunded.status_code == 202
    with session_factory() as db:
        raw_id = db.scalar(
            select(RawOrderWebhook.id).where(RawOrderWebhook.delivery_key == "refund-1")
        )
        process_job(db, "process_order_webhook", raw_id)
        db.commit()
        order = db.scalar(select(Order).where(Order.order_id == "#1001"))
        assert order is not None
        assert order.paid_eligible is False
        assert order.financial_status == "refunded"
        assert order.updated_version == 2
        assert db.scalar(
            select(func.count()).select_from(Attribution).where(Attribution.is_current.is_(True))
        ) == 0
