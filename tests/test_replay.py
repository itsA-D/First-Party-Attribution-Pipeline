import hashlib
import hmac

from scripts.replay import (
    ORDERS_ENDPOINT,
    WorkItem,
    _canonical,
    _delivery_key,
    _headers,
    _retry_after_seconds,
)


def test_canonical_payload_is_key_order_independent():
    assert _canonical({"b": 2, "a": 1}) == _canonical({"a": 1, "b": 2})


def test_delivery_key_is_stable_for_same_file_row_and_payload():
    first = _delivery_key("orders.csv", 2, {"order_id": "#1", "total": "10.00"})
    second = _delivery_key("orders.csv", 2, {"total": "10.00", "order_id": "#1"})
    assert first == second


def test_delivery_key_changes_with_row_even_for_identical_payload():
    first = _delivery_key("orders.csv", 2, {"order_id": "#1"})
    second = _delivery_key("orders.csv", 3, {"order_id": "#1"})
    assert first != second


def test_order_hmac_header_matches_body_signature():
    body = b'{"order_id":"#1"}'
    item = WorkItem("orders.csv", 2, ORDERS_ENDPOINT, {}, "delivery-1")
    headers = _headers(item, "secret", body)
    expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert headers["X-Signature"] == f"sha256={expected}"
    assert headers["X-Delivery-Id"] == "delivery-1"
    assert headers["Idempotency-Key"] == "delivery-1"


def test_event_requests_do_not_send_delivery_headers():
    headers = _headers(WorkItem("events.jsonl", 1, "/collect", {}), None, b"{}")
    assert "X-Delivery-Id" not in headers


def test_retry_after_seconds_and_http_date():
    assert _retry_after_seconds("2.5") == 2.5
    assert _retry_after_seconds(None) is None
    assert _retry_after_seconds("not-a-number") is None
