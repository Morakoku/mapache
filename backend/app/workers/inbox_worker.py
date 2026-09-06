"""Worker de sincronización de entrada (Fase 6).

Con Gmail o Graph conectados esto lo dispara el webhook y la respuesta aparece
en segundos. Con SMTP/IMAP no hay push, así que se llama cada pocos minutos.
En ambos casos el trabajo es el mismo: pedir lo nuevo al proveedor y pasarlo
por `InboxService`.

El cursor se guarda **después** de procesar el lote. Si el proceso muere a
mitad, la siguiente pasada repite mensajes ya vistos, y eso es inofensivo —
`InboxService` los descarta por `provider_message_id`. Guardarlo antes, en
cambio, perdería respuestas de clientes en silencio.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.database import session_scope
from app.core.enums import AccountStatus
from app.core.logging import get_logger
from app.mail.base import MailAuthError
from app.mail.factory import provider_for
from app.models.email_account import EmailAccount
from app.services.inbox_svc import InboxService
from app.services.job_svc import JobService

logger = get_logger(__name__)

# Tope por cuenta y pasada. Un buzón con miles de mensajes sin sincronizar no
# puede bloquear la cola: lo que quede entra en la siguiente vuelta.
_MAX_PER_RUN = 200


async def run_inbox_sync(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    account_id = payload.get("account_id")

    async with session_scope() as session:
        jobs = JobService(session)
        await jobs.mark_running(job_id)
        await session.commit()

        accounts = await _accounts(session, account_id)
        totals = {"replies": 0, "auto_replies": 0, "bounces": 0, "unmatched": 0, "duplicates": 0}

        await jobs.update_progress(
            job_id, current=0, total=len(accounts), message="Revisando buzones…"
        )
        await session.commit()

        for index, account in enumerate(accounts, start=1):
            try:
                counts = await _sync_account(session, account)
            except MailAuthError as exc:
                account.status = AccountStatus.TOKEN_EXPIRED
                account.sync_error = exc.message
                logger.warning("inbox_sync_auth_failed", account=str(account.id))
                await session.commit()
                continue
            except Exception as exc:  # noqa: BLE001 - un buzón roto no para los demás
                account.sync_error = str(exc)[:500]
                logger.warning("inbox_sync_failed", account=str(account.id), error=str(exc))
                await session.commit()
                continue

            for key, value in counts.items():
                totals[key] += value

            await jobs.update_progress(
                job_id,
                current=index,
                message=f"{account.email}: {counts['replies']} respuesta(s)",
            )
            await session.commit()

        await jobs.mark_completed(job_id, totals)
        await session.commit()

    logger.info("inbox_sync_finished", job_id=str(job_id), **totals)


async def _accounts(session: Any, account_id: str | None) -> list[EmailAccount]:
    if account_id:
        account = await session.get(EmailAccount, uuid.UUID(account_id))
        return [account] if account is not None else []

    result = await session.execute(
        select(EmailAccount).where(EmailAccount.status == AccountStatus.ACTIVE)
    )
    return list(result.scalars().all())


async def _sync_account(session: Any, account: EmailAccount) -> dict[str, int]:
    inbox = InboxService(session)
    provider = await provider_for(account, session)

    counts = {"replies": 0, "auto_replies": 0, "bounces": 0, "unmatched": 0, "duplicates": 0}
    key_by_kind = {
        "reply": "replies",
        "auto_reply": "auto_replies",
        "bounce": "bounces",
        "unmatched": "unmatched",
        "duplicate": "duplicates",
    }

    processed = 0
    async for inbound in provider.fetch_new():
        result = await inbox.ingest(account, inbound)
        counts[key_by_kind[result.kind]] += 1
        processed += 1
        if processed >= _MAX_PER_RUN:
            logger.info("inbox_sync_truncated", account=str(account.id), limit=_MAX_PER_RUN)
            break

    account.last_synced_at = datetime.now(UTC)
    account.sync_error = None
    await session.flush()

    logger.info("inbox_synced", account=str(account.id), processed=processed, **counts)
    return counts
