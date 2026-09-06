"""Módulo 10 — renderizado de plantillas con variables.

Sustitución simple de `{{variable}}`, sin motor de plantillas: no hace falta
lógica ni bucles en un correo de prospección, y un motor completo abre la
puerta a inyección desde datos scrapeados.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import ValidationError

_VARIABLE_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Variables que la UI ofrece al editar una plantilla.
AVAILABLE_VARIABLES: dict[str, str] = {
    "company_name": "Nombre de la empresa",
    "contact_name": "Nombre completo del contacto",
    "first_name": "Nombre de pila del contacto",
    "city": "Ciudad de la empresa",
    "category": "Categoría del negocio",
    "service_name": "Servicio que se ofrece",
    "sender_name": "Tu nombre",
    "website": "Sitio web de la empresa",
    "signal_summary": "Resumen de las señales detectadas",
    "unsubscribe_url": "Enlace de baja (obligatorio)",
}

# Sin esta variable no se puede enviar: la baja es obligatoria. Si el usuario
# no la coloca, el renderer la añade en el pie automáticamente.
REQUIRED_VARIABLE = "unsubscribe_url"


@dataclass(slots=True)
class RenderResult:
    subject: str
    body_text: str
    body_html: str | None = None
    # Variables que aparecían en la plantilla pero no tenían valor. La UI las
    # muestra como aviso antes de enviar.
    missing: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)


def extract_variables(*texts: str | None) -> list[str]:
    """Variables presentes en los textos dados, sin repetir y en orden."""
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in _VARIABLE_RE.finditer(text):
            name = match.group(1)
            if name not in found:
                found.append(name)
    return found


def validate_template(subject: str, body_text: str, body_html: str | None = None) -> list[str]:
    """Comprueba que la plantilla solo use variables conocidas.

    Una variable mal escrita (`{{compnay_name}}`) se enviaría literal al
    prospecto. Mejor fallar al guardar que en el correo.
    """
    used = extract_variables(subject, body_text, body_html)
    unknown = [v for v in used if v not in AVAILABLE_VARIABLES]
    if unknown:
        listed = ", ".join("{{" + u + "}}" for u in unknown)
        raise ValidationError(
            f"Variables desconocidas en la plantilla: {listed}.",
            code="UNKNOWN_TEMPLATE_VARIABLE",
            details={"unknown": unknown, "available": sorted(AVAILABLE_VARIABLES)},
        )
    return used


def render(
    *,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    context: dict[str, Any],
) -> RenderResult:
    """Sustituye las variables por sus valores.

    Una variable sin valor se sustituye por cadena vacía en vez de dejar el
    `{{...}}` visible: es preferible una frase algo coja a que el prospecto vea
    el andamiaje de la plantilla.
    """
    missing: list[str] = []
    used = extract_variables(subject, body_text, body_html)

    def _substitute(text: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            value = context.get(name)
            if value in (None, ""):
                if name not in missing:
                    missing.append(name)
                return ""
            return str(value)

        return _VARIABLE_RE.sub(_replace, text)

    return RenderResult(
        subject=_collapse_spaces(_substitute(subject)),
        body_text=_substitute(body_text),
        body_html=_substitute(body_html) if body_html else None,
        missing=missing,
        used=used,
    )


def _collapse_spaces(value: str) -> str:
    """Limpia los espacios dobles que deja una variable vacía en el asunto."""
    return re.sub(r"\s{2,}", " ", value).strip()


def build_context(
    *,
    company_name: str,
    sender_name: str,
    service_name: str | None = None,
    contact_name: str | None = None,
    city: str | None = None,
    category: str | None = None,
    website: str | None = None,
    signals: list[str] | None = None,
    unsubscribe_url: str | None = None,
) -> dict[str, Any]:
    """Contexto de sustitución a partir de los datos del lead."""
    first_name = contact_name.split()[0] if contact_name else None
    return {
        "company_name": company_name,
        "contact_name": contact_name,
        # Si no se conoce el nombre, el saludo cae a algo neutro en vez de
        # quedarse vacío ("Hola ,").
        "first_name": first_name or contact_name or "",
        "city": city,
        "category": category,
        "service_name": service_name,
        "sender_name": sender_name,
        "website": website,
        "signal_summary": _summarise_signals(signals or []),
        "unsubscribe_url": unsubscribe_url,
    }


def _summarise_signals(signals: list[str]) -> str:
    """Frase legible con las señales detectadas, para usar en la observación."""
    from app.enrichment.signal_detector import SIGNAL_LABELS

    labels = [SIGNAL_LABELS.get(s, s).lower() for s in signals[:3]]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} y {labels[-1]}"
