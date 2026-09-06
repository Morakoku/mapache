"""Motor de extracción por cascada de selectores.

Separado de `detail_page.py` a propósito: aquí no se sabe nada de Google Maps,
solo de "intenta estos selectores en orden y dime cuál funcionó". Eso permite
probarlo sin navegador.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.core.logging import get_logger
from app.scrapers.google_maps.selectors import SelectorChain

logger = get_logger(__name__)


class TextSource(Protocol):
    """Origen de texto sobre el que aplicar los selectores.

    Lo implementan tanto la página real de Playwright como el doble de prueba,
    y por eso el motor de cascada se puede testear sin levantar Chromium.
    """

    async def query_text(self, css: str, attribute: str | None) -> str | None: ...

    async def full_text(self) -> str: ...


async def extract(chain: SelectorChain, source: TextSource) -> str | None:
    """Recorre la cascada y devuelve el primer valor no vacío.

    Registra en qué nivel se obtuvo: si el nivel 1 (semántico) deja de
    funcionar y responde el 3 (regex), `fallback_ratio` sube y el healthcheck
    lo detecta antes de que los datos se degraden sin avisar.
    """
    for level, selector in enumerate(chain.selectors):
        try:
            if selector.kind == "css":
                value = await source.query_text(selector.expression, selector.attribute)
            else:
                haystack = await source.full_text()
                match = re.search(selector.expression, haystack)
                value = match.group(selector.group if match.re.groups else 0) if match else None
        except Exception as exc:  # noqa: BLE001 - un selector roto no debe tumbar la ficha
            logger.debug(
                "selector_failed",
                field=chain.field_name,
                level=level,
                error=str(exc),
            )
            continue

        if value and value.strip():
            chain.record_hit(level)
            if level > 0:
                logger.debug("selector_fallback", field=chain.field_name, level=level)
            return value.strip()

    return None


class PageTextSource:
    """Adaptador de una `Page` de Playwright al protocolo `TextSource`."""

    def __init__(self, page: object, timeout_ms: int = 2500) -> None:
        self._page = page
        self._timeout = timeout_ms
        self._cached_text: str | None = None

    async def query_text(self, css: str, attribute: str | None) -> str | None:
        locator = self._page.locator(css).first  # type: ignore[attr-defined]
        if await locator.count() == 0:
            return None
        if attribute:
            return await locator.get_attribute(attribute, timeout=self._timeout)
        return await locator.inner_text(timeout=self._timeout)

    async def full_text(self) -> str:
        """Texto del panel, cacheado: los regex de la cascada lo reusan."""
        if self._cached_text is None:
            try:
                main = self._page.locator("div[role='main']").first  # type: ignore[attr-defined]
                if await main.count() > 0:
                    self._cached_text = await main.inner_text(timeout=self._timeout)
                else:
                    self._cached_text = await self._page.inner_text("body", timeout=self._timeout)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                self._cached_text = ""
        return self._cached_text
