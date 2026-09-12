"""Runner de workers standalone (fuera del request HTTP).

Procesa la tabla `jobs` de forma continua: reclama jobs QUEUED (bloqueo
atomico con FOR UPDATE SKIP LOCKED), ejecuta el handler registrado y deja el
estado final. Pensado para correr en un contenedor Docker (`ops-worker`),
contra Supabase (pooler) via `DATABASE_URL`.

    python -m app.worker_runner

No depende del proceso web ni de la cola in-process de Vercel.

Resiliencia (Fase 2):
  - Cada worker tiene un id; al reclamar el job anota `locked_at/locked_by`.
  - Mientras el handler corre, un task aparte refresca `heartbeat_at`.
  - Si un worker muere, su heartbeat se vence y `recover_stale` reencola el
    job para que otro lo retome (al arrancar y cada `RECLAIM_SECONDS`).
  - Al fallar, `JobService.schedule_retry` aplica backoff exponencial con
    jitter; un 429/Retry-After reencola sin gastar intento.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import dispose_engine, session_scope
from app.core.enums import JobStatus
from app.core.jobs import JobHandler, JobRegistry
from app.core.logging import get_logger
from app.models.job import Job
from app.services.job_svc import JobService, retry_after_seconds
from app.workers import register_workers

logger = get_logger("worker_runner")

POLL_SECONDS = 5.0
RECLAIM_SECONDS = 60.0
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
_stop = asyncio.Event()


async def _claim_one() -> tuple | None:
    """Reclama atómicamente un job QUEUED. Devuelve (id, job_type, payload) o None.

    Solo toma jobs ya programados (`scheduled_at <= now`): el backoff y el
    Retry-After dejan el job en el futuro y no debe adelantarse.
    """
    async with session_scope() as session:
        if session is None:
            return None
        stmt = (
            select(Job)
            .where(Job.status == JobStatus.QUEUED, Job.scheduled_at <= datetime.now(UTC))
            .order_by(Job.scheduled_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = (await session.execute(stmt)).scalars().first()
        if job is None:
            return None
        ahora = datetime.now(UTC)
        job.status = JobStatus.RUNNING
        job.started_at = ahora
        job.locked_at = ahora
        job.heartbeat_at = ahora
        job.locked_by = WORKER_ID
        job.attempts = (job.attempts or 0) + 1
        await session.flush()
        return job.id, job.job_type, dict(job.payload or {})


async def _heartbeat_loop(job_id: uuid.UUID, stop: asyncio.Event, interval: float) -> None:
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            async with session_scope() as session:
                if session is not None:
                    await JobService(session).heartbeat(job_id, worker_id=WORKER_ID)
        except Exception as exc:  # noqa: BLE001 - un heartbeat perdido no debe matar el job
            logger.warning("heartbeat_error", job=str(job_id), error=str(exc)[:150])


async def _run_with_heartbeat(
    job_id: uuid.UUID, handler: JobHandler, payload: dict[str, Any]
) -> None:
    """Ejecuta el handler con un heartbeat concurrente hasta que termine."""
    interval = max(1.0, float(get_settings().job_heartbeat_interval_seconds))
    stop = asyncio.Event()
    beat = asyncio.create_task(_heartbeat_loop(job_id, stop, interval), name=f"heartbeat:{job_id}")
    try:
        await handler(job_id, payload)
    finally:
        stop.set()
        await asyncio.gather(beat, return_exceptions=True)


async def _mark_retry_or_fail(
    job_id: uuid.UUID, error: str, *, retry_after: float | None = None
) -> None:
    async with session_scope() as session:
        if session is None:
            return
        await JobService(session).schedule_retry(job_id, error, retry_after=retry_after)


async def _recover_stale(*, timeout_seconds: float | None) -> int:
    async with session_scope() as session:
        if session is None:
            return 0
        return await JobService(session).recover_stale(timeout_seconds=timeout_seconds)


async def main() -> None:
    registry = JobRegistry()
    register_workers(registry)
    settings = get_settings()
    logger.info("worker_runner_started", worker=WORKER_ID, handlers=len(registry._handlers))

    def _handle_stop(*_args: object) -> None:
        _stop.set()

    try:
        signal.signal(signal.SIGTERM, _handle_stop)
        signal.signal(signal.SIGINT, _handle_stop)
    except (ValueError, AttributeError):
        pass

    # Al arrancar, cualquier RUNNING pertenece a un proceso que ya no existe.
    try:
        recovered = await _recover_stale(timeout_seconds=None)
        if recovered:
            logger.warning("stale_jobs_reclaimed_at_startup", count=recovered)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stale_reclaim_failed", error=str(exc)[:200])

    last_reclaim = time.monotonic()
    while not _stop.is_set():
        if time.monotonic() - last_reclaim >= RECLAIM_SECONDS:
            last_reclaim = time.monotonic()
            try:
                recovered = await _recover_stale(
                    timeout_seconds=settings.job_stale_timeout_seconds
                )
                if recovered:
                    logger.warning("stale_jobs_reclaimed", count=recovered)
            except Exception as exc:  # noqa: BLE001
                logger.warning("stale_reclaim_failed", error=str(exc)[:200])

        try:
            claimed = await _claim_one()
        except Exception as exc:  # noqa: BLE001
            logger.warning("claim_error", error=str(exc)[:200])
            await asyncio.sleep(POLL_SECONDS)
            continue

        if claimed is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_stop.wait(), timeout=POLL_SECONDS)
            continue

        job_id, job_type, payload = claimed
        try:
            handler = registry.get(job_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning("no_handler", type=str(job_type), error=str(exc)[:120])
            await _mark_retry_or_fail(job_id, "sin handler registrado")
            continue

        try:
            await _run_with_heartbeat(job_id, handler, payload)
            logger.info("job_ok", job=str(job_id), type=str(job_type))
        except Exception as exc:  # noqa: BLE001
            logger.warning("job_error", job=str(job_id), type=str(job_type), error=str(exc)[:200])
            await _mark_retry_or_fail(job_id, str(exc), retry_after=retry_after_seconds(exc))

    logger.info("worker_runner_stopped")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
