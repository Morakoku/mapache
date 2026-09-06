"""Construcción del MIME: pixel, links rastreados, cabeceras y pie legal.

Todo lo que convierte un texto en un correo enviable y medible. Es común a los
tres proveedores: Gmail y Graph reciben el MIME ya montado, y SMTP también.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage as MIMEMessage
from email.utils import formataddr, formatdate, make_msgid

from app.mail.base import OutboundMessage

# `max_line_length=0` desactiva el plegado de cabeceras. No es cosmético: con
# el plegado por defecto, una URL larga sin espacios —justo lo que es
# `List-Unsubscribe`— se parte en palabras codificadas RFC 2047
# (`=?utf-8?q?=3Chttp...`), y ni Gmail ni Outlook reconocen ahí una baja de un
# clic. El texto no ASCII se sigue codificando donde toca (asunto, nombres).
_MIME_POLICY = policy.SMTP.clone(max_line_length=0)

# Tope de `References`. Sin plegado, una cadena larguísima chocaría con el
# límite duro de 998 caracteres por línea del RFC 5322. Conservar el primero y
# los últimos mantiene el hilo agrupado en cualquier cliente.
_MAX_REFERENCES = 10

# Links del cuerpo, en HTML y en texto plano.
_HTML_LINK_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', flags=re.IGNORECASE)
_TEXT_LINK_RE = re.compile(r'(?<![\w"\'=])(https?://[^\s<>\)"\']+)')


@dataclass(slots=True)
class TrackedLink:
    original_url: str
    token: uuid.UUID
    label: str | None = None
    position: int = 0


@dataclass(slots=True)
class BuiltEmail:
    """Resultado de preparar un correo para el envío."""

    body_text: str
    body_html: str | None
    links: list[TrackedLink] = field(default_factory=list)
    pixel_url: str | None = None
    unsubscribe_url: str | None = None


def rewrite_links(
    *,
    body_text: str,
    body_html: str | None,
    base_url: str,
    skip_urls: set[str] | None = None,
) -> tuple[str, str | None, list[TrackedLink]]:
    """Sustituye cada enlace por uno rastreado (Módulo 13).

    El mismo destino comparte token: dos botones al mismo sitio son el mismo
    interés, y contarlos por separado inflaría las métricas.
    """
    skip_urls = skip_urls or set()
    by_url: dict[str, TrackedLink] = {}

    def _token_for(url: str) -> TrackedLink:
        if url not in by_url:
            by_url[url] = TrackedLink(original_url=url, token=uuid.uuid4(), position=len(by_url))
        return by_url[url]

    def _tracked(url: str) -> str:
        return f"{base_url.rstrip('/')}/tracking/click/{_token_for(url).token}"

    new_html = body_html
    if body_html:

        def _html_sub(match: re.Match[str]) -> str:
            url = match.group(1)
            if url in skip_urls:
                return match.group(0)
            return f'href="{_tracked(url)}"'

        new_html = _HTML_LINK_RE.sub(_html_sub, body_html)

    def _text_sub(match: re.Match[str]) -> str:
        url = match.group(1)
        return url if url in skip_urls else _tracked(url)

    new_text = _TEXT_LINK_RE.sub(_text_sub, body_text)

    return new_text, new_html, list(by_url.values())


def inject_pixel(body_html: str | None, pixel_url: str) -> str:
    """Añade el pixel de apertura al final del HTML (Módulo 12).

    Si no hay parte HTML se crea una mínima: sin HTML no hay imagen que cargar
    y no habría forma de detectar la apertura.
    """
    img = (
        f'<img src="{pixel_url}" width="1" height="1" alt="" '
        'style="display:block;border:0;outline:none;" />'
    )
    if not body_html:
        return f"<html><body>{img}</body></html>"
    if "</body>" in body_html.lower():
        index = body_html.lower().rfind("</body>")
        return body_html[:index] + img + body_html[index:]
    return body_html + img


def build_footer(
    *,
    sender_name: str,
    sender_address: str | None,
    unsubscribe_url: str,
) -> tuple[str, str]:
    """Pie legal: identidad, dirección física y baja.

    Obligatorio por la Ley 1581/2012 y por cualquier norma antispam razonable.
    Se añade siempre, no es opcional.
    """
    address_line = f"{sender_address}\n" if sender_address else ""
    text = (
        "\n\n--\n"
        f"{sender_name}\n"
        f"{address_line}"
        f"Si prefieres no recibir más correos, cancela la suscripción aquí: {unsubscribe_url}"
    )
    address_html = f"{sender_address}<br>" if sender_address else ""
    html = (
        '<hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0 12px;">'
        '<div style="font-size:12px;color:#64748b;line-height:1.5;">'
        f"{sender_name}<br>{address_html}"
        f'<a href="{unsubscribe_url}" style="color:#64748b;">Cancelar suscripción</a>'
        "</div>"
    )
    return text, html


def build_mime(message: OutboundMessage, *, sending_domain: str) -> MIMEMessage:
    """Monta el MIME final con todas las cabeceras.

    `List-Unsubscribe` + `List-Unsubscribe-Post` implementan la baja en un
    clic (RFC 8058). Gmail y Outlook la exigen para remitentes de volumen y
    penalizan a quien no la lleva.
    """
    mime = MIMEMessage(policy=_MIME_POLICY)

    mime["From"] = formataddr((message.from_name, message.from_email))
    mime["To"] = message.to_email
    mime["Subject"] = message.subject
    mime["Date"] = formatdate(localtime=True)
    mime["Message-ID"] = message.message_id or make_msgid(domain=sending_domain)

    if message.reply_to:
        mime["Reply-To"] = message.reply_to
    if message.cc:
        mime["Cc"] = ", ".join(message.cc)

    # Threading estándar: es lo que hace que la respuesta caiga en el mismo
    # hilo del cliente de correo del prospecto.
    if message.in_reply_to:
        mime["In-Reply-To"] = message.in_reply_to
    if message.references:
        mime["References"] = message.references

    if message.unsubscribe_url:
        targets = [f"<{message.unsubscribe_url}>"]
        if message.unsubscribe_mailto:
            targets.append(f"<mailto:{message.unsubscribe_mailto}>")
        mime["List-Unsubscribe"] = ", ".join(targets)
        mime["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Marca el correo como no automático para los filtros: es correo
    # comercial uno-a-uno, no un boletín masivo.
    mime["Auto-Submitted"] = "no"

    for key, value in message.headers.items():
        if key not in mime:
            mime[key] = value

    mime.set_content(message.body_text)
    if message.body_html:
        mime.add_alternative(message.body_html, subtype="html")

    return mime


def extract_message_id(mime: MIMEMessage) -> str:
    return str(mime["Message-ID"])


def build_references(
    previous_references: str | None, previous_message_id: str | None
) -> str | None:
    """Encadena `References` para mantener el hilo.

    Se acumulan los Message-ID anteriores; los clientes de correo usan esta
    cadena para agrupar, incluso si el asunto cambia.
    """
    parts = []
    if previous_references:
        parts.extend(previous_references.split())
    if previous_message_id and previous_message_id not in parts:
        parts.append(previous_message_id)
    if not parts:
        return None
    if len(parts) > _MAX_REFERENCES:
        # El primero identifica el hilo; los últimos, el contexto inmediato.
        parts = [parts[0], *parts[-(_MAX_REFERENCES - 1) :]]
    return " ".join(parts)
