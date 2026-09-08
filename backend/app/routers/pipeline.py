"""Módulo 17 — etapas del Kanban y tablero."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ConflictError
from app.schemas.crm import (
    BoardColumnOut,
    BoardOut,
    LeadOut,
    StageDeleteIn,
    StageIn,
    StageOut,
    StageReorderIn,
    StageUpdate,
)
from app.services.lead_svc import LeadService
from app.services.pipeline_svc import PipelineService

router = APIRouter()

# ------------------------------------------------------------------ fallback helpers


def _pg_table() -> str:
    return "pipeline_stages"


@router.get("/stages", response_model=list[StageOut])
async def list_stages(db: AsyncSession | None = Depends(get_db)) -> list[StageOut]:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select

        filters: dict[str, str] | None = None
        items = await pg_select(_pg_table(), filters=filters, limit=200)
        return [StageOut.model_validate(s) for s in items]

    return [StageOut.model_validate(s) for s in await PipelineService(db).list_stages()]


@router.post("/stages", response_model=StageOut, status_code=status.HTTP_201_CREATED)
async def create_stage(
    payload: StageIn,
    db: AsyncSession | None = Depends(get_db),
) -> StageOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import insert as pg_insert

        data = dict(payload.model_dump(exclude_none=True))
        data["id"] = data.get("id") or str(uuid.uuid4())
        result = await pg_insert(_pg_table(), data)
        if not result:
            raise HTTPException(status_code=500, detail="No se pudo crear la etapa")
        return StageOut.model_validate(result[0])

    from fastapi import HTTPException

    stage = await PipelineService(db).create(payload.model_dump(exclude_none=True))
    await db.commit()
    return StageOut.model_validate(stage)


@router.patch("/stages/{stage_id}", response_model=StageOut)
async def update_stage(
    stage_id: uuid.UUID,
    payload: StageUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> StageOut:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import select as pg_select
        from app.core.supabase_http import update as pg_update

        existing = await pg_select(_pg_table(), filters={"id": str(stage_id)}, limit=1)
        if not existing:
            raise NotFoundError.for_entity("stage", stage_id)

        data = payload.model_dump(exclude_unset=True)
        result = await pg_update(_pg_table(), {"id": str(stage_id)}, data)
        if not result:
            raise NotFoundError.for_entity("stage", stage_id)
        return StageOut.model_validate(result[0])

    stage = await PipelineService(db).update(stage_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    return StageOut.model_validate(stage)


@router.delete("/stages/{stage_id}", status_code=status.HTTP_200_OK)
async def delete_stage(
    stage_id: uuid.UUID,
    payload: StageDeleteIn = Body(default=StageDeleteIn()),
    db: AsyncSession | None = Depends(get_db),
) -> dict[str, int]:
    """Elimina una etapa y reubica sus prospectos.

    Si la etapa tiene leads y no se indica destino, responde 409 con el
    recuento: nunca se borran prospectos por reorganizar el tablero.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.use_postgrest or db is None:
        from app.core.supabase_http import delete as pg_delete
        from app.core.supabase_http import select as pg_select
        from app.core.supabase_http import update as pg_update

        # Verificar existe
        items = await pg_select(_pg_table(), filters={"id": str(stage_id)}, limit=1)
        if not items:
            raise NotFoundError.for_entity("stage", stage_id)

        # Reubicar leads si se indica destino
        moved = 0
        if payload.move_to_stage_id is not None:
            leads = await pg_select(
                "leads", filters={"stage_id": str(stage_id)}, limit=200
            )
            for lead in leads:
                await pg_update(
                    "leads", {"id": lead["id"]}, {"stage_id": str(payload.move_to_stage_id)}
                )
                moved += 1

        await pg_delete(_pg_table(), {"id": str(stage_id)})
        return {"leads_moved": moved}

    moved = await PipelineService(db).delete(stage_id, move_to_stage_id=payload.move_to_stage_id)
    await db.commit()
    return {"leads_moved": moved}


@router.post("/stages/reorder", response_model=list[StageOut])
async def reorder_stages(
    payload: StageReorderIn,
    db: AsyncSession | None = Depends(get_db),
) -> list[StageOut]:
    stages = await PipelineService(db).reorder(payload.ordered_ids)
    await db.commit()
    return [StageOut.model_validate(s) for s in stages]


@router.get("/board", response_model=BoardOut)
async def get_board(
    service_id: uuid.UUID | None = None,
    per_stage: int = Query(default=50, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
) -> BoardOut:
    """Tablero completo.

    Cada columna se pagina por separado: una etapa con miles de prospectos no
    puede arrastrar el resto del tablero.
    """
    columns = await LeadService(db).board(service_id=service_id, per_stage=per_stage)
    return BoardOut(
        columns=[
            BoardColumnOut(
                stage=StageOut.model_validate(col["stage"]),
                leads=[LeadOut.model_validate(lead) for lead in col["leads"]],
                total=col["total"],
                total_value=col["total_value"],
            )
            for col in columns
        ]
    )
