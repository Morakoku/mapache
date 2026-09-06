"""Schemas de llamadas: guiones, preparación y registro."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.core.enums import CallOutcome, CallScriptType
from app.schemas.common import APIModel

# ------------------------------------------------------------------ guiones


class ObjectionIn(APIModel):
    objection: str = Field(min_length=1, max_length=300)
    response: str = Field(min_length=1)


class CallScriptIn(APIModel):
    name: str = Field(min_length=2, max_length=160)
    script_type: CallScriptType
    service_id: uuid.UUID | None = None
    opening: str = Field(min_length=1)
    context: str | None = None
    questions: list[str] = Field(default_factory=list, max_length=15)
    value_pitch: str | None = None
    close: str | None = None
    objections: list[ObjectionIn] = Field(default_factory=list, max_length=20)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("questions")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return [q.strip() for q in v if q and q.strip()]


class CallScriptUpdate(APIModel):
    name: str | None = None
    script_type: CallScriptType | None = None
    service_id: uuid.UUID | None = None
    opening: str | None = None
    context: str | None = None
    questions: list[str] | None = None
    value_pitch: str | None = None
    close: str | None = None
    objections: list[ObjectionIn] | None = None
    is_active: bool | None = None


class ObjectionOut(APIModel):
    objection: str
    response: str


class CallScriptOut(APIModel):
    id: uuid.UUID
    name: str
    script_type: CallScriptType
    service_id: uuid.UUID | None
    opening: str
    context: str | None
    questions: list[str]
    value_pitch: str | None
    close: str | None
    objections: list[ObjectionOut]
    is_active: bool
    is_system: bool
    times_used: int
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ preparar


class RenderedScriptOut(APIModel):
    """El guion con los datos del prospecto ya sustituidos."""

    script_id: uuid.UUID
    name: str
    script_type: CallScriptType
    opening: str
    context: str | None
    questions: list[str]
    value_pitch: str | None
    close: str | None
    objections: list[ObjectionOut]
    missing: list[str]


class CallBriefOut(APIModel):
    """Lo que hay que tener delante antes de marcar.

    `blocked_reason` es un no: alguien pidió expresamente que no le llamen.
    Los `warnings` son cosas que conviene saber pero no impiden la llamada.
    """

    lead_id: uuid.UUID
    company_name: str
    contact_name: str | None
    contact_id: uuid.UUID | None
    phone: str | None
    phone_source: str | None
    suggested_type: CallScriptType
    suggested_reason: str
    script: RenderedScriptOut | None
    available_types: list[CallScriptType]
    warnings: list[str]
    blocked_reason: str | None
    can_call: bool
    previous_calls: int
    last_call_at: datetime | None
    last_call_outcome: CallOutcome | None


# ------------------------------------------------------------------ registro


class CallLogIn(APIModel):
    outcome: CallOutcome
    script_id: uuid.UUID | None = None
    phone: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    notes: str | None = None
    occurred_at: datetime | None = None
    # Mover al prospecto a la etapa que sugiere el resultado. Por defecto no:
    # registrar lo que pasó no debería mover nada por sorpresa.
    apply_suggested_stage: bool = False
    # "Recuérdame llamar el martes": crea el seguimiento en la misma acción.
    follow_up_at: datetime | None = None


class CallLogOut(APIModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    contact_id: uuid.UUID | None
    script_id: uuid.UUID | None
    phone: str | None
    outcome: CallOutcome
    outcome_label: str = ""
    duration_seconds: int | None
    notes: str | None
    attempt: int
    occurred_at: datetime
    created_at: datetime
    company_name: str = ""


class CallLogResultOut(APIModel):
    """Lo que la interfaz necesita saber tras registrar la llamada."""

    call: CallLogOut
    stage_moved: bool
    follow_up_created: bool
    contact_blocked: bool
