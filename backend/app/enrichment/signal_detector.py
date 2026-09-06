"""Detección de señales de oportunidad.

Esto es lo que convierte "creo que este restaurante necesita una web" en un
dato con evidencia y fecha. El motor de scoring cruza estas claves contra
`services.opportunity_signals` para decidir si una empresa encaja con lo que
el usuario vende.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.enrichment.website_crawler import CrawlResult
from app.utils.url import is_directory_url, is_social_url

# Claves estables. Cambiar una obliga a migrar `services.opportunity_signals`,
# así que se tratan como parte del contrato público.
NO_WEBSITE = "no_website"
SITE_UNREACHABLE = "site_unreachable"
SOCIAL_ONLY = "social_only"
NO_SSL = "no_ssl"
NOT_RESPONSIVE = "not_responsive"
SLOW_SITE = "slow_site"
OUTDATED_SITE = "outdated_site"
NO_CONTACT_FORM = "no_contact_form"
NO_EMAIL = "no_email"
NO_WHATSAPP = "no_whatsapp"
NO_INSTAGRAM = "no_instagram"
LOW_REVIEWS = "low_reviews"
LOW_RATING = "low_rating"
NO_PHONE = "no_phone"

ALL_SIGNALS = (
    NO_WEBSITE,
    SITE_UNREACHABLE,
    SOCIAL_ONLY,
    NO_SSL,
    NOT_RESPONSIVE,
    SLOW_SITE,
    OUTDATED_SITE,
    NO_CONTACT_FORM,
    NO_EMAIL,
    NO_WHATSAPP,
    NO_INSTAGRAM,
    LOW_REVIEWS,
    LOW_RATING,
    NO_PHONE,
)

# Etiquetas para la UI. El usuario elige señales al configurar un servicio,
# así que necesita leerlas en su idioma, no en snake_case.
SIGNAL_LABELS: dict[str, str] = {
    NO_WEBSITE: "No tiene sitio web",
    SITE_UNREACHABLE: "El sitio web no responde",
    SOCIAL_ONLY: "Solo tiene redes sociales",
    NO_SSL: "El sitio no usa HTTPS",
    NOT_RESPONSIVE: "El sitio no es responsive",
    SLOW_SITE: "El sitio es lento",
    OUTDATED_SITE: "El sitio está desactualizado",
    NO_CONTACT_FORM: "No tiene formulario de contacto",
    NO_EMAIL: "No se encontró email",
    NO_WHATSAPP: "No tiene WhatsApp",
    NO_INSTAGRAM: "No tiene Instagram",
    LOW_REVIEWS: "Pocas reseñas",
    LOW_RATING: "Calificación baja",
    NO_PHONE: "No tiene teléfono",
}

_SLOW_SITE_MS = 3000
_OUTDATED_YEARS = 2
_LOW_REVIEWS_THRESHOLD = 20
_LOW_RATING_THRESHOLD = 4.0


@dataclass(frozen=True, slots=True)
class DetectedSignal:
    key: str
    value: dict[str, Any] | None = None

    @property
    def label(self) -> str:
        return SIGNAL_LABELS.get(self.key, self.key)


@dataclass(frozen=True, slots=True)
class CompanyFacts:
    """Lo que se sabe de la empresa al momento de evaluar señales."""

    website: str | None = None
    phone: str | None = None
    email: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    socials: dict[str, str] | None = None


def detect_signals(facts: CompanyFacts, crawl: CrawlResult | None = None) -> list[DetectedSignal]:
    """Evalúa todas las señales conocidas.

    Cada señal lleva su evidencia en `value`: sin eso, un "sitio lento" no es
    accionable en un correo. Con `{"response_ms": 4200}`, sí.
    """
    signals: list[DetectedSignal] = []
    socials = facts.socials or {}

    # --- presencia web ---
    if not facts.website:
        signals.append(DetectedSignal(NO_WEBSITE))
    elif is_social_url(facts.website):
        # Un negocio cuyo "sitio web" es su Instagram no tiene web propia.
        signals.append(DetectedSignal(SOCIAL_ONLY, {"url": facts.website}))
        signals.append(DetectedSignal(NO_WEBSITE))
    elif is_directory_url(facts.website):
        # Una ficha en un directorio tampoco: la creó el directorio, y el
        # negocio no controla ni el dominio ni el contenido.
        signals.append(DetectedSignal(NO_WEBSITE))
    elif crawl is not None and not crawl.reachable and not crawl.robots_blocked:
        signals.append(DetectedSignal(SITE_UNREACHABLE, {"error": crawl.error}))

    # --- calidad técnica del sitio ---
    if crawl is not None and crawl.reachable:
        if not crawl.uses_https:
            signals.append(DetectedSignal(NO_SSL, {"final_url": crawl.final_url}))

        if not crawl.contact.has_viewport_meta:
            # Sin `<meta name="viewport">` el sitio no puede ser responsive:
            # es la comprobación más barata y fiable que hay.
            signals.append(DetectedSignal(NOT_RESPONSIVE))

        if crawl.response_ms and crawl.response_ms > _SLOW_SITE_MS:
            signals.append(DetectedSignal(SLOW_SITE, {"response_ms": crawl.response_ms}))

        year = crawl.contact.copyright_year
        if year and year < datetime.now(UTC).year - _OUTDATED_YEARS:
            signals.append(DetectedSignal(OUTDATED_SITE, {"copyright_year": year}))

        if not crawl.contact.has_contact_form:
            signals.append(DetectedSignal(NO_CONTACT_FORM))

    # --- contactabilidad ---
    if not facts.email:
        signals.append(DetectedSignal(NO_EMAIL))
    if not facts.phone:
        signals.append(DetectedSignal(NO_PHONE))
    if "whatsapp" not in socials:
        signals.append(DetectedSignal(NO_WHATSAPP))
    if "instagram" not in socials:
        signals.append(DetectedSignal(NO_INSTAGRAM))

    # --- reputación ---
    if facts.reviews_count is not None and facts.reviews_count < _LOW_REVIEWS_THRESHOLD:
        signals.append(DetectedSignal(LOW_REVIEWS, {"reviews_count": facts.reviews_count}))
    if facts.rating is not None and facts.rating < _LOW_RATING_THRESHOLD:
        signals.append(DetectedSignal(LOW_RATING, {"rating": facts.rating}))

    return signals
