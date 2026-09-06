"""Normalización de texto para comparar nombres de empresa.

El objetivo es que "Restaurante El Sabor S.A.S." y "EL SABOR SAS" colapsen al
mismo valor: sin eso, el dedupe deja duplicados por diferencias de tildes,
mayúsculas o forma societaria.
"""

from __future__ import annotations

import re
import unicodedata

# Formas societarias colombianas y genéricas. Se quitan del nombre porque no
# distinguen a la empresa: "El Sabor" y "El Sabor S.A.S." son la misma.
_LEGAL_SUFFIXES = frozenset(
    {
        "sas",
        "sa",
        "sasbic",
        "ltda",
        "eu",
        "sca",
        "sence",
        "scs",
        "eat",
        "ese",
        "esp",
        "spa",
        "srl",
        "inc",
        "llc",
        "corp",
        "co",
        "cia",
        "y",
        "cía",
    }
)

# Palabras genéricas de categoría al inicio del nombre. Google Maps las
# incluye a veces y otras no, para el mismo negocio.
_GENERIC_PREFIXES = frozenset(
    {
        "restaurante",
        "restaurant",
        "hotel",
        "clinica",
        "clínica",
        "consultorio",
        "inmobiliaria",
        "panaderia",
        "panadería",
        "cafe",
        "café",
        "bar",
        "tienda",
        "almacen",
        "almacén",
        "supermercado",
        "farmacia",
        "droguería",
        "drogueria",
        "hostal",
        "spa",
        "gimnasio",
        "peluqueria",
        "peluquería",
        "barberia",
        "barbería",
    }
)

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")

# Siglas con puntos: `S.A.S.` -> `SAS`. Sin este paso, quitar la puntuación
# antes deja los tokens sueltos `s a s`, que ya no coinciden con la lista de
# formas societarias y se quedan pegados al nombre.
_DOTTED_ACRONYM_RE = re.compile(r"\b(?:\w\.){2,}", flags=re.UNICODE)


def _collapse_dotted_acronyms(value: str) -> str:
    return _DOTTED_ACRONYM_RE.sub(lambda m: m.group(0).replace(".", ""), value)


def strip_accents(value: str) -> str:
    """Quita tildes y diacríticos. `Bogotá` -> `Bogota`."""
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def slugify(value: str) -> str:
    """Slug ASCII en minúsculas separado por guiones."""
    cleaned = _PUNCT_RE.sub(" ", strip_accents(value).lower())
    return _SPACE_RE.sub("-", cleaned.strip()).strip("-")


def normalize_company_name(name: str) -> str:
    """Forma canónica del nombre de una empresa, para comparar.

    Quita tildes, puntuación, forma societaria y prefijos genéricos de
    categoría. Si al quitar el prefijo genérico no queda nada (el negocio se
    llama literalmente "Panadería"), se conserva el original: es preferible un
    duplicado a fusionar dos negocios distintos.
    """
    base = _PUNCT_RE.sub(" ", _collapse_dotted_acronyms(strip_accents(name).lower()))
    tokens = [t for t in _SPACE_RE.split(base) if t]

    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()

    if len(tokens) > 1 and tokens[0] in _GENERIC_PREFIXES:
        tokens = tokens[1:]

    if not tokens:
        return _SPACE_RE.sub(" ", base).strip()

    return " ".join(tokens)


def normalize_city(city: str | None) -> str:
    """Ciudad comparable: sin tildes, minúsculas, sin sufijo de departamento.

    `Medellín, Antioquia` -> `medellin`
    """
    if not city:
        return ""
    head = city.split(",")[0]
    return _SPACE_RE.sub(" ", strip_accents(head).lower().strip())


def snippet(text: str | None, length: int = 200) -> str | None:
    """Recorta a `length` sin partir una palabra por la mitad."""
    if not text:
        return None
    collapsed = _SPACE_RE.sub(" ", text).strip()
    if len(collapsed) <= length:
        return collapsed
    cut = collapsed[:length].rsplit(" ", 1)[0]
    return f"{cut}…"
