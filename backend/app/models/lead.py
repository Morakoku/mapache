"""Módulo 7 — el lead: empresa + servicio + contacto.

Es el objeto central del CRM. Una empresa puede tener varios leads (uno por
servicio que le vendes), y cada uno recorre el embudo por su cuenta.

`lead_stage_history` es la tabla que hace posible el embudo del Módulo 20:
sin ella solo se puede contar dónde está cada lead *ahora*, no cuántos
*pasaron* por cada etapa ni cuánto tardaron. Una foto no es un embudo.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ActorType, LeadStatus, ReplyIntent, StageType
from app.models.base import BaseModel, OwnedModel, pg_enum

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.contact import Contact
    from app.models.pipeline import PipelineStage
    from app.models.service import Service

# Puntos de engagement del Módulo 14. Son un indicador, no una verdad: la UI
# muestra siempre el detalle que los compone (3 aperturas, 1 click), nunca
# solo la etiqueta.
ENGAGEMENT_POINTS = {
    "EMAIL_SENT": 0,
    "EMAIL_OPENED": 5,
    "EMAIL_OPENED_MULTIPLE": 10,
    "EMAIL_CLICKED": 20,
    "EMAIL_REPLIED": 40,
    "INFO_REQUESTED": 50,
    "MEETING_REQUESTED": 80,
}


class Lead(OwnedModel):
    __tablename__ = "leads"

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_stages.id"), nullable=False
    )

    status: Mapped[LeadStatus] = mapped_column(
        pg_enum(LeadStatus, "lead_status"),
        nullable=False,
        server_default=LeadStatus.OPEN.value,
    )

    score: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    score_computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    engagement_score: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    reply_intent: Mapped[ReplyIntent | None] = mapped_column(
        pg_enum(ReplyIntent, "reply_intent"), nullable=True
    )

    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="COP")

    first_contact_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_follow_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    won_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Secuencias de la Fase 7. Se declaran ya para no migrar `leads` otra vez.
    sequence_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    sequence_step: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    sequence_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    company: Mapped[Company] = relationship(lazy="joined")
    contact: Mapped[Contact | None] = relationship(lazy="joined")
    stage: Mapped[PipelineStage] = relationship(lazy="joined")
    service: Mapped[Service] = relationship(lazy="joined")

    __table_args__ = (
        # Un lead por empresa+servicio. Permite que "Restaurante X" tenga lead
        # de web, de fotografía y de video, pero no dos de web.
        UniqueConstraint("company_id", "service_id", name="uq_leads_company_service"),
        Index("ix_leads_stage_score", "stage_id", "score"),
        Index(
            "ix_leads_next_followup",
            "next_follow_up_at",
            postgresql_where=text("next_follow_up_at IS NOT NULL AND status = 'OPEN'"),
        ),
        Index("ix_leads_owner_status_activity", "owner_id", "status", "last_activity_at"),
    )

    @property
    def engagement_band(self) -> str:
        """Banda legible del engagement (Módulo 14)."""
        if self.engagement_score >= 40:
            return "ALTO"
        if self.engagement_score >= 15:
            return "MEDIO"
        if self.engagement_score >= 1:
            return "BAJO"
        return "FRIO"

    @property
    def is_open(self) -> bool:
        return self.status == LeadStatus.OPEN


class LeadStageHistory(BaseModel):
    """Cada paso de un lead por el embudo, con quién lo movió y cuánto tardó.

    Tabla añadida en el diseño (decisión D8). Es la diferencia entre "tengo 8
    interesados" y "de 150 aperturas, 8 llegaron a interesado en 6 días de
    media — el cuello está entre apertura y respuesta".
    """

    __tablename__ = "lead_stage_history"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    from_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="SET NULL"), nullable=True
    )
    # SET NULL, no CASCADE: borrar una etapa del tablero no debe borrar el
    # historial que pasó por ella. Con CASCADE, reorganizar el Kanban
    # destruiría el embudo del Módulo 20 sin que nadie se enterara.
    to_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="SET NULL"), nullable=True
    )
    # Por eso mismo se desnormaliza el tipo: es lo que mantiene el historial
    # interpretable cuando la etapa ya no existe.
    from_stage_type: Mapped[StageType | None] = mapped_column(
        pg_enum(StageType, "stage_type"), nullable=True
    )
    to_stage_type: Mapped[StageType] = mapped_column(
        pg_enum(StageType, "stage_type"), nullable=False
    )

    actor: Mapped[ActorType] = mapped_column(
        pg_enum(ActorType, "actor_type"),
        nullable=False,
        server_default=ActorType.USER.value,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # Tiempo que pasó en la etapa anterior. Alimenta la métrica de velocidad.
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_lead_stage_history_lead", "lead_id", "entered_at"),
        Index("ix_lead_stage_history_type", "to_stage_type", "entered_at"),
    )
