"""Módulo 16 — bandeja de conversaciones.

El servicio trabaja sobre `conversations` / `conversation_messages`, que son
agnósticos de canal. Quién transporta la respuesta lo decide el
`ChannelAdapter` del hilo: email hoy, WhatsApp en la Fase 10, sin tocar esta
capa ni el frontend.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import Channel, Direction, StageType
from app.core.exceptions import NotFoundError
from app.models.email import Conversation, ConversationMessage
from app.models.lead import Lead
from app.models.pipeline import PipelineStage
from app.repositories.base import BaseRepository

# Etapas que la bandeja considera "con interés": el prospecto respondió y la
# conversación avanzó por encima del primer contacto.
_INTERESTED_STAGES = (
    StageType.INTERESTED,
    StageType.MEETING,
    StageType.OPPORTUNITY,
    StageType.PROPOSAL,
    StageType.NEGOTIATION,
)


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ConversationRepository(session)

    def build_list_query(
        self,
        *,
        filter_: str | None = None,
        channel: Channel | None = None,
        q: str | None = None,
    ) -> Select[tuple[Conversation]]:
        stmt = select(Conversation).order_by(
            Conversation.last_message_at.desc().nullslast(),
            Conversation.created_at.desc(),
        )

        if channel is not None:
            stmt = stmt.where(Conversation.channel == channel)

        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Conversation.subject.ilike(pattern),
                    select(ConversationMessage.id)
                    .where(
                        ConversationMessage.conversation_id == Conversation.id,
                        ConversationMessage.body_text.ilike(pattern),
                    )
                    .exists(),
                )
            )

        match (filter_ or "all").lower():
            case "unanswered":
                # Escribimos nosotros y aún no contestan.
                stmt = stmt.where(Conversation.last_direction == Direction.OUTBOUND)
            case "answered":
                stmt = stmt.where(Conversation.last_direction == Direction.INBOUND)
            case "pending":
                # Nos escribieron y la pelota está en nuestro tejado.
                stmt = stmt.where(
                    or_(Conversation.is_unread.is_(True), Conversation.status == "NEEDS_REPLY")
                )
            case "interested":
                stmt = stmt.where(
                    select(Lead.id)
                    .join(PipelineStage, PipelineStage.id == Lead.stage_id)
                    .where(
                        Lead.id == Conversation.lead_id,
                        PipelineStage.stage_type.in_(_INTERESTED_STAGES),
                    )
                    .exists()
                )
            case "closed":
                stmt = stmt.where(Conversation.status == "CLOSED")
            case _:
                pass

        return stmt

    async def get_or_404(self, conversation_id: uuid.UUID) -> Conversation:
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages))
            .execution_options(populate_existing=True)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise NotFoundError.for_entity("conversation", conversation_id)
        return conversation

    async def for_lead(self, lead_id: uuid.UUID) -> list[Conversation]:
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.lead_id == lead_id)
            .order_by(Conversation.last_message_at.desc().nullslast())
        )
        return list(result.scalars().all())

    async def mark_read(self, conversation: Conversation) -> Conversation:
        conversation.is_unread = False
        if conversation.status == "NEEDS_REPLY":
            conversation.status = "OPEN"
        await self.session.flush()
        return conversation

    async def close(self, conversation: Conversation) -> Conversation:
        conversation.status = "CLOSED"
        conversation.is_unread = False
        await self.session.flush()
        return conversation

    async def reply(
        self,
        conversation: Conversation,
        *,
        subject: str | None,
        body_text: str,
    ) -> ConversationMessage:
        """Responde por el canal del hilo.

        Importar el adaptador aquí dentro evita el ciclo
        `channels -> email_svc -> conversation_svc`.
        """
        from app.channels import adapter_for

        adapter = adapter_for(conversation.channel, self.session)
        return await adapter.send(conversation, subject=subject, body_text=body_text)

    async def record_inbound(
        self,
        conversation: Conversation,
        *,
        body_text: str,
        body_html: str | None,
        author_name: str | None,
        occurred_at: datetime | None = None,
    ) -> ConversationMessage:
        """Añade un mensaje entrante al hilo.

        Lo usa el sincronizador de bandeja de entrada (Fase 6); vive aquí para
        que los contadores del hilo se actualicen en un único sitio.
        """
        from app.utils.text import snippet

        occurred_at = occurred_at or datetime.now(UTC)
        message = ConversationMessage(
            conversation_id=conversation.id,
            channel=conversation.channel,
            direction=Direction.INBOUND,
            author_name=author_name,
            body_text=body_text,
            body_html=body_html,
            snippet=snippet(body_text, 200),
            occurred_at=occurred_at,
            owner_id=conversation.owner_id,
        )
        self.session.add(message)

        conversation.message_count += 1
        conversation.last_message_at = occurred_at
        conversation.last_direction = Direction.INBOUND
        conversation.is_unread = True
        conversation.status = "NEEDS_REPLY"
        await self.session.flush()
        return message
