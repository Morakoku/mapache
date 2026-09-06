"""Proveedor Microsoft Graph (Outlook / Microsoft 365).

Mismas ventajas que Gmail frente a SMTP: firma con la infraestructura de
Microsoft, hilo nativo (`conversationId`) y notificaciones push en vez de
polling.

Requiere credenciales OAuth (ver `oauth.py`). Sin ellas no se puede
instanciar y la UI ofrece solo SMTP.
"""

from __future__ import annotations

import base64
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx

from app.core.enums import MailProviderType
from app.core.logging import get_logger
from app.mail.base import InboundMessage, MailAuthError, MailError, OutboundMessage, SendResult
from app.mail.builder import build_mime, extract_message_id
from app.models.email_account import EmailAccount

logger = get_logger(__name__)

GRAPH_API = "https://graph.microsoft.com/v1.0/me"
# Las suscripciones de Graph caducan a los 3 días como máximo.
SUBSCRIPTION_TTL_DAYS = 3


class GraphProvider:
    provider_type = MailProviderType.MICROSOFT

    def __init__(self, account: EmailAccount, access_token: str) -> None:
        self.account = account
        self._token = access_token

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def send(self, message: OutboundMessage) -> SendResult:
        """Envía el MIME crudo.

        Se usa `sendMail` con MIME en vez del JSON de Graph porque así se
        conservan las cabeceras que necesitamos: `List-Unsubscribe`,
        `References` y el `Message-ID` que genera el CRM.
        """
        domain = message.from_email.rsplit("@", 1)[-1]
        mime = build_mime(message, sending_domain=domain)
        raw = base64.b64encode(mime.as_bytes()).decode()

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{GRAPH_API}/sendMail",
                headers={**self._headers, "Content-Type": "text/plain"},
                content=raw,
            )

        if response.status_code == 401:
            raise MailAuthError(
                "Microsoft rechazó el token. Reconecta la cuenta.",
                code="GRAPH_TOKEN_REJECTED",
            )
        if response.status_code not in (200, 202):
            raise MailError(f"Graph devolvió {response.status_code}: {response.text[:200]}")

        message_id = extract_message_id(mime)
        logger.info("graph_sent", to=message.to_email)
        return SendResult(
            provider_message_id=message_id,
            # `sendMail` no devuelve cuerpo, así que el conversationId se
            # resuelve al leer la respuesta entrante.
            provider_thread_id=message.provider_thread_id,
            sent_at=datetime.now(UTC),
        )

    async def fetch_new(self) -> AsyncIterator[InboundMessage]:
        """Consulta delta desde el `deltaLink` guardado."""
        url = self.account.sync_cursor or (
            f"{GRAPH_API}/mailFolders/inbox/messages/delta"
            "?$select=id,internetMessageId,subject,from,toRecipients,body,bodyPreview,"
            "receivedDateTime,conversationId,internetMessageHeaders,isRead"
        )

        async with httpx.AsyncClient(timeout=30) as client:
            while url:
                response = await client.get(url, headers=self._headers)
                if response.status_code == 401:
                    raise MailAuthError("El token de Microsoft caducó.", code="GRAPH_TOKEN_EXPIRED")
                if response.status_code >= 400:
                    logger.warning("graph_delta_failed", status=response.status_code)
                    return

                payload = response.json()
                for item in payload.get("value", []):
                    inbound = _to_inbound(item)
                    if inbound is not None:
                        yield inbound

                url = payload.get("@odata.nextLink")
                # El deltaLink marca el final: se guarda como cursor para la
                # siguiente sincronización.
                if not url and payload.get("@odata.deltaLink"):
                    self.account.sync_cursor = payload["@odata.deltaLink"]

    async def create_subscription(self, notification_url: str, client_state: str) -> datetime:
        """Registra el webhook de notificaciones y devuelve su caducidad."""
        expires = datetime.now(UTC) + timedelta(days=SUBSCRIPTION_TTL_DAYS)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://graph.microsoft.com/v1.0/subscriptions",
                headers=self._headers,
                json={
                    "changeType": "created",
                    "notificationUrl": notification_url,
                    "resource": "me/mailFolders('inbox')/messages",
                    "expirationDateTime": expires.isoformat(),
                    # Se valida en el webhook para descartar notificaciones
                    # que no vengan de nuestra suscripción.
                    "clientState": client_state,
                },
            )
        if response.status_code >= 400:
            raise MailError(f"No se pudo crear la suscripción: {response.text[:200]}")
        return expires

    async def verify(self) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(GRAPH_API, headers=self._headers)
        if response.status_code == 401:
            raise MailAuthError("El token de Microsoft no es válido.", code="GRAPH_TOKEN_INVALID")
        if response.status_code >= 400:
            raise MailError(f"Graph devolvió {response.status_code}.")


def _to_inbound(item: dict) -> InboundMessage | None:
    sender = (item.get("from") or {}).get("emailAddress", {})
    from_email = sender.get("address", "")
    if not from_email:
        return None

    recipients = item.get("toRecipients") or []
    to_email = recipients[0].get("emailAddress", {}).get("address", "") if recipients else ""

    body = item.get("body") or {}
    is_html = body.get("contentType", "").lower() == "html"
    content = body.get("content", "")

    headers = {
        h.get("name", "").lower(): h.get("value", "")
        for h in item.get("internetMessageHeaders", [])
    }

    received_raw = item.get("receivedDateTime")
    received = datetime.now(UTC)
    if isinstance(received_raw, str):
        # Una fecha ilegible no debe descartar el mensaje: se usa la hora
        # actual y el correo entra igual en la conversación.
        with contextlib.suppress(ValueError):
            received = datetime.fromisoformat(received_raw.replace("Z", "+00:00"))

    subject = item.get("subject", "")

    return InboundMessage(
        provider_message_id=item.get("internetMessageId") or item["id"],
        from_email=from_email,
        to_email=to_email,
        subject=subject,
        body_text=item.get("bodyPreview", "") if is_html else content,
        body_html=content if is_html else None,
        received_at=received,
        in_reply_to=headers.get("in-reply-to"),
        references=headers.get("references"),
        provider_thread_id=item.get("conversationId"),
        is_auto_reply=(
            headers.get("auto-submitted", "no").lower() != "no"
            or "automatic reply" in subject.lower()
            or "fuera de la oficina" in subject.lower()
        ),
        is_bounce="postmaster" in from_email.lower() or "mailer-daemon" in from_email.lower(),
        raw_headers=headers,
    )
