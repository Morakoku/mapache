"""Gestión del estado de los jobs en background.

El estado vive en la tabla `jobs`, no en memoria. Así un reinicio del proceso
no deja al usuario mirando una barra de progreso que ya no avanza, y se puede
rescatar lo que quedó a medias.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobStatus, JobType
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.job import Job
from app.repositories.base import BaseRepository

logger = get_logger(__name__)


class JobRepository(BaseRepository[Job]):
    model = Job


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = JobRepository(session)

    async def create(
        self,
        job_type: JobType,
        payload: dict[str, Any],
        *,
        owner_id: uuid.UUID | None = None,
        progress_total: int | None = None,
    ) -> Job:
        job = Job(
            job_type=job_type,
            payload=payload,
            owner_id=owner_id,
            progress_total=progress_total,
            status=JobStatus.QUEUED,
        )
        await self.repo.add(job)
        return job

    async def get_or_404(self, job_id: uuid.UUID) -> Job:
        job = await self.repo.get(job_id)
        if job is None:
            raise NotFoundError.for_entity("job", job_id)
        return job

    async def mark_running(self, job_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.RUNNING,
                started_at=datetime.now(UTC),
                attempts=Job.attempts + 1,
            )
        )

    async def update_progress(
        self,
        job_id: uuid.UUID,
        *,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        values: dict[str, Any] = {}
        if current is not None:
            values["progress_current"] = current
        if total is not None:
            values["progress_total"] = total
        if message is not None:
            values["progress_message"] = message
        if values:
            await self.session.execute(update(Job).where(Job.id == job_id).values(**values))

    async def mark_completed(self, job_id: uuid.UUID, result: dict[str, Any] | None = None) -> None:
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.COMPLETED,
                result=result or {},
                finished_at=datetime.now(UTC),
            )
        )

    async def mark_failed(self, job_id: uuid.UUID, error: str) -> None:
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.FAILED,
                # Truncado: un traceback entero en una columna de texto no
                # aporta nada a la UI y sí llena la tabla.
                error_message=error[:2000],
                finished_at=datetime.now(UTC),
            )
        )

    async def mark_cancelled(self, job_id: uuid.UUID) -> None:
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            .values(status=JobStatus.CANCELLED, finished_at=datetime.now(UTC))
        )

    async def recover_stale(self) -> int:
        """Devuelve a la cola los jobs que quedaron RUNNING tras un reinicio.

        `InProcessQueue` vive en el proceso, así que un reinicio deja huérfano
        todo lo que estuviera corriendo. Sin este rescate se quedan en RUNNING
        para siempre y bloquean visualmente la UI.
        """
        result = await self.session.execute(select(Job.id).where(Job.status == JobStatus.RUNNING))
        stale_ids = [row[0] for row in result]
        if not stale_ids:
            return 0

        await self.session.execute(
            update(Job)
            .where(Job.id.in_(stale_ids))
            .values(
                status=JobStatus.QUEUED,
                progress_message="Reencolado tras reinicio del servicio",
                started_at=None,
            )
        )
        logger.warning("jobs_recovered_after_restart", count=len(stale_ids))
        return len(stale_ids)
