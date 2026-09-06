"""Contrato de los proveedores de correo (decisión D13).

`EmailService` no sabe si debajo hay Gmail, Microsoft Graph o SMTP. Cambiar de
uno a otro para un lead en curso no rompe el hilo: el threading se resuelve
por `References`/`In-Reply-To` (estándar) y, cuando el proveedor lo ofrece,
además por `provider_thread_id`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.core.enums import MailProviderType
from app.core.exceptions import ExternalServiceError


@dataclass(slots=True)
class OutboundMessage:
    """Correo listo para enviar, ya renderizado y con tracking inyectado."""

    from_email: str
    from_name: str
    to_email: str
    subject: str
    body_text: str
    body_html: str | None = None
    reply_to: str | None = None
    cc: list[str] = field(default_factory=list)

    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    provider_thread_id: str | None = None

    unsubscribe_url: str | None = None
    unsubscribe_mailto: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendResult:
    provider_message_id: str
    provider_thread_id: str | None = None
    sent_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InboundMessage:
    """Correo entrante, normalizado a lo que el CRM necesita."""

    provider_message_id: str
    from_email: str
    to_email: str
    subject: str
    body_text: str
    body_html: str | None
    received_at: datetime
    in_reply_to: str | None = None
    references: str | None = None
    provider_thread_id: str | None = None
    # Bandera de auto-respuesta: no cuenta como respuesta real ni detiene una
    # secuencia, solo la reprograma.
    is_auto_reply: bool = False
    # Informe de entrega fallida (DSN). Se procesa como rebote, no como
    # respuesta.
    is_bounce: bool = False
    bounce_type: str | None = None
    raw_headers: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class MailProvider(Protocol):
    """Transporte de correo."""

    provider_type: MailProviderType

    async def send(self, message: OutboundMessage) -> SendResult: ...

    def fetch_new(self) -> AsyncIterator[InboundMessage]:
        """Mensajes entrantes desde el último cursor de sincronización."""
        ...

    async def verify(self) -> None:
        """Comprueba que las credenciales funcionan. Lanza si no."""
        ...


class MailError(ExternalServiceError):
    code = "MAIL_PROVIDER_ERROR"


class MailAuthError(MailError):
    """Credenciales inválidas o caducadas.

    Se distingue del fallo genérico porque la reacción es distinta: aquí no se
    reintenta, se marca la cuenta y se le pide al usuario que la reconecte.
    """

    code = "MAIL_AUTH_ERROR"
    http_status = 400
