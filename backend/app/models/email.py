"""Módulos 10, 12, 13 y 16 — plantillas, conversaciones y correo.

Arquitectura multicanal (decisiones D6 y D14), herencia por tabla de clase:

    conversations
      └── conversation_messages   (cuerpo, agnóstico de canal)  <- lo lee la UI
              ├── email_messages      (sobre email)             Fase 4
              └── whatsapp_messages   (sobre WhatsApp)          Fase 10

`email_events` es el log inmutable; los contadores de `email_messages` son
derivados y se pueden recalcular desde él si se corrompen.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    Channel,
    Direction,
    EmailEventType,
    EmailStatus,
    MailProviderType,
    ReplyIntent,
    StageType,
)
from app.models.base import Base, BaseModel, OwnedModel, pg_enum

if TYPE_CHECKING:  # pragma: no cover - solo para el tipo de la relación
    from app.models.lead import Lead


class EmailTemplate(OwnedModel):
    """Módulo 10 — plantillas reutilizables."""

    __tablename__ = "email_templates"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # first_contact | followup_1 | followup_2 | reactivation | proposal | meeting
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )

    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Se extraen al guardar, para que la UI avise si falta alguna variable.
    variables_used: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    times_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Materializadas por un job nocturno; calcularlas en cada listado sería caro.
    open_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    reply_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    __table_args__ = (
        Index(
            "uq_email_templates_owner_name",
            "owner_id",
            text("lower(name)"),
            unique=True,
            # Ver nota en `services`: con `owner_id` NULL, sin
            # `NULLS NOT DISTINCT` el índice único no impide duplicados.
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_email_templates_category", "category"),
    )


class Conversation(OwnedModel):
    """Módulo 16 — hilo con un prospecto, en cualquier canal."""

    __tablename__ = "conversations"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[Channel] = mapped_column(
        pg_enum(Channel, "channel"), nullable=False, server_default=Channel.EMAIL.value
    )

    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    # email: Message-ID raíz normalizado. whatsapp: wa_id del contacto.
    thread_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # OPEN | AWAITING_REPLY | NEEDS_REPLY | CLOSED
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="OPEN")
    is_unread: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_direction: Mapped[Direction | None] = mapped_column(
        pg_enum(Direction, "direction"), nullable=True
    )

    # --- intención detectada (Módulo 15) ---
    # La IA sugiere, el usuario decide: esto es una sugerencia hasta que
    # `intent_reviewed` diga lo contrario. Nada de esto mueve un lead solo.
    reply_intent: Mapped[ReplyIntent | None] = mapped_column(
        pg_enum(ReplyIntent, "reply_intent"), nullable=True
    )
    # Por debajo de 0.7 la UI muestra la sugerencia atenuada y no ofrece
    # aplicarla de un clic.
    intent_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    intent_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Etapa sugerida, en tipo estable — no en id: si el usuario renombra o
    # borra una etapa, la sugerencia guardada sigue teniendo sentido.
    intent_suggested_stage: Mapped[StageType | None] = mapped_column(
        pg_enum(StageType, "stage_type"), nullable=True
    )
    intent_reply_points: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    # "ai" | "rules". Permite medir con el tiempo si la IA acierta más que las
    # palabras clave, en vez de suponerlo.
    intent_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # El usuario ya aceptó o corrigió la sugerencia: deja de proponerse.
    intent_reviewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.occurred_at",
    )

    # La bandeja necesita saber de quién es cada hilo. Sin esto, pintar una
    # lista de 50 conversaciones costaba 50 peticiones más desde el frontend.
    # `joined` porque el prospecto se usa en todas las lecturas del hilo, y
    # arrastra ya cargados empresa, contacto y etapa.
    lead: Mapped[Lead] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "lead_id", "channel", "thread_key", name="uq_conversations_lead_channel_thread"
        ),
        Index("ix_conversations_status", "owner_id", "status", "last_message_at"),
        Index(
            "ix_conversations_intent_pending",
            "reply_intent",
            postgresql_where=text("reply_intent IS NOT NULL AND intent_reviewed IS FALSE"),
        ),
        Index(
            "ix_conversations_unread",
            "owner_id",
            "channel",
            postgresql_where=text("is_unread"),
        ),
    )


class ConversationMessage(OwnedModel):
    """Mensaje agnóstico de canal (decisión D6).

    Es lo que consulta la bandeja de entrada. No sabe qué es SMTP: cuando
    entre WhatsApp se insertará aquí con `channel='WHATSAPP'` y el inbox lo
    pintará sin tocar una línea del frontend.
    """

    __tablename__ = "conversation_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(pg_enum(Channel, "channel"), nullable=False)
    direction: Mapped[Direction] = mapped_column(pg_enum(Direction, "direction"), nullable=False)

    author_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Salió de una secuencia automática, no de una acción manual.
    is_automated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_conversation_messages_thread", "conversation_id", "occurred_at"),)


class EmailMessage(OwnedModel):
    """Sobre del canal email: cabeceras, entrega y tracking.

    El cuerpo vive en `conversation_messages`. Aquí solo lo específico de
    email, para que añadir WhatsApp no toque nada de esta tabla.
    """

    __tablename__ = "email_messages"

    conversation_message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversation_messages.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    # De qué seguimiento salió, si salió de uno. Permite responder "¿este
    # correo lo mandé yo o la secuencia?" mirando una columna.
    follow_up_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("follow_ups.id", ondelete="SET NULL"), nullable=True
    )
    email_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )

    direction: Mapped[Direction] = mapped_column(pg_enum(Direction, "direction"), nullable=False)
    from_email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    to_email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    cc: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[EmailStatus] = mapped_column(
        pg_enum(EmailStatus, "email_status"),
        nullable=False,
        server_default=EmailStatus.DRAFT.value,
    )
    provider: Mapped[MailProviderType | None] = mapped_column(
        pg_enum(MailProviderType, "mail_provider"), nullable=True
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # threadId de Gmail / conversationId de Graph: threading nativo, más fiable
    # que reconstruirlo desde las cabeceras.
    provider_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    references_header: Mapped[str | None] = mapped_column(Text, nullable=True)

    tracking_token: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()")
    )
    unsubscribe_token: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()")
    )
    tracking_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    open_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    bounce_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_ai_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    ai_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Permite medir con el tiempo si los borradores de la IA valen: si el
    # usuario reescribe el 90%, el prompt necesita trabajo.
    was_edited_by_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    links: Mapped[list[EmailLink]] = relationship(
        back_populates="email", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("uq_email_messages_tracking_token", "tracking_token", unique=True),
        Index("uq_email_messages_unsubscribe_token", "unsubscribe_token", unique=True),
        Index(
            "uq_email_messages_provider_message_id",
            "provider_message_id",
            unique=True,
            postgresql_where=text("provider_message_id IS NOT NULL"),
        ),
        Index("ix_email_messages_lead", "lead_id", "created_at"),
        Index("ix_email_messages_conversation", "conversation_id", "created_at"),
        Index(
            "ix_email_messages_thread",
            "provider_thread_id",
            postgresql_where=text("provider_thread_id IS NOT NULL"),
        ),
        Index(
            "ix_email_messages_pending",
            "status",
            postgresql_where=text("status IN ('QUEUED', 'SENDING')"),
        ),
        Index("ix_email_messages_account", "email_account_id", "sent_at"),
    )


class EmailLink(BaseModel):
    """Módulo 13 — link rastreado dentro de un correo."""

    __tablename__ = "email_links"

    email_message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False
    )
    tracking_token: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()")
    )
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)

    click_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_clicked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    email: Mapped[EmailMessage] = relationship(back_populates="links")

    __table_args__ = (
        Index("uq_email_links_token", "tracking_token", unique=True),
        Index("ix_email_links_message", "email_message_id"),
    )


class EmailEvent(Base):
    """Log inmutable de eventos (Módulo 12).

    Fuente de verdad del tracking. Los contadores de `email_messages` son
    derivados: si se corrompen, se recalculan desde aquí.
    """

    __tablename__ = "email_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email_message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False
    )
    email_link_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_links.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[EmailEventType] = mapped_column(
        pg_enum(EmailEventType, "email_event_type"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    # Apple MPP y los proxies de Gmail precargan imágenes: esas aperturas se
    # registran, pero no suman a los contadores ni al engagement (§11.3).
    is_likely_bot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (
        Index("ix_email_events_message", "email_message_id", "occurred_at"),
        Index("ix_email_events_type", "event_type", "occurred_at"),
    )


class SuppressionEntry(OwnedModel):
    """Lista de no contactar.

    Se consulta en `EmailService.send()` antes de cada envío, sin excepción.
    No es un filtro de la UI: es una barrera en el servicio.
    """

    __tablename__ = "suppression_list"

    email: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # unsubscribed | hard_bounce | complaint | manual | competitor | customer
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    source_email_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_messages.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "uq_suppression_email",
            "owner_id",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "uq_suppression_domain",
            "owner_id",
            text("lower(domain)"),
            unique=True,
            postgresql_where=text("domain IS NOT NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )
