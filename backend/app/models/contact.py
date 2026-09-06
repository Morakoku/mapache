"""Módulo 6 — personas de contacto dentro de una empresa.

Una empresa puede tener varias: el gerente, la persona de marketing. El lead
apunta a una como principal, pero se guardan todas porque la que responde no
siempre es a la que escribiste primero.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import SourceType, VerificationStatus
from app.models.base import OwnedModel, pg_enum

if TYPE_CHECKING:
    from app.models.company import Company


class Contact(OwnedModel):
    __tablename__ = "contacts"

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    first_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # owner | c_level | manager | staff | unknown
    seniority: Mapped[str | None] = mapped_column(String(20), nullable=True)

    email: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    email_verified: Mapped[VerificationStatus] = mapped_column(
        pg_enum(VerificationStatus, "verification_status"),
        nullable=False,
        server_default=VerificationStatus.UNVERIFIED.value,
    )
    # `info@`, `contacto@`: sirve para escribir, pero no es una persona. Se
    # marca porque cambia el tono del primer correo.
    is_role_email: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String(20), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[SourceType] = mapped_column(
        pg_enum(SourceType, "source_type"),
        nullable=False,
        server_default=SourceType.WEBSITE.value,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Bandera individual, además de la lista global de supresión: permite
    # excluir a una persona sin bloquear el dominio entero de la empresa.
    do_not_contact: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    company: Mapped[Company] = relationship(back_populates="contacts")

    __table_args__ = (
        # Único por empresa+email, insensible a mayúsculas. Parcial porque un
        # contacto sin email sigue siendo válido (se conoce el nombre y el
        # cargo, y el email llega después).
        Index(
            "uq_contacts_company_email",
            "company_id",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index("ix_contacts_company", "company_id"),
        Index("ix_contacts_email", text("lower(email)")),
    )

    @property
    def display_name(self) -> str:
        """Nombre para la UI y para `{{contact_name}}` en las plantillas."""
        if self.full_name:
            return self.full_name
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return " ".join(parts)
        return self.email or "Sin nombre"

    @property
    def is_contactable(self) -> bool:
        return bool(
            self.email
            and not self.do_not_contact
            and self.email_verified not in {VerificationStatus.INVALID, VerificationStatus.BOUNCED}
        )
