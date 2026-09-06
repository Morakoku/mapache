"""Schemas del contrato Hermes → Mapache (LOOP-16)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.core.enums import JobStatus, JobType
from app.schemas.common import APIModel


class HermesDispatchIn(APIModel):
    """Solicitud de dispatch. `job_type` se valida contra la whitelist en el
    router (SEND_BATCH y el resto quedan DENY)."""

    job_type: JobType
    payload: dict[str, Any] = Field(default_factory=dict)


class HermesJobOut(APIModel):
    """Estado de un job del contrato (solo el del cliente autenticado)."""

    job_id: uuid.UUID
    job_type: str
    status: JobStatus
    progress_current: int = 0
    progress_total: int | None = None
    progress_message: str | None = None
    error_message: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime
