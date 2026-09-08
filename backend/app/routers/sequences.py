"""Módulo 18 — secuencias de seguimiento.

Inscribir es un acto explícito y en dos tiempos: primero se ve el calendario
(`/preview-schedule`), después se confirma (`/enroll`). Nunca hay envío
automático que el usuario no haya puesto en marcha.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.idempotency import require_idempotency
from app.schemas.sequence import (
    EnrollIn,
    EnrollResultOut,
    PlannedStepOut,
    SchedulePreviewOut,
    SequenceIn,
    SequenceOut,
    SequenceUpdate,
)
from app.services.sequence_svc import SchedulePreview, SequenceService

router = APIRouter(dependencies=[Depends(require_idempotency)])


def _to_preview(preview: SchedulePreview) -> SchedulePreviewOut:
    return SchedulePreviewOut(
        lead_id=preview.lead_id,
        company_name=preview.company_name,
        steps=[
            PlannedStepOut(
                step_number=s.step_number,
                template_id=s.template_id,
                template_name=s.template_name,
                scheduled_at=s.scheduled_at,
                condition=s.condition,
            )
            for s in preview.steps
        ],
        warnings=preview.warnings,
    )


@router.get("", response_model=list[SequenceOut])
async def list_sequences(
    active_only: bool = Query(default=False),
    db: AsyncSession | None = Depends(get_db),
) -> list[SequenceOut]:
    service = SequenceService(db)
    result = await db.execute(service.build_list_query(active_only=active_only))
    return [SequenceOut.model_validate(s) for s in result.scalars().unique().all()]


@router.post("", response_model=SequenceOut, status_code=status.HTTP_201_CREATED)
async def create_sequence(payload: SequenceIn, db: AsyncSession | None = Depends(get_db)) -> SequenceOut:
    data = payload.model_dump()
    data["steps"] = [{**step, "condition": step.get("condition")} for step in data.get("steps", [])]
    sequence = await SequenceService(db).create(data)
    await db.commit()
    return SequenceOut.model_validate(sequence)


@router.get("/{sequence_id}", response_model=SequenceOut)
async def get_sequence(sequence_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> SequenceOut:
    return SequenceOut.model_validate(await SequenceService(db).get_or_404(sequence_id))


@router.patch("/{sequence_id}", response_model=SequenceOut)
async def update_sequence(
    sequence_id: uuid.UUID,
    payload: SequenceUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> SequenceOut:
    """Edita la secuencia.

    Cambiar los pasos no reescribe los seguimientos ya programados: el
    calendario que el usuario confirmó se mantiene.
    """
    sequence = await SequenceService(db).update(sequence_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    return SequenceOut.model_validate(sequence)


@router.delete("/{sequence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sequence(sequence_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    """Borra la secuencia y cancela sus seguimientos pendientes."""
    await SequenceService(db).delete(sequence_id)
    await db.commit()


@router.post("/{sequence_id}/preview-schedule", response_model=list[SchedulePreviewOut])
async def preview_schedule(
    sequence_id: uuid.UUID,
    payload: EnrollIn,
    db: AsyncSession | None = Depends(get_db),
) -> list[SchedulePreviewOut]:
    """Calendario previsto, sin escribir nada.

    Es lo que se enseña antes de confirmar: qué correo sale, a quién y qué día.
    """
    service = SequenceService(db)
    sequence = await service.get_or_404(sequence_id)
    previews = await service.preview(sequence, payload.lead_ids, start_at=payload.start_at)
    return [_to_preview(p) for p in previews]


@router.post("/{sequence_id}/enroll", response_model=EnrollResultOut)
async def enroll(
    sequence_id: uuid.UUID,
    payload: EnrollIn,
    db: AsyncSession | None = Depends(get_db),
) -> EnrollResultOut:
    """Inscribe prospectos y programa el primer paso.

    Los pasos siguientes se programan al ejecutar cada uno, para que una
    respuesta detenga la secuencia antes de que el correo exista siquiera.
    """
    service = SequenceService(db)
    sequence = await service.get_or_404(sequence_id)
    created, previews = await service.enroll(sequence, payload.lead_ids, start_at=payload.start_at)
    await db.commit()

    sends = [step.scheduled_at for p in previews for step in p.steps]
    return EnrollResultOut(
        enrolled=len(created),
        skipped=len(payload.lead_ids) - len(created),
        first_send_at=min(sends) if sends else None,
        last_send_at=max(sends) if sends else None,
        preview=[_to_preview(p) for p in previews],
    )
