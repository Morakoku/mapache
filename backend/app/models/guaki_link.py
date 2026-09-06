"""Vínculo Prospecto (Mapache) ↔ Negocio (Guaki) — LOOP-24.

No se relacionan registros solo por el nombre: el vínculo se establece con
señales (dominio, email, teléfono, identificador externo, nombre+ubicación) y
guarda un estado (UNMATCHED / POSSIBLE_MATCH / MATCHED / REJECTED), confianza,
señales usadas y los datos comerciales de lado Guaki (plan, estado, registro).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

# Estados posibles del vínculo (se guardan como String para no añadir un tipo ENUM).
UNMATCHED = "UNMATCHED"
POSSIBLE_MATCH = "POSSIBLE_MATCH"
MATCHED = "MATCHED"
REJECTED = "REJECTED"
MATCH_STATES = frozenset({UNMATCHED, POSSIBLE_MATCH, MATCHED, REJECTED})


class GuakiLink(BaseModel):
    """Relación formal entre un prospecto de Mapache y un negocio registrado en Guaki."""

    __tablename__ = "guaki_links"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    guaki_business_id: Mapped[str] = mapped_column(String(64), nullable=False)
    match_state: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=UNMATCHED
    )
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Señales usadas para el match: dominio, email, telefono, identificador_externo,
    # nombre+ubicacion, ...
    signals: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        Index("ix_guaki_links_match_state", "match_state"),
        Index("ix_guaki_links_guaki_business_id", "guaki_business_id"),
    )
