"""Guaki Filter (LOOP-22) — clasificación de oportunidad de presencia digital.

Identifica negocios locales con una oportunidad razonable de mejorar su
presencia digital. Es una función pura sobre el `ScoreInput` de Mapache (mismo
patrón que `vevra.py`): no toca la base de datos y devuelve razones explicables.

Reglas:
- NO asume que un negocio necesita Guaki solo porque no tiene web: usa la
  evidencia disponible (web, redes, contacto, completitud de información).
- NO convierte ausencia de información en una afirmación ("no tiene redes" solo
  se dice si una señal lo observa; si no hay evidencia, se dice que no se verificó).
- Clasificación determinista y documentada; umbrales = HYPOTHESIS (validar con
  datos reales de ventas/registros).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.scoring.dimensions import ScoreInput
from app.utils.text import strip_accents

# Señales de presencia en redes que Mapache puede emitir (HYPOTHESIS — depende de
# qué señales produzca el enrichment). Si no hay ninguna, la presencia social se
# reporta como "no verificada", nunca como "no tiene".
GUAKI_SOCIAL_SIGNALS = frozenset({"instagram", "facebook", "social", "social_links", "tiktok"})

# Completitud de información pública suficiente (0-100 de `data_quality_score`).
_QUALITY_OK = 70


def _norm(value: str | None) -> str:
    return strip_accents(value or "").lower().strip()


@dataclass(frozen=True, slots=True)
class GuakiOpportunity:
    """Clasificación de oportunidad Guaki con razones explicables."""

    opportunity: str  # HIGH | MEDIUM | LOW | NOT_RELEVANT
    reasons: list[str] = field(default_factory=list)

    @property
    def is_relevant(self) -> bool:
        return self.opportunity in {"HIGH", "MEDIUM", "LOW"}


def guaki_opportunity(data: ScoreInput) -> GuakiOpportunity:
    """Clasifica el negocio según su oportunidad de mejorar su presencia digital.

    Evidencia considerada (solo lo observado): categoría+ciudad (negocio local),
    actividad (rating/reseñas/contacto), sitio web, señales de redes, datos de
    contacto y completitud de la información pública.
    """
    reasons: list[str] = []

    category = _norm(data.company_category)
    city = _norm(data.company_city)
    has_activity = bool(
        data.rating is not None
        or data.reviews_count
        or data.company_phone
        or data.company_email
        or data.contact_email
    )

    if not category or not city:
        reasons.append("no se identificó como negocio local (faltan categoría o ciudad)")
    if not has_activity:
        reasons.append("sin evidencia de actividad comercial (sin rating, reseñas o contacto)")
    if not category or not city or not has_activity:
        return GuakiOpportunity("NOT_RELEVANT", reasons or ["sin evidencia suficiente"])

    # --- sitio web (OBSERVED / UNKNOWN)
    has_web = bool(data.company_website)
    no_web = "no_website" in data.signals
    web_outdated = "website_outdated" in data.signals
    if has_web:
        reasons.append("tiene sitio web")
    elif no_web:
        reasons.append("no se encontró sitio web")
    else:
        reasons.append("no se pudo verificar si tiene sitio web")

    # --- presencia en redes (OBSERVED / UNKNOWN, nunca se afirma que no tiene)
    social = data.signals & GUAKI_SOCIAL_SIGNALS
    if social:
        reasons.append("presencia en redes detectada")
    else:
        reasons.append("presencia en redes no verificada")

    # --- datos de contacto
    has_contact = bool(data.company_phone or data.company_email or data.contact_email)
    if has_contact:
        reasons.append("datos de contacto disponibles")
    else:
        reasons.append("sin datos de contacto verificables")

    # --- completitud de información pública
    complete = (
        data.data_quality_score >= _QUALITY_OK
        and bool(data.company_phone)
        and bool(data.company_email or data.contact_email)
    )
    if complete:
        reasons.append("información pública suficiente")
    else:
        reasons.append("información pública incompleta o parcial")

    # --- clasificación (determinista)
    if has_web and complete and has_contact:
        return GuakiOpportunity(
            "LOW",
            [*reasons, "presencia digital suficiente: no es candidato prioritario"],
        )

    if (no_web or web_outdated) and has_contact and (social or not complete):
        return GuakiOpportunity(
            "HIGH",
            [*reasons, "oportunidad alta: presencia digital débil con datos de contacto"],
        )

    if (no_web or web_outdated) and not has_contact:
        return GuakiOpportunity(
            "MEDIUM",
            [*reasons, "oportunidad media: presencia débil pero falta contacto"],
        )

    if not complete and not has_web and not no_web:
        return GuakiOpportunity(
            "MEDIUM",
            [*reasons, "oportunidad media: información parcial y presencia web no verificada"],
        )

    if (has_web and not complete) or web_outdated:
        return GuakiOpportunity(
            "MEDIUM",
            [
                *reasons,
                "oportunidad media: presencia web pero información incompleta o desactualizada",
            ],
        )

    return GuakiOpportunity(
        "LOW",
        [*reasons, "señales insuficientes para priorizar"],
    )


__all__ = ["GuakiOpportunity", "guaki_opportunity"]
