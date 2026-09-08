"""Llamadas y sus guiones.

`/calls/brief/{lead_id}` es el endpoint que usa la ficha del prospecto: pide
la situación y devuelve el guion listo para leer, con los avisos que hagan
falta. Todo lo demás es CRUD de guiones e histórico.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import CallOutcome, CallScriptType
from app.core.exceptions import ConflictError
from app.models.call import CallLog
from app.schemas.call import (
    CallBriefOut,
    CallLogIn,
    CallLogOut,
    CallLogResultOut,
    CallScriptIn,
    CallScriptOut,
    CallScriptUpdate,
)
from app.schemas.common import Page
from app.services.call_svc import OUTCOME_LABELS, CallLogRepository, CallService
from app.services.email_svc import EmailService
from app.services.lead_svc import LeadService
from app.services.sequence_svc import FollowUpService, next_valid_slot

router = APIRouter()
scripts_router = APIRouter()


# ------------------------------------------------------------------ guiones


@scripts_router.get("", response_model=list[CallScriptOut])
async def list_scripts(
    script_type: CallScriptType | None = None,
    service_id: uuid.UUID | None = None,
    include_inactive: bool = False,
    db: AsyncSession | None = Depends(get_db),
) -> list[CallScriptOut]:
    service = CallService(db)
    stmt = service.build_script_query(
        script_type=script_type, service_id=service_id, only_active=not include_inactive
    )
    result = await db.execute(stmt)
    return [CallScriptOut.model_validate(s) for s in result.scalars()]


@scripts_router.post("", response_model=CallScriptOut, status_code=status.HTTP_201_CREATED)
async def create_script(payload: CallScriptIn, db: AsyncSession | None = Depends(get_db)) -> CallScriptOut:
    data = payload.model_dump()
    script = await CallService(db).create_script(data)
    await db.commit()
    await db.refresh(script)
    return CallScriptOut.model_validate(script)


@scripts_router.get("/{script_id}", response_model=CallScriptOut)
async def get_script(script_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> CallScriptOut:
    return CallScriptOut.model_validate(await CallService(db).get_script_or_404(script_id))


@scripts_router.patch("/{script_id}", response_model=CallScriptOut)
async def update_script(
    script_id: uuid.UUID,
    payload: CallScriptUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> CallScriptOut:
    service = CallService(db)
    script = await service.get_script_or_404(script_id)
    data = payload.model_dump(exclude_unset=True)
    await service.update_script(script, data)
    await db.commit()
    # `updated_at` lo pone la base al hacer UPDATE: sin refrescar, serializarlo
    # dispara una carga perezosa fuera del greenlet y revienta.
    await db.refresh(script)
    return CallScriptOut.model_validate(script)


@scripts_router.delete("/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_script(script_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    service = CallService(db)
    script = await service.get_script_or_404(script_id)
    if script.is_system:
        raise ConflictError(
            "Los guiones que trae el CRM no se borran. Puedes desactivarlos o editarlos.",
            code="CALL_SCRIPT_IS_SYSTEM",
        )
    await db.delete(script)
    await db.commit()


# ------------------------------------------------------------------ llamar


@router.get("/brief/{lead_id}", response_model=CallBriefOut)
async def call_brief(
    lead_id: uuid.UUID,
    script_type: CallScriptType | None = None,
    script_id: uuid.UUID | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> CallBriefOut:
    """Prepara la llamada a un prospecto.

    Sin `script_type` elige la situación por el estado del prospecto y explica
    por qué. La interfaz deja cambiarla: quien llama sabe cosas que el CRM no.
    """
    lead = await LeadService(db).get_or_404(lead_id)
    brief = await CallService(db).build_brief(lead, script_type=script_type, script_id=script_id)
    return CallBriefOut(**asdict(brief), can_call=brief.can_call)


@router.post("/{lead_id}", response_model=CallLogResultOut, status_code=status.HTTP_201_CREATED)
async def log_call(
    lead_id: uuid.UUID,
    payload: CallLogIn,
    db: AsyncSession | None = Depends(get_db),
) -> CallLogResultOut:
    """Registra lo que pasó en la llamada.

    Mover de etapa y programar el siguiente intento son opcionales y van en la
    misma petición: al colgar es cuando se sabe, y obligar a tres pantallas
    distintas garantiza que no se haga.
    """
    leads = LeadService(db)
    lead = await leads.get_or_404(lead_id)
    service = CallService(db)

    call = await service.log_call(
        lead,
        outcome=payload.outcome,
        script_id=payload.script_id,
        phone=payload.phone,
        duration_seconds=payload.duration_seconds,
        notes=payload.notes,
        occurred_at=payload.occurred_at,
    )
    contact_blocked = payload.outcome == CallOutcome.DO_NOT_CALL and lead.contact is not None

    stage_moved = False
    if payload.apply_suggested_stage:
        stage_moved = await service.apply_suggested_stage(lead, payload.outcome)

    follow_up_created = False
    if payload.follow_up_at is not None:
        # "Recuérdame en dos días" caería a la hora exacta de la llamada, que
        # de madrugada no sirve de nada. Se empuja al primer hueco del horario
        # de contacto, nunca hacia atrás.
        settings = await EmailService(db).get_settings_row()
        cuando = next_valid_slot(payload.follow_up_at, settings=settings)
        await FollowUpService(db).create_manual(
            {
                "lead_id": lead.id,
                "scheduled_at": cuando,
                "note": f"Volver a llamar. {payload.notes or ''}".strip(),
            }
        )
        follow_up_created = True

    await db.commit()
    await db.refresh(call)

    return CallLogResultOut(
        call=_to_out(call),
        stage_moved=stage_moved,
        follow_up_created=follow_up_created,
        contact_blocked=contact_blocked,
    )


@router.get("", response_model=Page[CallLogOut])
async def list_calls(
    lead_id: uuid.UUID | None = None,
    outcome: CallOutcome | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[CallLogOut]:
    service = CallService(db)
    stmt = service.build_log_query(lead_id=lead_id, outcome=outcome)
    items, total = await CallLogRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([_to_out(c) for c in items], total, page, size)


def _to_out(call: CallLog) -> CallLogOut:
    out = CallLogOut.model_validate(call)
    out.outcome_label = OUTCOME_LABELS[call.outcome]
    out.company_name = call.lead.company.name
    return out
