"""Proveedor Resend para envío de emails.

Resend ofrece 100 emails/día gratis (3,000/mes). Usa HTTP API (funciona en serverless).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from app.core.enums import MailProviderType
from app.core.logging import get_logger
from app.core.security import decrypt_optional
from app.mail.base import MailAuthError, MailError, OutboundMessage, SendResult
from app.models.email_account import EmailAccount

logger = get_logger(__name__)


class ResendProvider:
    provider_type = MailProviderType.RESEND

    def __init__(self, account: EmailAccount) -> None:
        self.account = account
        self._api_key = decrypt_optional(account.smtp_password_enc)
        self._domain = account.smtp_host  # Dominio verificado en Resend

        if not self._api_key:
            raise MailError(
                "La cuenta Resend no tiene API key configurada.",
                code="RESEND_NOT_CONFIGURED",
            )

    async def send(self, message: OutboundMessage) -> SendResult:
        url = "https://api.resend.com/emails"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        data: dict[str, str | list[str]] = {
            "from": f"{message.from_name} <{message.from_email}>",
            "to": message.to_email,
            "subject": message.subject,
            "text": message.body_text,
        }
        if message.body_html:
            data["html"] = message.body_html

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=data)
                if resp.status_code == 200:
                    result = resp.json()
                    message_id = result.get("id", "")
                    logger.info("resend_sent", to=message.to_email, message_id=message_id)
                    return SendResult(
                        provider_message_id=message_id,
                        provider_thread_id=None,
                        sent_at=datetime.now(UTC),
                    )
                else:
                    logger.error("resend_error", status=resp.status_code, detail=resp.text)
                    raise MailError(
                        f"Resend error {resp.status_code}: {resp.text}",
                        code="RESEND_SEND_FAILED",
                    )
        except httpx.HTTPError as exc:
            raise MailError(f"Resend HTTP error: {exc}") from exc

    async def fetch_new(self) -> AsyncIterator:
        """Resend no soporta fetch de entrada."""
        return
        yield

    async def verify(self) -> None:
        """Verifica que la API key sea válida."""
        url = "https://api.resend.com/api-keys"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    raise MailAuthError(
                        f"Resend verify failed: {resp.status_code}",
                        code="RESEND_AUTH_FAILED",
                    )
        except httpx.HTTPError as exc:
            raise MailError(f"Resend connection error: {exc}") from exc
