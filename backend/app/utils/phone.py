"""Normalización de teléfonos a E.164, con Colombia como región por defecto.

Google Maps devuelve el teléfono en formato local (`(604) 444-1234`,
`300 123 4567`). Guardar eso tal cual hace imposible deduplicar por teléfono
y rompe cualquier integración futura de WhatsApp, que exige E.164.
"""

from __future__ import annotations

import re

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType

_WHATSAPP_URL_RE = re.compile(r"(?:wa\.me|whatsapp\.com|api\.whatsapp\.com)/(\d{7,20})")


def to_e164(raw: str | None, region: str = "CO") -> str | None:
    """`300 123 4567` -> `+573001234567`. Devuelve None si no es válido.

    Un número inválido se descarta en vez de guardarse a medias: un teléfono
    que no se puede marcar no aporta nada y ensucia el score de calidad.
    """
    if not raw or not raw.strip():
        return None
    try:
        parsed = phonenumbers.parse(raw, region)
    except NumberParseException:
        return None

    if not phonenumbers.is_valid_number(parsed):
        return None

    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def is_mobile(e164: str | None, region: str = "CO") -> bool:
    """¿Es un móvil? Relevante para WhatsApp (Fase 10) y para priorizar.

    En Colombia los móviles empiezan por 3 y los fijos son indicativo + 7
    dígitos, así que la distinción es útil de verdad.
    """
    if not e164:
        return False
    try:
        parsed = phonenumbers.parse(e164, region)
    except NumberParseException:
        return False

    return phonenumbers.number_type(parsed) in {
        PhoneNumberType.MOBILE,
        PhoneNumberType.FIXED_LINE_OR_MOBILE,
    }


def to_whatsapp_id(e164: str | None) -> str | None:
    """E.164 sin el `+`, que es el formato de `wa.me/` y de la API de Meta."""
    if not e164:
        return None
    return e164.lstrip("+")


def whatsapp_from_url(url: str | None) -> str | None:
    """Extrae el número E.164 de un enlace `wa.me/573001234567`.

    Los enlaces de WhatsApp llevan el número sin `+` ni formato
    (`573001234567`). Se devuelve en E.164 (`+573001234567`) para que quede
    igual que `company.phone`; `to_whatsapp_id` le quita el `+` cuando hace
    falta el `wa_id` que pide la API de Meta.
    """
    if not url:
        return None
    match = _WHATSAPP_URL_RE.search(url)
    return f"+{match.group(1)}" if match else None


def format_national(e164: str | None, region: str = "CO") -> str | None:
    """Formato legible para la UI: `+573001234567` -> `300 1234567`."""
    if not e164:
        return None
    try:
        parsed = phonenumbers.parse(e164, region)
    except NumberParseException:
        return e164
    return phonenumbers.format_number(parsed, PhoneNumberFormat.NATIONAL)
