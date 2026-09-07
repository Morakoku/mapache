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
from app.scrapers.base import ProviderBlockedError, RawPlace
from app.scrapers.google_maps.browser import BrowserConfig
from app.scrapers.registry import build_provider, source_type_for
from app.services.catalog_svc import SearchService
from app.services.company_svc import CompanyService
from app.services.job_svc import JobService
from app.services.mail_admin_svc import SettingsService
from app.utils.url import is_own_website

logger = get_logger(__name__)

# Cada cuántas empresas se hace commit. Un commit por empresa es demasiada
# escritura; uno al final perdería todo ante un fallo a mitad.
_COMMIT_EVERY = 5


def _meets_requirements(raw: RawPlace, search: Search) -> bool:
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
    search_id = uuid.UUID(payload["search_id"])
    provider_name = payload.get("provider", "google_maps_scraper")

    async with session_scope() as session:
        jobs = JobService(session)
        searches = SearchService(session)
        companies = CompanyService(session)

        await jobs.mark_running(job_id)
        try:
            search = await searches.get_or_404(search_id)
        except Exception as exc:  # noqa: BLE001 - target inválido no debe dejar el job RUNNING
            await jobs.mark_failed(job_id, f"{type(exc).__name__}: {exc}")
            await session.commit()
            logger.warning("discovery_failed_early", job_id=str(job_id), error=str(exc))
            return
        query = searches.to_query(search)

        run = await searches.start_run(
            search, provider=source_type_for(provider_name), job_id=job_id
        )
        await session.commit()

        await jobs.update_progress(
            job_id, current=0, total=query.limit, message="Buscando empresas…"
        )
        await session.commit()

        # Los knobs de velocidad/concurrencia viajan en el payload (se plomean
        # en el router desde app_settings). Si la UI los cambia, el scraper los
        # respeta sin volver a desplegar.
        browser_config = None
        if provider_name == "google_maps_scraper":
            browser_config = BrowserConfig(
                headless=bool(payload.get("scraper_headless", True)),
                delay_ms_min=int(payload.get("scraper_delay_min", 1200)),
                delay_ms_max=int(payload.get("scraper_delay_max", 3500)),
            )

        # LOOP-13: las claves se leen y descifran del AppSettings aquí, en la
        # sesión del worker; nunca viajan en el payload del job (que se
        # persiste en `jobs.payload`). Los knobs de velocidad siguen en payload.
        settings_row = await SettingsService(session).get()
        provider = build_provider(
            provider_name,
            browser_config=browser_config,
            concurrency=int(payload.get("scraper_concurrency", 2)),
            google_places_key=(
                decrypt(settings_row.google_places_key_enc)
                if settings_row.google_places_key_enc
                else None
            ),
            serp_provider=(
                settings_row.serp_provider
                if settings_row.serp_provider
                else SerpProvider.GOOGLE_CSE
            ),
            serp_api_key=(
                decrypt(settings_row.serp_api_key_enc) if settings_row.serp_api_key_enc else None
            ),
            serp_engine_id=settings_row.serp_engine_id,
        )

        found = new = duplicate = skipped = 0
        new_company_ids: list[str] = []
        error: str | None = None
        blocked = False

        try:
            async for raw in provider.search(query):
                # Se descarta antes de guardar: una empresa a la que no se
                # puede llamar ni escribir no es un prospecto, y guardarla
                # obliga a filtrarla a mano cada mañana.
                if not _meets_requirements(raw, search):
                    skipped += 1
                    continue

                result = await companies.upsert_from_raw(raw, owner_id=search.owner_id)
                found += 1
                if result.is_new:
                    new += 1
                    new_company_ids.append(str(result.company.id))
                else:
                    duplicate += 1

                await searches.link_result(
                    run,
                    result.company.id,
                    is_new=result.is_new,
                    position=raw.position,
                    raw_payload=raw.raw or None,
                )

                if found % _COMMIT_EVERY == 0:
                    await jobs.update_progress(
                        job_id,
                        current=found,
                        message=f"{found} empresas ({new} nuevas)",
                    )
                    await session.commit()

        except ProviderBlockedError as exc:
            # No se reintenta: insistir ante un challenge solo empeora las
            # cosas. Se conserva lo extraído y se avisa con claridad.
            blocked = True
            error = str(exc)
            logger.warning("discovery_blocked", job_id=str(job_id), found=found)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("discovery_failed", job_id=str(job_id), found=found)

        await searches.finish_run(
            run,
            status=JobStatus.FAILED if (error and found == 0) else JobStatus.COMPLETED,
            found=found,
            new=new,
            duplicate=duplicate,
            error=error,
        )

        result_payload: dict[str, Any] = {
            "search_run_id": str(run.id),
            "found": found,
            "new": new,
            "duplicate": duplicate,
            "skipped_requirements": skipped,
            "blocked": blocked,
        }
        # Los descartes del proveedor, por motivo. Sin esto el usuario ve
        # "trajo 35" y no sabe si es que no hay más panaderías o que el filtro
        # se comió 45 resultados buenos.
        provider_stats = getattr(provider, "last_stats", None)
        if provider_stats:
            result_payload["filtered"] = {
                clave.removeprefix("filtered_"): valor
                for clave, valor in provider_stats.items()
                if clave.startswith("filtered_")
            }
            result_payload["filtered_total"] = provider_stats.get("filtered", 0)

        if error and found == 0:
            await jobs.mark_failed(job_id, error)
        else:
            if error:
                result_payload["partial_error"] = error
            await jobs.mark_completed(job_id, result_payload)

        await session.commit()

    # El enriquecimiento se encola fuera de la transacción anterior: así el
    # resultado del descubrimiento ya está confirmado en base de datos cuando
    # arranca el siguiente job.
    if search.auto_enrich and new_company_ids:
        from app.core.container import get_job_queue

        enrich_payload: dict[str, Any] = {"company_ids": new_company_ids}
        # El enriquecimiento es quien encuentra el email, así que es quien
        # puede aplicar el requisito. Se le pasa la orden en el payload.
        if search.require_email:
            enrich_payload["require_email"] = True
            enrich_payload["search_id"] = str(search.id)

        async with session_scope() as session:
            enrich_job = await JobService(session).create(
                JobType.ENRICHMENT,
                enrich_payload,
                owner_id=search.owner_id,
                progress_total=len(new_company_ids),
            )
            enrich_job_id = enrich_job.id

        await get_job_queue().enqueue(
            JobType.ENRICHMENT,
            enrich_payload,
            job_id=enrich_job_id,
        )
        logger.info("enrichment_chained", job_id=str(enrich_job_id), companies=len(new_company_ids))


async def run_provider_health(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Canario diario del proveedor activo (§4.8.6).

    Avisa de que el scraper se rompió *antes* de que el usuario lance una
    búsqueda de 100 empresas y reciba basura.
    """
    provider_name = payload.get("provider", "google_maps_scraper")

    async with session_scope() as session:
        jobs = JobService(session)
        await jobs.mark_running(job_id)
        await session.commit()

        try:
            provider = build_provider(provider_name, google_places_key=payload.get("api_key"))
            health = await provider.healthcheck()
            await jobs.mark_completed(
                job_id,
                {
                    "provider": health.provider,
                    "healthy": health.healthy,
                    "checked_fields": health.checked_fields,
                    "degraded_fields": health.degraded_fields,
                    "message": health.message,
                },
            )
            if not health.healthy:
                logger.warning(
                    "provider_health_degraded",
                    provider=health.provider,
                    degraded=health.degraded_fields,
                )
        except Exception as exc:  # noqa: BLE001 - un fallo del proveedor no debe perder lo extraído
            await jobs.mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        await session.commit()


# Registrado como SourceType para que el worker no dependa del enum de rutas.
DISCOVERY_DEFAULT_SOURCE = SourceType.GOOGLE_MAPS
