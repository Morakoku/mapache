"""Módulo 18 — secuencias de seguimiento y sus instancias.

Dos niveles a propósito:

    sequences  +  sequence_steps      la *plantilla*: "3 correos, a los 3 y 7 días"
        └── follow_ups                la *instancia*: "a este lead, el martes a las 10"

Sin esa separación, editar una secuencia cambiaría el pasado de los prospectos
que ya la recorrieron, y el historial dejaría de ser cierto.

Las secuencias son **opt-in por prospecto**: nada se envía solo hasta que el
usuario inscribe explícitamente unos leads y confirma el calendario.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, OwnedModel

if TYPE_CHECKING:  # pragma: no cover - solo para el tipo de la relación
    from app.models.lead import Lead

# Estados de un seguimiento programado.
FOLLOWUP_PENDING = "PENDING"
FOLLOWUP_SENT = "SENT"
FOLLOWUP_SKIPPED = "SKIPPED"
FOLLOWUP_CANCELLED = "CANCELLED"

# Condiciones que puede llevar un paso. Se guardan como JSON para poder añadir
# más sin migrar, pero solo se aceptan estas: una condición desconocida
# significaría enviar cuando no toca.
STEP_CONDITIONS = frozenset({"always", "not_replied", "opened_not_replied", "not_opened"})


class Sequence(OwnedModel):
    """Plantilla de seguimiento."""

    __tablename__ = "sequences"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Reglas de parada. `stop_on_reply` no se puede desactivar sin pensarlo dos
    # veces: seguir escribiendo a quien ya contestó es la forma más rápida de
    # perder al cliente que acabas de ganar.
    stop_on_reply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    stop_on_click: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    stop_on_meeting: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    max_steps: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")

    steps: Mapped[list[SequenceStep]] = relationship(
        back_populates="sequence",
        cascade="all, delete-orphan",
        order_by="SequenceStep.step_number",
        lazy="selectin",
    )

    __table_args__ = (
        Index(
            "uq_sequences_owner_name",
            "owner_id",
            text("lower(name)"),
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class SequenceStep(BaseModel):
    """Un paso: qué plantilla, cuánto después y bajo qué condición."""

    __tablename__ = "sequence_steps"

    sequence_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sequences.id", ondelete="CASCADE"), nullable=False
    )
    step_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="RESTRICT"), nullable=False
    )

    # Retraso respecto al paso anterior (o al primer contacto, en el paso 1).
    delay_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    delay_hours: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")

    # {"if": "opened_not_replied"} — ver STEP_CONDITIONS.
    condition: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Ventana propia del paso. Permite que el último recordatorio salga a otra
    # hora que el primer contacto.
    send_window_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    send_window_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    skip_weekends: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    sequence: Mapped[Sequence] = relationship(back_populates="steps")

    __table_args__ = (
        UniqueConstraint("sequence_id", "step_number", name="uq_sequence_steps_number"),
    )

    @property
    def condition_key(self) -> str:
        if not self.condition:
            return "always"
        return str(self.condition.get("if", "always"))


class FollowUp(OwnedModel):
    """Seguimiento programado para un prospecto concreto.

    Sale de una secuencia o lo crea el usuario a mano (`is_manual`). En ambos
    casos es una fila con fecha: el worker no recalcula nada, solo ejecuta lo
    que ya está escrito. Así el calendario que el usuario confirmó es el que
    ocurre.
    """

    __tablename__ = "follow_ups"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    sequence_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sequences.id", ondelete="SET NULL"), nullable=True
    )
    sequence_step: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # PENDING | SENT | SKIPPED | CANCELLED
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=FOLLOWUP_PENDING)

    sent_email_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="SET NULL"), nullable=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Por qué no salió. Se enseña tal cual en la UI: "ya respondió", "en lista
    # de no contactar"... Un "no se envió" sin motivo no le sirve a nadie.
    skip_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")

    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # La agenda de seguimientos se lee por empresa, no por identificador. Con
    # `joined` la lista de 50 filas sigue siendo una sola consulta.
    lead: Mapped[Lead] = relationship(lazy="joined")

    __table_args__ = (
        Index(
            "ix_follow_ups_due",
            "scheduled_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_follow_ups_lead", "lead_id", "scheduled_at"),
        # Un paso de secuencia, una sola vez por prospecto. Sin esto, inscribir
        # dos veces por error duplicaría los correos.
        Index(
            "uq_follow_ups_lead_step",
            "lead_id",
            "sequence_id",
            "sequence_step",
            unique=True,
            postgresql_where=text("sequence_id IS NOT NULL AND status = 'PENDING'"),
        ),
    )

    @property
    def is_pending(self) -> bool:
        return self.status == FOLLOWUP_PENDING
