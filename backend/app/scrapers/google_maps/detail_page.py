"""Fase B: abrir la ficha de un negocio y extraer los campos del Módulo 3."""

from __future__ import annotations

import asyncio
import re

from playwright.async_api import Page

from app.core.enums import SourceType
from app.core.logging import get_logger
from app.scrapers.base import ProviderBlockedError, RawPlace
from app.scrapers.google_maps.extraction import PageTextSource, extract
from app.scrapers.google_maps.parsers import (
    canonical_maps_url,
    is_challenge_page,
    parse_address,
    parse_coordinates,
    parse_ftid,
    parse_opening_hours,
    parse_phone,
    parse_rating,
    parse_reviews_count,
    split_address,
)
from app.scrapers.google_maps.selectors import SelectorChain
from app.utils.url import (
    detect_social_platform,
    is_share_url,
    normalize_url,
    social_handle,
    unwrap_redirect,
)

logger = get_logger(__name__)

_CLOSED_MARKERS = ("Cerrado permanentemente", "Permanently closed", "Cerrado definitivamente")

# Solo los enlaces del panel de la ficha. Sin acotar, entran los del pie de
# Google y los perfiles de quienes dejaron reseñas, y acabaríamos guardando
# el Facebook de Google como si fuera el del negocio.
_PANEL_LINKS = "div[role='main'] a[href^='http'], div[role='main'] a[href^='/url?']"

# Nivel 2: el perfil escrito en la descripción en vez de enlazado.
_SOCIAL_TEXT_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:instagram\.com|tiktok\.com|linkedin\.com|x\.com|twitter\.com|facebook\.com|"
    r"youtube\.com|wa\.me|t\.me)/[^\s\"'<>)\]]+",
    re.IGNORECASE,
)


async def scrape_place(
    page: Page,
    url: str,
    chains: dict[str, SelectorChain],
    position: int | None = None,
) -> RawPlace | None:
    """Extrae una ficha. Devuelve None si no hay datos utilizables.

    Un fallo en una ficha concreta no debe abortar el run entero: se registra
    y se sigue con la siguiente. Solo el bloqueo de Google interrumpe todo,
    porque ahí seguir insistiendo empeora la situación.
    """
    # Retry ante fallos transitorios de terceros (DOM lento, timeout, tab
    # caprichosa). El bloqueo NO se reintenta: es un ProviderBlockedError y se
    # deja subir tal cual para que el circuit breaker dispare.
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return await _scrape_place_once(page, url, chains, position)
        except ProviderBlockedError as exc:
            raise exc
        except Exception as exc:  # noqa: BLE001 - re-levantamos el último intento
            last_err = exc
            if attempt < 2:
                await asyncio.sleep(2**attempt)  # backoff 1s -> 2s
    logger.warning("detail_page_retries_exhausted", url=url, error=str(last_err))
    return None


async def _scrape_place_once(
    page: Page,
    url: str,
    chains: dict[str, SelectorChain],
    position: int | None,
) -> RawPlace | None:
    await page.goto(url, wait_until="domcontentloaded")

    if is_challenge_page(page.url, await page.content()):
        raise ProviderBlockedError("Google presentó un desafío al abrir la ficha del negocio.")

    try:
        await page.wait_for_selector("h1", timeout=8000)
    except Exception:  # noqa: BLE001
        logger.warning("detail_page_no_heading", url=url)
        return None

    source = PageTextSource(page)
    final_url = page.url

    name = await extract(chains["name"], source)
    if not name:
        logger.warning("detail_page_no_name", url=url)
        return None

    address_raw = await extract(chains["address"], source)
    address = parse_address(address_raw)
    address_parts = split_address(address)

    phone_raw = await extract(chains["phone"], source)
    website = await extract(chains["website"], source)
    rating_raw = await extract(chains["rating"], source)
    reviews_raw = await extract(chains["reviews"], source)
    category = await extract(chains["category"], source)
    description = await extract(chains["description"], source)

    latitude, longitude = parse_coordinates(final_url)
    body_text = await source.full_text()
    socials = await extract_socials(page, body_text)

    place = RawPlace(
        name=name,
        source=SourceType.GOOGLE_MAPS,
        ftid=parse_ftid(final_url),
        external_id=parse_ftid(final_url),
        description=description,
        category=category,
        categories=[category] if category else [],
        address=address,
        city=address_parts["city"],
        region=address_parts["region"],
        postal_code=address_parts["postal_code"],
        phone=parse_phone(phone_raw),
        website=website,
        maps_url=canonical_maps_url(final_url),
        latitude=latitude,
        longitude=longitude,
        rating=parse_rating(rating_raw),
        reviews_count=parse_reviews_count(reviews_raw),
        opening_hours=await _extract_hours(page),
        socials=socials,
        is_permanently_closed=any(marker in body_text for marker in _CLOSED_MARKERS),
        position=position,
        raw={"url": final_url, "scraped_name": name},
    )

    if not place.is_usable:
        logger.warning("detail_page_unusable", url=url, name=name)
        return None

    return place


async def extract_socials(page: Page, body_text: str) -> dict[str, str]:
    """Todas las redes que enlaza la ficha, no solo la del campo "sitio web".

    Dos niveles, como el resto del scraper: primero los enlaces reales del
    panel —que es lo semántico y lo que Google necesita para su propia
    accesibilidad—, y si no hay ninguno, una pasada de regex sobre el texto,
    porque a veces el perfil aparece escrito en la descripción del negocio en
    vez de enlazado.
    """
    socials: dict[str, str] = {}

    try:
        hrefs: list[str] = await page.locator(_PANEL_LINKS).evaluate_all(
            "els => els.map(e => e.href).filter(Boolean)"
        )
    except Exception as exc:  # noqa: BLE001 - las redes son opcionales
        logger.debug("socials_links_failed", error=str(exc))
        hrefs = []

    for href in hrefs:
        _add_social(socials, href)

    if not socials:
        for match in _SOCIAL_TEXT_RE.finditer(body_text):
            _add_social(socials, match.group(0))

    return socials


def _add_social(socials: dict[str, str], raw: str) -> None:
    """Registra la URL si es un perfil social y aún no hay uno de esa red."""
    url = unwrap_redirect(raw)
    platform = detect_social_platform(url)
    if not platform or platform in socials:
        return

    # Un botón de compartir no es una cuenta del negocio.
    if is_share_url(url):
        return

    normalized = normalize_url(url)
    if not normalized:
        return

    # Un enlace a la portada de la red no es el perfil de nadie.
    if social_handle(normalized) is None:
        return

    socials[platform] = normalized


async def _extract_hours(page: Page) -> dict | None:
    """Lee la tabla de horarios.

    Va aparte de la cascada general porque no devuelve un string sino filas
    día/horario, y a veces está colapsada tras un botón.
    """
    try:
        table = page.locator("div[jsaction*='openhours'] table, table.eK4R0e").first
        if await table.count() == 0:
            return None

        rows = await table.locator("tr").evaluate_all(
            """rows => rows.map(r => {
                const cells = r.querySelectorAll('td, th');
                return cells.length >= 2
                    ? [cells[0].innerText.trim(), cells[1].innerText.trim()]
                    : null;
            }).filter(Boolean)"""
        )
        return parse_opening_hours([(r[0], r[1]) for r in rows if len(r) == 2])
    except Exception as exc:  # noqa: BLE001 - los horarios son opcionales
        logger.debug("hours_extraction_failed", error=str(exc))
        return None
