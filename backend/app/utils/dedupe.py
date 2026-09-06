"""Clave de deduplicación de empresas.

Nivel 2 del dedupe de 4 niveles (§4.3 del diseño). Los niveles 1 (ftid /
place_id) y 3 (similitud trigram en Postgres) viven en el repositorio; aquí
solo se calcula la clave determinista.
"""

from __future__ import annotations

import hashlib

from app.utils.text import normalize_city, normalize_company_name
from app.utils.url import extract_domain


def build_dedupe_key(
    *,
    name: str,
    website: str | None = None,
    phone_e164: str | None = None,
    city: str | None = None,
) -> str:
    """Hash determinista de los rasgos que identifican a una empresa.

    Se incluyen dominio y teléfono porque dos negocios con el mismo nombre en
    la misma ciudad (una cadena con dos sedes) son entradas distintas, y esos
    dos campos son lo que normalmente las separa.

    El nombre nunca va vacío al hash: si la normalización lo deja en blanco,
    se usa el original en crudo antes que producir claves que colisionen entre
    empresas sin relación.
    """
    normalized_name = normalize_company_name(name) or name.strip().lower()
    parts = [
        normalized_name,
        extract_domain(website) or "",
        (phone_e164 or "").strip(),
        normalize_city(city),
    ]
    joined = "|".join(parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def name_similarity(a: str, b: str) -> float:
    """Similitud 0..1 entre dos nombres normalizados, por tokens (Jaccard).

    Es una aproximación local para tests y para ordenar candidatos. La
    comparación que decide de verdad la hace Postgres con `pg_trgm`, que es
    la que está indexada.
    """
    tokens_a = set(normalize_company_name(a).split())
    tokens_b = set(normalize_company_name(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)
