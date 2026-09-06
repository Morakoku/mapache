"""Contrato Hermes → Mapache (LOOP-16).

Dos únicas operaciones para identidades de servicio:
- POST /api/v1/hermes/dispatch   → encola un job (whitelist estricta, Idempotency-Key).
- GET  /api/v1/hermes/jobs/{id}  → estado de un job de la identidad autenticada.

El enforcement de L1 (Bearer HMAC) y L2 (hermes.dispatch / hermes.jobs.read) lo
hace `ServiceAuthMiddleware`. Aquí solo queda la lógica del contrato: whitelist
de job_types, idempotencia obligatoria, ownership y auditoría. El resto de los
141 endpoints no pasa por este router (aislamiento garantizado por scopes).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import JobType
from app.core.exceptions import ForbiddenError, ValidationError
from app.core.idempotency import IdempotencyService
from app.core.logging import get_logger
from app.core.tenant import resolve_tenant_id
from app.models.audit import AuditLog
from app.schemas.common import JobAcceptedOut
from app.schemas.hermes import HermesDispatchIn, HermesJobOut
from app.services.job_svc import JobService

logger = get_logger(__name__)

router = APIRouter()

# Whitelist estricta del contrato. SEND_BATCH queda BLOQUEADO por decisión del
# fundador; SCORING / INBOX_SYNC / SCRAPER_HEALTH tampoco entran.
ALLOWED_DISPATCH_JOB_TYPES = frozenset(
    {JobType.DISCOVERY, JobType.ENRICHMENT, JobType.FOLLOWUP_TICK}
)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


@router.post("/dispatch", response_model=JobAcceptedOut, status_code=status.HTTP_202_ACCEPTED)
async def dispatch(
    payload: HermesDispatchIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedOut:
    """Encola un job del contrato. Devuelve 202 + job_id."""
    key = request.headers.get("idempotency-key")
    if not key or not (8 <= len(key) <= 128):
        raise ValidationError(
            "Header Idempotency-Key obligatorio (8-128 caracteres).",
            code="IDEMPOTENCY_KEY_REQUIRED",
        )
    if payload.job_type == JobType.SEND_BATCH:
        raise ForbiddenError(
            "SEND_BATCH está bloqueado para Hermes.",
            code="JOB_TYPE_BLOCKED",
            details={"job_type": "SEND_BATCH"},
        )
    if payload.job_type not in ALLOWED_DISPATCH_JOB_TYPES:
        raise ForbiddenError(
            "Tipo de job no permitido en el contrato Hermes.",
            code="JOB_TYPE_NOT_ALLOWED",
            details={"job_type": payload.job_type.value},
        )

    client_id = getattr(request.state, "service_client", None)
    settings = request.app.state.settings if hasattr(request.app.state, "settings") else None
    tenant_enforced = bool(getattr(settings, "tenant_isolation_enforced", False))
    try:
        tenant_id = resolve_tenant_id(
            request.headers.get("x-tenant-id"),
            payload.payload,
            enforced=tenant_enforced,
        )
    except ValueError as exc:
        raise ValidationError(str(exc), code=str(exc)) from exc

    # Idempotencia: la misma key no crea un segundo job (409 con job_id previo).
    idem = IdempotencyService(db)
    await idem.begin(key, request.method, request.url.path)

    job = await JobService(db).create(payload.job_type, payload.payload)
    job.client_id = client_id
    job.owner_id = tenant_id
    await idem.attach_job(key, request.method, request.url.path, job.id)

    # Auditoría del dispatch (identidad, key, path) — sin bodies ni secretos.
    db.add(
        AuditLog(
            method="POST",
            path=request.url.path,
            status=status.HTTP_202_ACCEPTED,
            duration_ms=0,
            client_ip=_client_ip(request),
            client_id=client_id,
            idempotency_key=key,
            action="hermes.dispatch",
        )
    )
    await db.commit()

    await get_job_queue().enqueue(payload.job_type, payload.payload, job_id=job.id)
    logger.info(
        "hermes_dispatch", job_id=str(job.id), job_type=payload.job_type.value, client=client_id
    )
    return JobAcceptedOut(job_id=job.id, status="QUEUED", message="Job encolado.")


@router.get("/jobs/{job_id}", response_model=HermesJobOut)
async def get_job(
    job_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HermesJobOut:
    """Estado de un job del contrato. Solo jobs de la identidad autenticada."""
    job = await JobService(db).get_or_404(job_id)
    caller = getattr(request.state, "service_client", None)
    if job.client_id != caller:
        raise ForbiddenError(
            "El job no pertenece a esta identidad.",
            code="JOB_NOT_OWNED",
            details={"job_id": str(job.id)},
        )
    settings = request.app.state.settings if hasattr(request.app.state, "settings") else None
    try:
        tenant_id = resolve_tenant_id(
            request.headers.get("x-tenant-id"),
            {},
            enforced=bool(getattr(settings, "tenant_isolation_enforced", False)),
        )
    except ValueError as exc:
        raise ValidationError(str(exc), code=str(exc)) from exc
    if tenant_id is not None and job.owner_id != tenant_id:
        raise ForbiddenError(
            "El job no pertenece a este tenant.",
            code="TENANT_MISMATCH",
            details={"job_id": str(job.id)},
        )
    return HermesJobOut(
        job_id=job.id,
        job_type=job.job_type.value,
        status=job.status,
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        progress_message=job.progress_message,
        error_message=job.error_message,
        result=job.result,
        created_at=job.created_at,
    )


__all__ = ["ALLOWED_DISPATCH_JOB_TYPES", "router"]
