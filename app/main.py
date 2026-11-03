from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as OrmSession

from app.config import settings, utcnow
from app.db import get_db
from app.models import RawAdSpend, RawEvent, RawOrderWebhook, gen_uuid
from app.processing import enqueue_job
from app.reporting import channel_report, daily_report, order_detail, order_ledger, status_report
from app.schemas import AdSpendIn, EventIn, OrderWebhookIn

logger = logging.getLogger("urban_thread.api")


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


_configure_logging()

app = FastAPI(
    title="Urban Thread Attribution API",
    version="1.0.0",
    description="First-party event, order and ad-spend ingestion with last-click attribution.",
)


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_request_bytes:
                return JSONResponse(status_code=413, content={"detail": "payload too large"})
        except ValueError:
            pass
    return await call_next(request)


async def _read_json(request: Request) -> tuple[bytes, Any]:
    body = await request.body()
    if len(body) > settings.max_request_bytes:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        return body, json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="malformed JSON") from None


def _source_metadata(request: Request) -> tuple[str | None, int | None]:
    file_name = request.headers.get("x-source-file")
    raw_row = request.headers.get("x-source-row")
    try:
        file_row = int(raw_row) if raw_row else None
    except ValueError:
        file_row = None
    return file_name, file_row


def _delivery_key(request: Request, payload: dict) -> str:
    supplied = (
        request.headers.get("x-delivery-id")
        or request.headers.get("idempotency-key")
        or payload.get("delivery_id")
        or payload.get("webhook_id")
    )
    return str(supplied) if supplied else str(uuid.uuid4())


def _verify_hmac(request: Request, body: bytes) -> None:
    secret = settings.order_webhook_secret
    if not secret:
        return
    supplied = request.headers.get("x-signature") or request.headers.get("x-hub-signature-256")
    if not supplied:
        raise HTTPException(status_code=401, detail="missing webhook signature")
    signature = supplied.removeprefix("sha256=")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="invalid webhook signature")


def _commit(db: OrmSession) -> None:
    try:
        db.commit()
    except IntegrityError:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("database commit failed")
        raise HTTPException(status_code=503, detail="storage unavailable") from exc


@app.get("/")
def root() -> dict:
    return {"service": "urban-thread-attribution", "docs": "/docs"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready(db: OrmSession = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}


@app.post("/collect", status_code=202)
async def collect(request: Request, db: OrmSession = Depends(get_db)):
    _, payload = await _read_json(request)
    try:
        event = EventIn.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="event payload must be a JSON object")

    external_event_id = str(event.event_id) if event.event_id else None
    dedupe_key = f"event:{external_event_id}" if external_event_id else f"event:uuid:{uuid.uuid4()}"
    existing = db.scalar(select(RawEvent).where(RawEvent.dedupe_key == dedupe_key))
    if existing:
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "duplicate": True, "id": existing.id},
        )

    file_name, file_row = _source_metadata(request)
    raw = RawEvent(
        id=gen_uuid(),
        source="tracking_pixel",
        external_event_id=external_event_id,
        dedupe_key=dedupe_key,
        payload=payload,
        file_name=file_name,
        file_row=file_row,
        received_at=utcnow(),
    )
    db.add(raw)
    enqueue_job(db, "process_event", "raw_event", raw.id, f"job:event:{dedupe_key}")
    try:
        _commit(db)
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(RawEvent).where(RawEvent.dedupe_key == dedupe_key))
        if existing:
            return JSONResponse(
                status_code=200,
                content={"status": "duplicate", "duplicate": True, "id": existing.id},
            )
        raise HTTPException(status_code=503, detail="could not persist event") from None
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "duplicate": False, "id": raw.id},
    )


@app.post("/webhooks/orders", status_code=202)
async def order_webhook(request: Request, db: OrmSession = Depends(get_db)):
    body, payload = await _read_json(request)
    _verify_hmac(request, body)
    try:
        order = OrderWebhookIn.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="order payload must be a JSON object")

    delivery_key = _delivery_key(request, payload)
    existing = db.scalar(
        select(RawOrderWebhook).where(RawOrderWebhook.delivery_key == delivery_key)
    )
    if existing:
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "duplicate": True, "id": existing.id},
        )

    file_name, file_row = _source_metadata(request)
    raw = RawOrderWebhook(
        id=gen_uuid(),
        source="order_system",
        delivery_key=delivery_key,
        order_id=str(order.order_id),
        payload=payload,
        file_name=file_name,
        file_row=file_row,
        received_at=utcnow(),
    )
    db.add(raw)
    enqueue_job(db, "process_order_webhook", "raw_order_webhook", raw.id, f"job:order:{delivery_key}")
    try:
        _commit(db)
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(RawOrderWebhook).where(RawOrderWebhook.delivery_key == delivery_key)
        )
        if existing:
            return JSONResponse(
                status_code=200,
                content={"status": "duplicate", "duplicate": True, "id": existing.id},
            )
        raise HTTPException(status_code=503, detail="could not persist order webhook") from None
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "duplicate": False, "id": raw.id},
    )


@app.post("/webhooks/ad-spend", status_code=202)
async def ad_spend_webhook(request: Request, db: OrmSession = Depends(get_db)):
    _, payload = await _read_json(request)
    try:
        spend = AdSpendIn.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="spend payload must be a JSON object")

    delivery_key = _delivery_key(request, payload)
    existing = db.scalar(select(RawAdSpend).where(RawAdSpend.dedupe_key == delivery_key))
    if existing:
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "duplicate": True, "id": existing.id},
        )

    file_name, file_row = _source_metadata(request)
    raw = RawAdSpend(
        id=gen_uuid(),
        source="ad_platform",
        dedupe_key=delivery_key,
        payload=payload,
        file_name=file_name,
        file_row=file_row,
        received_at=utcnow(),
    )
    db.add(raw)
    enqueue_job(db, "process_spend", "raw_ad_spend", raw.id, f"job:spend:{delivery_key}")
    try:
        _commit(db)
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(RawAdSpend).where(RawAdSpend.dedupe_key == delivery_key))
        if existing:
            return JSONResponse(
                status_code=200,
                content={"status": "duplicate", "duplicate": True, "id": existing.id},
            )
        raise HTTPException(status_code=503, detail="could not persist spend record") from None
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "duplicate": False, "id": raw.id},
    )


def _validate_dates(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be before or equal to end_date")


@app.get("/api/v1/reports/channels")
def report_channels(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: OrmSession = Depends(get_db),
) -> dict:
    _validate_dates(start_date, end_date)
    return channel_report(db, start_date, end_date)


@app.get("/api/v1/reports/daily")
def report_daily(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: OrmSession = Depends(get_db),
) -> dict:
    _validate_dates(start_date, end_date)
    return daily_report(db, start_date, end_date)


@app.get("/api/v1/orders")
def list_orders(
    start_date: date = Query(...),
    end_date: date = Query(...),
    channel: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
    db: OrmSession = Depends(get_db),
) -> dict:
    _validate_dates(start_date, end_date)
    try:
        return order_ledger(db, start_date, end_date, channel, limit, cursor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/orders/{order_id}")
def get_order(order_id: str, db: OrmSession = Depends(get_db)) -> dict:
    result = order_detail(db, order_id)
    if result is None:
        raise HTTPException(status_code=404, detail="order not found")
    return result


@app.get("/api/v1/status")
def status(db: OrmSession = Depends(get_db)) -> dict:
    return status_report(db)
