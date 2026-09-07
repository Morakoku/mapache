"""Módulos 3, 4 y 5 — la empresa y todo lo que sabemos de ella.

`companies` guarda el estado actual; `company_sources` guarda de dónde salió
cada dato y con qué confianza. Esa separación es la que permite responder
"¿de dónde sacaste este email?" meses después.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import SourceType, VerificationStatus
from app.models.base import BaseModel, OwnedModel, pg_enum

if TYPE_CHECKING:
    from app.models.contact import Contact


class Company(OwnedModel):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    category: Mapped[str | None] = mapped_column(String(160), nullable=True)
    categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)  # E.164
    phone_raw: Mapped[str | None] = mapped_column(String(60), nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String(20), nullable=True)  # E.164
    email: Mapped[str | None] = mapped_column(CITEXT, nullable=True)

    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    website_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)

    google_maps_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # El scraper obtiene el FTID (`0x8e44...:0x9f1b...`); la Places API el
    # place_id (`ChIJ...`). Son identificadores distintos y no intercambiables,
    # así que se guardan los dos y el dedupe prueba ambos.
    google_ftid: Mapped[str | None] = mapped_column(String(120), nullable=True)
    google_place_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), nullable=True)
    reviews_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    opening_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    is_permanently_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    employee_range: Mapped[str | None] = mapped_column(String(40), nullable=True)

    data_quality_score: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    dedupe_key: Mapped[str] = mapped_column(String(40), nullable=False)

    first_extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sources: Mapped[list[CompanySource]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )
    socials: Mapped[list[CompanySocial]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )
    signals: Mapped[list[CompanySignal]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )
    contacts: Mapped[list[Contact]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Índices únicos parciales: los niveles 1 del dedupe.
        Index(
            "uq_companies_google_ftid",
            "google_ftid",
            unique=True,
            postgresql_where=text("google_ftid IS NOT NULL"),
        ),
        Index(
            "uq_companies_google_place_id",
            "google_place_id",
            unique=True,
            postgresql_where=text("google_place_id IS NOT NULL"),
        ),
        # Nivel 2: clave determinista. Único por owner porque en multiusuario
        # dos personas pueden prospectar la misma empresa.
        UniqueConstraint(
            "owner_id",
            "dedupe_key",
            name="uq_companies_owner_dedupe_key",
            # Sin esto, con `owner_id` NULL (MVP de un solo usuario) Postgres
            # considera distintas todas las filas y la restricción no impide
            # nada: la capa 2 del dedupe se quedaría sin garantía en base.
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_companies_website_domain",
            "website_domain",
            postgresql_where=text("website_domain IS NOT NULL"),
        ),
        Index("ix_companies_city_category", "city", "category"),
        # Nivel 3: similitud difusa por nombre.
        Index("ix_companies_name_trgm", text("name gin_trgm_ops"), postgresql_using="gin"),
    )

    @property
    def has_website(self) -> bool:
        return bool(self.website_domain)

    @property
    def has_email(self) -> bool:
        return bool(self.email)

    # Lo que se encontró de la empresa en el índice del buscador. Se guarda
    # para no volver a gastar cuota: cada consulta cuesta, y la información
    # pública de un negocio no cambia de un día para otro.
    web_findings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    web_findings_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_verified(self) -> bool:
        """¿El negocio tiene una ficha real en Google?

        `google_ftid` y `google_place_id` los emite Google y solo se rellenan
        leyendo una ficha existente: no se pueden fabricar. Es la única
        garantía dura que tenemos de que la empresa existe de verdad, y por eso
        la interfaz distingue estas de las que se dieron de alta a mano.
        """
        return bool(self.google_ftid or self.google_place_id)


class CompanySource(BaseModel):
    """Procedencia de un dato concreto (Módulo 4).

    Un mismo campo puede tener varios valores de fuentes distintas; gana el de
    mayor `confidence` y ese es el que se copia a `companies`.
    """

    __tablename__ = "company_sources"

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    source: Mapped[SourceType] = mapped_column(pg_enum(SourceType, "source_type"), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="50")
    verification: Mapped[VerificationStatus] = mapped_column(
        pg_enum(VerificationStatus, "verification_status"),
        nullable=False,
        server_default=VerificationStatus.UNVERIFIED.value,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    company: Mapped[Company] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "field_name", "value", name="uq_company_sources_company_field_value"
        ),
        Index("ix_company_sources_company_field", "company_id", "field_name"),
    )


class CompanySocial(BaseModel):
    __tablename__ = "company_socials"

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    handle: Mapped[str | None] = mapped_column(String(120), nullable=True)
    followers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_post_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[SourceType] = mapped_column(pg_enum(SourceType, "source_type"), nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    company: Mapped[Company] = relationship(back_populates="socials")

    __table_args__ = (
        UniqueConstraint("company_id", "platform", "url", name="uq_company_socials_company_url"),
    )


class CompanySignal(BaseModel):
    """Señal de oportunidad detectada, con evidencia y fecha.

    Es lo que convierte "creo que necesita una web" en un dato defendible:
    `no_website` detectado el 27/07 desde el crawler.
    """

    __tablename__ = "company_signals"

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    signal_key: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    source: Mapped[SourceType] = mapped_column(pg_enum(SourceType, "source_type"), nullable=False)

    company: Mapped[Company] = relationship(back_populates="signals")

    __table_args__ = (
        UniqueConstraint("company_id", "signal_key", name="uq_company_signals_company_key"),
        Index("ix_company_signals_key", "signal_key"),
    )
