"""Schemas del puente Guaki (LOOP-23/24): prospectos y vínculo Prospecto↔Negocio."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.common import APIModel


class GuakiLinkIn(APIModel):
    """Datos para crear/actualizar el vínculo de un prospecto con un negocio Guaki."""

    guaki_business_id: str
    match_state: str = "POSSIBLE_MATCH"
    confidence: int = 0
    signals: list[str] = Field(default_factory=list)
    matched_at: datetime | None = None
    registered_at: datetime | None = None
    plan: str | None = None
    status: str | None = None


class GuakiMatchIn(APIModel):
    """Datos de un negocio de Guaki para buscar el prospecto de Mapache que le corresponde."""

    name: str
    domain: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    external_id: str | None = None
