"""Runner de workers standalone (fuera del request HTTP).

Procesa la tabla `jobs` de forma continua: reclama jobs QUEUED (bloqueo
atomico con FOR UPDATE SKIP LOCKED), ejecuta el handler registrado y deja el
estado final. Pensado para correr en un contenedor Docker (`ops-worker`),
contra Supabase (pooler) via `DATABASE_URL`.

    python -m app.worker_runner

No depende del proceso web ni de la cola in-process de Vercel.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.database import dispose_engine, session_scope
from app.core.enums import JobStatus
from app.core.jobs import JobRegistry
from app.core.logging import get_logger
from app.models.job import Job
from app.workers import register_workers

logger = get_logger("worker_runner")

POLL_SECONDS = 5.0
_stop = asyncio.Event()


async def _claim_one() -> tuple | None:
    """Reclama atómicamente un job QUEUED. Devuelve (id, job_type, payload) o None."""
    async with session_scope() as session:
        if session is None:
            return None
        stmt = (
            select(Job)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.scheduled_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = (await session.execute(stmt)).scalars().first()
        if job is None:
            return None
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        job.attempts = (job.attempts or 0) + 1
        await session.flush()
        return job.id, job.job_type, dict(job.payload or {})


async def _mark_retry_or_fail(job_id, error: str) -> None:
    async with session_scope() as session:
        if session is None:
            return
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.error_message = error[:2000]
        if (job.attempts or 0) < (job.max_attempts or 3):
            job.status = JobStatus.QUEUED  # reintento
        else:
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(UTC)


async def main() -> None:
    registry = JobRegistry()
    register_workers(registry)
    logger.info("worker_runner_started", handlers=len(registry._handlers))

    def _handle_stop(*_):
        _stop.set()

    try:
        signal.signal(signal.SIGTERM, _handle_stop)
        signal.signal(signal.SIGINT, _handle_stop)
    except (ValueError, AttributeError):
        pass

    while not _stop.is_set():
        try:
            claimed = await _claim_one()
        except Exception as exc:  # noqa: BLE001
            logger.warning("claim_error", error=str(exc)[:200])
            await asyncio.sleep(POLL_SECONDS)
            continue

        if claimed is None:
            try:
                await asyncio.wait_for(_stop.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        job_id, job_type, payload = claimed
        try:
            handler = registry.get(job_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning("no_handler", type=str(job_type), error=str(exc)[:120])
            await _mark_retry_or_fail(job_id, "sin handler registrado")
            continue

        try:
            await handler(job_id, payload)
            logger.info("job_ok", job=str(job_id), type=str(job_type))
        except Exception as exc:  # noqa: BLE001
            logger.warning("job_error", job=str(job_id), type=str(job_type), error=str(exc)[:200])
            await _mark_retry_or_fail(job_id, str(exc))

    logger.info("worker_runner_stopped")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
