"""Schemas de la Fase 4 — cuentas, plantillas, correo y configuración."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator

from app.core.enums import (
    AccountStatus,
    AIProvider,
    Channel,
    Direction,
    EmailEventType,
    EmailStatus,
    MailProviderType,
    ReplyIntent,
    SerpProvider,
    StageType,
)
from app.schemas.common import APIModel

if TYPE_CHECKING:  # pragma: no cover - solo para tipar la conversión
    from app.services.email_svc import EmailDraft, SendOutcome

# ------------------------------------------------------------------ cuentas


class SmtpAccountIn(APIModel):
    email: str
    display_name: str | None = None
    smtp_host: str
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    imap_host: str | None = None
    imap_port: int | None = Field(default=993, ge=1, le=65535)
    imap_user: str | None = None
    imap_password: str | None = None
    is_default: bool = False


class AccountUpdate(APIModel):
    display_name: str | None = None
    is_default: bool | None = None


class EmailAccountOut(APIModel):
    """Salida de una cuenta conectada.

    **No incluye ningún campo `*_enc`**: los tokens y contraseñas no salen
    nunca de la base, ni siquiera cifrados.
    """

    id: uuid.UUID
    provider: MailProviderType
    email: str
    display_name: str | None
    status: AccountStatus
    is_default: bool
    is_oauth: bool
    last_synced_at: datetime | None
    watch_expires_at: datetime | None
    sync_error: str | None
    sent_today: int
    sent_this_hour: int
    last_sent_at: datetime | None
    created_at: datetime


class OAuthProvidersOut(APIModel):
    """Proveedores OAuth disponibles.

    Vacío cuando no hay credenciales configuradas: la UI entonces ofrece solo
    SMTP en vez de un botón que fallaría.
    """

    available: list[str]
    smtp_always_available: bool = True
    setup_hint: str | None = None


# ------------------------------------------------------------------ plantillas


class TemplateIn(APIModel):
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(default="first_contact", max_length=40)
    service_id: uuid.UUID | None = None
    subject: str = Field(min_length=1)
    body_text: str = Field(min_length=1)
    body_html: str | None = None
    is_active: bool = True

    @field_validator("name", "subject")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class TemplateUpdate(APIModel):
    name: str | None = None
    category: str | None = None
    service_id: uuid.UUID | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    is_active: bool | None = None


class TemplateOut(APIModel):
    id: uuid.UUID
    name: str
    category: str
    service_id: uuid.UUID | None
    subject: str
    body_text: str
    body_html: str | None
    variables_used: list[str]
    is_active: bool
    times_used: int
    open_rate: Decimal | None
    reply_rate: Decimal | None
    created_at: datetime
    updated_at: datetime


class TemplateVariableOut(APIModel):
    key: str
    label: str


# ------------------------------------------------------------------ envío


class PreviewIn(APIModel):
    lead_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    template_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    subject: str | None = None
    body_text: str | None = None


class DraftOut(APIModel):
    """Borrador editable antes de enviar (Módulo 9)."""

    lead_id: uuid.UUID
    company_name: str
    to_email: str | None
    contact_name: str | None
    subject: str
    body_text: str
    body_html: str | None
    template_id: uuid.UUID | None
    warnings: list[str]
    blocked_reason: str | None
    can_send: bool

    @classmethod
    def from_draft(cls, draft: EmailDraft) -> DraftOut:
        # `can_send` es propiedad, no campo, así que no viene en `asdict`.
        return cls(**asdict(draft), can_send=draft.can_send)


class PersonalizeIn(APIModel):
    lead_id: uuid.UUID
    template_id: uuid.UUID | None = None
    # "tu" | "usted". Por defecto, el de Configuración.
    tone: str | None = None
    goal: str | None = None


class PersonalizedDraftOut(APIModel):
    """Borrador generado por IA. **No se envía nada al devolverlo.**

    El envío es un segundo paso explícito con este texto ya editable, y
    `was_edited_by_user` registra si el usuario lo tocó.
    """

    lead_id: uuid.UUID
    subject: str
    body_text: str
    reasoning: str
    observation: str
    cta: str
    model: str
    estimated_cost_usd: float
    warnings: list[str]
    # False cuando la IA no estaba disponible y esto es la plantilla de siempre.
    is_ai_generated: bool


class AIProviderOptionOut(APIModel):
    """Un proveedor que se puede elegir, con dónde sacar su clave."""

    provider: AIProvider
    label: str
    suggested_model: str
    api_keys_url: str


class AIStatusOut(APIModel):
    """Qué puede hacer la IA ahora mismo.

    La UI la usa para decidir si enseñar el botón de "generar con IA" o
    esconderlo: sin clave no tiene sentido ofrecerlo.
    """

    configured: bool
    enabled: bool
    provider: AIProvider = AIProvider.ANTHROPIC
    model: str
    tone: str
    # Si hay clave guardada. Nunca se devuelve la clave en sí.
    has_stored_key: bool = False
    setup_hint: str | None = None
    estimated_cost_per_email_usd: float | None = None
    providers: list[AIProviderOptionOut] = Field(default_factory=list)


class AIKeyIn(APIModel):
    """Alta o cambio de la clave de IA.

    `api_key` es opcional al cambiar solo de modelo o proveedor: si no viene,
    se conserva la que ya estuviera guardada.
    """

    provider: AIProvider
    api_key: str | None = Field(default=None, min_length=8, max_length=400)
    model: str | None = Field(default=None, max_length=60)


class SerpKeyIn(APIModel):
    """Credenciales del buscador web.

    `api_key` es opcional al cambiar solo el motor: si no viene, se conserva
    la que ya estuviera guardada. `engine_id` solo lo usa Google Custom
    Search; Brave busca en toda la web sin motor que crear.
    """

    provider: SerpProvider = SerpProvider.GOOGLE_CSE
    api_key: str | None = Field(default=None, min_length=8, max_length=200)
    engine_id: str | None = Field(default=None, max_length=60)


class SendDraftIn(APIModel):
    lead_id: uuid.UUID
    subject: str = Field(min_length=1)
    body_text: str = Field(min_length=1)
    body_html: str | None = None
    template_id: uuid.UUID | None = None
    was_edited: bool = False


class SendIn(APIModel):
    drafts: list[SendDraftIn] = Field(min_length=1, max_length=200)
    account_id: uuid.UUID | None = None


class SendOutcomeOut(APIModel):
    lead_id: uuid.UUID
    sent: bool
    email_id: uuid.UUID | None
    skip_reason: str | None
    message: str | None

    @classmethod
    def from_outcome(cls, outcome: SendOutcome) -> SendOutcomeOut:
        return cls(
            lead_id=outcome.lead_id,
            sent=outcome.sent,
            email_id=outcome.email_id,
            skip_reason=outcome.skip_reason.value if outcome.skip_reason else None,
            message=outcome.message,
        )


class SendResultOut(APIModel):
    sent: int
    skipped: int
    failed: int
    outcomes: list[SendOutcomeOut]


# ------------------------------------------------------------------ mensajes


class EmailEventOut(APIModel):
    id: int
    event_type: EmailEventType
    occurred_at: datetime
    user_agent: str | None
    is_likely_bot: bool


class EmailLinkOut(APIModel):
    id: uuid.UUID
    original_url: str
    click_count: int
    first_clicked_at: datetime | None


class EmailMessageOut(APIModel):
    id: uuid.UUID
    lead_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    direction: Direction
    from_email: str
    to_email: str
    subject: str
    status: EmailStatus
    provider: MailProviderType | None
    sent_at: datetime | None
    delivered_at: datetime | None
    opened_at: datetime | None
    clicked_at: datetime | None
    replied_at: datetime | None
    bounced_at: datetime | None
    open_count: int
    click_count: int
    bounce_type: str | None
    error_message: str | None
    is_ai_generated: bool
    created_at: datetime


class EmailDetailOut(EmailMessageOut):
    events: list[EmailEventOut] = Field(default_factory=list)
    links: list[EmailLinkOut] = Field(default_factory=list)


# ------------------------------------------------------------------ conversaciones


class ConversationMessageOut(APIModel):
    id: uuid.UUID
    channel: Channel
    direction: Direction
    author_name: str | None
    body_text: str
    body_html: str | None
    snippet: str | None
    occurred_at: datetime
    is_automated: bool


class ConversationOut(APIModel):
    """Fila de la bandeja.

    Lleva el nombre de la empresa y su etapa porque una bandeja que solo dice
    "conversación 3f2a…" no sirve para trabajar, y resolverlo en el cliente
    costaba una petición por fila.
    """

    id: uuid.UUID
    lead_id: uuid.UUID
    contact_id: uuid.UUID | None
    channel: Channel
    subject: str | None
    status: str
    is_unread: bool
    message_count: int
    last_message_at: datetime | None
    last_direction: Direction | None
    company_name: str = ""
    contact_name: str | None = None
    stage_name: str | None = None
    stage_type: StageType | None = None
    reply_intent: ReplyIntent | None = None


class IntentOut(APIModel):
    """Intención detectada en la respuesta del prospecto (Módulo 15).

    Es una **sugerencia**: `is_confident` en falso significa que la UI debe
    mostrarla atenuada y no ofrecer aplicarla de un clic.
    """

    reply_intent: ReplyIntent | None
    confidence: float | None
    summary: str | None
    suggested_stage: StageType | None
    reply_points: list[str] = Field(default_factory=list)
    source: str | None
    reviewed: bool
    is_confident: bool


class ConversationDetailOut(ConversationOut):
    messages: list[ConversationMessageOut] = Field(default_factory=list)
    intent: IntentOut | None = None


class IntentIn(APIModel):
    """Corrección manual de la intención. Manda siempre sobre la de la IA."""

    reply_intent: ReplyIntent
    # Aplicar además la etapa sugerida. Por defecto no: mover un prospecto es
    # decisión del usuario, no efecto secundario de corregir una etiqueta.
    apply_suggested_stage: bool = False


class ReplyIn(APIModel):
    subject: str | None = None
    body_text: str = Field(min_length=1)


# ------------------------------------------------------------------ supresión


class SuppressionIn(APIModel):
    email: str | None = None
    domain: str | None = None
    reason: str = Field(default="manual", max_length=40)
    notes: str | None = None


class SuppressionOut(APIModel):
    id: uuid.UUID
    email: str | None
    domain: str | None
    reason: str
    notes: str | None
    created_at: datetime


# ------------------------------------------------------------------ configuración


class SettingsOut(APIModel):
    sender_name: str
    default_account_id: uuid.UUID | None
    reply_to: str | None
    tracking_domain: str | None
    address_of_sender: str | None

    country_code: str
    currency: str
    locale: str
    timezone: str

    daily_send_limit: int
    hourly_send_limit: int
    min_seconds_between: int
    send_window_start: time
    send_window_end: time
    skip_weekends: bool
    warmup_enabled: bool
    warmup_started_on: date | None
    # Límite real de hoy, ya con la rampa aplicada. La UI muestra este, no el
    # configurado: son distintos durante las tres primeras semanas.
    effective_daily_limit: int | None = None

    ai_enabled: bool
    ai_model: str
    ai_tone: str

    discovery_provider: str
    # Si el buscador de perfiles sociales está configurado. La clave nunca sale.
    serp_configured: bool = False
    serp_provider: SerpProvider = SerpProvider.GOOGLE_CSE
    serp_engine_id: str | None = None
    scraper_concurrency: int
    score_weights: dict[str, Any]
    automations_paused: bool


class SettingsUpdate(APIModel):
    sender_name: str | None = None
    default_account_id: uuid.UUID | None = None
    reply_to: str | None = None
    tracking_domain: str | None = None
    address_of_sender: str | None = None
    daily_send_limit: int | None = Field(default=None, ge=1, le=2000)
    hourly_send_limit: int | None = Field(default=None, ge=1, le=500)
    min_seconds_between: int | None = Field(default=None, ge=0, le=3600)
    send_window_start: time | None = None
    send_window_end: time | None = None
    skip_weekends: bool | None = None
    warmup_enabled: bool | None = None
    ai_enabled: bool | None = None
    ai_model: str | None = None
    ai_tone: str | None = None
    discovery_provider: str | None = None
    serp_engine_id: str | None = None
    automations_paused: bool | None = None
