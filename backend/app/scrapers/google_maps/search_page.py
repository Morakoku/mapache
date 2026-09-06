"""Fase A: recorrer el listado de resultados y recoger los enlaces de ficha.

El listado usa scroll infinito. La estrategia es hacer scroll hasta que deje
de aparecer contenido nuevo, no un número fijo de veces: el número de
resultados por búsqueda varía muchísimo.
"""

from __future__ import annotations

from urllib.parse import quote

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.core.logging import get_logger
from app.scrapers.base import ProviderBlockedError, SearchQuery
from app.scrapers.google_maps.browser import BrowserConfig
from app.scrapers.google_maps.parsers import canonical_maps_url, is_challenge_page, zoom_for_radius
from app.scrapers.google_maps.selectors import (
    CONSENT_BUTTONS,
    END_OF_LIST_PATTERNS,
    RESULT_CARD_LINK,
    RESULT_FEED,
)

logger = get_logger(__name__)

# Nº de scrolls sin resultados nuevos antes de dar el listado por agotado.
_MAX_IDLE_SCROLLS = 3
# Tope de seguridad: evita un bucle infinito si Google devuelve resultados sin
# fin para una consulta muy genérica.
_MAX_SCROLLS = 60


def build_search_url(query: SearchQuery) -> str:
    """URL del listado.

    `hl=es` fuerza el idioma: los selectores dependen de `aria-label` en
    español, así que sin esto la extracción se vuelve no determinista según
    dónde esté la IP de salida.
    """
    term = quote(query.text_query)
    zoom = zoom_for_radius(query.radius_km)

    if query.latitude is not None and query.longitude is not None:
        center = f"/@{query.latitude},{query.longitude},{zoom}z"
    else:
        center = ""

    return f"https://www.google.com/maps/search/{term}{center}?hl={query.language}"


async def dismiss_consent(page: Page) -> None:
    """Cierra el diálogo de cookies si aparece; si no, sigue sin ruido."""
    for selector in CONSENT_BUTTONS:
        try:
            button = page.locator(selector).first
            if await button.count() > 0 and await button.is_visible():
                await button.click(timeout=3000)
                logger.debug("consent_dismissed", selector=selector)
                await page.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001 - el diálogo es opcional
            continue


async def collect_place_links(
    page: Page,
    query: SearchQuery,
    config: BrowserConfig,
) -> list[str]:
    """Devuelve las URLs de ficha del listado, hasta `query.limit`."""
    url = build_search_url(query)
    logger.info("search_page_open", url=url, limit=query.limit)

    await page.goto(url, wait_until="domcontentloaded")
    await dismiss_consent(page)

    content = await page.content()
    if is_challenge_page(page.url, content):
        raise ProviderBlockedError(
            "Google presentó un desafío de verificación. La búsqueda se detuvo; "
            "reintenta más tarde o cambia de proveedor en Configuración."
        )

    # Maps es una SPA: en `domcontentloaded` el listado todavía no existe, lo
    # pinta el JS unos segundos después. Sin esta espera explícita el scraper
    # mide una página vacía y concluye que no hay resultados.
    feed = await _wait_for_feed(page)

    # Resiliencia del listado: una página en blanco transitoria no debe matar la
    # búsqueda. Si no aparece ni feed ni tarjetas y no es una ficha directa, se
    # recarga una vez (los retries del detalle no cubren este paso).
    if feed is None and "/maps/place/" not in page.url:
        logger.info("search_page_no_feed_retry")
        await page.goto(url, wait_until="domcontentloaded")
        await dismiss_consent(page)
        feed = await _wait_for_feed(page)

    if feed is None:
        # Búsqueda de un único negocio: Google va directo a la ficha en vez de
        # mostrar listado. Es un resultado válido, no un error.
        if "/maps/place/" in page.url:
            logger.info("search_page_single_result")
            canonical = canonical_maps_url(page.url)
            return [canonical] if canonical else []
        logger.warning("search_page_no_feed", url=page.url)
        return []

    seen: list[str] = []
    seen_set: set[str] = set()
    idle_scrolls = 0

    for scroll_num in range(_MAX_SCROLLS):
        hrefs = await feed.locator(RESULT_CARD_LINK).evaluate_all("nodes => nodes.map(n => n.href)")
        added = 0
        for href in hrefs:
            canonical = canonical_maps_url(href)
            if canonical and canonical not in seen_set:
                seen_set.add(canonical)
                seen.append(canonical)
                added += 1

        if len(seen) >= query.limit:
            break

        if added == 0:
            idle_scrolls += 1
            if idle_scrolls >= _MAX_IDLE_SCROLLS:
                logger.info("search_page_exhausted", found=len(seen), scrolls=scroll_num)
                break
        else:
            idle_scrolls = 0

        if await _reached_end(page):
            logger.info("search_page_end_marker", found=len(seen))
            break

        await feed.evaluate("node => node.scrollTo(0, node.scrollHeight)")
        await config.sleep()

    result = seen[: query.limit]
    logger.info("search_page_collected", total=len(result))
    return result


async def _wait_for_feed(page: Page, timeout_ms: int = 15_000) -> Locator | None:
    """Espera a que el JS pinte el listado de resultados.

    Devuelve el locator del feed, o None si no aparece (búsqueda de un único
    negocio, sin resultados, o cambio de layout).
    """
    try:
        await page.wait_for_selector(RESULT_FEED, timeout=timeout_ms, state="attached")
    except PlaywrightTimeout:
        # Puede que no haya feed pero sí tarjetas: Google tiene más de un
        # layout de resultados y conviene no descartar la búsqueda por eso.
        if await page.locator(RESULT_CARD_LINK).count() > 0:
            logger.info("search_page_no_feed_but_cards")
            return page.locator("div[role='main']").first
        return None

    feed = page.locator(RESULT_FEED).first
    return feed if await feed.count() > 0 else None


async def _reached_end(page: Page) -> bool:
    """¿Apareció el texto de fin de lista?"""
    try:
        body = await page.inner_text("body", timeout=2000)
    except Exception:  # noqa: BLE001
        return False
    return any(pattern in body for pattern in END_OF_LIST_PATTERNS)
