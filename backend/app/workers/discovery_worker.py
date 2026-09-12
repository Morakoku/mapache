"""Worker de descubrimiento: ejecuta una búsqueda y persiste las empresas.

Persiste de forma incremental. Si el proveedor muere en el resultado 73 de
100, los 72 anteriores ya están en base de datos y el job queda como parcial,
no como fallido: el trabajo hecho no se tira.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.database import session_scope
from app.core.logging import get_logger
from app.core.security import decrypt
from app.models.search import Search

# Lazy imports for scrapers (avoid playwright in serverless)
# from app.scrapers.base import ProviderBlockedError, RawPlace
# from app.scrapers.google_maps.browser import BrowserConfig
# from app.scrapers.registry import build_provider, source_type_for
from app.services.catalog_svc import SearchService
from app.services.company_svc import CompanyService
from app.services.job_svc import JobService
from app.services.mail_admin_svc import SettingsService
from app.utils.url import is_own_website

logger = get_logger(__name__)

# Cada cuántas empresas se hace commit. Un commit por empresa es demasiada
# escritura; uno al final perdería todo ante un fallo a mitad.
_COMMIT_EVERY = 5


def _meets_requirements(raw: Any, search: Search) -> bool:
    """¿Cumple esta ficha los requisitos de contacto de la búsqueda?

    Solo se comprueba lo que la ficha de Google trae de verdad. `require_email`
    no se mira aquí a propósito: ninguna ficha de Maps incluye email —sale de
    rastrear la web después—, así que aplicarlo en este punto descartaría
    absolutamente todo. Se aplica al terminar el enriquecimiento.
    """
    if search.require_phone and not raw.phone:
        return False
    # "Con web" significa sitio propio: un negocio cuyo enlace es su Instagram
    # no cumple el requisito, aunque tenga algo en el campo "sitio web".
    tiene_web_propia = is_own_website(raw.website)
    return not (search.require_website and not tiene_web_propia)


async def run_discovery(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Ejecuta un job de descubrimiento.

    Payload esperado:
    {
        "search_id": "uuid",
        "provider": "GOOGLE_MAPS" | "APIFY" | "SERPAPI" | ...
    }
    """
    search_id = uuid.UUID(payload["search_id"])
    provider_name = payload.get("provider", "APIFY")  # Default to APIFY for serverless

    async with session_scope() as session:
        jobs = JobService(session)
        await jobs.mark_running(job_id)
        await session.commit()

        # Lazy import for scraper provider. Debe resolverse ANTES del try grande:
        # el `except ProviderBlockedError` posterior necesita el nombre ligado.
        try:
            from app.scrapers.base import ProviderBlockedError
            from app.scrapers.registry import build_provider
        except ImportError as e:
            await jobs.mark_failed(job_id, f"Proveedor no disponible: {e}")
            logger.error("scraper_import_failed", error=str(e))
            return

        # Todo el cuerpo va bajo el mismo try: un fallo temprano (p. ej. la
        # búsqueda no existe) también debe dejar el job en FAILED. El
        # `InProcessQueue` solo registra `job_failed`; persistir el estado es
        # responsabilidad del handler.
        try:
            search = await SearchService(session).get_or_404(search_id)
            if not search.is_active:
                await jobs.mark_failed(job_id, "Búsqueda inactiva")
                return

            provider_kwargs: dict[str, Any] = {}
            if provider_name in {"GOOGLE_PLACES_API", "google_places_api"}:
                settings = await SettingsService(session).get_or_404("scraper")
                api_key = decrypt(settings.get("google_places_key", ""))
                if not api_key:
                    await jobs.mark_failed(job_id, "Google Places API key no configurada")
                    return
                provider_kwargs["google_places_key"] = api_key

            provider = build_provider(provider_name, **provider_kwargs)

            async with session_scope() as s:
                companies_svc = CompanyService(s)
                saved = 0
                duplicates = 0

                # SearchQuery es el contrato del proveedor, agnóstico de la
                # fila de `searches`: campos normalizados, no los de la tabla.
                from app.scrapers.base import SearchQuery

                query = SearchQuery(
                    business_type=search.business_type,
                    city=search.city,
                    keywords=search.keywords or [],
                    zone=search.zone,
                    country=search.country,
                    region=search.region,
                    latitude=search.latitude,
                    longitude=search.longitude,
                    radius_km=float(search.radius_km or 10),
                    limit=search.target_count or 100,
                    min_rating=float(search.min_rating) if search.min_rating is not None else None,
                    max_reviews=search.max_reviews,
                    strict_match=search.strict_match,
                )

                async for raw_place in provider.search(query):
                    if not _meets_requirements(raw_place, search):
                        continue

                    # Upsert: crea o actualiza la empresa (dedupe por google_id
                    # y dedupe_key). Un resultado repetido actualiza la ficha
                    # en vez de duplicarla.
                    result = await companies_svc.upsert_from_raw(
                        raw_place, owner_id=search.owner_id
                    )
                    if result.is_new:
                        saved += 1
                    else:
                        duplicates += 1

                    if saved % _COMMIT_EVERY == 0:
                        await s.commit()
                        await jobs.update_progress(job_id, current=saved)

                await s.commit()

            await jobs.mark_completed(
                job_id,
                {
                    "saved": saved,
                    "duplicates": duplicates,
                    "provider": provider_name,
                },
            )
            logger.info(
                "discovery_completed", job_id=str(job_id), saved=saved, duplicates=duplicates
            )

        except ProviderBlockedError as e:
            await jobs.mark_failed(job_id, f"Proveedor bloqueado: {e}")
            logger.warning("provider_blocked", provider=provider_name, error=str(e))
        except Exception as e:
            await jobs.mark_failed(job_id, str(e))
            logger.exception("discovery_failed", job_id=str(job_id), error=str(e))


async def run_provider_health(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Health check de proveedores de scraping."""

    async with session_scope() as session:
        jobs = JobService(session)
        await jobs.mark_running(job_id)
        await session.commit()
        try:
            from app.scrapers.registry import AVAILABLE_PROVIDERS

            await jobs.mark_completed(job_id, {"providers": list(AVAILABLE_PROVIDERS)})
        except Exception as e:
            await jobs.mark_failed(job_id, str(e))
            logger.exception("provider_health_failed", job_id=str(job_id), error=str(e))
