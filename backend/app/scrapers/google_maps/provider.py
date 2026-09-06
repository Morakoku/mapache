"""Proveedor de descubrimiento: scraper propio de Google Maps.

Orquesta las dos fases (listado y detalle) y emite `RawPlace` según van
saliendo, para que el worker pueda persistir de forma incremental.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.core.enums import SourceType
from app.core.logging import get_logger
from app.scrapers.base import (
    DiscoveryProvider,
    ProviderBlockedError,
    ProviderHealth,
    RawPlace,
    SearchQuery,
)
from app.scrapers.google_maps.browser import BrowserConfig, browser_session
from app.scrapers.google_maps.detail_page import scrape_place
from app.scrapers.google_maps.search_page import collect_place_links
from app.scrapers.google_maps.selectors import build_selector_chains

logger = get_logger(__name__)

# Campos que deben salir en la mayoría de fichas. Si casi ninguna los trae, el
# scraper está roto aunque no lance ninguna excepción — que es el modo de
# fallo peligroso, porque produce datos vacíos en silencio.
_CRITICAL_FIELDS = ("address", "phone", "website", "rating")
_MIN_CRITICAL_RATIO = 0.20


class GoogleMapsScraperProvider(DiscoveryProvider):
    name = "google_maps_scraper"
    source_type = SourceType.GOOGLE_MAPS

    def __init__(self, config: BrowserConfig | None = None, concurrency: int = 2) -> None:
        self.config = config or BrowserConfig()
        # Concurrencia baja a propósito: dos fichas a la vez ya saturan una
        # máquina de desarrollo, y el objetivo (100 empresas/día) no pide más.
        self.concurrency = max(1, concurrency)
        self._last_stats: dict[str, int] = {}

    @property
    def last_stats(self) -> dict[str, int]:
        """Cómo fue la última búsqueda: cuántas se descartaron y por qué."""
        return dict(self._last_stats)

    async def search(self, query: SearchQuery) -> AsyncIterator[RawPlace]:
        chains = build_selector_chains()
        stats = {"links": 0, "scraped": 0, "failed": 0, "filtered": 0}

        async with browser_session(self.config) as session:
            async with session.page() as page:
                links = await collect_place_links(page, query, self.config)
            stats["links"] = len(links)

            if not links:
                logger.warning("scraper_no_links", query=query.text_query)
                self._last_stats = stats
                return

            queue: asyncio.Queue[RawPlace] = asyncio.Queue()
            semaphore = asyncio.Semaphore(self.concurrency)
            blocked: list[ProviderBlockedError] = []
            # Circuit breaker: ante el primer bloqueo se aborta el fan-out.
            # Golpear a Google bloqueado con las N fichas restantes solo agrava
            # el challenge y quema minutos; con esto el run se corta en segundos.
            trip = asyncio.Event()

            async def worker(index: int, url: str) -> None:
                async with semaphore:
                    if trip.is_set():
                        return
                    try:
                        async with session.page() as page:
                            place = await scrape_place(page, url, chains, position=index)
                    except ProviderBlockedError as exc:
                        blocked.append(exc)
                        trip.set()
                        return
                    except Exception as exc:  # noqa: BLE001 - una ficha rota no aborta el run
                        stats["failed"] += 1
                        logger.warning("scraper_place_failed", url=url, error=str(exc))
                        return
                    finally:
                        await self.config.sleep()

                    if place is None:
                        stats["failed"] += 1
                    elif (motivo := query.rejection_reason(place)) is not None:
                        # Se cuenta por motivo: "descartó 45 porque no eran
                        # panaderías" es accionable; "descartó 45" no.
                        stats["filtered"] += 1
                        stats[f"filtered_{motivo}"] = stats.get(f"filtered_{motivo}", 0) + 1
                    else:
                        stats["scraped"] += 1
                        await queue.put(place)

            tasks = [asyncio.create_task(worker(i, url)) for i, url in enumerate(links)]
            pending = set(tasks)

            try:
                while pending or not queue.empty():
                    while not queue.empty():
                        yield queue.get_nowait()
                    if not pending:
                        break
                    _done, pending = await asyncio.wait(
                        pending, return_when=asyncio.FIRST_COMPLETED
                    )
                    if trip.is_set():
                        # Ya se disparó el circuito: cancelar el resto de fichas
                        # y entregar lo que ya se extrajo sin esperar el drenado.
                        for task in pending:
                            task.cancel()
                        break
                while not queue.empty():
                    yield queue.get_nowait()
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            self._last_stats = stats
            self._log_quality(chains, stats)

            if blocked:
                raise blocked[0]

    def _log_quality(self, chains: dict, stats: dict[str, int]) -> None:
        """Deja constancia de si los selectores están degradándose."""
        scraped = stats.get("scraped", 0)
        logger.info("scraper_run_finished", **stats)

        if scraped == 0:
            return

        for field in _CRITICAL_FIELDS:
            chain = chains.get(field)
            if chain is None:
                continue
            ratio = chain.total_hits / scraped
            if ratio < _MIN_CRITICAL_RATIO:
                logger.warning(
                    "scraper_field_mostly_missing",
                    field=field,
                    ratio=round(ratio, 2),
                    hint="posible cambio de DOM: revisar selectors.py",
                )
            if chain.fallback_ratio > 0.30:
                logger.warning(
                    "scraper_selector_degraded",
                    field=field,
                    fallback_ratio=round(chain.fallback_ratio, 2),
                    hint="el selector semántico dejó de funcionar",
                )

    async def healthcheck(self) -> ProviderHealth:
        """Búsqueda canario contra una cadena conocida.

        Si esto falla, el scraper está roto y conviene saberlo antes de lanzar
        una búsqueda de 100 empresas que devolvería basura.
        """
        query = SearchQuery(
            business_type="Éxito",
            city="Medellín",
            limit=3,
            latitude=6.2088,
            longitude=-75.5906,
        )
        checked: dict[str, bool] = {}
        try:
            places = [place async for place in self.search(query)]
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(
                provider=self.name, healthy=False, message=f"El canario falló: {exc}"
            )

        if not places:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                message="El canario no devolvió resultados: probable cambio del listado.",
            )

        checked["name"] = all(p.name for p in places)
        checked["address"] = any(p.address for p in places)
        checked["coordinates"] = any(p.latitude for p in places)
        checked["ftid"] = any(p.ftid for p in places)
        checked["maps_url"] = all(p.maps_url for p in places)

        healthy = all(checked.values())
        return ProviderHealth(
            provider=self.name,
            healthy=healthy,
            checked_fields=checked,
            message=None if healthy else "Campos degradados: revisar selectors.py",
        )
