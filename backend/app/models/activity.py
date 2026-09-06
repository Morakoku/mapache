"""Módulo 19 — timeline de actividad, y tareas del usuario.

`activities` es el registro de todo lo que le pasa a un prospecto: quién hizo
qué y cuándo. Es lo que responde "¿por qué está este lead donde está?" seis
semanas después.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ActivityType, ActorType
from app.models.base import Base, OwnedModel, pg_enum


class Activity(Base):
    """Evento del timeline.

    PK `BIGSERIAL` en vez de UUID: es la tabla que más crece con diferencia
    (cada apertura, cada click, cada cambio de etapa) y aquí el orden de
    inserción y el tamaño del índice sí importan.
    """

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )

    activity_type: Mapped[ActivityType] = mapped_column(
        pg_enum(ActivityType, "activity_type"), nullable=False
    )
    actor: Mapped[ActorType] = mapped_column(
        pg_enum(ActorType, "actor_type"),
        nullable=False,
        server_default=ActorType.SYSTEM.value,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    activity_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_activities_lead", "lead_id", "occurred_at"),
        Index("ix_activities_company", "company_id", "occurred_at"),
        Index("ix_activities_type", "activity_type", "occurred_at"),
    )


class Task(OwnedModel):
    """Tarea manual del usuario, asociada o no a un lead."""

    __tablename__ = "tasks"

    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 1 alta, 2 media, 3 baja
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="2")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Índice parcial: la vista de "pendientes" es la única que se consulta
        # a diario, y las completadas se acumulan sin fin.
        Index("ix_tasks_pending", "due_at", postgresql_where=text("completed_at IS NULL")),
        Index("ix_tasks_lead", "lead_id"),
    )

    @property
    def is_done(self) -> bool:
        return self.completed_at is not None
