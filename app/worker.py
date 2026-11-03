from __future__ import annotations

import argparse
import logging
import os
import random
import socket
import time
from datetime import timedelta

from sqlalchemy import and_, or_, select, text

from app.config import settings, utcnow
from app.db import SessionLocal
from app.models import DataQualityIssue, ProcessingJob
from app.processing import process_job

logger = logging.getLogger("urban_thread.worker")


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _claim_jobs(batch_size: int) -> list[str]:
    now = utcnow()
    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(ProcessingJob)
                .where(
                    ProcessingJob.available_at <= now,
                    or_(
                        ProcessingJob.status.in_(("pending", "retry")),
                        and_(
                            ProcessingJob.status == "running",
                            ProcessingJob.locked_until.is_not(None),
                            ProcessingJob.locked_until < now,
                        ),
                    ),
                )
                .order_by(ProcessingJob.created_at, ProcessingJob.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            ).all()
        )
        if not jobs:
            return []
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        for job in jobs:
            job.status = "running"
            job.attempts += 1
            job.locked_by = worker_id
            job.locked_until = now + timedelta(seconds=settings.worker_lease_seconds)
            job.started_at = job.started_at or now
        db.commit()
        return [job.id for job in jobs]


def _process_one(job_id: str) -> None:
    with SessionLocal() as db:
        # One shared advisory lock keeps derived writes safe across worker processes.
        # A per-visitor/customer lock can replace this when throughput justifies it.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('urban_thread_worker'))"))
        job = db.get(ProcessingJob, job_id)
        if job is None or job.status != "running":
            db.rollback()
            return
        try:
            process_job(db, job.job_type, job.entity_id)
            job.status = "completed"
            job.completed_at = utcnow()
            job.locked_by = None
            job.locked_until = None
            job.last_error = None
            db.commit()
            logger.info("job completed", extra={"job_id": job.id, "job_type": job.job_type})
        except ValueError as exc:
            db.rollback()
            _fail(job_id, str(exc), permanent=True)
            logger.warning("job dead-lettered", extra={"job_id": job_id, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - retry unexpected processing failures
            db.rollback()
            logger.exception("job failed", extra={"job_id": job_id})
            _fail(job_id, str(exc), permanent=False)


def _fail(job_id: str, error: str, *, permanent: bool) -> None:
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            return
        job.last_error = error[:2000]
        job.locked_by = None
        job.locked_until = None
        if permanent or job.attempts >= job.max_attempts:
            job.status = "dead_letter"
            job.completed_at = utcnow()
            existing_issue = db.scalar(
                select(DataQualityIssue.id).where(
                    DataQualityIssue.source_type == "processing_job",
                    DataQualityIssue.source_ref == job.id,
                    DataQualityIssue.rule == "permanent_processing_failure",
                )
            )
            if existing_issue is None:
                db.add(
                    DataQualityIssue(
                        source_type="processing_job",
                        source_ref=job.id,
                        rule="permanent_processing_failure",
                        severity="error",
                        message=error[:2000],
                        payload_excerpt={
                            "job_type": job.job_type,
                            "entity_type": job.entity_type,
                            "entity_id": job.entity_id,
                        },
                    )
                )
        else:
            delay = min(60, 2**job.attempts) * random.uniform(0.5, 1.0)
            job.status = "retry"
            job.available_at = utcnow() + timedelta(seconds=delay)
        db.commit()


def run() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("worker starting")
    while True:
        job_ids = _claim_jobs(settings.worker_batch_size)
        if not job_ids:
            time.sleep(settings.worker_poll_seconds)
            continue
        for job_id in job_ids:
            _process_one(job_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Urban Thread PostgreSQL processing worker")
    parser.add_argument("--once", action="store_true", help="process one batch and exit")
    args = parser.parse_args()
    if args.once:
        logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
        for job_id in _claim_jobs(settings.worker_batch_size):
            _process_one(job_id)
    else:
        run()


if __name__ == "__main__":
    main()
