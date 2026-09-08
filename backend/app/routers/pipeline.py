"""Módulo 17 — etapas del Kanban y tablero."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
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


@router.get("/stages", response_model=list[StageOut])
async def list_stages(db: AsyncSession | None = Depends(get_db)) -> list[StageOut]:
    return [StageOut.model_validate(s) for s in await PipelineService(db).list_stages()]


@router.post("/stages", response_model=StageOut, status_code=status.HTTP_201_CREATED)
async def create_stage(
    payload: StageIn,
    db: AsyncSession | None = Depends(get_db),
) -> StageOut:
    stage = await PipelineService(db).create(payload.model_dump(exclude_none=True))
    await db.commit()
    return StageOut.model_validate(stage)


@router.patch("/stages/{stage_id}", response_model=StageOut)
async def update_stage(
    stage_id: uuid.UUID,
    payload: StageUpdate,
    db: AsyncSession | None = Depends(get_db),
) -> StageOut:
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
