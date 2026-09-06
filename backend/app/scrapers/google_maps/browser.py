"""Ciclo de vida del navegador headless.

Un contexto por ejecución de búsqueda, reutilizado entre fichas. Abrir un
contexto por ficha es lento y, paradójicamente, más llamativo que reutilizar
uno: nadie navega abriendo un navegador nuevo por cada clic.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.core.logging import get_logger

logger = get_logger(__name__)

# UA de un Chrome de escritorio reciente. Se declara explícitamente porque el
# de Playwright incluye "HeadlessChrome", que muchos sitios tratan distinto.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    headless: bool = True
    locale: str = "es-CO"
    timezone: str = "America/Bogota"
    viewport_width: int = 1440
    viewport_height: int = 900
    delay_ms_min: int = 1200
    delay_ms_max: int = 3500
    nav_timeout_ms: int = 20_000
    action_timeout_ms: int = 8_000

    async def sleep(self) -> None:
        """Pausa aleatoria entre acciones.

        Aleatoria y no fija: un intervalo exacto y repetido es un patrón
        obvio, y además concentra la carga en el servidor de forma innecesaria.
        """
        await asyncio.sleep(random.uniform(self.delay_ms_min, self.delay_ms_max) / 1000)


class BrowserSession:
    """Envuelve un contexto de Playwright con la configuración del scraper."""

    def __init__(self, context: BrowserContext, config: BrowserConfig) -> None:
        self._context = context
        self.config = config

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        page = await self._context.new_page()
        page.set_default_timeout(self.config.action_timeout_ms)
        page.set_default_navigation_timeout(self.config.nav_timeout_ms)
        try:
            yield page
        finally:
            await page.close()


@asynccontextmanager
async def browser_session(config: BrowserConfig) -> AsyncIterator[BrowserSession]:
    """Abre navegador y contexto, y los cierra pase lo que pase.

    El `finally` importa: un job cancelado a mitad dejaría procesos de Chromium
    huérfanos comiendo memoria hasta reiniciar.
    """
    playwright = await async_playwright().start()
    browser: Browser | None = None
    try:
        browser = await playwright.chromium.launch(
            headless=config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            locale=config.locale,
            timezone_id=config.timezone,
            viewport={"width": config.viewport_width, "height": config.viewport_height},
            geolocation=None,
            permissions=[],
        )
        # No cargar imágenes ni fuentes: la ficha se parsea del DOM, así que
        # descargarlas es ancho de banda y tiempo tirados. Baja el tiempo por
        # ficha a más o menos la mitad.
        await context.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in {"image", "media", "font"}
                else route.continue_()
            ),
        )
        logger.info("browser_started", headless=config.headless, locale=config.locale)
        yield BrowserSession(context, config)
    finally:
        if browser is not None:
            await browser.close()
        await playwright.stop()
        logger.info("browser_stopped")
