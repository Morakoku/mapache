"""Parseo de los valores crudos que salen del DOM de Google Maps.

Funciones puras y sin dependencia de Playwright: se pueden probar con strings
literales, que es justo lo que hacen los tests. Cuando Google cambie un
formato, el test falla aquí y no en mitad de un scraping de 100 fichas.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from app.utils.text import strip_accents

# Iconos de Material Symbols: viven en el área de uso privado de Unicode.
_PRIVATE_USE_RE = re.compile(r"[-]")

# `!1s0x8e442e2a...:0x9f1b...` — identificador de feature (FTID) de Google.
# NO es el place_id `ChIJ...` de la Places API: son espacios de identificadores
# distintos y no se pueden convertir el uno en el otro sin llamar a la API.
_FTID_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", flags=re.IGNORECASE)
_FTID_DATA_RE = re.compile(r"(0x[0-9a-f]+:0x[0-9a-f]+)", flags=re.IGNORECASE)

# `!3d6.2088!4d-75.5906` — coordenadas reales del negocio.
_COORDS_DATA_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
# `/@6.2088,-75.5906,17z` — centro del mapa, NO del negocio. Solo como último
# recurso: en un listado apunta al centro de la búsqueda, no a la empresa.
_COORDS_AT_RE = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+)")

_RATING_RE = re.compile(r"([0-5][.,]\d)")
_REVIEWS_RE = re.compile(r"([\d.,]+)")
_PHONE_FROM_ITEM_ID_RE = re.compile(r"phone:tel:(.+)$")

_DAY_MAP = {
    "lunes": "mon",
    "monday": "mon",
    "martes": "tue",
    "tuesday": "tue",
    "miercoles": "wed",
    "miércoles": "wed",
    "wednesday": "wed",
    "jueves": "thu",
    "thursday": "thu",
    "viernes": "fri",
    "friday": "fri",
    "sabado": "sat",
    "sábado": "sat",
    "saturday": "sat",
    "domingo": "sun",
    "sunday": "sun",
}

_CLOSED_WORDS = ("cerrado", "closed")
_OPEN_24H_WORDS = ("abierto 24 horas", "24 horas", "open 24 hours", "24 hours")

_TIME_RANGE_RE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?\s*(?:a|–|-|—|to)\s*"  # noqa: RUF001
    r"(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?",
    flags=re.IGNORECASE,
)


def parse_ftid(url: str | None) -> str | None:
    """Extrae el FTID de una URL de ficha de Google Maps."""
    if not url:
        return None
    decoded = unquote(url)
    match = _FTID_RE.search(decoded)
    if match:
        return match.group(1).lower()
    # Algunas URLs traen el par hexadecimal sin el prefijo `!1s`.
    match = _FTID_DATA_RE.search(decoded)
    return match.group(1).lower() if match else None


def parse_coordinates(url: str | None) -> tuple[float | None, float | None]:
    """Coordenadas del negocio a partir de la URL.

    Prioriza `!3d/!4d` (posición real del negocio) sobre `/@lat,lng` (centro
    del viewport). Confundirlos hace que todas las empresas de una búsqueda
    caigan en el mismo punto.
    """
    if not url:
        return None, None

    decoded = unquote(url)
    match = _COORDS_DATA_RE.search(decoded)
    if match:
        return float(match.group(1)), float(match.group(2))

    match = _COORDS_AT_RE.search(decoded)
    if match:
        return float(match.group(1)), float(match.group(2))

    return None, None


def parse_rating(raw: str | None) -> float | None:
    """`4,5` / `4.5` / `4,5 estrellas` -> 4.5"""
    if not raw:
        return None
    match = _RATING_RE.search(raw)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return value if 0 <= value <= 5 else None


def parse_reviews_count(raw: str | None) -> int | None:
    """`1.234 reseñas` -> 1234. Ojo: en es-CO el punto es separador de miles."""
    if not raw:
        return None
    match = _REVIEWS_RE.search(raw.replace("\xa0", " "))
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def parse_phone(raw: str | None) -> str | None:
    """Teléfono desde `data-item-id="phone:tel:+576044441234"` o texto suelto."""
    if not raw:
        return None
    match = _PHONE_FROM_ITEM_ID_RE.search(raw)
    if match:
        return match.group(1).strip()

    cleaned = re.sub(r"^(?:Teléfono|Phone)\s*:?\s*", "", raw.strip(), flags=re.IGNORECASE)
    return cleaned.strip() or None


def parse_address(raw: str | None) -> str | None:
    """Limpia la dirección del `aria-label`.

    Google emite direcciones con segmentos repetidos —verificado en fichas
    reales, el `aria-label` llega como
    `Dirección: Cl. 8 #43C - 68, El Poblado, Medellín, El Poblado, Medellín,
    Antioquia`—. La duplicación viene de origen, así que se limpia aquí:
    guardarla tal cual ensucia la ficha y descoloca el parseo de ciudad.
    """
    if not raw:
        return None

    cleaned = re.sub(r"^(?:Dirección|Address)\s*:?\s*", "", raw.strip(), flags=re.IGNORECASE)
    # Los iconos de Material van en el área de uso privado de Unicode y se
    # cuelan cuando el valor sale del texto del nodo en vez del aria-label.
    cleaned = _PRIVATE_USE_RE.sub("", cleaned)
    return _dedupe_address_segments(cleaned) or None


def _dedupe_address_segments(address: str) -> str:
    """Elimina segmentos repetidos conservando el orden y la primera aparición."""
    segments = [s.strip() for s in address.split(",")]
    seen: set[str] = set()
    unique: list[str] = []
    for segment in segments:
        if not segment:
            continue
        key = strip_accents(segment).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(segment)
    return ", ".join(unique)


def split_address(address: str | None) -> dict[str, str | None]:
    """Descompone una dirección colombiana en ciudad / región / código postal.

    Formato habitual de Google en Colombia:
        `Cra. 43A #7-50, El Poblado, Medellín, Antioquia`

    Es heurístico a propósito: la dirección canónica se guarda entera en
    `address` y estos campos son para filtrar y agrupar, no para facturar.
    """
    if not address:
        return {"city": None, "region": None, "postal_code": None}

    parts = [p.strip() for p in address.split(",") if p.strip()]
    postal = None
    postal_match = re.search(r"\b(\d{6})\b", address)
    if postal_match:
        postal = postal_match.group(1)

    city = region = None
    if len(parts) >= 2:
        region = re.sub(r"\b\d{6}\b", "", parts[-1]).strip() or None
        city = re.sub(r"\b\d{6}\b", "", parts[-2]).strip() or None
    elif parts:
        city = parts[-1]

    return {"city": city, "region": region, "postal_code": postal}


def _to_24h(hour: int, minute: int, meridiem: str | None) -> str:
    if meridiem:
        normalized = meridiem.lower().replace(".", "").replace(" ", "")
        if normalized.startswith("p") and hour != 12:
            hour += 12
        elif normalized.startswith("a") and hour == 12:
            hour = 0
    return f"{hour:02d}:{minute:02d}"


def parse_opening_hours(rows: list[tuple[str, str]]) -> dict[str, Any] | None:
    """Convierte las filas de la tabla de horarios a un dict estable.

    Entrada: [("lunes", "9:00 a 18:00"), ("domingo", "Cerrado")]
    Salida:  {"mon": [["09:00", "18:00"]], "sun": []}

    Una lista vacía significa cerrado, y la ausencia de la clave, que no
    tenemos el dato. Son cosas distintas y conviene poder distinguirlas.
    """
    if not rows:
        return None

    result: dict[str, Any] = {}
    for day_raw, hours_raw in rows:
        day_key = _DAY_MAP.get(day_raw.strip().lower())
        if not day_key:
            continue

        text = hours_raw.strip().lower()
        if any(word in text for word in _CLOSED_WORDS):
            result[day_key] = []
            continue
        if any(word in text for word in _OPEN_24H_WORDS):
            result[day_key] = [["00:00", "23:59"]]
            continue

        ranges = []
        for match in _TIME_RANGE_RE.finditer(hours_raw):
            start = _to_24h(int(match.group(1)), int(match.group(2) or 0), match.group(3))
            end = _to_24h(int(match.group(4)), int(match.group(5) or 0), match.group(6))
            ranges.append([start, end])

        if ranges:
            result[day_key] = ranges

    return result or None


def canonical_maps_url(href: str | None) -> str | None:
    """URL de la ficha sin parámetros de sesión ni de viewport."""
    if not href:
        return None
    base = href.split("?")[0]
    if base.startswith("/"):
        base = f"https://www.google.com{base}"
    return base or None


def is_challenge_page(url: str, content: str) -> bool:
    """¿Google presentó un CAPTCHA o bloqueó la petición?"""
    from app.scrapers.google_maps.selectors import CHALLENGE_PATTERNS

    haystack = f"{url}\n{content[:4000]}".lower()
    return any(pattern.lower() in haystack for pattern in CHALLENGE_PATTERNS)


def zoom_for_radius(radius_km: float) -> int:
    """Nivel de zoom de Google Maps aproximado para un radio dado.

    El zoom no filtra resultados por sí solo, pero condiciona qué carga el
    listado: demasiado abierto trae ruido de otras zonas, demasiado cerrado
    deja fuera negocios del radio pedido.
    """
    if radius_km <= 1:
        return 16
    if radius_km <= 2:
        return 15
    if radius_km <= 5:
        return 14
    if radius_km <= 10:
        return 13
    if radius_km <= 25:
        return 12
    if radius_km <= 50:
        return 11
    return 10
