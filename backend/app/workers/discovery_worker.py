"""Worker de descubrimiento: ejecuta una búsqueda y persiste las empresas.

Persiste de forma incremental. Si el proveedor muere en el resultado 73 de
100, los 72 anteriores ya están en base de datos y el job queda como parcial,
no como fallido: el trabajo hecho no se tira.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.database import session_scope
from app.core.enums import JobStatus, JobType, SerpProvider, SourceType
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

        search = await SearchService(session).get_or_404(search_id)
        if not search.is_active:
            await jobs.mark_failed(job_id, "Búsqueda inactiva")
            return

        # Lazy import for scraper provider
        try:
            from app.scrapers.registry import build_provider, source_type_for
            from app.scrapers.base import ProviderBlockedError
        except ImportError as e:
            await jobs.mark_failed(job_id, f"Proveedor no disponible: {e}")
            logger.error("scraper_import_failed", error=str(e))
            return

        provider = build_provider(provider_name)
        source_type = source_type_for(provider_name)

        # Configuración del proveedor
        if provider_name in {"APIFY", "SERPAPI"}:
            # API key from settings
            settings = await SettingsService(session).get_or_404("scraper")
            api_key = decrypt(settings.get(f"{provider_name.lower()}_api_key", ""))
            if not api_key:
                await jobs.mark_failed(job_id, f"{provider_name} API key no configurada")
                return

        try:
            async with session_scope() as s:
                companies_svc = CompanyService(s)
                saved = 0
                duplicates = 0

                async for raw_place in provider.search(
                    query=search.query,
                    location=search.city,
                    max_results=search.max_results,
                    api_key=api_key if provider_name in {"APIFY", "SERPAPI"} else None,
                ):
                    if not _meets_requirements(raw_place, search):
                        continue

                    # Check duplicate
                    existing = await companies_svc.find_by_dedupe_key(
                        search.owner_id, raw_place.dedupe_key
                    )
                    if existing:
                        duplicates += 1
                        continue

                    # Create company
                    await companies_svc.create_from_raw(raw_place, source_type, search.owner_id)
                    saved += 1

                    if saved % _COMMIT_EVERY == 0:
                        await s.commit()
                        await jobs.update_progress(job_id, current=saved)

                await s.commit()

            await jobs.mark_completed(job_id, {
                "saved": saved,
                "duplicates": duplicates,
                "provider": provider_name,
            })
            logger.info("discovery_completed", job_id=str(job_id), saved=saved, duplicates=duplicates)

        except ProviderBlockedError as e:
            await jobs.mark_failed(job_id, f"Proveedor bloqueado: {e}")
            logger.warning("provider_blocked", provider=provider_name, error=str(e))
        except Exception as e:  # noqa: BLE001
            await jobs.mark_failed(job_id, str(e))
            logger.exception("discovery_failed", job_id=str(job_id), error=str(e))


async def run_provider_health(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Health check de proveedores de scraping."""
    # Simple health check - just verify we can import
    try:
        from app.scrapers.registry import AVAILABLE_PROVIDERS
        await JobService(session).mark_completed(job_id, {"providers": list(AVAILABLE_PROVIDERS)})
    except Exception as e:
        await JobService(session).mark_failed(job_id, str(e))