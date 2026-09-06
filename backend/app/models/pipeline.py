"""Módulo 17 — etapas configurables del Kanban.

Decisión D5 del diseño: el usuario puede renombrar, reordenar, recolorear y
crear etapas, pero `stage_type` no cambia. Las métricas del embudo se calculan
sobre el tipo, no sobre el nombre — sin esa separación, renombrar una columna
rompería el dashboard en silencio.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import StageType
from app.models.base import OwnedModel, pg_enum


class PipelineStage(OwnedModel):
    __tablename__ = "pipeline_stages"

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Slug estable para referenciar la etapa desde código y automatizaciones,
    # aunque el usuario cambie el nombre visible.
    stage_key: Mapped[str] = mapped_column(String(60), nullable=False)
    stage_type: Mapped[StageType] = mapped_column(pg_enum(StageType, "stage_type"), nullable=False)

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str] = mapped_column(String(9), nullable=False, server_default="#64748b")

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_won: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_lost: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Las etapas de sistema no se pueden borrar: el motor de automatización y
    # las métricas del embudo cuentan con que existan.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    # Eventos que mueven un lead a esta etapa automáticamente, p. ej.
    # ['EMAIL_OPENED']. Solo hacia adelante; nunca retrocede.
    auto_advance_on: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "stage_key",
            name="uq_pipeline_stages_owner_key",
            # `owner_id` es NULL en el MVP; sin `NULLS NOT DISTINCT` la
            # restricción no impediría dos etapas con la misma clave.
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_pipeline_stages_position", "owner_id", "position"),
    )


# Seed de las 14 etapas del Módulo 17, en orden. `stage_type` es lo que hace
# comparables las métricas entre usuarios que renombren sus columnas.
DEFAULT_STAGES: tuple[dict, ...] = (
    {
        "name": "Prospecto",
        "stage_key": "prospect",
        "stage_type": StageType.NEW,
        "color": "#94a3b8",
        "is_default": True,
    },
    {
        "name": "Calificado",
        "stage_key": "qualified",
        "stage_type": StageType.QUALIFIED,
        "color": "#64748b",
    },
    {
        "name": "Contacto encontrado",
        "stage_key": "contact_found",
        "stage_type": StageType.CONTACT_FOUND,
        "color": "#0ea5e9",
    },
    {
        "name": "Primer contacto",
        "stage_key": "first_contact",
        "stage_type": StageType.CONTACTED,
        "color": "#3b82f6",
        "auto_advance_on": ["EMAIL_SENT"],
    },
    {
        "name": "Email abierto",
        "stage_key": "opened",
        "stage_type": StageType.OPENED,
        "color": "#6366f1",
        "auto_advance_on": ["EMAIL_OPENED"],
    },
    {
        "name": "Respondió",
        "stage_key": "replied",
        "stage_type": StageType.REPLIED,
        "color": "#8b5cf6",
        "auto_advance_on": ["EMAIL_REPLIED"],
    },
    {
        "name": "Conversación",
        "stage_key": "conversation",
        "stage_type": StageType.CONVERSATION,
        "color": "#a855f7",
    },
    {
        "name": "Interesado",
        "stage_key": "interested",
        "stage_type": StageType.INTERESTED,
        "color": "#d946ef",
    },
    {
        "name": "Reunión",
        "stage_key": "meeting",
        "stage_type": StageType.MEETING,
        "color": "#ec4899",
    },
    {
        "name": "Oportunidad",
        "stage_key": "opportunity",
        "stage_type": StageType.OPPORTUNITY,
        "color": "#f59e0b",
    },
    {
        "name": "Propuesta",
        "stage_key": "proposal",
        "stage_type": StageType.PROPOSAL,
        "color": "#f97316",
    },
    {
        "name": "Negociación",
        "stage_key": "negotiation",
        "stage_type": StageType.NEGOTIATION,
        "color": "#ef4444",
    },
    {
        "name": "Ganado",
        "stage_key": "won",
        "stage_type": StageType.WON,
        "color": "#22c55e",
        "is_won": True,
    },
    {
        "name": "Perdido",
        "stage_key": "lost",
        "stage_type": StageType.LOST,
        "color": "#71717a",
        "is_lost": True,
    },
)

# Etapas hasta las que el sistema puede avanzar solo. Más allá de REPLIED /
# CONVERSATION la decisión es comercial y la toma el usuario: que alguien abra
# un correo no lo convierte en "interesado".
AUTO_ADVANCE_CEILING = StageType.CONVERSATION
