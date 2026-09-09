"""Seguimientos programados: la agenda del CRM.

Vienen de una secuencia o los crea el usuario a mano. La lista con
`status=QUEUED` es la pantalla de "qué hay que enviar ahora".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import JobType
from app.core.exceptions import NotFoundError
from app.core.idempotency import require_idempotency
from app.models.sequence import FollowUp
from app.schemas.common import JobAcceptedOut, Page
from app.schemas.sequence import FollowUpIn, FollowUpOut, FollowUpUpdate
from app.services.job_svc import JobService
from app.services.sequence_svc import FollowUpRepository, FollowUpService
from app.workers.followup_worker import SKIP_LABELS

router = APIRouter(dependencies=[Depends(require_idempotency)])


def _pg_table() -> str:
    return "follow_ups"


def _to_out(follow_up: FollowUp) -> FollowUpOut:
    """Cuando viene de SQLAlchemy — con los joins cargados."""
    out = FollowUpOut.model_validate(follow_up)
    out.skip_label = SKIP_LABELS.get(out.skip_reason or "", out.skip_reason)
    if follow_up.lead_id is not None:
        out.stage_name = (out.stage_name or "")
    return out


def _to_out_pg(item: dict) -> FollowUpOut:
    """Cuando viene de PostgREST: sin joins, datos planos."""
    return FollowUpOut(
        id=item["id"],
        lead_id=item["lead_id"],
        scheduled_at=item["scheduled_at"],
        status=item["status"],
        note=item.get("note"),
        skip_reason=item.get("skip_reason"),
        skip_label=SKIP_LABELS.get(item.get("skip_reason") or "", item.get("skip_reason")),
        created_at=item.get("created_at"),
        company_name="",
        contact_name=None,
        stage_name=None,
        stage_type=None,
    )


# ------------------------------------------------------------------ list


@router.get("", response_model=Page[FollowUpOut])
async def list_follow_ups(
    follow_up_status: str | None = Query(default=None, alias="status"),
    lead_id: uuid.UUID | None = None,
    due_before: datetime | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[FollowUpOut]:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select

    settings = get_settings()
    if db is None or settings.use_postgrest:
        filters: dict[str, str] = {}
        if follow_up_status is not None:
            filters["status"] = follow_up_status
        if lead_id is not None:
            filters["lead_id"] = str(lead_id)
        if due_before is not None:
            filters["scheduled_at"] = f"lt.{due_before.isoformat()}"
        items = await pg_select(_pg_table(), filters=filters if filters else None, limit=size)
        return Page.build([_to_out_pg(f) for f in items], len(items), page, size)

    service = FollowUpService(db)
    stmt = service.build_list_query(status=follow_up_status, lead_id=lead_id, due_before=due_before)
    items, total = await FollowUpRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([_to_out(f) for f in items], total, page, size)


# ------------------------------------------------------------------ create


@router.post("", response_model=FollowUpOut, status_code=status.HTTP_201_CREATED)
async def create_follow_up(payload: FollowUpIn, db: AsyncSession | None = Depends(get_db)) -> FollowUpOut:
    """Seguimiento manual: "recuérdame escribirle el martes"."""
    from app.core.config import get_settings
    from app.core.supabase_http import insert as pg_insert

    settings = get_settings()
    if db is None or settings.use_postgrest:
        data = dict(payload.model_dump())
        data["id"] = data.get("id") or str(uuid.uuid4())
        result = await pg_insert(_pg_table(), data)
        if not result:
            raise HTTPException(status_code=500, detail="No se pudo crear el seguimiento")
        return _to_out_pg(result[0])

    follow_up = await FollowUpService(db).create_manual(payload.model_dump())
    await db.commit()
    return _to_out(follow_up)


# ------------------------------------------------------------------ update


@router.patch("/{follow_up_id}", response_model=FollowUpOut)
async def update_follow_up(
    follow_up_id: uuid.UUID,
    payload: FollowUpUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> FollowUpOut:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update

    settings = get_settings()
    if db is None or settings.use_postgrest:
        existing = await pg_select(_pg_table(), filters={"id": str(follow_up_id)}, limit=1)
        if not existing:
            raise NotFoundError.for_entity("follow_up", follow_up_id)

        data = payload.model_dump(exclude_unset=True)
        result = await pg_update(_pg_table(), {"id": str(follow_up_id)}, data)
        if not result:
            raise NotFoundError.for_entity("follow_up", follow_up_id)
        return _to_out_pg(result[0])

    follow_up = await FollowUpService(db).update(follow_up_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    return _to_out(follow_up)


# ------------------------------------------------------------------ skip


@router.post("/{follow_up_id}/skip", response_model=FollowUpOut)
async def skip_follow_up(follow_up_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> FollowUpOut:
    """Salta este envío pero deja viva la secuencia."""
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update

    settings = get_settings()
    if db is None or settings.use_postgrest:
        existing = await pg_select(_pg_table(), filters={"id": str(follow_up_id)}, limit=1)
        if not existing:
            raise NotFoundError.for_entity("follow_up", follow_up_id)

        result = await pg_update(
            _pg_table(),
            {"id": str(follow_up_id)},
            {"status": "SKIPPED", "skip_reason": "user_skip"},
        )
        if not result:
            raise NotFoundError.for_entity("follow_up", follow_up_id)
        return _to_out_pg(result[0])

    follow_up = await FollowUpService(db).skip(follow_up_id)
    await db.commit()
    return _to_out(follow_up)


# ------------------------------------------------------------------ cancel


@router.delete("/{follow_up_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_follow_up(follow_up_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    """Cancela el seguimiento. No se borra: queda el rastro de que existió."""
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update

    settings = get_settings()
    if db is None or settings.use_postgrest:
        existing = await pg_select(_pg_table(), filters={"id": str(follow_up_id)}, limit=1)
        if not existing:
            raise NotFoundError.for_entity("follow_up", follow_up_id)
        await pg_update(_pg_table(), {"id": str(follow_up_id)}, {"status": "CANCELLED"})
        return

    await FollowUpService(db).cancel(follow_up_id)
    await db.commit()


# ------------------------------------------------------------------ run now (job)


@router.post("/run", response_model=JobAcceptedOut, status_code=status.HTTP_202_ACCEPTED)
async def run_now(db: AsyncSession | None = Depends(get_db)) -> JobAcceptedOut:
    """Ejecuta la tanda de seguimientos vencidos ahora mismo.

    En marcha normal esto lo dispara el planificador cada 15 minutos; el
    endpoint existe para no tener que esperar al probar.
    """
    from app.core.config import get_settings
    from app.core.supabase_http import insert as pg_insert

    settings = get_settings()
    if db is None or settings.use_postgrest:
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        data = {
            "id": job_id,
            "job_type": JobType.FOLLOWUP_TICK.value,
            "payload": "{}",
            "status": "QUEUED",
            "progress_total": 0,
            "created_at": now,
        }
        result = await pg_insert("jobs", data)
        if result:
            await get_job_queue().enqueue(JobType.FOLLOWUP_TICK, {}, job_id=job_id)
            return JobAcceptedOut(job_id=job_id, message="Revisando los seguimientos vencidos.")
        raise HTTPException(status_code=500, detail="No se pudo crear el job")

    payload: dict[str, int] = {}
    job = await JobService(db).create(JobType.FOLLOWUP_TICK, payload)
    await db.commit()

    await get_job_queue().enqueue(JobType.FOLLOWUP_TICK, payload, job_id=job.id)
    return JobAcceptedOut(job_id=job.id, message="Revisando los seguimientos vencidos.")
