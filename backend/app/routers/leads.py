"""Módulo 7 — prospectos."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import ActivityType, ActorType, JobType, LeadStatus
from app.models.lead import Lead, LeadStageHistory
from app.schemas.common import JobAcceptedOut, Page
from app.schemas.crm import (
    ActivityOut,
    LeadBulkIn,
    LeadBulkResultOut,
    LeadBulkStageIn,
    LeadDetailOut,
    LeadEngagementOut,
    LeadIn,
    LeadLoseIn,
    LeadOut,
    LeadRescoreIn,
    LeadStageIn,
    LeadUpdate,
    LeadWinIn,
    NoteIn,
    SegmentSummaryOut,
    StageHistoryOut,
)
from app.services.activity_svc import ActivityService
from app.services.job_svc import JobService
from app.services.lead_svc import SEGMENTS, LeadRepository, LeadService
from app.services.scoring_svc import ScoringService

router = APIRouter()


@router.get("", response_model=Page[LeadOut])
async def list_leads(
    stage_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
    lead_status: LeadStatus | None = Query(default=None, alias="status"),
    min_score: int | None = Query(default=None, ge=0, le=100),
    engagement: str | None = Query(default=None, description="FRIO|BAJO|MEDIO|ALTO"),
    has_email: bool | None = None,
    q: str | None = None,
    followup_before: datetime | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[LeadOut]:
    service = LeadService(db)
    stmt = service.build_list_query(
        stage_id=stage_id,
        service_id=service_id,
        status=lead_status,
        min_score=min_score,
        engagement=engagement,
        has_email=has_email,
        q=q,
        followup_before=followup_before,
    )
    items, total = await LeadRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([LeadOut.model_validate(x) for x in items], total, page, size)


@router.get("/segments/summary", response_model=SegmentSummaryOut)
async def segment_summary(
    service_id: uuid.UUID | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> SegmentSummaryOut:
    """Cuántos prospectos hay en cada segmento de seguimiento."""
    service = LeadService(db)
    counts: dict[str, int] = {}
    for segment in SEGMENTS:
        stmt = service.build_segment_query(segment, service_id=service_id)
        _, total = await service.paginate_segment(stmt, page=1, size=1)
        counts[segment] = total
    return SegmentSummaryOut(**counts)


@router.get("/segments/{segment}", response_model=Page[LeadEngagementOut])
async def list_segment(
    segment: str,
    min_days: int = Query(default=0, ge=0, le=365),
    service_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[LeadEngagementOut]:
    """Prospectos por lo que hicieron con el correo.

    `opened_no_reply` es el que se usa a diario: **abrieron y no respondieron**.
    Interés demostrado sin conversación abierta.

    `min_days` filtra por antigüedad de la apertura: con `min_days=2` se ve a
    quien abrió hace dos días o más y sigue callado —escribir el mismo día
    suele ser prematuro—.
    """
    service = LeadService(db)
    stmt = service.build_segment_query(segment, min_days=min_days, service_id=service_id)
    rows, total = await service.paginate_segment(stmt, page=page, size=size)
    return Page.build([_to_engagement(row) for row in rows], total, page, size)


def _to_engagement(row: Any) -> LeadEngagementOut:
    lead = row[0]
    now = datetime.now(UTC)
    days_since_open = (now - row.last_opened_at).days if row.last_opened_at else None
    days_since_contact = (now - row.last_sent_at).days if row.last_sent_at else None

    return LeadEngagementOut(
        lead=LeadOut.model_validate(lead),
        emails_sent=row.emails_sent,
        last_sent_at=row.last_sent_at,
        opens=row.opens,
        last_opened_at=row.last_opened_at,
        clicks=row.clicks,
        last_clicked_at=row.last_clicked_at,
        bounces=row.bounces,
        days_since_open=days_since_open,
        days_since_contact=days_since_contact,
        note=_note(row, days_since_open),
    )


def _note(row: Any, days_since_open: int | None) -> str:
    """Lectura en una línea de lo que hizo el prospecto.

    Se describe lo observado, sin adornarlo: una apertura es una apertura, no
    "está muy interesado".
    """
    if row.bounces:
        return "El correo rebotó. Revisa la dirección antes de insistir."
    if row.clicks:
        return f"Visitó un enlace {row.clicks} vez(ces): señal fuerte, no solo una apertura."
    if not row.opens:
        return "Enviado, sin apertura detectada. Puede ser el asunto o el filtro de spam."

    cuando = "hoy" if not days_since_open else f"hace {days_since_open} día(s)"
    if row.opens >= 3:
        return f"Abrió {row.opens} veces, la última {cuando}, y no respondió."
    return f"Abrió {row.opens} vez(ces), la última {cuando}, y no respondió."


@router.post("", response_model=LeadDetailOut, status_code=status.HTTP_201_CREATED)
async def create_lead(payload: LeadIn, db: AsyncSession | None = Depends(get_db)) -> LeadDetailOut:
    lead = await LeadService(db).create(**payload.model_dump())
    # Se puntúa al crear, no en un job: un prospecto recién dado de alta con
    # score 0 se lee como "malo" cuando en realidad es "sin calcular".
    await ScoringService(db).score_leads([lead.id])
    await db.commit()
    return LeadDetailOut.model_validate(await LeadService(db).get_or_404(lead.id))


@router.post("/bulk", response_model=LeadBulkResultOut, status_code=status.HTTP_201_CREATED)
async def create_leads_bulk(
    payload: LeadBulkIn,
    db: AsyncSession | None = Depends(get_db),
) -> LeadBulkResultOut:
    """Alta en lote desde una selección de empresas.

    Los duplicados se saltan en silencio: seleccionar 50 empresas de las que 3
    ya son prospectos es lo normal, no un error que deba abortar el lote.
    """
    result = await LeadService(db).create_bulk(
        company_ids=payload.company_ids, service_id=payload.service_id
    )
    await ScoringService(db).score_leads([lead.id for lead in result.created])
    await db.commit()
    return LeadBulkResultOut(
        created=len(result.created),
        skipped_existing=result.skipped_existing,
        skipped_no_company=result.skipped_no_company,
        lead_ids=[x.id for x in result.created],
    )


@router.get("/{lead_id}", response_model=LeadDetailOut)
async def get_lead(lead_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> LeadDetailOut:
    return LeadDetailOut.model_validate(await LeadService(db).get_or_404(lead_id))


@router.patch("/{lead_id}", response_model=LeadDetailOut)
async def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> LeadDetailOut:
    service = LeadService(db)
    lead = await service.get_or_404(lead_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(lead, key, value)
    await db.commit()
    return LeadDetailOut.model_validate(await service.get_or_404(lead_id))


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lead(lead_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    service = LeadService(db)
    await service.repo.delete(await service.get_or_404(lead_id))
    await db.commit()


@router.post("/{lead_id}/stage", response_model=LeadDetailOut)
async def move_stage(
    lead_id: uuid.UUID,
    payload: LeadStageIn,
    db: AsyncSession | None = Depends(get_db),
) -> LeadDetailOut:
    """Mueve un prospecto de etapa (drag & drop del Kanban).

    Registra el cambio en el historial y crea una actividad automáticamente.
    """
    service = LeadService(db)
    lead = await service.get_or_404(lead_id)
    await service.move_stage(lead, payload.stage_id, actor=ActorType.USER, reason=payload.reason)
    await db.commit()
    return LeadDetailOut.model_validate(await service.get_or_404(lead_id))


@router.post("/bulk-stage", response_model=dict)
async def move_stage_bulk(
    payload: LeadBulkStageIn,
    db: AsyncSession | None = Depends(get_db),
) -> dict[str, int]:
    service = LeadService(db)
    moved = 0
    for lead_id in payload.lead_ids:
        lead = await service.get_or_404(lead_id)
        await service.move_stage(
            lead, payload.stage_id, actor=ActorType.USER, reason=payload.reason
        )
        moved += 1
    await db.commit()
    return {"moved": moved}


@router.post("/{lead_id}/score", response_model=LeadDetailOut)
async def rescore_lead(lead_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> LeadDetailOut:
    """Recalcula el score de un prospecto ahora mismo.

    Uno solo es barato, así que va en línea: el usuario que pulsa "recalcular"
    en la ficha espera ver el número nuevo, no un job en curso.
    """
    service = LeadService(db)
    await service.get_or_404(lead_id)
    await ScoringService(db).score_leads([lead_id])
    await db.commit()
    return LeadDetailOut.model_validate(await service.get_or_404(lead_id))


@router.post("/score", response_model=JobAcceptedOut, status_code=status.HTTP_202_ACCEPTED)
async def rescore_all(
    payload: LeadRescoreIn | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> JobAcceptedOut:
    """Recálculo en lote. Es lo que hay que lanzar tras cambiar los pesos."""
    lead_ids = [str(x) for x in (payload.lead_ids if payload else [])]
    job_payload: dict[str, Any] = {"lead_ids": lead_ids}

    total = len(lead_ids) or await db.scalar(select(func.count()).select_from(Lead)) or 0
    job = await JobService(db).create(JobType.SCORING, job_payload, progress_total=total)
    await db.commit()

    await get_job_queue().enqueue(JobType.SCORING, job_payload, job_id=job.id)
    return JobAcceptedOut(job_id=job.id, message=f"Recalculando el score de {total} prospectos.")


@router.get("/{lead_id}/timeline", response_model=Page[ActivityOut])
async def lead_timeline(
    lead_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[ActivityOut]:
    await LeadService(db).get_or_404(lead_id)
    activities = ActivityService(db)
    stmt = activities.build_timeline_query(lead_id=lead_id)
    items, total = await activities.repo.paginate(stmt, page=page, size=size)
    return Page.build([ActivityOut.model_validate(a) for a in items], total, page, size)


@router.get("/{lead_id}/history", response_model=list[StageHistoryOut])
async def lead_stage_history(
    lead_id: uuid.UUID,
    db: AsyncSession | None = Depends(get_db),
) -> list[StageHistoryOut]:
    """Recorrido del prospecto por el embudo.

    Es la base del cálculo de velocidad y de la conversión etapa a etapa.
    """
    await LeadService(db).get_or_404(lead_id)
    result = await db.execute(
        select(LeadStageHistory)
        .where(LeadStageHistory.lead_id == lead_id)
        .order_by(LeadStageHistory.entered_at)
    )
    return [StageHistoryOut.model_validate(h) for h in result.scalars().all()]


@router.post("/{lead_id}/note", response_model=ActivityOut, status_code=status.HTTP_201_CREATED)
async def add_note(
    lead_id: uuid.UUID,
    payload: NoteIn,
    db: AsyncSession | None = Depends(get_db),
) -> ActivityOut:
    service = LeadService(db)
    lead = await service.get_or_404(lead_id)
    activity = await ActivityService(db).record(
        ActivityType.NOTE,
        title="Nota",
        description=payload.text,
        lead_id=lead.id,
        company_id=lead.company_id,
        actor=ActorType.USER,
    )
    await db.commit()
    await db.refresh(activity)
    return ActivityOut.model_validate(activity)


@router.post("/{lead_id}/win", response_model=LeadDetailOut)
async def win_lead(
    lead_id: uuid.UUID,
    payload: LeadWinIn,
    db: AsyncSession | None = Depends(get_db),
) -> LeadDetailOut:
    service = LeadService(db)
    lead = await service.get_or_404(lead_id)
    await service.mark_won(lead, value=payload.value, note=payload.note)
    await db.commit()
    return LeadDetailOut.model_validate(await service.get_or_404(lead_id))


@router.post("/{lead_id}/lose", response_model=LeadDetailOut)
async def lose_lead(
    lead_id: uuid.UUID,
    payload: LeadLoseIn,
    db: AsyncSession | None = Depends(get_db),
) -> LeadDetailOut:
    service = LeadService(db)
    lead = await service.get_or_404(lead_id)
    await service.mark_lost(lead, reason=payload.reason)
    await db.commit()
    return LeadDetailOut.model_validate(await service.get_or_404(lead_id))
