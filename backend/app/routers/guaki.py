"""Puente Guaki (LOOP-23/24): prospectos comerciales + vínculo Prospecto↔Negocio.

- GET  /guaki/prospects          → prospectos con su clasificación y vínculo.
- POST /guaki/prospects/{id}/link → registra/actualiza el vínculo con un negocio
                                    de Guaki (plan, estado, registro).
- POST /guaki/link/match          → dado un negocio de Guaki, encuentra el
                                    prospecto de Mapache que le corresponde.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.lead import Lead
from app.schemas.guaki import GuakiLinkIn, GuakiMatchIn
from app.services.guaki_link_svc import GuakiLinkService
from app.services.guaki_prospect_svc import GuakiProspectService

router = APIRouter()


@router.get("/prospects")
async def prospects(
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Prospectos de Guaki con clasificación, vínculo y resumen del embudo."""
    items, summary = await GuakiProspectService(db).prospects(limit)
    return {"items": items, "summary": summary}


@router.get("/funnel")
async def funnel(
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Embudo comercial de Guaki (detectados → … → pagos) con métricas reales."""
    return await GuakiProspectService(db).funnel()


@router.post("/prospects/{lead_id}/link")
async def link_prospect(
    lead_id: uuid.UUID,
    payload: GuakiLinkIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Registra/actualiza el vínculo de un prospecto con un negocio de Guaki."""
    exists = await db.scalar(select(Lead.id).where(Lead.id == lead_id))
    if not exists:
        raise HTTPException(status_code=404, detail="LEAD_NOT_FOUND")

    service = GuakiLinkService(db)
    link = await service.upsert(lead_id, payload)
    await db.flush()
    return {"vinculo": service.to_dict(link)}


@router.post("/link/match")
async def link_match(
    payload: GuakiMatchIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Dado un negocio registrado en Guaki, devuelve los prospectos candidatos."""
    candidates = await GuakiLinkService(db).find_candidates(payload.model_dump())
    return {"candidates": candidates}
