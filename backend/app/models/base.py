"""Base declarativa y mixins comunes.

Convención de nombres de constraints fijada explícitamente: sin ella Alembic
genera nombres autogenerados por Postgres que cambian entre entornos y hacen
que los `downgrade` fallen.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


def pg_enum(enum_cls: type, name: str) -> SAEnum:
    """ENUM nativo de Postgres a partir de un StrEnum de Python.

    `values_callable` guarda el *valor* del miembro, no su nombre. Sin esto
    SQLAlchemy persiste el nombre y cualquier divergencia entre nombre y valor
    rompe silenciosamente.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


class UUIDMixin:
    """PK UUID generada en el servidor.

    `gen_random_uuid()` es nativa desde PG 13 — no hace falta la extensión
    pgcrypto.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class OwnerMixin:
    """Preparación para multiusuario (decisión D12).

    Nullable y sin FK en el MVP de un solo usuario. Cuando entre
    multi-tenancy: poblar, poner NOT NULL, añadir la FK e índices compuestos.
    Añadir esta columna ahora cuesta nada; añadirla a 26 tablas con datos
    vivos, bastante.
    """

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        index=True,
    )


class BaseModel(Base, UUIDMixin, TimestampMixin):
    """Raíz para entidades con UUID + timestamps, sin owner."""

    __abstract__ = True


class OwnedModel(BaseModel, OwnerMixin):
    """Raíz para entidades que en el futuro pertenecerán a un usuario."""

    __abstract__ = True

    def to_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}
