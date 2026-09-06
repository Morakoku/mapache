"""Extracción de datos de contacto del HTML de un sitio web.

Aquí es donde aparecen los emails: ni el scraper de Maps ni la Places API los
devuelven. El resto del embudo depende de que esto funcione.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from selectolax.parser import HTMLParser

from app.utils.phone import to_e164
from app.utils.url import (
    detect_social_platform,
    extract_domain,
    is_share_url,
    normalize_url,
)

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Emails que no son de contacto comercial: los inyectan plantillas, CDNs y
# librerías. Meterlos en el CRM significa escribirle a un desarrollador de
# WordPress en vez de al dueño del restaurante.
_JUNK_EMAIL_PATTERNS = (
    "example.com",
    "example.org",
    "domain.com",
    "yourdomain",
    "email.com",
    "sentry.io",
    "wixpress.com",
    "godaddy.com",
    "@2x.png",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".css",
    ".js",
)

# Buzones genéricos: valen como contacto pero no son de una persona concreta.
ROLE_LOCAL_PARTS = frozenset(
    {
        "info",
        "contacto",
        "contact",
        "ventas",
        "sales",
        "hola",
        "hello",
        "admin",
        "administracion",
        "gerencia",
        "soporte",
        "support",
        "atencionalcliente",
        "servicioalcliente",
        "comercial",
        "mercadeo",
        "marketing",
        "recepcion",
        "reservas",
        "pedidos",
        "facturacion",
        "noreply",
        "no-reply",
        "webmaster",
        "postmaster",
        "correo",
    }
)

_TEL_HREF_RE = re.compile(r"^tel:(.+)$", flags=re.IGNORECASE)
_MAILTO_HREF_RE = re.compile(r"^mailto:([^?]+)", flags=re.IGNORECASE)
_JSON_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})|\\(/)")


@dataclass(slots=True)
class ExtractedContact:
    """Todo lo encontrado en una página, con su procedencia."""

    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    company_name: str | None = None
    description: str | None = None
    has_contact_form: bool = False
    has_viewport_meta: bool = False
    copyright_year: int | None = None

    def merge(self, other: ExtractedContact) -> None:
        """Fusiona los hallazgos de otra página del mismo sitio."""
        for email in other.emails:
            if email not in self.emails:
                self.emails.append(email)
        for phone in other.phones:
            if phone not in self.phones:
                self.phones.append(phone)
        for platform, url in other.socials.items():
            self.socials.setdefault(platform, url)

        self.company_name = self.company_name or other.company_name
        self.description = self.description or other.description
        self.has_contact_form = self.has_contact_form or other.has_contact_form
        self.has_viewport_meta = self.has_viewport_meta or other.has_viewport_meta
        self.copyright_year = self.copyright_year or other.copyright_year


def is_role_email(email: str) -> bool:
    """`info@`, `ventas@`... Cuenta como contacto, pero no es una persona."""
    local = email.split("@")[0].lower()
    normalized = re.sub(r"[._\-]", "", local)
    return normalized in ROLE_LOCAL_PARTS or local in ROLE_LOCAL_PARTS


def _is_junk_email(email: str) -> bool:
    lowered = email.lower()
    return any(pattern in lowered for pattern in _JUNK_EMAIL_PATTERNS)


def _unescape_json_sequences(html: str) -> str:
    """Convierte `\\uXXXX` y `\\/` a sus caracteres reales.

    Muchos sitios llevan JSON incrustado en `<script>`, y ahí `>` viaja como
    `\\u003e`. El regex de email no incluye la barra invertida en su clase de
    caracteres, pero sí las letras y dígitos que la siguen, así que sin este
    paso captura basura pegada al email.
    """
    if "\\u" not in html and "\\/" not in html:
        return html
    return _JSON_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)) if m.group(1) else "/", html)


def extract_emails(html: str, site_domain: str | None = None) -> list[str]:
    """Emails del HTML, priorizando los del dominio del propio sitio.

    El orden importa: `contacto@elrestaurante.com` vale mucho más que el email
    de la agencia que le hizo la web, y ambos suelen aparecer en el footer.
    """
    tree = HTMLParser(html)

    found: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        email = candidate.strip().strip(".,;:").lower()
        if not email or email in seen or _is_junk_email(email):
            return
        if not _EMAIL_RE.fullmatch(email):
            return
        seen.add(email)
        found.append(email)

    # 1. `mailto:` — es una declaración explícita, la señal más fuerte.
    for node in tree.css("a"):
        href = node.attributes.get("href") or ""
        match = _MAILTO_HREF_RE.match(href)
        if match:
            _add(match.group(1))

    # 2. Texto visible.
    for match in _EMAIL_RE.finditer(tree.text(separator=" ")):
        _add(match.group(0))

    # 3. HTML crudo: cubre emails en atributos `data-*` y en JSON embebido.
    #    Se desescapan antes las secuencias \uXXXX: sin eso, un email dentro de
    #    JSON incrustado se captura como `u003ejustocolombia@getjusto.com`
    #    —el `\` no entra en la clase de caracteres, pero `u003e` sí—.
    #    Detectado con datos reales de un restaurante en El Poblado.
    for match in _EMAIL_RE.finditer(_unescape_json_sequences(html)):
        _add(match.group(0))

    if site_domain:
        own = [e for e in found if e.split("@")[-1] == site_domain]
        others = [e for e in found if e.split("@")[-1] != site_domain]
        found = own + others

    return found


def extract_phones(html: str, region: str = "CO") -> list[str]:
    """Teléfonos en E.164. Prioriza `tel:`, que es inequívoco."""
    tree = HTMLParser(html)
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        e164 = to_e164(raw, region)
        if e164 and e164 not in seen:
            seen.add(e164)
            found.append(e164)

    for node in tree.css("a"):
        href = node.attributes.get("href") or ""
        match = _TEL_HREF_RE.match(href)
        if match:
            _add(match.group(1))

    # Sin `tel:`, se buscan patrones colombianos en el texto. Es ruidoso, por
    # eso solo se aceptan los que `phonenumbers` valida de verdad.
    text = tree.text(separator=" ")
    phone_pattern = r"(?:\+?57[\s\-]?)?(?:\(?\d{1,4}\)?[\s\-.]?)?\d{3}[\s\-.]?\d{4}"
    for match in re.finditer(phone_pattern, text):
        _add(match.group(0))

    return found


def extract_socials(html: str, base_url: str | None = None) -> dict[str, str]:
    """Enlaces a redes sociales, uno por plataforma (el primero que aparece)."""
    tree = HTMLParser(html)
    socials: dict[str, str] = {}

    for node in tree.css("a"):
        href = node.attributes.get("href") or ""
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if href.startswith("/") and base_url:
            domain = extract_domain(base_url)
            href = f"https://{domain}{href}" if domain else href

        platform = detect_social_platform(href)
        # Las webs de directorios están llenas de botones de compartir que
        # apuntan a las redes: sin filtrarlos, cada empresa acaba con un
        # «LinkedIn» que en realidad comparte la página.
        if platform and platform not in socials and not is_share_url(href):
            normalized = normalize_url(href)
            if normalized:
                socials[platform] = normalized

    return socials


def extract_jsonld(html: str) -> list[dict[str, Any]]:
    """Bloques JSON-LD de schema.org.

    Cuando existen son la mejor fuente que hay: el propio negocio declara su
    email, teléfono y dirección en formato estructurado.
    """
    tree = HTMLParser(html)
    blocks: list[dict[str, Any]] = []

    for node in tree.css("script[type='application/ld+json']"):
        raw = node.text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        if isinstance(data, dict):
            blocks.append(data)
            blocks.extend(g for g in data.get("@graph", []) if isinstance(g, dict))
        elif isinstance(data, list):
            blocks.extend(item for item in data if isinstance(item, dict))

    return blocks


def extract_from_jsonld(blocks: list[dict[str, Any]]) -> ExtractedContact:
    """Datos de contacto declarados en JSON-LD."""
    result = ExtractedContact()

    for block in blocks:
        block_type = block.get("@type", "")
        types = block_type if isinstance(block_type, list) else [block_type]
        if not any(
            t in {"LocalBusiness", "Organization", "Restaurant", "Store", "Hotel", "Corporation"}
            or str(t).endswith("Business")
            for t in types
        ):
            continue

        email = block.get("email")
        if isinstance(email, str):
            cleaned = email.replace("mailto:", "").strip().lower()
            if _EMAIL_RE.fullmatch(cleaned) and cleaned not in result.emails:
                result.emails.append(cleaned)

        phone = block.get("telephone")
        if isinstance(phone, str):
            e164 = to_e164(phone)
            if e164 and e164 not in result.phones:
                result.phones.append(e164)

        name = block.get("name")
        if isinstance(name, str) and not result.company_name:
            result.company_name = name.strip()

        description = block.get("description")
        if isinstance(description, str) and not result.description:
            result.description = description.strip()

        same_as = block.get("sameAs")
        if isinstance(same_as, list):
            for url in same_as:
                if not isinstance(url, str):
                    continue
                platform = detect_social_platform(url)
                if platform:
                    result.socials.setdefault(platform, normalize_url(url) or url)

    return result


def extract_page_signals(html: str) -> ExtractedContact:
    """Señales técnicas de la página, para el detector de oportunidades."""
    tree = HTMLParser(html)
    result = ExtractedContact()

    result.has_viewport_meta = bool(tree.css("meta[name='viewport']"))

    # Un formulario de contacto necesita un campo de email o de mensaje.
    for form in tree.css("form"):
        html_form = form.html or ""
        form_field_re = r"type=[\"']?email|name=[\"']?(email|correo|mensaje|message)"
        if re.search(form_field_re, html_form, re.I):
            result.has_contact_form = True
            break

    meta_desc = tree.css_first("meta[name='description']")
    if meta_desc:
        content = meta_desc.attributes.get("content")
        if content and content.strip():
            result.description = content.strip()

    title = tree.css_first("title")
    if title:
        text = title.text(strip=True)
        if text:
            result.company_name = text

    years = re.findall(r"(?:©|&copy;|copyright)[^\d]{0,20}(20\d{2})", html, flags=re.IGNORECASE)
    if years:
        result.copyright_year = max(int(y) for y in years)

    return result


def extract_all(html: str, url: str) -> ExtractedContact:
    """Pasada completa sobre una página."""
    domain = extract_domain(url)

    result = extract_page_signals(html)
    result.merge(extract_from_jsonld(extract_jsonld(html)))

    for email in extract_emails(html, domain):
        if email not in result.emails:
            result.emails.append(email)
    for phone in extract_phones(html):
        if phone not in result.phones:
            result.phones.append(phone)
    for platform, social_url in extract_socials(html, url).items():
        result.socials.setdefault(platform, social_url)

    return result
