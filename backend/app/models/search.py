"""Módulo 2 — búsquedas y sus ejecuciones.

`searches` es la configuración reutilizable; `search_runs` es cada vez que se
lanza. Separarlas permite relanzar la misma búsqueda cada mes y ver qué
apareció nuevo.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import JobStatus, SourceType
from app.models.base import Base, BaseModel, OwnedModel, pg_enum


class Search(OwnedModel):
    __tablename__ = "searches"

    service_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    business_type: Mapped[str] = mapped_column(String(160), nullable=False)
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    zone: Mapped[str | None] = mapped_column(String(160), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    radius_km: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, server_default="10")

    target_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    source: Mapped[SourceType] = mapped_column(
        pg_enum(SourceType, "source_type"),
        nullable=False,
        server_default=SourceType.GOOGLE_MAPS.value,
    )

    # Filtros de calidad: descartan cadenas grandes y negocios sin tracción.
    min_rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), nullable=True)
    max_reviews: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exclude_chains: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # Requisitos de contacto. Descartan al extraer, antes de guardar: una base
    # llena de negocios a los que no se puede escribir ni llamar no es una base
    # de prospectos, es ruido que hay que filtrar a mano cada mañana.
    #
    # El teléfono y la web están en la ficha de Google, así que se comprueban
    # en el momento. **El email no**: ninguna ficha de Maps lo trae, sale de
    # rastrear la web después. Por eso `require_email` se aplica al terminar el
    # enriquecimiento y no aquí — comprobarlo al extraer descartaría todo.
    require_phone: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    require_website: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    require_email: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Exigir que el resultado sea del tipo de negocio y la ciudad pedidos.
    # Activado por defecto: Google devuelve de todo alrededor del término, y
    # sin esto la base se llena de empresas que hay que descartar a mano.
    strict_match: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    auto_enrich: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    auto_score: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    runs: Mapped[list[SearchRun]] = relationship(
        back_populates="search", cascade="all, delete-orphan"
    )


class SearchRun(BaseModel):
    __tablename__ = "search_runs"

    search_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("searches.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        nullable=False,
        server_default=JobStatus.QUEUED.value,
    )
    provider: Mapped[SourceType] = mapped_column(pg_enum(SourceType, "source_type"), nullable=False)

    results_found: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    results_new: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    results_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    search: Mapped[Search] = relationship(back_populates="runs")

    __table_args__ = (Index("ix_search_runs_search_created", "search_id", "created_at"),)


class SearchResult(Base):
    """Enlace ejecución <-> empresa, con el payload crudo del proveedor.

    Guardar `raw_payload` permite reprocesar sin volver a llamar al proveedor:
    si mañana se extrae un campo nuevo, se saca de aquí en vez de repetir el
    scraping.
    """

    __tablename__ = "search_results"

    search_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("search_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_new: Mapped[bool] = mapped_column(Boolean, nullable=False)
    position: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
