"""Resiliencia de la cola de jobs (Fase 2).

Cubre las piezas puras (backoff, lectura de Retry-After) y, contra el Postgres
de test, el reclaim de RUNNING huérfanos y la reprogramación con backoff.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobStatus, JobType
from app.core.exceptions import RateLimitedError
from app.models.job import Job
from app.services.job_svc import JobService, compute_backoff, retry_after_seconds

# --------------------------------------------------------------- backoff


def test_backoff_exponencial_sin_jitter() -> None:
    assert compute_backoff(1, base_seconds=2, max_seconds=600, jitter=0) == 2
    assert compute_backoff(2, base_seconds=2, max_seconds=600, jitter=0) == 4
    assert compute_backoff(3, base_seconds=2, max_seconds=600, jitter=0) == 8


def test_backoff_respeta_el_techo() -> None:
    assert compute_backoff(20, base_seconds=2, max_seconds=600, jitter=0) == 600


def test_backoff_jitter_dentro_del_rango() -> None:
    rng = random.Random(1234)
    for intento in range(1, 9):
        espera = compute_backoff(intento, base_seconds=2, max_seconds=600, jitter=0.3, rng=rng)
        base = min(600, 2 * 2 ** (intento - 1))
        assert base * 0.7 - 1e-9 <= espera <= base * 1.3 + 1e-9


def test_backoff_nunca_es_negativo() -> None:
    class _RngAbajo:
        def uniform(self, a: float, b: float) -> float:
            return -1e9

    assert compute_backoff(3, jitter=1.0, rng=_RngAbajo()) == 0.0


# ----------------------------------------------------------- retry-after


def test_retry_after_desde_rate_limited() -> None:
    assert retry_after_seconds(RateLimitedError("cupo", retry_after=42)) == 42


def test_retry_after_desde_cabecera_http() -> None:
    class _Respuesta:
        def __init__(self) -> None:
            self.headers = {"Retry-After": "30"}

    class _Error(Exception):
        response = _Respuesta()

    assert retry_after_seconds(_Error()) == 30


def test_retry_after_ausente_es_none() -> None:
    assert retry_after_seconds(RuntimeError("boom")) is None


# --------------------------------------------------------------- helpers


async def _crear_running(
    db: AsyncSession,
    *,
    heartbeat_at: datetime | None,
    locked_at: datetime | None = None,
    attempts: int = 1,
    max_attempts: int = 3,
) -> Job:
    job = Job(
        job_type=JobType.FOLLOWUP_TICK,
        payload={},
        status=JobStatus.RUNNING,
        attempts=attempts,
        max_attempts=max_attempts,
    )
    job.started_at = heartbeat_at
    job.locked_at = locked_at if locked_at is not None else heartbeat_at
    job.heartbeat_at = heartbeat_at
    db.add(job)
    await db.flush()
    return job


# --------------------------------------------------------------- reclaim


async def test_recover_stale_solo_reencola_los_vencidos(db: AsyncSession) -> None:
    ahora = datetime.now(UTC)
    viejo = await _crear_running(db, heartbeat_at=ahora - timedelta(minutes=10))
    fresco = await _crear_running(db, heartbeat_at=ahora - timedelta(seconds=5))

    n = await JobService(db).recover_stale(timeout_seconds=60, now=ahora)

    assert n == 1
    await db.refresh(viejo)
    await db.refresh(fresco)
    assert viejo.status is JobStatus.QUEUED
    assert viejo.heartbeat_at is None
    assert viejo.locked_at is None
    assert viejo.locked_by is None
    assert viejo.started_at is None
    assert fresco.status is JobStatus.RUNNING


async def test_recover_stale_sin_timeout_reencola_todos(db: AsyncSession) -> None:
    ahora = datetime.now(UTC)
    a = await _crear_running(db, heartbeat_at=ahora)
    b = await _crear_running(db, heartbeat_at=ahora)

    assert await JobService(db).recover_stale(now=ahora) == 2
    await db.refresh(a)
    await db.refresh(b)
    assert a.status is JobStatus.QUEUED
    assert b.status is JobStatus.QUEUED


async def test_recover_stale_usa_locked_at_si_no_hay_heartbeat(db: AsyncSession) -> None:
    ahora = datetime.now(UTC)
    job = await _crear_running(
        db,
        heartbeat_at=None,
        locked_at=ahora - timedelta(minutes=10),
    )
    assert await JobService(db).recover_stale(timeout_seconds=60, now=ahora) == 1
    await db.refresh(job)
    assert job.status is JobStatus.QUEUED


async def test_recover_stale_sin_lease_tambien_es_huerfano(db: AsyncSession) -> None:
    ahora = datetime.now(UTC)
    job = await _crear_running(db, heartbeat_at=None, locked_at=None)
    assert await JobService(db).recover_stale(timeout_seconds=60, now=ahora) == 1
    await db.refresh(job)
    assert job.status is JobStatus.QUEUED


# -------------------------------------------------------- schedule_retry


async def test_schedule_retry_backoff_reencola_en_el_futuro(db: AsyncSession) -> None:
    ahora = datetime.now(UTC)
    job = await _crear_running(db, heartbeat_at=ahora, attempts=1)

    reencolado = await JobService(db).schedule_retry(
        job.id, "fallo temporal", now=ahora, rng=random.Random(0)
    )

    assert reencolado is True
    await db.refresh(job)
    assert job.status is JobStatus.QUEUED
    assert job.scheduled_at > ahora
    assert job.heartbeat_at is None
    assert job.locked_at is None
    assert job.error_message == "fallo temporal"


async def test_schedule_retry_429_no_quema_intento(db: AsyncSession) -> None:
    ahora = datetime.now(UTC)
    job = await _crear_running(db, heartbeat_at=ahora, attempts=2, max_attempts=3)

    reencolado = await JobService(db).schedule_retry(
        job.id, "rate limit", retry_after=120, now=ahora
    )

    assert reencolado is True
    await db.refresh(job)
    assert job.status is JobStatus.QUEUED
    assert job.attempts == 1, "el 429 no debe gastar un intento"
    assert job.scheduled_at == ahora + timedelta(seconds=120)


async def test_schedule_retry_agotado_queda_failed(db: AsyncSession) -> None:
    ahora = datetime.now(UTC)
    job = await _crear_running(db, heartbeat_at=ahora, attempts=3, max_attempts=3)

    reencolado = await JobService(db).schedule_retry(job.id, "sin cupo", now=ahora)

    assert reencolado is False
    await db.refresh(job)
    assert job.status is JobStatus.FAILED
    assert job.finished_at == ahora
