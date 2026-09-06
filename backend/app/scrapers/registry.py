"""Selección del proveedor de descubrimiento activo.

El worker pide "el proveedor configurado" y recibe una implementación del
Protocol. Cambiar de scraper propio a Places API es cambiar un valor en
`app_settings`, sin desplegar.
"""

from __future__ import annotations

from app.core.enums import SerpProvider, SourceType
from app.core.exceptions import ConfigurationError
from app.scrapers.base import DiscoveryProvider
from app.scrapers.google_maps.browser import BrowserConfig
from app.scrapers.google_maps.provider import GoogleMapsScraperProvider
from app.scrapers.google_places_api import GooglePlacesApiProvider
from app.scrapers.social_serp import build_serp_provider

GOOGLE_MAPS_SCRAPER = "google_maps_scraper"
GOOGLE_PLACES_API = "google_places_api"
INSTAGRAM_SERP = "instagram_serp"
LINKEDIN_SERP = "linkedin_serp"

AVAILABLE_PROVIDERS = (
    GOOGLE_MAPS_SCRAPER,
    GOOGLE_PLACES_API,
    INSTAGRAM_SERP,
    LINKEDIN_SERP,
)

_SOURCE_BY_PROVIDER = {
    GOOGLE_MAPS_SCRAPER: SourceType.GOOGLE_MAPS,
    GOOGLE_PLACES_API: SourceType.GOOGLE_PLACES_API,
    INSTAGRAM_SERP: SourceType.INSTAGRAM,
    LINKEDIN_SERP: SourceType.LINKEDIN,
}


def source_type_for(provider_name: str) -> SourceType:
    return _SOURCE_BY_PROVIDER.get(provider_name, SourceType.MANUAL)


def build_provider(
    provider_name: str,
    *,
    browser_config: BrowserConfig | None = None,
    concurrency: int = 2,
    google_places_key: str | None = None,
    serp_provider: SerpProvider = SerpProvider.GOOGLE_CSE,
    serp_api_key: str | None = None,
    serp_engine_id: str | None = None,
) -> DiscoveryProvider:
    """Instancia el proveedor pedido.

    Falla con un mensaje accionable si le falta configuración: "no tienes
    clave de Places" es un problema del usuario, no un error interno, y la UI
    debe poder decírselo tal cual.
    """
    if provider_name == GOOGLE_MAPS_SCRAPER:
        return GoogleMapsScraperProvider(config=browser_config, concurrency=concurrency)

    if provider_name == GOOGLE_PLACES_API:
        if not google_places_key:
            raise ConfigurationError(
                "El proveedor Google Places requiere una clave de API. "
                "Añádela en Configuración o cambia a 'google_maps_scraper'.",
                code="MISSING_PLACES_API_KEY",
            )
        return GooglePlacesApiProvider(api_key=google_places_key)

    if provider_name in (INSTAGRAM_SERP, LINKEDIN_SERP):
        return build_serp_provider(
            "instagram" if provider_name == INSTAGRAM_SERP else "linkedin",
            provider=serp_provider,
            api_key=serp_api_key,
            engine_id=serp_engine_id,
        )

    raise ConfigurationError(
        f"Proveedor de descubrimiento desconocido: '{provider_name}'. "
        f"Opciones válidas: {', '.join(AVAILABLE_PROVIDERS)}.",
        code="UNKNOWN_DISCOVERY_PROVIDER",
    )
