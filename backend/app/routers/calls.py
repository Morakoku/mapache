"""Llamadas y sus guiones.

`/calls/brief/{lead_id}` es el endpoint que usa la ficha del prospecto: pide
la situación y devuelve el guion listo para leer, con los avisos que hagan
falta. Todo lo demás es CRUD de guiones e histórico.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import CallOutcome, CallScriptType
from app.core.exceptions import ConflictError, NotFoundError
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

# ------------------------------------------------------------------ helpers


def _pg_table_scripts() -> str:
    return "call_scripts"


def _pg_table_logs() -> str:
    return "call_logs"


# ------------------------------------------------------------------ guiones


@scripts_router.get("", response_model=list[CallScriptOut])
async def list_scripts(
    script_type: CallScriptType | None = None,
    include_inactive: bool = False,
    db: AsyncSession | None = Depends(get_db),
) -> list[CallScriptOut]:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select

    settings = get_settings()
    if db is None or settings.use_postgrest:
        filters: dict[str, str] = {}
        if script_type is not None:
            filters["script_type"] = script_type.value
        if not include_inactive:
            filters["is_active"] = "true"
        items = await pg_select(_pg_table_scripts(), filters=filters if filters else None, limit=200)
        return [CallScriptOut.model_validate(s) for s in items]

    service = CallService(db)
    stmt = service.build_script_query(script_type=script_type, only_active=not include_inactive)
    result = await db.execute(stmt)
    return [CallScriptOut.model_validate(s) for s in result.scalars()]


@scripts_router.post("", response_model=CallScriptOut, status_code=status.HTTP_201_CREATED)
async def create_script(payload: CallScriptIn, db: AsyncSession | None = Depends(get_db)) -> CallScriptOut:
    from app.core.config import get_settings
    from app.core.supabase_http import insert as pg_insert

    settings = get_settings()
    if db is None or settings.use_postgrest:
        data = dict(payload.model_dump())
        data["id"] = data.get("id") or str(uuid.uuid4())
        result = await pg_insert(_pg_table_scripts(), data)
        if not result:
            raise HTTPException(status_code=500, detail="No se pudo crear el guión")
        return CallScriptOut.model_validate(result[0])

    data = payload.model_dump()
    script = await CallService(db).create_script(data)
    await db.commit()
    await db.refresh(script)
    return CallScriptOut.model_validate(script)


@scripts_router.get("/{script_id}", response_model=CallScriptOut)
async def get_script(script_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> CallScriptOut:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select

    settings = get_settings()
    if db is None or settings.use_postgrest:
        items = await pg_select(_pg_table_scripts(), filters={"id": str(script_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("call_script", script_id)
        return CallScriptOut.model_validate(items[0])

    return CallScriptOut.model_validate(await CallService(db).get_script_or_404(script_id))


@scripts_router.patch("/{script_id}", response_model=CallScriptOut)
async def update_script(
    script_id: uuid.UUID,
    payload: CallScriptUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> CallScriptOut:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update

    settings = get_settings()
    if db is None or settings.use_postgrest:
        existing = await pg_select(_pg_table_scripts(), filters={"id": str(script_id)}, limit=1)
        if not existing:
            raise NotFoundError.for_entity("call_script", script_id)

        data = payload.model_dump(exclude_unset=True)
        result = await pg_update(_pg_table_scripts(), {"id": str(script_id)}, data)
        if not result:
            raise NotFoundError.for_entity("call_script", script_id)
        return CallScriptOut.model_validate(result[0])

    service = CallService(db)
    script = await service.get_script_or_404(script_id)
    data = payload.model_dump(exclude_unset=True)
    await service.update_script(script, data)
    await db.commit()
    await db.refresh(script)
    return CallScriptOut.model_validate(script)


@scripts_router.delete("/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_script(script_id: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> None:
    from app.core.config import get_settings
    from app.core.supabase_http import delete as pg_delete
    from app.core.supabase_http import select as pg_select

    settings = get_settings()
    if db is None or settings.use_postgrest:
        items = await pg_select(_pg_table_scripts(), filters={"id": str(script_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("call_script", script_id)
        if items[0].get("is_system"):
            raise ConflictError(
                "Los guiones que trae el CRM no se borran. Puedes desactivarlos o editarlos.",
                code="CALL_SCRIPT_IS_SYSTEM",
            )
        await pg_delete(_pg_table_scripts(), {"id": str(script_id)})
        return

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
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select

    settings = get_settings()
    if db is None or settings.use_postgrest:
        lead_items = await pg_select("leads", filters={"id": str(lead_id)}, limit=1)
        if not lead_items:
            raise NotFoundError.for_entity("lead", lead_id)
        lead = lead_items[0]

        return CallBriefOut(
            lead_id=lead_id,
            lead_name=lead.get("display_name") or "",
            company_name=lead.get("company_name") or "",
            phone=lead.get("phone"),
            email=lead.get("email"),
            stage=lead.get("stage_name") or "",
            script_suggestion=None,
            warnings=[],
            can_call=True,
        )

    lead = await LeadService(db).get_or_404(lead_id)
    brief = await CallService(db).build_brief(lead, script_type=script_type, script_id=script_id)
    return CallBriefOut(**brief.model_dump(), can_call=brief.can_call)


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
    from app.core.config import get_settings
    from app.core.supabase_http import insert as pg_insert
    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update

    settings = get_settings()
    if db is None or settings.use_postgrest:
        lead_items = await pg_select("leads", filters={"id": str(lead_id)}, limit=1)
        if not lead_items:
            raise NotFoundError.for_entity("lead", lead_id)
        lead = lead_items[0]

        now_iso = datetime.now(UTC).isoformat()
        call_id = str(uuid.uuid4())

        call_data: dict[str, Any] = {
            "id": call_id,
            "lead_id": str(lead_id),
            "contact_id": lead.get("contact_id"),
            "direction": _infer_direction(lead),
            "outcome": payload.outcome.value,
            "duration_seconds": payload.duration_seconds,
            "notes": payload.notes,
            "occurred_at": (payload.occurred_at.isoformat() if payload.occurred_at else now_iso),
            "created_at": now_iso,
        }
        result = await pg_insert(_pg_table_logs(), call_data)
        if not result:
            raise HTTPException(status_code=500, detail="No se pudo registrar la llamada")
        call = result[0]

        stage_moved = False
        if payload.apply_suggested_stage and payload.suggested_stage_id:
            await pg_update(
                "leads", {"id": str(lead_id)}, {"stage_id": str(payload.suggested_stage_id)}
            )
            stage_moved = True

        follow_up_created = False
        if payload.follow_up_at is not None:
            try:
                settings_row = await pg_select("app_settings", limit=1)
                settings_data = settings_row[0] if settings_row else {}
            except Exception:
                settings_data = {}

            cuando = payload.follow_up_at.isoformat()
            try:
                when = next_valid_slot(payload.follow_up_at, settings=settings_data)
                cuando = when.isoformat()
            except Exception:
                cuando = payload.follow_up_at.isoformat()

            await pg_insert(
                "follow_ups",
                {
                    "id": str(uuid.uuid4()),
                    "lead_id": str(lead_id),
                    "scheduled_at": cuando,
                    "note": f"Volver a llamar. {payload.notes or ''}".strip(),
                    "created_at": now_iso,
                },
            )
            follow_up_created = True

        contact_blocked = payload.outcome == CallOutcome.DO_NOT_CALL and lead.get("contact_id")

        return CallLogResultOut(
            call=_to_out(call),
            stage_moved=stage_moved,
            follow_up_created=follow_up_created,
            contact_blocked=contact_blocked,
        )

    lead = await LeadService(db).get_or_404(lead_id)
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
        settings = await EmailService(db).get_settings_row()
        cuando = next_valid_slot(payload.follow_up_at, settings=settings)
        await FollowUpService(db).create_manual(
            {"lead_id": lead.id, "scheduled_at": cuando,
             "note": f"Volver a llamar. {payload.notes or ''}".strip()}
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


def _infer_direction(lead: dict) -> str:
    """En la tabla real no hay columna `direction` del router, pero la migración
    sí la define. Por defecto OUTBOUND si el router envía un payload de llamada."""
    return "OUTBOUND"


@router.get("", response_model=Page[CallLogOut])
async def list_calls(
    lead_id: uuid.UUID | None = None,
    outcome: CallOutcome | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> Page[CallLogOut]:
    from app.core.config import get_settings
    from app.core.supabase_http import select as pg_select

    settings = get_settings()
    if db is None or settings.use_postgrest:
        filters: dict[str, str] = {}
        if lead_id is not None:
            filters["lead_id"] = str(lead_id)
        if outcome is not None:
            filters["outcome"] = outcome.value
        items = await pg_select(_pg_table_logs(), filters=filters if filters else None, limit=size)
        return Page.build([_to_out(c) for c in items], len(items), page, size)

    service = CallService(db)
    stmt = service.build_log_query(lead_id=lead_id, outcome=outcome)
    items, total = await CallLogRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([_to_out(c) for c in items], total, page, size)


def _to_out(call: CallLog) -> CallLogOut:
    out = CallLogOut.model_validate(call)
    out.outcome_label = OUTCOME_LABELS[call.outcome]
    out.company_name = call.lead.company.name
    return out
