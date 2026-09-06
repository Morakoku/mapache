"""Adaptador del canal email.

Reutiliza `EmailService.send_one`, así que una respuesta manual pasa por las
mismas barreras de cumplimiento que un envío en frío: supresión, no contactar
y email inválido siguen bloqueando. Lo que no aplica es el ritmo (ventana
horaria, límites diarios): responder a quien acaba de escribirte no es
prospección.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Channel
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.models.email import Conversation, ConversationMessage, EmailMessage
from app.services.email_svc import EmailService
from app.services.lead_svc import LeadService

logger = get_logger(__name__)

_REPLY_PREFIX = "Re: "


class EmailChannelAdapter:
    channel = Channel.EMAIL

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.emails = EmailService(session)

    async def send(
        self,
        conversation: Conversation,
        *,
        subject: str | None,
        body_text: str,
    ) -> ConversationMessage:
        lead = await LeadService(self.session).get_or_404(conversation.lead_id)
        settings = await self.emails.get_settings_row()
        account = await self.emails.resolve_account(await self._account_of(conversation))

        outcome = await self.emails.send_one(
            lead,
            subject=subject or self._reply_subject(conversation),
            body_text=body_text,
            body_html=None,
            template=None,
            settings=settings,
            account=account,
            was_edited=True,
            manual=True,
        )

        if not outcome.sent or outcome.conversation_message_id is None:
            raise ValidationError(
                outcome.message or "No se pudo enviar la respuesta.",
                code="REPLY_NOT_SENT",
                details={"reason": outcome.skip_reason.value if outcome.skip_reason else None},
            )

        message = await self.session.get(ConversationMessage, outcome.conversation_message_id)
        assert message is not None  # lo acaba de escribir send_one
        return message

    async def _account_of(self, conversation: Conversation) -> uuid.UUID | None:
        """Cuenta desde la que se venía escribiendo en este hilo.

        Cambiar de buzón a mitad de conversación confundiría al prospecto, así
        que se reutiliza el último usado y solo se cae a la predeterminada si
        el hilo aún no tiene correos salientes.
        """
        result = await self.session.execute(
            select(EmailMessage.email_account_id)
            .where(
                EmailMessage.conversation_id == conversation.id,
                EmailMessage.email_account_id.is_not(None),
            )
            .order_by(EmailMessage.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _reply_subject(conversation: Conversation) -> str:
        subject = conversation.subject or "Sin asunto"
        if subject.lower().startswith("re:"):
            return subject
        return f"{_REPLY_PREFIX}{subject}"
