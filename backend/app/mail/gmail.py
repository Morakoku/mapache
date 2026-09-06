"""Proveedor Gmail API.

Ventaja sobre SMTP para correo en frío: SPF y DKIM los firma Google con su
infraestructura y su reputación, así que desaparece el problema de reputación
de IP propia. Además da `threadId` nativo y notificación push de respuestas,
que elimina el polling.

Requiere credenciales OAuth (ver `oauth.py`). Sin ellas este proveedor no se
puede instanciar y la UI ofrece solo SMTP.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx

from app.core.enums import MailProviderType
from app.core.logging import get_logger
from app.mail.base import InboundMessage, MailAuthError, MailError, OutboundMessage, SendResult
from app.mail.builder import build_mime, extract_message_id
from app.models.email_account import EmailAccount

logger = get_logger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
# El `watch` de Gmail caduca a los 7 días. Se renueva antes para que las
# respuestas no dejen de llegar en silencio.
WATCH_TTL_DAYS = 7


class GmailProvider:
    provider_type = MailProviderType.GMAIL

    def __init__(self, account: EmailAccount, access_token: str) -> None:
        self.account = account
        self._token = access_token

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def send(self, message: OutboundMessage) -> SendResult:
        domain = message.from_email.rsplit("@", 1)[-1]
        mime = build_mime(message, sending_domain=domain)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

        payload: dict[str, object] = {"raw": raw}
        # Con `threadId`, Gmail agrupa el envío en la conversación existente:
        # es threading nativo, más fiable que reconstruirlo por cabeceras.
        if message.provider_thread_id:
            payload["threadId"] = message.provider_thread_id

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{GMAIL_API}/messages/send", headers=self._headers, json=payload
            )

        if response.status_code == 401:
            raise MailAuthError(
                "Gmail rechazó el token. Reconecta la cuenta.", code="GMAIL_TOKEN_REJECTED"
            )
        if response.status_code >= 400:
            raise MailError(f"Gmail devolvió {response.status_code}: {response.text[:200]}")

        data = response.json()
        logger.info("gmail_sent", to=message.to_email, thread=data.get("threadId"))
        return SendResult(
            provider_message_id=extract_message_id(mime),
            provider_thread_id=data.get("threadId"),
            sent_at=datetime.now(UTC),
            raw={"gmail_id": data.get("id")},
        )

    async def fetch_new(self) -> AsyncIterator[InboundMessage]:
        """Mensajes nuevos desde el `historyId` guardado.

        Con `watch` activo esto lo dispara el webhook, no un temporizador: la
        respuesta de un prospecto aparece en el CRM en segundos.
        """
        cursor = self.account.sync_cursor
        async with httpx.AsyncClient(timeout=30) as client:
            if cursor:
                message_ids = await self._history_since(client, cursor)
            else:
                message_ids = await self._recent_inbox(client)

            for message_id in message_ids:
                detail = await client.get(
                    f"{GMAIL_API}/messages/{message_id}",
                    headers=self._headers,
                    params={"format": "full"},
                )
                if detail.status_code != 200:
                    continue
                inbound = _to_inbound(detail.json())
                if inbound is not None:
                    yield inbound

    async def _history_since(self, client: httpx.AsyncClient, history_id: str) -> list[str]:
        response = await client.get(
            f"{GMAIL_API}/history",
            headers=self._headers,
            params={"startHistoryId": history_id, "historyTypes": "messageAdded"},
        )
        if response.status_code == 404:
            # El historyId caducó (Gmail los rota). Se cae al listado reciente
            # en vez de perder los mensajes.
            logger.warning("gmail_history_expired", account=str(self.account.id))
            return await self._recent_inbox(client)
        if response.status_code != 200:
            return []

        ids: list[str] = []
        for entry in response.json().get("history", []):
            for added in entry.get("messagesAdded", []):
                message = added.get("message", {})
                if "INBOX" in message.get("labelIds", []):
                    ids.append(message["id"])
        return ids

    async def _recent_inbox(self, client: httpx.AsyncClient) -> list[str]:
        response = await client.get(
            f"{GMAIL_API}/messages",
            headers=self._headers,
            params={"labelIds": "INBOX", "maxResults": 25, "q": "is:unread"},
        )
        if response.status_code != 200:
            return []
        return [m["id"] for m in response.json().get("messages", [])]

    async def start_watch(self, topic_name: str) -> datetime:
        """Activa el push de Pub/Sub y devuelve cuándo caduca."""
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{GMAIL_API}/watch",
                headers=self._headers,
                json={"topicName": topic_name, "labelIds": ["INBOX"]},
            )
        if response.status_code >= 400:
            raise MailError(f"No se pudo activar el watch de Gmail: {response.text[:200]}")
        return datetime.now(UTC) + timedelta(days=WATCH_TTL_DAYS)

    async def verify(self) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{GMAIL_API}/profile", headers=self._headers)
        if response.status_code == 401:
            raise MailAuthError("El token de Gmail no es válido.", code="GMAIL_TOKEN_INVALID")
        if response.status_code >= 400:
            raise MailError(f"Gmail devolvió {response.status_code}.")


def _to_inbound(data: dict) -> InboundMessage | None:
    """Traduce la respuesta de la API al DTO del CRM."""
    payload = data.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    from_email = _address(headers.get("from", ""))
    if not from_email:
        return None

    body_text, body_html = _extract_parts(payload)
    received = datetime.fromtimestamp(int(data.get("internalDate", 0)) / 1000, tz=UTC)

    subject = headers.get("subject", "")
    labels = data.get("labelIds", [])

    return InboundMessage(
        provider_message_id=headers.get("message-id", data["id"]),
        from_email=from_email,
        to_email=_address(headers.get("to", "")),
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        received_at=received,
        in_reply_to=headers.get("in-reply-to"),
        references=headers.get("references"),
        provider_thread_id=data.get("threadId"),
        is_auto_reply=(
            headers.get("auto-submitted", "no").lower() != "no"
            or "vacation" in subject.lower()
            or "fuera de la oficina" in subject.lower()
        ),
        # Gmail etiqueta los informes de entrega fallida; también se comprueba
        # el remitente por si la etiqueta no está.
        is_bounce="mailer-daemon" in from_email.lower() or "SPAM" in labels,
        raw_headers=headers,
    )


def _address(value: str) -> str:
    from email.utils import parseaddr

    return parseaddr(value)[1]


def _extract_parts(payload: dict) -> tuple[str, str | None]:
    text = html = None

    def _walk(part: dict) -> None:
        nonlocal text, html
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")

        if data:
            decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            if mime_type == "text/plain" and text is None:
                text = decoded
            elif mime_type == "text/html" and html is None:
                html = decoded

        for child in part.get("parts", []):
            _walk(child)

    _walk(payload)
    return (text or "").strip(), html
