"""Worker de seguimiento (§4.7 del diseño).

Recorre los seguimientos vencidos y decide uno a uno. El orden de las reglas
de parada no es casual: primero lo que significa "ya no toca escribir" (ya
respondió, no le interesa, está cerrado), después lo que significa "no se
puede escribir" (pausado, suprimido, rebotado). Un correo enviado después de
un "no me interesa" es peor que no enviar nada.

Fuera de la ventana horaria **no se salta el envío, se reprograma**: perder un
seguimiento porque el worker corrió a las 3 de la mañana sería un fallo del
sistema, no una decisión comercial.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_scope
from app.core.enums import ActivityType, ActorType, Direction, LeadStatus, ReplyIntent
from app.core.exceptions import DomainError
from app.core.logging import get_logger
from app.mail.guardrails import Guardrails, is_within_window
from app.models.email import EmailMessage, EmailTemplate
from app.models.lead import Lead
from app.models.sequence import (
    FOLLOWUP_PENDING,
    FOLLOWUP_SENT,
    FOLLOWUP_SKIPPED,
    FollowUp,
    Sequence,
)
from app.services.activity_svc import ActivityService
from app.services.email_svc import EmailService
from app.services.job_svc import JobService
from app.services.lead_svc import LeadService
from app.services.sequence_svc import SequenceService, next_valid_slot

logger = get_logger(__name__)

# Cuántos vencidos se procesan por vuelta. El worker corre cada 15 minutos;
# más de esto en una tanda solo sirve para saturar el proveedor de correo.
_BATCH = 100

# Motivos de descarte, tal como los ve el usuario en la ficha del prospecto.
SKIP_LABELS: dict[str, str] = {
    "replied": "Ya respondió",
    "not_interested": "Dijo que no le interesa",
    "advanced": "La conversación ya avanzó",
    "closed": "El prospecto está cerrado",
    "paused": "La secuencia está pausada",
    "suppressed": "Está en la lista de no contactar",
    "bounced": "Su correo rebotó",
    "condition_not_met": "No se cumplió la condición del paso",
    "no_template": "El paso no tiene plantilla",
    "send_failed": "Falló el envío",
}


async def run_followup_tick(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    limit = int(payload.get("limit", _BATCH))

    async with session_scope() as session:
        jobs = JobService(session)
        await jobs.mark_running(job_id)
        await session.commit()

        due = await _due(session, limit)
        await jobs.update_progress(
            job_id, current=0, total=len(due), message="Revisando seguimientos…"
        )
        await session.commit()

        counts = {"sent": 0, "skipped": 0, "rescheduled": 0, "failed": 0}

        for index, follow_up in enumerate(due, start=1):
            try:
                outcome = await _process(session, follow_up)
                counts[outcome] += 1
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - un seguimiento roto no para la tanda
                await session.rollback()
                counts["failed"] += 1
                logger.warning("followup_failed", follow_up=str(follow_up.id), error=str(exc))

            await jobs.update_progress(job_id, current=index)

        await jobs.mark_completed(job_id, counts)
        await session.commit()

    logger.info("followup_tick_finished", job_id=str(job_id), **counts)


async def _due(session: AsyncSession, limit: int) -> list[FollowUp]:
    """Seguimientos vencidos que este worker puede ejecutar.

    Un seguimiento manual sin plantilla no es un envío: es un recordatorio
    para una persona —"vuelve a llamarle el jueves"—. Si entrara aquí, el
    worker lo marcaría como saltado por "no tiene plantilla" y el recordatorio
    desaparecería de la agenda sin que nadie lo hubiera atendido.
    """
    result = await session.execute(
        select(FollowUp)
        .where(
            FollowUp.status == FOLLOWUP_PENDING,
            FollowUp.scheduled_at <= datetime.now(UTC),
            or_(
                FollowUp.template_id.is_not(None),
                FollowUp.sequence_id.is_not(None),
            ),
        )
        .order_by(FollowUp.scheduled_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def _process(session: AsyncSession, follow_up: FollowUp) -> str:
    """Decide y ejecuta un seguimiento. Devuelve sent | skipped | rescheduled."""
    leads = LeadService(session)
    emails = EmailService(session)
    sequences = SequenceService(session)

    lead = await leads.get_or_404(follow_up.lead_id)
    sequence = await sequences.get_or_404(follow_up.sequence_id) if follow_up.sequence_id else None
    settings = await emails.get_settings_row()

    # ── reglas de parada ──
    reason = await _stop_reason(session, lead, sequence, follow_up)
    if reason is not None:
        return await _skip(session, follow_up, lead, reason)

    # ── ventana de envío: se reprograma, no se pierde ──
    if not is_within_window(settings, datetime.now(UTC)):
        follow_up.scheduled_at = next_valid_slot(datetime.now(UTC), settings=settings)
        follow_up.attempts += 1
        lead.next_follow_up_at = follow_up.scheduled_at
        await session.flush()
        logger.info(
            "followup_rescheduled",
            follow_up=str(follow_up.id),
            to=follow_up.scheduled_at.isoformat(),
        )
        return "rescheduled"

    # ── envío ──
    template = (
        await session.get(EmailTemplate, follow_up.template_id) if follow_up.template_id else None
    )
    if template is None:
        return await _skip(session, follow_up, lead, "no_template")

    account = await emails.resolve_account(settings.default_account_id)
    draft = await emails.build_draft(lead, template=template, settings=settings, account=account)

    try:
        outcome = await emails.send_one(
            lead,
            subject=draft.subject,
            body_text=draft.body_text,
            body_html=draft.body_html,
            template=template,
            settings=settings,
            account=account,
            is_automated=True,
        )
    except DomainError as exc:
        logger.warning("followup_send_error", follow_up=str(follow_up.id), error=exc.message)
        return await _skip(session, follow_up, lead, "send_failed")

    if not outcome.sent:
        # Los guardrails ya decidieron; su motivo es más preciso que el nuestro.
        return await _skip(
            session,
            follow_up,
            lead,
            outcome.skip_reason.value if outcome.skip_reason else "send_failed",
        )

    follow_up.status = FOLLOWUP_SENT
    follow_up.sent_email_id = outcome.email_id
    follow_up.executed_at = datetime.now(UTC)
    if outcome.email_id is not None:
        email = await session.get(EmailMessage, outcome.email_id)
        if email is not None:
            email.follow_up_id = follow_up.id

    if follow_up.sequence_step is not None:
        lead.sequence_step = follow_up.sequence_step

    await ActivityService(session).record(
        ActivityType.FOLLOWUP_SENT,
        title=(
            f"Seguimiento {follow_up.sequence_step} enviado"
            if follow_up.sequence_step
            else "Seguimiento enviado"
        ),
        description=draft.subject,
        lead_id=lead.id,
        company_id=lead.company_id,
        actor=ActorType.SYSTEM,
        metadata={"follow_up_id": str(follow_up.id)},
        owner_id=lead.owner_id,
    )

    nxt = await sequences.schedule_next_step(follow_up)
    lead.next_follow_up_at = nxt.scheduled_at if nxt else None
    await session.flush()

    logger.info("followup_sent", follow_up=str(follow_up.id), lead=str(lead.id))
    return "sent"


async def _stop_reason(
    session: AsyncSession,
    lead: Lead,
    sequence: Sequence | None,
    follow_up: FollowUp,
) -> str | None:
    """Primera regla que impide el envío, en orden de importancia."""
    if lead.replied_at is not None and (sequence is None or sequence.stop_on_reply):
        return "replied"
    if lead.reply_intent is ReplyIntent.NEGATIVE:
        return "not_interested"
    if lead.reply_intent in {ReplyIntent.MEETING_REQUEST, ReplyIntent.PRICING}:
        return "advanced"
    if lead.status is not LeadStatus.OPEN:
        return "closed"
    if lead.sequence_paused:
        return "paused"

    to_email = (lead.contact.email if lead.contact else None) or lead.company.email
    if to_email and await Guardrails(session).is_suppressed(to_email):
        return "suppressed"
    if lead.contact is not None and lead.contact.email_verified.value in {"BOUNCED", "INVALID"}:
        return "bounced"

    if sequence is not None and sequence.stop_on_click and await _has_clicked(session, lead.id):
        return "advanced"

    if not await _condition_met(session, lead, follow_up, sequence):
        return "condition_not_met"

    return None


async def _condition_met(
    session: AsyncSession,
    lead: Lead,
    follow_up: FollowUp,
    sequence: Sequence | None,
) -> bool:
    """Condición del paso: `opened_not_replied`, `not_opened`, `not_replied`.

    Permite secuencias que se ramifican sin motor de reglas: "si abrió y no
    contestó, insiste; si ni abrió, prueba con otro asunto".
    """
    if sequence is None or follow_up.sequence_step is None:
        return True

    step = next((s for s in sequence.steps if s.step_number == follow_up.sequence_step), None)
    if step is None:
        return True

    match step.condition_key:
        case "opened_not_replied":
            return await _has_opened(session, lead.id) and lead.replied_at is None
        case "not_opened":
            return not await _has_opened(session, lead.id)
        case "not_replied":
            return lead.replied_at is None
        case _:
            return True


async def _has_opened(session: AsyncSession, lead_id: uuid.UUID) -> bool:
    """Aperturas reales: las marcadas como bot no cuentan (§11.3)."""
    result = await session.execute(
        select(EmailMessage.id)
        .where(
            EmailMessage.lead_id == lead_id,
            EmailMessage.direction == Direction.OUTBOUND,
            EmailMessage.open_count > 0,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _has_clicked(session: AsyncSession, lead_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(EmailMessage.id)
        .where(
            EmailMessage.lead_id == lead_id,
            EmailMessage.direction == Direction.OUTBOUND,
            EmailMessage.click_count > 0,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _skip(session: AsyncSession, follow_up: FollowUp, lead: Lead, reason: str) -> str:
    follow_up.status = FOLLOWUP_SKIPPED
    follow_up.skip_reason = reason
    follow_up.executed_at = datetime.now(UTC)

    # Una parada definitiva corta la secuencia entera; una condición no
    # cumplida solo salta este paso y deja seguir al siguiente.
    if reason == "condition_not_met":
        nxt = await SequenceService(session).schedule_next_step(follow_up)
        lead.next_follow_up_at = nxt.scheduled_at if nxt else None
    else:
        lead.next_follow_up_at = None
        if reason in {"replied", "not_interested", "advanced", "closed"}:
            lead.sequence_paused = True

    await session.flush()
    logger.info("followup_skipped", follow_up=str(follow_up.id), reason=reason)
    return "skipped"
