"""Buzones conectados: Gmail, Outlook o SMTP genérico (decisión D13).

Los tokens se guardan cifrados con Fernet y no salen nunca por la API. El
schema de salida expone solo lo que la UI necesita para pintar la lista de
cuentas conectadas.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AccountStatus, MailProviderType
from app.models.base import OwnedModel, pg_enum


class EmailAccount(OwnedModel):
    __tablename__ = "email_accounts"

    provider: Mapped[MailProviderType] = mapped_column(
        pg_enum(MailProviderType, "mail_provider"), nullable=False
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[AccountStatus] = mapped_column(
        pg_enum(AccountStatus, "account_status"),
        nullable=False,
        server_default=AccountStatus.ACTIVE.value,
    )

    # --- OAuth (GMAIL / MICROSOFT) ---
    oauth_access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    oauth_scopes: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- SMTP / IMAP ---
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imap_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- sincronización de entrada ---
    # historyId (Gmail) / deltaLink (Graph) / UID (IMAP): cada proveedor usa lo
    # suyo, pero el worker solo necesita "por dónde iba".
    sync_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # El `watch` de Gmail dura 7 días y la suscripción de Graph, 3. Si caducan,
    # las respuestas dejan de llegar en silencio — por eso está indexado.
    watch_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- contadores de envío ---
    sent_today: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sent_this_hour: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    counters_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        Index(
            "uq_email_accounts_owner_email",
            "owner_id",
            text("lower(email)"),
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_email_accounts_unhealthy",
            "status",
            postgresql_where=text("status <> 'ACTIVE'"),
        ),
        Index(
            "ix_email_accounts_watch_expiry",
            "watch_expires_at",
            postgresql_where=text("watch_expires_at IS NOT NULL"),
        ),
    )

    @property
    def is_oauth(self) -> bool:
        return self.provider in {MailProviderType.GMAIL, MailProviderType.MICROSOFT}

    @property
    def is_usable(self) -> bool:
        return self.status == AccountStatus.ACTIVE
