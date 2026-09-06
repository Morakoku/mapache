"""Contrato de canal (decisión D14).

La bandeja de entrada no sabe si un hilo va por correo o por WhatsApp: pide
el adaptador del canal y le manda el texto. Añadir WhatsApp en la Fase 10 es
escribir una implementación más y registrarla, sin tocar servicios ni UI.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.enums import Channel
from app.models.email import Conversation, ConversationMessage


@runtime_checkable
class ChannelAdapter(Protocol):
    """Transporte de un canal concreto dentro de una conversación."""

    channel: Channel

    async def send(
        self,
        conversation: Conversation,
        *,
        subject: str | None,
        body_text: str,
    ) -> ConversationMessage:
        """Envía la respuesta y devuelve el mensaje persistido en el hilo."""
        ...
