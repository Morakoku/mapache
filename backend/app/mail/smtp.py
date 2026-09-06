"""Proveedor SMTP/IMAP genérico.

Es la alternativa cuando el usuario no conecta Gmail ni Outlook, y lo que se
usa contra Mailhog en desarrollo. Funciona, pero con correo en frío la
entregabilidad depende enteramente de SPF/DKIM/DMARC y de la reputación de la
IP de salida — por eso los proveedores OAuth son los recomendados.
"""

from __future__ import annotations

import contextlib
import email
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime

import aioimaplib
import aiosmtplib

from app.core.enums import MailProviderType
from app.core.logging import get_logger
from app.core.security import decrypt_optional
from app.mail.base import InboundMessage, MailAuthError, MailError, OutboundMessage, SendResult
from app.mail.builder import build_mime, extract_message_id
from app.models.email_account import EmailAccount

logger = get_logger(__name__)

# Cabeceras que marcan una respuesta automática. No cuentan como respuesta
# real: detener una secuencia por un "estoy de vacaciones" sería un error.
_AUTO_REPLY_HEADERS = (
    ("auto-submitted", lambda v: v.lower() != "no"),
    ("x-autoreply", lambda v: True),
    ("x-autorespond", lambda v: True),
    ("precedence", lambda v: v.lower() in {"auto_reply", "bulk", "junk"}),
)
_AUTO_REPLY_SUBJECTS = (
    "fuera de la oficina",
    "out of office",
    "automatic reply",
    "respuesta automática",
    "auto-respuesta",
    "vacation",
)
_HARD_BOUNCE_CODES = re.compile(r"\b5\.\d\.\d\b")
_SOFT_BOUNCE_CODES = re.compile(r"\b4\.\d\.\d\b")


class SmtpProvider:
    provider_type = MailProviderType.SMTP

    def __init__(self, account: EmailAccount) -> None:
        if not account.smtp_host:
            raise MailError(
                "La cuenta SMTP no tiene servidor configurado.", code="SMTP_NOT_CONFIGURED"
            )
        self.account = account
        self._password = decrypt_optional(account.smtp_password_enc)
        self._imap_password = decrypt_optional(account.imap_password_enc) or self._password

    # ------------------------------------------------------------ envío

    async def send(self, message: OutboundMessage) -> SendResult:
        domain = message.from_email.rsplit("@", 1)[-1]
        mime = build_mime(message, sending_domain=domain)

        try:
            await aiosmtplib.send(
                # Se manda el MIME ya serializado, no el objeto: pasándole el
                # objeto, aiosmtplib lo vuelve a aplanar con `policy.SMTP` y
                # replega las cabeceras, que es justo lo que rompe el
                # `List-Unsubscribe` (ver `builder._MIME_POLICY`).
                mime.as_bytes(),
                sender=message.from_email,
                recipients=[message.to_email, *message.cc],
                hostname=self.account.smtp_host,
                port=self.account.smtp_port or 587,
                username=self.account.smtp_user or None,
                password=self._password or None,
                # STARTTLS solo si hay TLS activo y no es el puerto de SMTPS.
                start_tls=(self.account.smtp_use_tls and (self.account.smtp_port or 587) != 465),
                use_tls=(self.account.smtp_port == 465),
                timeout=30,
            )
        except aiosmtplib.SMTPAuthenticationError as exc:
            raise MailAuthError(
                f"SMTP rechazó las credenciales: {exc}", code="SMTP_AUTH_FAILED"
            ) from exc
        except Exception as exc:
            raise MailError(f"Fallo al enviar por SMTP: {exc}") from exc

        message_id = extract_message_id(mime)
        logger.info("smtp_sent", to=message.to_email, message_id=message_id)
        return SendResult(
            provider_message_id=message_id,
            # SMTP no tiene concepto de hilo: el threading se resuelve por
            # References/In-Reply-To, que ya van en el MIME.
            provider_thread_id=None,
            sent_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------ entrada

    async def fetch_new(self) -> AsyncIterator[InboundMessage]:
        """Lee lo no visto desde el último UID (Módulo 12).

        IMAP no tiene push, así que esto lo llama el `InboxWorker` cada pocos
        minutos. Con Gmail o Graph conectados, en cambio, hay webhook.
        """
        if not self.account.imap_host:
            return

        client = aioimaplib.IMAP4_SSL(
            host=self.account.imap_host, port=self.account.imap_port or 993
        )
        try:
            await client.wait_hello_from_server()
            response = await client.login(
                self.account.imap_user or self.account.email, self._imap_password or ""
            )
            if response.result != "OK":
                raise MailAuthError("IMAP rechazó las credenciales.", code="IMAP_AUTH_FAILED")

            await client.select("INBOX")

            criteria = "UNSEEN"
            if self.account.sync_cursor:
                # UID mayor que el último visto: evita reprocesar lo antiguo
                # aunque el usuario lo marque como no leído a mano.
                criteria = f"UID {int(self.account.sync_cursor) + 1}:*"

            search = await client.uid_search(criteria)
            if search.result != "OK" or not search.lines:
                return

            uids = search.lines[0].split()
            for raw_uid in uids:
                uid = raw_uid.decode() if isinstance(raw_uid, bytes) else str(raw_uid)
                fetched = await client.uid("fetch", uid, "(RFC822)")
                if fetched.result != "OK" or len(fetched.lines) < 2:
                    continue
                raw_bytes = fetched.lines[1]
                if isinstance(raw_bytes, str):
                    raw_bytes = raw_bytes.encode()
                parsed = email.message_from_bytes(raw_bytes)
                yield _to_inbound(parsed, uid)
        finally:
            # Cerrar la sesión IMAP es best-effort: si el servidor ya cortó,
            # no debe enmascarar el resultado del fetch.
            with contextlib.suppress(Exception):
                await client.logout()

    async def verify(self) -> None:
        """Comprueba que el servidor SMTP acepta la conexión y el login."""
        try:
            client = aiosmtplib.SMTP(
                hostname=self.account.smtp_host,
                port=self.account.smtp_port or 587,
                start_tls=(self.account.smtp_use_tls and (self.account.smtp_port or 587) != 465),
                use_tls=(self.account.smtp_port == 465),
                timeout=15,
            )
            await client.connect()
            if self.account.smtp_user and self._password:
                await client.login(self.account.smtp_user, self._password)
            await client.quit()
        except aiosmtplib.SMTPAuthenticationError as exc:
            raise MailAuthError(f"Credenciales SMTP inválidas: {exc}") from exc
        except Exception as exc:
            raise MailError(f"No se pudo conectar al servidor SMTP: {exc}") from exc


def _to_inbound(parsed: Message, uid: str) -> InboundMessage:
    """Normaliza un mensaje MIME a lo que el CRM necesita."""
    headers = {k.lower(): str(v) for k, v in parsed.items()}
    subject = str(parsed.get("Subject", ""))
    body_text, body_html = _extract_bodies(parsed)

    try:
        received = parsedate_to_datetime(str(parsed.get("Date")))
    except (TypeError, ValueError):
        received = datetime.now(UTC)
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)

    from_header = str(parsed.get("From", ""))
    from_email = email.utils.parseaddr(from_header)[1]

    is_bounce, bounce_type = _detect_bounce(parsed, headers, body_text)

    return InboundMessage(
        provider_message_id=str(parsed.get("Message-ID", f"uid-{uid}")),
        from_email=from_email,
        to_email=email.utils.parseaddr(str(parsed.get("To", "")))[1],
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        received_at=received,
        in_reply_to=headers.get("in-reply-to"),
        references=headers.get("references"),
        is_auto_reply=_detect_auto_reply(headers, subject),
        is_bounce=is_bounce,
        bounce_type=bounce_type,
        raw_headers=headers,
    )


def _extract_bodies(parsed: Message) -> tuple[str, str | None]:
    text_part = html_part = None
    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_maintype() == "multipart":
                continue
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if part.get_content_type() == "text/plain" and text_part is None:
                text_part = decoded
            elif part.get_content_type() == "text/html" and html_part is None:
                html_part = decoded
    else:
        payload = parsed.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = parsed.get_content_charset() or "utf-8"
            text_part = payload.decode(charset, errors="replace")

    return (text_part or "").strip(), html_part


def _detect_auto_reply(headers: dict[str, str], subject: str) -> bool:
    for name, predicate in _AUTO_REPLY_HEADERS:
        value = headers.get(name)
        if value and predicate(value):
            return True
    lowered = subject.lower()
    return any(marker in lowered for marker in _AUTO_REPLY_SUBJECTS)


def _detect_bounce(
    parsed: Message, headers: dict[str, str], body_text: str
) -> tuple[bool, str | None]:
    """Detecta un DSN (RFC 3464) y si el rebote es duro o blando.

    La distinción importa: un rebote duro va a la lista de supresión, uno
    blando (buzón lleno) no debe descartar al prospecto para siempre.
    """
    content_type = headers.get("content-type", "").lower()
    is_dsn = "report-type=delivery-status" in content_type
    from_addr = headers.get("from", "").lower()
    looks_like_daemon = any(
        marker in from_addr for marker in ("mailer-daemon", "postmaster", "no-reply@")
    )

    if not (is_dsn or looks_like_daemon):
        return False, None

    haystack = f"{body_text}\n{parsed.as_string()[:4000]}"
    if _HARD_BOUNCE_CODES.search(haystack):
        return True, "hard"
    if _SOFT_BOUNCE_CODES.search(haystack):
        return True, "soft"
    return True, "hard" if is_dsn else "soft"
