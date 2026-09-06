"""Schemas del CRM (Fase 3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from app.core.enums import (
    ActivityType,
    ActorType,
    LeadStatus,
    ReplyIntent,
    SourceType,
    StageType,
    VerificationStatus,
)
from app.schemas.common import APIModel
from app.schemas.prospecting import CompanySocialOut

# ------------------------------------------------------------------ contactos


class ContactIn(APIModel):
    company_id: uuid.UUID
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    full_name: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=160)
    seniority: str | None = None
    email: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    linkedin_url: str | None = None
    source: SourceType = SourceType.MANUAL
    is_primary: bool = False
    notes: str | None = None


class ContactUpdate(APIModel):
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    job_title: str | None = None
    seniority: str | None = None
    email: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    linkedin_url: str | None = None
    is_primary: bool | None = None
    do_not_contact: bool | None = None
    notes: str | None = None


class ContactOut(APIModel):
    id: uuid.UUID
    company_id: uuid.UUID
    first_name: str | None
    last_name: str | None
    full_name: str | None
    display_name: str
    job_title: str | None
    seniority: str | None
    email: str | None
    email_verified: VerificationStatus
    is_role_email: bool
    phone: str | None
    whatsapp: str | None
    linkedin_url: str | None
    source: SourceType
    is_primary: bool
    do_not_contact: bool
    is_contactable: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ pipeline


class StageIn(APIModel):
    name: str = Field(min_length=1, max_length=80)
    stage_key: str | None = Field(default=None, max_length=60)
    stage_type: StageType
    position: int | None = None
    color: str = Field(default="#64748b", pattern=r"^#[0-9a-fA-F]{6}$")
    auto_advance_on: list[str] = Field(default_factory=list)


class StageUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    position: int | None = None
    is_default: bool | None = None
    auto_advance_on: list[str] | None = None


class StageOut(APIModel):
    id: uuid.UUID
    name: str
    stage_key: str
    stage_type: StageType
    position: int
    color: str
    is_default: bool
    is_won: bool
    is_lost: bool
    is_system: bool
    auto_advance_on: list[str]


class StageReorderIn(APIModel):
    ordered_ids: list[uuid.UUID] = Field(min_length=1)


class StageDeleteIn(APIModel):
    move_to_stage_id: uuid.UUID | None = None


# ------------------------------------------------------------------ leads


class LeadIn(APIModel):
    company_id: uuid.UUID
    service_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    stage_id: uuid.UUID | None = None


class LeadBulkIn(APIModel):
    company_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    service_id: uuid.UUID


class LeadUpdate(APIModel):
    contact_id: uuid.UUID | None = None
    estimated_value: Decimal | None = Field(default=None, ge=0)
    next_follow_up_at: datetime | None = None
    reply_intent: ReplyIntent | None = None


class LeadStageIn(APIModel):
    stage_id: uuid.UUID
    reason: str | None = None


class LeadBulkStageIn(APIModel):
    lead_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    stage_id: uuid.UUID
    reason: str | None = None


class LeadWinIn(APIModel):
    value: float | None = Field(default=None, ge=0)
    note: str | None = None


class LeadLoseIn(APIModel):
    reason: str = Field(min_length=1)


class CompanyBriefOut(APIModel):
    """La empresa como se ve desde el prospecto.

    Lleva la ficha de Google y las redes porque son lo que se mira justo antes
    de llamar: comprobar que el negocio existe y tener un canal alternativo si
    no contestan al teléfono.
    """

    id: uuid.UUID
    name: str
    city: str | None
    category: str | None
    email: str | None
    phone: str | None
    whatsapp: str | None
    website_domain: str | None
    google_maps_url: str | None
    socials: list[CompanySocialOut] = Field(default_factory=list)


class ContactBriefOut(APIModel):
    id: uuid.UUID
    display_name: str
    job_title: str | None
    email: str | None
    is_contactable: bool


class StageBriefOut(APIModel):
    id: uuid.UUID
    name: str
    stage_key: str
    stage_type: StageType
    color: str


class ServiceBriefOut(APIModel):
    id: uuid.UUID
    name: str


class LeadOut(APIModel):
    """Tarjeta del Kanban y fila de la tabla de prospectos.

    Trae los objetos anidados en versión breve para que la UI pinte una
    tarjeta completa sin encadenar peticiones.
    """

    id: uuid.UUID
    company: CompanyBriefOut
    contact: ContactBriefOut | None
    stage: StageBriefOut
    service: ServiceBriefOut
    status: LeadStatus
    score: int
    engagement_score: int
    engagement_band: str
    reply_intent: ReplyIntent | None
    estimated_value: Decimal | None
    currency: str
    first_contact_at: datetime | None
    last_contact_at: datetime | None
    last_activity_at: datetime | None
    next_follow_up_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LeadDetailOut(LeadOut):
    score_breakdown: dict[str, Any] | None
    score_computed_at: datetime | None
    replied_at: datetime | None
    won_at: datetime | None
    lost_at: datetime | None
    lost_reason: str | None
    sequence_step: int
    sequence_paused: bool


class LeadEngagementOut(APIModel):
    """Fila de un segmento de seguimiento (Módulo 14).

    Trae los hechos que justifican el segmento —cuántas veces abrió, cuándo fue
    la última— para que la decisión de volver a escribir no dependa de confiar
    en una etiqueta.
    """

    lead: LeadOut
    emails_sent: int
    last_sent_at: datetime | None
    opens: int
    last_opened_at: datetime | None
    clicks: int
    last_clicked_at: datetime | None
    bounces: int
    days_since_open: int | None
    days_since_contact: int | None
    note: str


class SegmentSummaryOut(APIModel):
    """Cuántos prospectos hay en cada segmento."""

    opened_no_reply: int
    clicked_no_reply: int
    sent_no_open: int
    replied: int
    bounced: int


class LeadRescoreIn(APIModel):
    """Recálculo selectivo. Sin lista, se recalcula todo el embudo."""

    lead_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)


class LeadBulkResultOut(APIModel):
    created: int
    skipped_existing: int
    skipped_no_company: int
    lead_ids: list[uuid.UUID]


class BoardColumnOut(APIModel):
    stage: StageOut
    leads: list[LeadOut]
    total: int
    total_value: Decimal


class BoardOut(APIModel):
    columns: list[BoardColumnOut]


# ------------------------------------------------------------------ historial


class StageHistoryOut(APIModel):
    id: uuid.UUID
    from_stage_type: StageType | None
    to_stage_type: StageType
    actor: ActorType
    reason: str | None
    entered_at: datetime
    duration_seconds: int | None


# ------------------------------------------------------------------ actividades


class ActivityOut(APIModel):
    id: int
    lead_id: uuid.UUID | None
    company_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    activity_type: ActivityType
    actor: ActorType
    title: str
    description: str | None
    # `serialization_alias`, no `alias`: con `alias` + `from_attributes`,
    # Pydantic busca el atributo `metadata` en el modelo ORM y se encuentra el
    # objeto `MetaData` de SQLAlchemy en vez de la columna. Así se lee de
    # `activity_metadata` y se publica como `metadata`.
    activity_metadata: dict[str, Any] | None = Field(default=None, serialization_alias="metadata")
    occurred_at: datetime


class NoteIn(APIModel):
    text: str = Field(min_length=1)


# ------------------------------------------------------------------ tareas


class TaskIn(APIModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    lead_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    due_at: datetime | None = None
    priority: int = Field(default=2, ge=1, le=3)

    @field_validator("title")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class TaskUpdate(APIModel):
    title: str | None = None
    description: str | None = None
    due_at: datetime | None = None
    priority: int | None = Field(default=None, ge=1, le=3)


class TaskOut(APIModel):
    id: uuid.UUID
    lead_id: uuid.UUID | None
    company_id: uuid.UUID | None
    title: str
    description: str | None
    due_at: datetime | None
    priority: int
    completed_at: datetime | None
    is_done: bool
    created_at: datetime
