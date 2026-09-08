"""Estado de los procesos en background."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import JobStatus, JobType
from app.core.idempotency import require_idempotency
from app.models.job import Job
from app.schemas.common import JobAcceptedOut, Page
from app.schemas.prospecting import JobOut
from app.services.job_svc import JobRepository, JobService

router = APIRouter(dependencies=[Depends(require_idempotency)])


@router.get("", response_model=Page[JobOut])
async def list_jobs(
    status_filter: JobStatus | None = Query(default=None, alias="status"),
    job_type: JobType | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[JobOut]:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        filters: dict[str, str] = {}
        if status_filter is not None:
            filters["status"] = status_filter.value
        if job_type is not None:
            filters["job_type"] = job_type.value
        items = await pg_select("jobs", filters=filters if filters else None, limit=size)
        total = len(items)
        return Page.build([JobOut.model_validate(j) for j in items], total, page, size)

    stmt = select(Job).order_by(Job.created_at.desc())
    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter)
    if job_type is not None:
        stmt = stmt.where(Job.job_type == job_type)

    items, total = await JobRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([JobOut.model_validate(j) for j in items], total, page, size)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession | None = Depends(get_db),
) -> JobOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        items = await pg_select("jobs", filters={"id": str(job_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("job", job_id)
        return JobOut.model_validate(items[0])

    return JobOut.model_validate(await JobService(db).get_or_404(job_id))


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession | None = Depends(get_db),
) -> JobOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select
        from app.core.supabase_http import update as pg_update

        items = await pg_select("jobs", filters={"id": str(job_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("job", job_id)
        job_data = items[0]
        result = await pg_update("jobs", {"id": str(job_id)}, {"status": "CANCELLED"})
        if result:
            return JobOut.model_validate(result[0])
        raise NotFoundError.for_entity("job", job_id)

    service = JobService(db)
    job = await service.get_or_404(job_id)

    await get_job_queue().cancel(job_id)
    await service.mark_cancelled(job_id)
    await db.commit()
    await db.refresh(job)
    return JobOut.model_validate(job)


@router.post(
    "/provider-health",
    response_model=JobAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def check_provider_health(
    db: AsyncSession | None = Depends(get_db),
) -> JobAcceptedOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import insert as pg_insert
        from app.core.supabase_http import select as pg_select
        from uuid import uuid4

        job_id = str(uuid4())
        data = {
            "id": job_id,
            "job_type": JobType.SCRAPER_HEALTH.value,
            "payload": {"provider": "google_maps_scraper"},
            "status": "QUEUED",
            "created_at": datetime.now(UTC).isoformat(),
        }
        result = await pg_insert("jobs", data)
        if result:
            return JobAcceptedOut(job_id=job_id, message="Comprobando el proveedor de búsqueda.")
        raise HTTPException(status_code=500, detail="No se pudo crear el job")

    payload = {"provider": "google_maps_scraper"}
    job = await JobService(db).create(JobType.SCRAPER_HEALTH, payload)
    await db.commit()

    await get_job_queue().enqueue(JobType.SCRAPER_HEALTH, payload, job_id=job.id)
    return JobAcceptedOut(job_id=job.id, message="Comprobando el proveedor de búsqueda.")
