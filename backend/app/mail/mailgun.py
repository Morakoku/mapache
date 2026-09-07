"""Proveedor Mailgun para envío de emails.

Mailgun ofrece 5,000 emails/mes gratis. Usa HTTP API (funciona en serverless).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from app.core.enums import MailProviderType
from app.core.logging import get_logger
from app.core.security import decrypt_optional
from app.mail.base import MailAuthError, MailError, OutboundMessage, SendResult
from app.mail.builder import build_mime, extract_message_id
from app.models.email_account import EmailAccount

logger = get_logger(__name__)


class MailgunProvider:
    provider_type = MailProviderType.MAILGUN

    def __init__(self, account: EmailAccount) -> None:
        self.account = account
        self._api_key = decrypt_optional(account.smtp_password_enc)
        self._domain = account.smtp_host  # Dominio configurado en Mailgun

        if not self._api_key:
            raise MailError(
                "La cuenta Mailgun no tiene API key configurada.",
                code="MAILGUN_NOT_CONFIGURED",
            )
        if not self._domain:
            raise MailError(
                "La cuenta Mailgun no tiene dominio configurado.",
                code="MAILGUN_NOT_CONFIGURED",
            )

    async def send(self, message: OutboundMessage) -> SendResult:
        url = f"https://api.mailgun.net/v3/{self._domain}/messages"
        auth = ("api", self._api_key)

        data = {
            "from": f"{message.from_name} <{message.from_email}>",
            "to": message.to_email,
            "subject": message.subject,
            "text": message.body_text,
        }
        if message.body_html:
            data["html"] = message.body_html

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, auth=auth, data=data)
                if resp.status_code == 200:
                    result = resp.json()
                    message_id = result.get("id", "").strip("<>")
                    logger.info("mailgun_sent", to=message.to_email, message_id=message_id)
                    return SendResult(
                        provider_message_id=message_id,
                        provider_thread_id=None,
                        sent_at=datetime.now(UTC),
                    )
                else:
                    logger.error("mailgun_error", status=resp.status_code, detail=resp.text)
                    raise MailError(
                        f"Mailgun error {resp.status_code}: {resp.text}",
                        code="MAILGUN_SEND_FAILED",
                    )
        except httpx.HTTPError as exc:
            raise MailError(f"Mailgun HTTP error: {exc}") from exc

    async def fetch_new(self) -> AsyncIterator:
        """Mailgun no soporta fetch de entrada vía API básica."""
        return
        yield  # Make it an async generator

    async def verify(self) -> None:
        """Verifica que la API key y dominio sean válidos."""
        url = f"https://api.mailgun.net/v3/{self._domain}"
        auth = ("api", self._api_key)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, auth=auth)
                if resp.status_code != 200:
                    raise MailAuthError(
                        f"Mailgun verify failed: {resp.status_code}",
                        code="MAILGUN_AUTH_FAILED",
                    )
        except httpx.HTTPError as exc:
            raise MailError(f"Mailgun connection error: {exc}") from exc
