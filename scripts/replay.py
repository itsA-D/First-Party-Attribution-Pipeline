from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import hmac
import json
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

EVENTS_ENDPOINT = "/collect"
ORDERS_ENDPOINT = "/webhooks/orders"
SPEND_ENDPOINT = "/webhooks/ad-spend"


@dataclass(slots=True)
class WorkItem:
    file: str
    row: int
    endpoint: str
    payload: dict[str, Any]
    delivery_key: str | None = None


@dataclass
class Stats:
    accepted: int = 0
    duplicate: int = 0
    retried: int = 0
    rejected: int = 0
    failed: int = 0
    sent: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _delivery_key(file_name: str, row: int, payload: dict[str, Any]) -> str:
    return f"{file_name}:{row}:{_fingerprint(payload)}"


def _iter_jsonl(path: Path) -> AsyncIterator[WorkItem]:
    async def iterator() -> AsyncIterator[WorkItem]:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError(f"{path.name}:{line_number}: JSON value must be an object")
                yield WorkItem(path.name, line_number, EVENTS_ENDPOINT, payload)
    return iterator()


def _iter_csv(path: Path, endpoint: str) -> AsyncIterator[WorkItem]:
    async def iterator() -> AsyncIterator[WorkItem]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                row_number = reader.line_num
                payload = dict(row)
                yield WorkItem(
                    path.name,
                    row_number,
                    endpoint,
                    payload,
                    _delivery_key(path.name, row_number, payload),
                )
    return iterator()


def _iter_all(data_dir: Path, only: str = "all") -> AsyncIterator[WorkItem]:
    async def iterator() -> AsyncIterator[WorkItem]:
        files = [
            (data_dir / "events.jsonl", EVENTS_ENDPOINT, "jsonl", "events"),
            (data_dir / "orders.csv", ORDERS_ENDPOINT, "csv", "orders"),
            (data_dir / "ad_spend.csv", SPEND_ENDPOINT, "csv", "spend"),
        ]
        for path, endpoint, kind, name in files:
            if only != "all" and only != name:
                continue
            if not path.exists():
                raise FileNotFoundError(f"missing source file: {path}")
            source = _iter_jsonl(path) if kind == "jsonl" else _iter_csv(path, endpoint)
            async for item in source:
                yield item
    return iterator()


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _headers(item: WorkItem, secret: str | None, body: bytes) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Source-File": item.file,
        "X-Source-Row": str(item.row),
    }
    if item.delivery_key:
        headers["X-Delivery-Id"] = item.delivery_key
        headers["Idempotency-Key"] = item.delivery_key
    if secret and item.endpoint == ORDERS_ENDPOINT:
        headers["X-Signature"] = "sha256=" + hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
    return headers


async def _send(
    client: httpx.AsyncClient,
    base_url: str,
    item: WorkItem,
    secret: str | None,
    max_attempts: int,
    stats: Stats,
) -> None:
    body = _canonical(item.payload).encode("utf-8")
    headers = _headers(item, secret, body)
    attempt = 0
    while True:
        attempt += 1
        try:
            response = await client.post(
                base_url + item.endpoint,
                content=body,
                headers=headers,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt >= max_attempts:
                stats.failed += 1
                stats.failures.append(
                    {
                        "file": item.file,
                        "row": item.row,
                        "endpoint": item.endpoint,
                        "error": f"{type(exc).__name__}: {exc}",
                        "attempts": attempt,
                        "delivery_key": item.delivery_key,
                    }
                )
                return
            stats.retried += 1
            await asyncio.sleep(_backoff(attempt))
            continue

        if response.status_code == 429 or response.status_code >= 500:
            if attempt >= max_attempts:
                stats.failed += 1
                stats.failures.append(
                    {
                        "file": item.file,
                        "row": item.row,
                        "endpoint": item.endpoint,
                        "status": response.status_code,
                        "error": response.text[:500],
                        "attempts": attempt,
                        "delivery_key": item.delivery_key,
                    }
                )
                return
            stats.retried += 1
            delay = _retry_after_seconds(response.headers.get("retry-after"))
            await asyncio.sleep(max(delay if delay is not None else 0, _backoff(attempt)))
            continue

        if 400 <= response.status_code < 500:
            stats.rejected += 1
            stats.failures.append(
                {
                    "file": item.file,
                    "row": item.row,
                    "endpoint": item.endpoint,
                    "status": response.status_code,
                    "error": response.text[:500],
                    "attempts": attempt,
                    "delivery_key": item.delivery_key,
                }
            )
            return

        if 200 <= response.status_code < 300:
            try:
                result = response.json()
            except ValueError:
                result = {}
            if result.get("duplicate") is True:
                stats.duplicate += 1
            else:
                stats.accepted += 1
            return

        stats.failed += 1
        stats.failures.append(
            {
                "file": item.file,
                "row": item.row,
                "endpoint": item.endpoint,
                "status": response.status_code,
                "error": response.text[:500],
                "attempts": attempt,
                "delivery_key": item.delivery_key,
            }
        )
        return


def _backoff(attempt: int) -> float:
    return min(30.0, 2.0**attempt) * random.uniform(0.5, 1.0)


async def run(args: argparse.Namespace) -> int:
    data_dir = args.data_dir
    stats = Stats()
    queue: asyncio.Queue[WorkItem | None] = asyncio.Queue(maxsize=args.concurrency * 8)
    secret = args.secret or os.getenv("ORDER_WEBHOOK_SECRET") or None

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    stats.sent += 1
                    await _send(client, args.base_url, item, secret, args.max_attempts, stats)
                    if stats.sent % 5000 == 0:
                        print(f"submitted {stats.sent} records", flush=True)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(args.concurrency)]
        try:
            async for item in _iter_all(data_dir, args.only):
                await queue.put(item)
        finally:
            for _ in workers:
                await queue.put(None)
            await queue.join()
            await asyncio.gather(*workers)

    report_path = Path(args.failure_report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(stats.failures, indent=2), encoding="utf-8")

    print(
        "submitted={sent} accepted={accepted} duplicate={duplicate} "
        "retried={retried} rejected={rejected} failed={failed}".format(
            sent=stats.sent,
            accepted=stats.accepted,
            duplicate=stats.duplicate,
            retried=stats.retried,
            rejected=stats.rejected,
            failed=stats.failed,
        )
    )
    print(f"failure report: {report_path.resolve()}")
    return 0 if stats.failed == 0 and stats.rejected == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay Urban Thread source files over HTTP")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--base-url", default=os.getenv("API_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("REPLAY_CONCURRENCY", "15")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("REPLAY_TIMEOUT_SECONDS", "30")))
    parser.add_argument("--max-attempts", type=int, default=int(os.getenv("REPLAY_MAX_RETRIES", "4")))
    parser.add_argument("--secret", default=None)
    parser.add_argument("--failure-report", default="replay_failures.json")
    parser.add_argument(
        "--only",
        choices=("all", "events", "orders", "spend"),
        default="all",
        help="replay one source file family instead of all three",
    )
    args = parser.parse_args()
    if args.concurrency < 1 or args.concurrency > 50:
        parser.error("--concurrency must be between 1 and 50")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be at least 1")
    try:
        return asyncio.run(run(args))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - surface replay failures with a non-zero exit
        print(f"replay failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
