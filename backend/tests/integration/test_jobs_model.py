"""Verifica que el pipeline modelo -> ENUM nativo -> JSONB funciona de verdad.

Es el test que demuestra que la Fase 1 está montada: si los ENUM de Postgres
o los defaults del servidor estuvieran mal declarados, esto falla aquí y no
tres fases más adelante.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobStatus, JobType
from app.models.job import Job


async def test_job_roundtrip_with_server_defaults(db: AsyncSession) -> None:
    job = Job(job_type=JobType.DISCOVERY, payload={"search_id": "abc", "limit": 100})
    db.add(job)
    await db.flush()
    await db.refresh(job)

    assert job.id is not None, "la PK la genera el servidor con gen_random_uuid()"
    assert job.created_at is not None
    assert job.updated_at is not None
    assert job.status is JobStatus.QUEUED, "server_default de status"
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.owner_id is None, "MVP monousuario: owner_id nullable (D12)"
    assert job.payload == {"search_id": "abc", "limit": 100}


async def test_job_enum_is_queryable(db: AsyncSession) -> None:
    db.add_all(
        [
            Job(job_type=JobType.DISCOVERY, payload={}),
            Job(job_type=JobType.ENRICHMENT, payload={}, status=JobStatus.COMPLETED),
        ]
    )
    await db.flush()

    result = await db.execute(select(Job).where(Job.status == JobStatus.QUEUED))
    pending = result.scalars().all()

    assert len(pending) == 1
    assert pending[0].job_type is JobType.DISCOVERY


async def test_progress_pct_is_none_without_total(db: AsyncSession) -> None:
    job = Job(job_type=JobType.SCORING, payload={}, progress_current=7)
    db.add(job)
    await db.flush()

    assert job.progress_pct is None, "sin total no se puede calcular un porcentaje"

    job.progress_total = 28
    assert job.progress_pct == 25


async def test_can_retry_only_when_failed_under_max_attempts(db: AsyncSession) -> None:
    job = Job(job_type=JobType.SEND_BATCH, payload={}, status=JobStatus.FAILED, attempts=1)
    db.add(job)
    await db.flush()

    assert job.can_retry is True

    job.attempts = 3
    assert job.can_retry is False, "agotados los intentos, no se reintenta"

    job.status = JobStatus.COMPLETED
    job.attempts = 0
    assert job.can_retry is False, "un job completado no se reintenta"
