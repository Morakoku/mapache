"""Schemas del Módulo 18 — secuencias y seguimientos."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from pydantic import Field, field_validator

from app.schemas.common import APIModel

# ------------------------------------------------------------------ pasos


class SequenceStepIn(APIModel):
    step_number: int | None = Field(default=None, ge=1, le=20)
    template_id: uuid.UUID
    delay_days: int = Field(default=3, ge=0, le=180)
    delay_hours: int = Field(default=0, ge=0, le=23)
    # "always" | "not_replied" | "opened_not_replied" | "not_opened"
    condition: str | None = None
    send_window_start: time | None = None
    send_window_end: time | None = None
    skip_weekends: bool = True


class SequenceStepOut(APIModel):
    id: uuid.UUID
    step_number: int
    template_id: uuid.UUID
    delay_days: int
    delay_hours: int
    condition: dict[str, Any] | None
    condition_key: str
    send_window_start: time | None
    send_window_end: time | None
    skip_weekends: bool


# ------------------------------------------------------------------ secuencias


class SequenceIn(APIModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    service_id: uuid.UUID | None = None
    is_active: bool = True
    # Desactivar esto es decidir escribirle a quien ya te contestó. Se puede,
    # pero es una decisión consciente.
    stop_on_reply: bool = True
    stop_on_click: bool = False
    stop_on_meeting: bool = True
    max_steps: int = Field(default=3, ge=1, le=20)
    steps: list[SequenceStepIn] = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class SequenceUpdate(APIModel):
    name: str | None = None
    description: str | None = None
    service_id: uuid.UUID | None = None
    is_active: bool | None = None
    stop_on_reply: bool | None = None
    stop_on_click: bool | None = None
    stop_on_meeting: bool | None = None
    max_steps: int | None = Field(default=None, ge=1, le=20)
    steps: list[SequenceStepIn] | None = None


class SequenceOut(APIModel):
    id: uuid.UUID
    name: str
    description: str | None
    service_id: uuid.UUID | None
    is_active: bool
    stop_on_reply: bool
    stop_on_click: bool
    stop_on_meeting: bool
    max_steps: int
    steps: list[SequenceStepOut]
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ calendario


class EnrollIn(APIModel):
    lead_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    # Punto de partida del cálculo. Por defecto, ahora.
    start_at: datetime | None = None


class PlannedStepOut(APIModel):
    step_number: int
    template_id: uuid.UUID
    template_name: str
    scheduled_at: datetime
    condition: str


class SchedulePreviewOut(APIModel):
    lead_id: uuid.UUID
    company_name: str
    steps: list[PlannedStepOut]
    # Los que empiezan por BLOQUEA impiden inscribir a ese prospecto.
    warnings: list[str]


class EnrollResultOut(APIModel):
    """Lo que la UI enseña tras inscribir: cuántos entraron y el calendario."""

    enrolled: int
    skipped: int
    first_send_at: datetime | None
    last_send_at: datetime | None
    preview: list[SchedulePreviewOut]


# ------------------------------------------------------------------ seguimientos


class FollowUpIn(APIModel):
    lead_id: uuid.UUID
    scheduled_at: datetime
    template_id: uuid.UUID | None = None
    note: str | None = None


class FollowUpUpdate(APIModel):
    scheduled_at: datetime | None = None
    template_id: uuid.UUID | None = None
    note: str | None = None


class FollowUpOut(APIModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    sequence_id: uuid.UUID | None
    sequence_step: int | None
    template_id: uuid.UUID | None
    scheduled_at: datetime
    status: str
    sent_email_id: uuid.UUID | None
    executed_at: datetime | None
    skip_reason: str | None
    skip_label: str | None = None
    attempts: int
    is_manual: bool
    note: str | None
    created_at: datetime
    # La agenda se lee por empresa; el id del prospecto no dice nada.
    company_name: str = ""
    contact_name: str | None = None
    stage_name: str | None = None
    stage_type: str | None = None
