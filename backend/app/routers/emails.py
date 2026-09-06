"""Módulos 9 y 12 — preview, envío y seguimiento de correos.

El envío nunca es síncrono: 50 correos con el ritmo configurado son cuarenta
minutos. El endpoint encola un `SEND_BATCH` y devuelve 202; el progreso se
sigue por `/jobs/{id}`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.personalizer import personalize
from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import Direction, EmailStatus, JobType
from app.core.exceptions import NotFoundError, ValidationError
from app.core.idempotency import require_idempotency
from app.models.email import EmailEvent, EmailMessage, EmailTemplate
from app.repositories.base import BaseRepository
from app.schemas.common import JobAcceptedOut, Page
from app.schemas.email import (
    DraftOut,
    EmailDetailOut,
    EmailEventOut,
    EmailLinkOut,
    EmailMessageOut,
    PersonalizedDraftOut,
    PersonalizeIn,
    PreviewIn,
    SendIn,
)
from app.services.email_svc import EmailService
from app.services.job_svc import JobService
from app.services.lead_svc import LeadService

router = APIRouter(dependencies=[Depends(require_idempotency)])


class EmailRepository(BaseRepository[EmailMessage]):
    model = EmailMessage


@router.post("/preview", response_model=list[DraftOut])
async def preview(payload: PreviewIn, db: AsyncSession = Depends(get_db)) -> list[DraftOut]:
    """Borradores renderizados, sin enviar nada (Módulo 9).

    Cada borrador trae sus avisos y, si algún guardrail lo bloquearía, el
    motivo. El usuario ve el problema antes de pulsar enviar, no después.
    """
    emails = EmailService(db)
    leads = LeadService(db)

    settings = await emails.get_settings_row()
    account = await emails.resolve_account(payload.account_id)
    template = await db.get(EmailTemplate, payload.template_id) if payload.template_id else None
    if payload.template_id and template is None:
        raise NotFoundError.for_entity("template", payload.template_id)

    if template is None and not (payload.subject and payload.body_text):
        raise ValidationError(
            "Indica una plantilla o un asunto y un cuerpo.",
            code="TEMPLATE_OR_BODY_REQUIRED",
        )

    drafts = []
    for lead_id in payload.lead_ids:
        lead = await leads.get_or_404(lead_id)
        draft = await emails.build_draft(
            lead,
            template=template,
            settings=settings,
            account=account,
            override_subject=payload.subject,
            override_body=payload.body_text,
        )
        drafts.append(DraftOut.from_draft(draft))
    return drafts


@router.post("/personalize", response_model=PersonalizedDraftOut)
async def personalize_email(
    payload: PersonalizeIn,
    db: AsyncSession = Depends(get_db),
) -> PersonalizedDraftOut:
    """Genera un borrador con IA (Módulo 11).

    **No envía nada**: devuelve texto editable. Si la IA está apagada o no
    disponible, devuelve la plantilla renderizada con `is_ai_generated` en
    falso — el usuario nunca se queda sin borrador por culpa de la IA.
    """
    emails = EmailService(db)
    lead = await LeadService(db).get_or_404(payload.lead_id)
    settings = await emails.get_settings_row()
    template = await db.get(EmailTemplate, payload.template_id) if payload.template_id else None
    if payload.template_id and template is None:
        raise NotFoundError.for_entity("template", payload.template_id)

    draft = await personalize(
        db, lead, settings=settings, template=template, tone=payload.tone, goal=payload.goal
    )

    if draft is not None:
        return PersonalizedDraftOut(
            lead_id=lead.id,
            subject=draft.subject,
            body_text=draft.body_text,
            reasoning=draft.reasoning,
            observation=draft.observation,
            cta=draft.cta,
            model=draft.model,
            estimated_cost_usd=round(draft.estimated_cost_usd, 4),
            warnings=draft.warnings,
            is_ai_generated=True,
        )

    # Camino sin IA: la plantilla de siempre.
    if template is None:
        raise ValidationError(
            "La IA no está disponible y no se indicó plantilla. Elige una plantilla "
            "o configura ANTHROPIC_API_KEY.",
            code="AI_UNAVAILABLE_NO_TEMPLATE",
        )

    account = await emails.resolve_account()
    fallback = await emails.build_draft(lead, template=template, settings=settings, account=account)
    return PersonalizedDraftOut(
        lead_id=lead.id,
        subject=fallback.subject,
        body_text=fallback.body_text,
        reasoning="Generado desde la plantilla: la IA no estaba disponible.",
        observation="",
        cta="",
        model="",
        estimated_cost_usd=0.0,
        warnings=fallback.warnings,
        is_ai_generated=False,
    )


@router.post("/send", response_model=JobAcceptedOut, status_code=status.HTTP_202_ACCEPTED)
async def send(payload: SendIn, db: AsyncSession = Depends(get_db)) -> JobAcceptedOut:
    """Encola el lote de envío.

    Se valida aquí que exista una cuenta utilizable: fallar en el worker
    dejaría al usuario mirando un job fallido sin saber por qué.
    """
    emails = EmailService(db)
    account = await emails.resolve_account(payload.account_id)

    job_payload = {
        "account_id": str(account.id),
        "drafts": [
            {
                "lead_id": str(d.lead_id),
                "subject": d.subject,
                "body_text": d.body_text,
                "body_html": d.body_html,
                "template_id": str(d.template_id) if d.template_id else None,
                "was_edited": d.was_edited,
            }
            for d in payload.drafts
        ],
    }
    job = await JobService(db).create(
        JobType.SEND_BATCH, job_payload, progress_total=len(payload.drafts)
    )
    await db.commit()

    await get_job_queue().enqueue(JobType.SEND_BATCH, job_payload, job_id=job.id)
    return JobAcceptedOut(
        job_id=job.id,
        message=f"Enviando {len(payload.drafts)} correos desde {account.email}.",
    )


@router.get("", response_model=Page[EmailMessageOut])
async def list_emails(
    lead_id: uuid.UUID | None = None,
    email_status: EmailStatus | None = Query(default=None, alias="status"),
    direction: Direction | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[EmailMessageOut]:
    stmt = select(EmailMessage).order_by(EmailMessage.created_at.desc())
    if lead_id is not None:
        stmt = stmt.where(EmailMessage.lead_id == lead_id)
    if email_status is not None:
        stmt = stmt.where(EmailMessage.status == email_status)
    if direction is not None:
        stmt = stmt.where(EmailMessage.direction == direction)

    items, total = await EmailRepository(db).paginate(stmt, page=page, size=size)
    return Page.build([EmailMessageOut.model_validate(e) for e in items], total, page, size)


@router.get("/{email_id}", response_model=EmailDetailOut)
async def get_email(email_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> EmailDetailOut:
    email = await _get_or_404(db, email_id)
    events = await _events_of(db, email_id)
    detail = EmailDetailOut.model_validate(email)
    detail.events = [EmailEventOut.model_validate(e) for e in events]
    detail.links = [EmailLinkOut.model_validate(link) for link in email.links]
    return detail


@router.get("/{email_id}/events", response_model=list[EmailEventOut])
async def list_events(
    email_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[EmailEventOut]:
    await _get_or_404(db, email_id)
    return [EmailEventOut.model_validate(e) for e in await _events_of(db, email_id)]


@router.post("/{email_id}/cancel", response_model=EmailMessageOut)
async def cancel_email(
    email_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> EmailMessageOut:
    """Cancela un correo aún no enviado.

    Solo vale en QUEUED: una vez el proveedor lo aceptó, ya salió y no hay
    nada que cancelar — decir lo contrario sería mentirle al usuario.
    """
    email = await _get_or_404(db, email_id)
    if email.status is not EmailStatus.QUEUED:
        raise ValidationError(
            "Solo se puede cancelar un correo que todavía está en cola.",
            code="EMAIL_NOT_CANCELLABLE",
            details={"status": email.status.value},
        )
    email.status = EmailStatus.CANCELLED
    await db.commit()
    return EmailMessageOut.model_validate(email)


@router.post("/{email_id}/resend", response_model=JobAcceptedOut, status_code=202)
async def resend_email(
    email_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedOut:
    """Reencola un correo que falló.

    Reenviar uno ya entregado sería spam para el prospecto, así que solo se
    permite sobre FAILED y CANCELLED.
    """
    email = await _get_or_404(db, email_id)
    if email.status not in {EmailStatus.FAILED, EmailStatus.CANCELLED}:
        raise ValidationError(
            "Solo se reenvían correos fallidos o cancelados.",
            code="EMAIL_NOT_RESENDABLE",
            details={"status": email.status.value},
        )
    if email.lead_id is None:
        raise ValidationError(
            "El correo no está asociado a ningún prospecto.", code="EMAIL_WITHOUT_LEAD"
        )

    conversation_message = None
    if email.conversation_message_id is not None:
        from app.models.email import ConversationMessage

        conversation_message = await db.get(ConversationMessage, email.conversation_message_id)

    job_payload = {
        "account_id": str(email.email_account_id) if email.email_account_id else None,
        "drafts": [
            {
                "lead_id": str(email.lead_id),
                "subject": email.subject,
                "body_text": (
                    conversation_message.body_text if conversation_message else email.subject
                ),
                "body_html": conversation_message.body_html if conversation_message else None,
                # Sin plantilla: el guardrail de duplicado bloquearía el
                # reenvío del correo que ya se intentó con ella.
                "template_id": None,
                "was_edited": False,
            }
        ],
    }
    job = await JobService(db).create(JobType.SEND_BATCH, job_payload, progress_total=1)
    await db.commit()

    await get_job_queue().enqueue(JobType.SEND_BATCH, job_payload, job_id=job.id)
    return JobAcceptedOut(job_id=job.id, message=f"Reenviando a {email.to_email}.")


async def _get_or_404(db: AsyncSession, email_id: uuid.UUID) -> EmailMessage:
    result = await db.execute(
        select(EmailMessage)
        .where(EmailMessage.id == email_id)
        .options(selectinload(EmailMessage.links))
    )
    email = result.scalar_one_or_none()
    if email is None:
        raise NotFoundError.for_entity("email", email_id)
    return email


async def _events_of(db: AsyncSession, email_id: uuid.UUID) -> list[EmailEvent]:
    result = await db.execute(
        select(EmailEvent)
        .where(EmailEvent.email_message_id == email_id)
        .order_by(EmailEvent.occurred_at)
    )
    return list(result.scalars().all())
