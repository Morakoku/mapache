"""Eventos de idempotencia (LOOP-13).

Evita duplicar operaciones cuando un cliente reintenta con la misma
`Idempotency-Key`. La unicidad es (key, método, ruta): el mismo reintento con la
misma key devuelve 409 y no vuelve a crear el job ni a enviar el correo.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IdempotencyEvent(Base):
    __tablename__ = "idempotency_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            "method",
            "path",
            name="uq_idempotency_events_key_method_path",
        ),
    )
