"""Módulo 1 — qué vende el usuario.

Es el ancla de todo: la búsqueda sabe qué buscar y el scoring sabe qué
califica como buen prospecto porque leen de aquí.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import OwnedModel


class Service(OwnedModel):
    __tablename__ = "services"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "PYMES", "profesionales independientes"...
    ideal_customer: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Arrays nativos de Postgres: son listas cortas que solo se leen enteras,
    # no merece la pena una tabla hija por cada una.
    target_industries: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    problems_solved: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    # Claves de company_signals (`no_website`, `not_responsive`...), no texto
    # libre: el motor de scoring las cruza contra las señales detectadas.
    opportunity_signals: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    value_proposition: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_from: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="COP")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        Index(
            "uq_services_owner_name",
            "owner_id",
            text("lower(name)"),
            unique=True,
            # `owner_id` es NULL en el MVP y los NULL son distintos entre sí
            # para Postgres: sin esto, el índice único no une nada.
            postgresql_nulls_not_distinct=True,
        ),
    )
