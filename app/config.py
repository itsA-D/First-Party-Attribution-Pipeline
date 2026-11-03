from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def _load_dotenv() -> None:
    path = Path(".env")
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    order_webhook_secret: str
    reporting_timezone: str
    attribution_model: str
    attribution_lookback_days: int
    session_timeout_minutes: int
    historical_validity_days: int
    identity_backfill_days: int
    max_request_bytes: int
    log_level: str
    worker_poll_seconds: float
    worker_batch_size: int
    worker_lease_seconds: int
    worker_max_attempts: int
    replay_concurrency: int
    replay_max_retries: int
    replay_timeout_seconds: float
    api_base_url: str

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.reporting_timezone)


settings = Settings(
    app_env=os.getenv("APP_ENV", "local"),
    database_url=os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/urban_thread",
    ),
    order_webhook_secret=os.getenv("ORDER_WEBHOOK_SECRET", ""),
    reporting_timezone=os.getenv("REPORTING_TIMEZONE", "UTC"),
    attribution_model=os.getenv("ATTRIBUTION_MODEL", "last_click"),
    attribution_lookback_days=_int("ATTRIBUTION_LOOKBACK_DAYS", 30),
    session_timeout_minutes=_int("SESSION_TIMEOUT_MINUTES", 30),
    historical_validity_days=_int("HISTORICAL_VALIDITY_DAYS", 365),
    identity_backfill_days=_int("IDENTITY_BACKFILL_DAYS", 30),
    max_request_bytes=_int("MAX_REQUEST_BYTES", 1_048_576),
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    worker_poll_seconds=_float("WORKER_POLL_SECONDS", 1.0),
    worker_batch_size=_int("WORKER_BATCH_SIZE", 5),
    worker_lease_seconds=_int("WORKER_LEASE_SECONDS", 120),
    worker_max_attempts=_int("WORKER_MAX_ATTEMPTS", 5),
    replay_concurrency=_int("REPLAY_CONCURRENCY", 15),
    replay_max_retries=_int("REPLAY_MAX_RETRIES", 5),
    replay_timeout_seconds=_float("REPLAY_TIMEOUT_SECONDS", 30.0),
    api_base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
