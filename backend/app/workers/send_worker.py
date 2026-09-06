"""Worker de envío por lotes (Módulo 9).

Enviar 50 correos con el ritmo configurado son 40 minutos: fuera del request,
siempre. El worker persiste cada envío en cuanto ocurre, así que si el proceso
muere a mitad del lote lo ya enviado queda registrado y no se duplica —el
guardrail de duplicado lo impide al reintentar.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import Any

from app.core.database import session_scope
from app.core.logging import get_logger
from app.models.email import EmailTemplate
from app.services.email_svc import EmailService
from app.services.job_svc import JobService
from app.services.lead_svc import LeadService

logger = get_logger(__name__)

# Jitter sobre la pausa configurada. Mandar exactamente cada 45 s es un patrón
# de máquina; variarlo no oculta nada al proveedor —el remitente y el dominio
# son los reales— pero evita la cadencia de reloj que dispara los filtros.
_JITTER = 0.35


async def run_send_batch(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    drafts: list[dict[str, Any]] = payload.get("drafts", [])
    account_id = payload.get("account_id")

    async with session_scope() as session:
        jobs = JobService(session)
        emails = EmailService(session)
        leads = LeadService(session)

        await jobs.mark_running(job_id)
        await jobs.update_progress(
            job_id, current=0, total=len(drafts), message="Preparando envío…"
        )
        await session.commit()

        if not drafts:
            await jobs.mark_completed(job_id, {"sent": 0, "skipped": 0, "failed": 0})
            await session.commit()
            return

        account_uuid = uuid.UUID(account_id) if account_id else None
        settings = await emails.get_settings_row()
        account = await emails.resolve_account(account_uuid)

        sent = skipped = failed = 0
        outcomes: list[dict[str, Any]] = []
        total = len(drafts)

        for index, draft in enumerate(drafts, start=1):
            lead_id = uuid.UUID(draft["lead_id"])
            try:
                lead = await leads.get_or_404(lead_id)
                template = (
                    await session.get(EmailTemplate, uuid.UUID(draft["template_id"]))
                    if draft.get("template_id")
                    else None
                )
                outcome = await emails.send_one(
                    lead,
                    subject=draft["subject"],
                    body_text=draft["body_text"],
                    body_html=draft.get("body_html"),
                    template=template,
                    settings=settings,
                    account=account,
                    was_edited=bool(draft.get("was_edited")),
                    is_automated=bool(payload.get("is_automated")),
                )
            except Exception as exc:  # noqa: BLE001 - un lead roto no aborta el lote
                failed += 1
                outcomes.append({"lead_id": str(lead_id), "sent": False, "error": str(exc)[:300]})
                logger.warning("send_batch_lead_failed", lead=str(lead_id), error=str(exc))
                # El rollback expira todo lo cargado; en async, tocar un
                # atributo expirado después dispara un lazy load fuera del
                # greenlet y revienta. Se releen los dos objetos que el bucle
                # sigue necesitando.
                await session.rollback()
                settings = await emails.get_settings_row()
                account = await emails.resolve_account(account_uuid)
            else:
                if outcome.sent:
                    sent += 1
                elif outcome.skip_reason is not None:
                    skipped += 1
                else:
                    failed += 1
                outcomes.append(
                    {
                        "lead_id": str(outcome.lead_id),
                        "sent": outcome.sent,
                        "email_id": str(outcome.email_id) if outcome.email_id else None,
                        "skip_reason": (outcome.skip_reason.value if outcome.skip_reason else None),
                        "message": outcome.message,
                    }
                )

            await jobs.update_progress(
                job_id,
                current=index,
                message=f"{index}/{total} — {sent} enviados, {skipped} omitidos",
            )
            # Commit por correo: lo enviado queda escrito aunque el lote se
            # interrumpa. Un envío ya salió del servidor; perder su registro
            # significaría reenviarlo.
            await session.commit()

            if index < total:
                await _pace(settings.min_seconds_between)

        await jobs.mark_completed(
            job_id,
            {"sent": sent, "skipped": skipped, "failed": failed, "outcomes": outcomes},
        )
        await session.commit()

    logger.info(
        "send_batch_finished", job_id=str(job_id), sent=sent, skipped=skipped, failed=failed
    )


async def _pace(min_seconds: int) -> None:
    if min_seconds <= 0:
        return
    delay = min_seconds * random.uniform(1 - _JITTER, 1 + _JITTER)
    await asyncio.sleep(delay)
