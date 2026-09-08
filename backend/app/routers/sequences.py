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
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        filters: dict[str, str] | None = None
        if active_only:
            filters = {"is_active": "true"}
        items = await pg_select("sequences", filters=filters, limit=200)
        return [SequenceOut.model_validate(s) for s in items]

    service = SequenceService(db)
    result = await db.execute(service.build_list_query(active_only=active_only))
    return [SequenceOut.model_validate(s) for s in result.scalars().unique().all()]


@router.post("", response_model=SequenceOut, status_code=status.HTTP_201_CREATED)
async def create_sequence(payload: SequenceIn, db: AsyncSession | None = Depends(get_db)) -> SequenceOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import insert as pg_insert
        from app.core.supabase_http import update as pg_update

        data = dict(payload.model_dump())
        data["id"] = str(uuid.uuid4())
        data["is_active"] = data.get("is_active", True)
        result = await pg_insert("sequences", data)
        if not result:
            raise HTTPException(status_code=500, detail="No se pudo crear la secuencia")
        seq = result[0]

        # Crear los pasos
        steps_data = [{**step, "condition": step.get("condition")} for step in data.get("steps", [])]
        for step in steps_data:
            step["id"] = str(uuid.uuid4())
            step["sequence_id"] = seq["id"]
            await pg_insert("sequence_steps", step)

        return SequenceOut.model_validate(seq)

    data = payload.model_dump()
    data["steps"] = [{**step, "condition": step.get("condition")} for step in data.get("steps", [])]
    sequence = await SequenceService(db).create(data)
    await db.commit()
    return SequenceOut.model_validate(sequence)


@router.get("/{sequence_id}", response_model=SequenceOut)
async def get_sequence(sequence_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> SequenceOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        items = await pg_select("sequences", filters={"id": str(sequence_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("sequence", sequence_id)
        return SequenceOut.model_validate(items[0])

    return SequenceOut.model_validate(await SequenceService(db).get_or_404(sequence_id))


@router.patch("/{sequence_id}", response_model=SequenceOut)
async def update_sequence(
    sequence_id: uuid.UUID,
    payload: SequenceUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> SequenceOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import update as pg_update
        from app.core.supabase_http import select as pg_select

        existing = await pg_select("sequences", filters={"id": str(sequence_id)}, limit=1)
        if not existing:
            raise NotFoundError.for_entity("sequence", sequence_id)

        data = payload.model_dump(exclude_unset=True)
        result = await pg_update("sequences", {"id": str(sequence_id)}, data)
        if not result:
            raise NotFoundError.for_entity("sequence", sequence_id)
        return SequenceOut.model_validate(result[0])

    sequence = await SequenceService(db).update(sequence_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    return SequenceOut.model_validate(sequence)


@router.delete("/{sequence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sequence(sequence_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import delete as pg_delete
        from app.core.supabase_http import select as pg_select

        # Verificar existe
        items = await pg_select("sequences", filters={"id": str(sequence_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("sequence", sequence_id)

        # Cancelar seguimientos pendientes
        followups = await pg_select("follow_ups", filters={"sequence_id": str(sequence_id)}, limit=200)
        for fu in followups:
            if fu.get("status") == "PENDING":
                await pg_update("follow_ups", {"id": fu["id"]}, {"status": "CANCELLED"})

        await pg_delete("sequences", {"id": str(sequence_id)})
        return

    await SequenceService(db).delete(sequence_id)
    await db.commit()


@router.post("/{sequence_id}/preview-schedule", response_model=list[SchedulePreviewOut])
async def preview_schedule(
    sequence_id: uuid.UUID,
    payload: EnrollIn,
    db: AsyncSession | None = Depends(get_db),
) -> list[SchedulePreviewOut]:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        seq_items = await pg_select("sequences", filters={"id": str(sequence_id)}, limit=1)
        if not seq_items:
            raise NotFoundError.for_entity("sequence", sequence_id)
        sequence = seq_items[0]
        step_count = len(sequence.get("steps") or [])

        leads_data = []
        for lead_id in payload.lead_ids:
            lead_items = await pg_select("leads", filters={"id": str(lead_id)}, limit=1)
            if lead_items:
                leads_data.append(lead_items[0])

        previews = []
        for lead in leads_data:
            company_items = await pg_select("companies", filters={"id": str(lead.get("company_id", ""))}, limit=1)
            company_name = company_items[0].get("name", "") if company_items else ""
            steps = [
                PlannedStepOut(
                    step_number=i + 1,
                    template_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
                    template_name="",
                    scheduled_at=datetime.now(UTC),
                    condition="",
                )
                for i in range(step_count)
            ]
            previews.append(
                SchedulePreviewOut(
                    lead_id=lead["id"],
                    company_name=company_name,
                    steps=steps,
                    warnings=[],
                )
            )
        return [_to_preview(p) for p in previews]

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
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import insert as pg_insert
        from app.core.supabase_http import select as pg_select
        from app.core.supabase_http import update as pg_update
        from datetime import datetime as dt

        seq_items = await pg_select("sequences", filters={"id": str(sequence_id)}, limit=1)
        if not seq_items:
            raise NotFoundError.for_entity("sequence", sequence_id)
        sequence = seq_items[0]
        step_count = len(sequence.get("steps") or [])

        created = 0
        previews = []
        for lead_id in payload.lead_ids:
            lead_items = await pg_select("leads", filters={"id": str(lead_id)}, limit=1)
            if not lead_items:
                continue
            lead = lead_items[0]

            company_items = await pg_select("companies", filters={"id": str(lead.get("company_id", ""))}, limit=1)
            company_name = company_items[0].get("name", "") if company_items else ""

            # Crear seguimientos (follow_ups)
            for i in range(step_count):
                fu_id = str(uuid.uuid4())
                scheduled_at = dt.now(UTC)
                fu_data = {
                    "id": fu_id,
                    "lead_id": str(lead_id),
                    "sequence_id": str(sequence_id),
                    "step_number": i + 1,
                    "scheduled_at": scheduled_at.isoformat(),
                    "status": "PENDING",
                    "note": "",
                }
                await pg_insert("follow_ups", fu_data)
                created += 1

            steps = [
                PlannedStepOut(
                    step_number=i + 1,
                    template_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
                    template_name="",
                    scheduled_at=scheduled_at,
                    condition="",
                )
                for i in range(step_count)
            ]
            previews.append(
                SchedulePreviewOut(
                    lead_id=lead_id,
                    company_name=company_name,
                    steps=steps,
                    warnings=[],
                )
            )

        sends = [p.steps[0].scheduled_at for p in previews if p.steps] if previews else []
        return EnrollResultOut(
            enrolled=created,
            skipped=len(payload.lead_ids) - created,
            first_send_at=min(sends) if sends else None,
            last_send_at=max(sends) if sends else None,
            preview=[_to_preview(p) for p in previews],
        )

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
