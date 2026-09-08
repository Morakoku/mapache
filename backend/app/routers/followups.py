"""Seguimientos programados: la agenda del CRM.

Vienen de una secuencia o los crea el usuario a mano. La lista con
`status=PENDING&due_before=hoy` es la pantalla de "qué tengo que hacer hoy".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import JobType
from app.core.idempotency import require_idempotency
from app.models.sequence import FollowUp
from app.schemas.common import JobAcceptedOut, Page
from app.schemas.sequence import FollowUpIn, FollowUpOut, FollowUpUpdate
from app.services.job_svc import JobService
from app.services.sequence_svc import FollowUpRepository, FollowUpService
from app.workers.followup_worker import SKIP_LABELS

router = APIRouter(dependencies=[Depends(require_idempotency)])


def _to_out(follow_up: FollowUp) -> FollowUpOut:
    out = FollowUpOut.model_validate(follow_up)
    # El motivo técnico se traduce a algo que el usuario entienda sin manual.
    out.skip_label = SKIP_LABELS.get(out.skip_reason or "", out.skip_reason)

    lead = follow_up.lead
    out.company_name = lead.company.name
    out.contact_name = lead.contact.display_name if lead.contact else None
    out.stage_name = lead.stage.name
    out.stage_type = lead.stage.stage_type.value
    return out


@router.get("", response_model=Page[FollowUpOut])
async def list_follow_ups(
    follow_up_status: str | None = Query(default=None, alias="status"),
    lead_id: uuid.UUID | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[FollowUpOut]:
    service = FollowUpService(db)
    stmt = service.build_list_query(
        status=follow_up_status, lead_id=lead_id, due_before=due_before, due_after=due_after
    )
    items, total = await FollowUpRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([_to_out(f) for f in items], total, page, size)


@router.post("", response_model=FollowUpOut, status_code=status.HTTP_201_CREATED)
async def create_follow_up(
    payload: FollowUpIn,
    db: AsyncSession | None = Depends(get_db),
) -> FollowUpOut:
    """Seguimiento manual: "recuérdame escribirle el martes"."""
    follow_up = await FollowUpService(db).create_manual(payload.model_dump())
    await db.commit()
    return _to_out(follow_up)


@router.patch("/{follow_up_id}", response_model=FollowUpOut)
async def update_follow_up(
    follow_up_id: uuid.UUID,
    payload: FollowUpUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> FollowUpOut:
    follow_up = await FollowUpService(db).update(
        follow_up_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return _to_out(follow_up)


@router.post("/{follow_up_id}/skip", response_model=FollowUpOut)
async def skip_follow_up(
    follow_up_id: uuid.UUID,
    db: AsyncSession | None = Depends(get_db),
) -> FollowUpOut:
    """Salta este envío pero deja viva la secuencia."""
    follow_up = await FollowUpService(db).skip(follow_up_id)
    await db.commit()
    return _to_out(follow_up)


@router.delete("/{follow_up_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_follow_up(follow_up_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    """Cancela el seguimiento. No se borra: queda el rastro de que existió."""
    await FollowUpService(db).cancel(follow_up_id)
    await db.commit()


@router.post("/run", response_model=JobAcceptedOut, status_code=status.HTTP_202_ACCEPTED)
async def run_now(db: AsyncSession | None = Depends(get_db)) -> JobAcceptedOut:
    """Ejecuta la tanda de seguimientos vencidos ahora mismo.

    En marcha normal esto lo dispara el planificador cada 15 minutos; el
    endpoint existe para no tener que esperar al probar.
    """
    payload: dict[str, int] = {}
    job = await JobService(db).create(JobType.FOLLOWUP_TICK, payload)
    await db.commit()

    await get_job_queue().enqueue(JobType.FOLLOWUP_TICK, payload, job_id=job.id)
    return JobAcceptedOut(job_id=job.id, message="Revisando los seguimientos vencidos.")
