"""Registro de canales de conversación (decisión D14)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import ChannelAdapter
from app.channels.email_channel import EmailChannelAdapter
from app.core.enums import Channel
from app.core.exceptions import ValidationError

# Canal -> constructor del adaptador. WhatsApp entra aquí en la Fase 10.
_ADAPTERS: dict[Channel, Callable[[AsyncSession], ChannelAdapter]] = {
    Channel.EMAIL: EmailChannelAdapter,
}


def adapter_for(channel: Channel, session: AsyncSession) -> ChannelAdapter:
    factory = _ADAPTERS.get(channel)
    if factory is None:
        raise ValidationError(
            f"El canal {channel.value} todavía no permite responder desde el CRM.",
            code="CHANNEL_NOT_SUPPORTED",
            details={"channel": channel.value, "available": [c.value for c in _ADAPTERS]},
        )
    return factory(session)


def available_channels() -> list[str]:
    return [channel.value for channel in _ADAPTERS]


__all__ = ["ChannelAdapter", "EmailChannelAdapter", "adapter_for", "available_channels"]
