"""Gestión del estado de los jobs en background.

El estado vive en la tabla `jobs`, no en memoria. Así un reinicio del proceso
no deja al usuario mirando una barra de progreso que ya no avanza, y se puede
rescatar lo que quedó a medias.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import func as sa_func
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import JobStatus, JobType
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.job import Job
from app.repositories.base import BaseRepository

logger = get_logger(__name__)


def compute_backoff(
    attempts: int,
    *,
    base_seconds: float = 2.0,
    max_seconds: float = 600.0,
    jitter: float = 0.3,
    rng: random.Random | Any | None = None,
) -> float:
    """Retardo para el siguiente intento: exponencial, con techo y jitter.

    `base_seconds * 2^(attempts-1)`, acotado a `max_seconds` y multiplicado por
    un factor aleatorio en `[1-jitter, 1+jitter]`. El jitter evita que varios
    jobs que fallan a la vez vuelvan a martillear el proveedor en el mismo
    instante. `rng` permite inyectar un `random.Random` sembrado en tests.
    """
    intento = max(1, int(attempts))
    espera = min(max_seconds, base_seconds * (2 ** (intento - 1)))
    aleatorio = rng or random
    factor = 1.0 + aleatorio.uniform(-jitter, jitter)
    return max(0.0, espera * factor)


def retry_after_seconds(exc: BaseException) -> float | None:
    """Extrae los segundos de un `Retry-After`/429, si los hay.

    Cubre la excepción de dominio (`RateLimitedError.retry_after`) y una
    respuesta HTTP cruda de `httpx` (cabecera `Retry-After`, en segundos o en
    fecha HTTP). Devuelve None si el error no trae pista de cuándo reintentar.
    """
    valor = getattr(exc, "retry_after", None)
    if valor is not None:
        try:
            return max(0.0, float(valor))
        except (TypeError, ValueError):
            return None

    respuesta = getattr(exc, "response", None)
    headers = getattr(respuesta, "headers", None)
    if not headers:
        return None
    cabecera = headers.get("Retry-After") or headers.get("retry-after")
    if not cabecera:
        return None
    try:
        return max(0.0, float(cabecera))
    except (TypeError, ValueError):
        try:
            delta = parsedate_to_datetime(str(cabecera)) - datetime.now(UTC)
            return max(0.0, delta.total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


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

    async def mark_running(self, job_id: uuid.UUID, *, worker_id: str | None = None) -> None:
        ahora = datetime.now(UTC)
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.RUNNING,
                started_at=ahora,
                locked_at=ahora,
                heartbeat_at=ahora,
                locked_by=worker_id,
                attempts=Job.attempts + 1,
            )
        )

    async def heartbeat(self, job_id: uuid.UUID, *, worker_id: str | None = None) -> None:
        """Marca que el worker sigue vivo. Sin esto, otro worker lo reclamaría."""
        values: dict[str, Any] = {"heartbeat_at": datetime.now(UTC)}
        if worker_id is not None:
            values["locked_by"] = worker_id
        await self.session.execute(update(Job).where(Job.id == job_id).values(**values))

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

    async def schedule_retry(
        self,
        job_id: uuid.UUID,
        error: str,
        *,
        retry_after: float | None = None,
        now: datetime | None = None,
        rng: random.Random | Any | None = None,
    ) -> bool:
        """Reencola un job fallido con backoff. Devuelve False si queda FAILED.

        Dos caminos:
          - `retry_after` (429/Retry-After): el proveedor ya dice cuándo volver.
            Se reencola para esa fecha y NO se gasta intento.
          - fallo normal: backoff exponencial con jitter mientras queden
            intentos; agotados, se marca FAILED.

        No toca el resultado; el handler es responsable de marcarlo si terminó.
        """
        job = await self.session.get(Job, job_id)
        if job is None:
            return False

        settings = get_settings()
        momento = now or datetime.now(UTC)
        job.error_message = error[:2000]
        limite = job.max_attempts or settings.job_max_attempts

        def _requeue(cuando: datetime) -> None:
            job.status = JobStatus.QUEUED
            job.scheduled_at = cuando
            job.started_at = None
            job.locked_at = None
            job.locked_by = None
            job.heartbeat_at = None

        if retry_after is not None:
            # El intento ya se contó al reclamar; aquí lo devolvemos.
            job.attempts = max(0, (job.attempts or 1) - 1)
            espera = max(0.0, float(retry_after))
            _requeue(momento + timedelta(seconds=espera))
            await self.session.flush()
            logger.info(
                "job_requeued_retry_after",
                job_id=str(job_id),
                retry_after=espera,
                attempts=job.attempts,
            )
            return True

        if (job.attempts or 0) < limite:
            espera = compute_backoff(
                job.attempts or 1,
                base_seconds=settings.job_backoff_base_seconds,
                max_seconds=settings.job_backoff_max_seconds,
                jitter=settings.job_backoff_jitter,
                rng=rng,
            )
            _requeue(momento + timedelta(seconds=espera))
            await self.session.flush()
            logger.info(
                "job_requeued_backoff",
                job_id=str(job_id),
                delay_seconds=round(espera, 2),
                attempts=job.attempts,
            )
            return True

        job.status = JobStatus.FAILED
        job.finished_at = momento
        job.locked_at = None
        job.locked_by = None
        job.heartbeat_at = None
        await self.session.flush()
        logger.warning("job_failed_permanent", job_id=str(job_id), attempts=job.attempts)
        return False

    async def recover_stale(
        self,
        *,
        timeout_seconds: float | None = None,
        now: datetime | None = None,
    ) -> int:
        """Devuelve a la cola los jobs RUNNING cuyo lease venció.

        `timeout_seconds=None` reencola *todos* los RUNNING (arranque de un
        worker: cualquier cosa corriendo es de un proceso anterior). Con un
        timeout, solo reencola los huérfanos: heartbeat/locked_at/started_at
        más viejo que `now - timeout`. Un job sin ninguna referencia de lease
        también se considera huérfano.
        """
        momento = now or datetime.now(UTC)
        stmt = select(Job.id).where(Job.status == JobStatus.RUNNING)
        mensaje = "Reencolado tras reinicio del servicio"

        if timeout_seconds is not None:
            corte = momento - timedelta(seconds=timeout_seconds)
            lease = sa_func.coalesce(Job.heartbeat_at, Job.locked_at, Job.started_at)
            stmt = stmt.where(or_(lease < corte, lease.is_(None)))
            mensaje = "Reencolado: heartbeat del worker vencido"

        stale_ids = [row[0] for row in await self.session.execute(stmt)]
        if not stale_ids:
            return 0

        await self.session.execute(
            update(Job)
            .where(Job.id.in_(stale_ids))
            .values(
                status=JobStatus.QUEUED,
                scheduled_at=momento,
                progress_message=mensaje,
                started_at=None,
                locked_at=None,
                locked_by=None,
                heartbeat_at=None,
            )
        )
        logger.warning("jobs_recovered_after_restart", count=len(stale_ids))
        return len(stale_ids)
