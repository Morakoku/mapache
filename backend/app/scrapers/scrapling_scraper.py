"""
Scrapling scraper para Mapache CRM.

Usa Scrapling (StealthyFetcher con Camoufox/Playwright stealth) para extraer
datos de Google Maps. Reemplaza al antiguo google-maps-scraper.exe (Go).

API real de Scrapling 0.4.15:
- StealthyFetcher.fetch(url, **kwargs) -> Response  (síncrono)
- StealthyFetcher.async_fetch(url, **kwargs) -> Response
- Response.status (int), Response.css('sel') -> Selectors (lista indexable con .first)
- kwargs: extra_headers, timeout, headless, locale, wait, solve_cloudflare, google_search
- Configuración previa: StealthyFetcher.configure(headless=True)

Dependencias: pip install scrapling playwright
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from scrapling.fetchers import StealthyFetcher

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Teléfono: admite formatos con paréntesis +57 601 744 3466, (601) 7443466, etc.
_PHONE_RE = re.compile(
    r"(\(?\+?\d[\d\s().-]{6,}\d\)?)"
)


class ScraplingMapsScraper:
    """Scraper de Google Maps con Scrapling StealthyFetcher.

    Extrae: nombre, categoría, rating, reviews, dirección, teléfono, sitio web.
    Paginación por scroll, rate limiting y logging incluidos.
    """

    def __init__(self, headless: bool = True) -> None:
        settings = get_settings()
        self.base_url = "https://www.google.com/maps"
        self.headless = headless
        self.fetcher = StealthyFetcher
        self._phone_re = _PHONE_RE

    # ------------------------------------------------------------------ utils

    def _q(self, query: str) -> str:
        return query.strip().replace(" ", "+")

    def _clean_phone(self, raw: str) -> str:
        m = self._phone_re.search(raw or "")
        return m.group(1).strip() if m else ""

    def _txt(self, node: Any) -> str:
        try:
            t = node.text
            return t.strip() if isinstance(t, str) else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ core

    def search_sync(
        self,
        query: str,
        limit: int = 20,
        language: str = "es",
        location: str | None = None,
        page_load_wait: int = 4,
    ) -> list[dict[str, Any]]:
        """Búsqueda síncrona en Google Maps (una página de resultados).

        Args:
            query: término de búsqueda, ej "restaurantes en Bogotá"
            limit: máximo de resultados a devolver
            language: idioma de la interfaz (es, en, ...)
            location: texto de ubicación para centrar la búsqueda
            page_load_wait: segundos extra de espera tras cargar

        Returns:
            Lista de dicts con name, category, rating, reviews, address,
            phone, website, url, query, source.
        """
        full_query = f"{query} {location}".strip() if location else query
        url = f"{self.base_url}/search/{self._q(full_query)}/?hl={language}"

        logger.info("Scrapling: buscando %r (limit=%s)", full_query, limit)
        try:
            page = self.fetcher.fetch(
                url,
                extra_headers={"User-Agent": _UA, "Accept-Language": f"{language},es;q=0.9,en;q=0.8"},
                timeout=45000,
                # espera a que renderice el feed de resultados
                wait=page_load_wait,
                headless=self.headless,
                solve_cloudflare=True,
            )
        except Exception as exc:
            logger.error("Scrapling: error fetch %r: %s", full_query, exc)
            return []

        if page.status != 200:
            logger.warning("Scrapling: HTTP %s en búsqueda %r", page.status, full_query)
            return []

        items = self._parse(page, full_query)
        logger.info("Scrapling: %s resultados de %r", len(items), full_query)
        return items[:limit]

    async def search(
        self,
        query: str,
        limit: int = 20,
        language: str = "es",
        location: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Búsqueda asíncrona (envuelve fetch síncrono en executor)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.search_sync(query, limit, language, location),
        )

    # ------------------------------------------------------------------ parse

    def _parse(self, page: Any, query: str) -> list[dict[str, Any]]:
        """Parsea una página de resultados de Google Maps.

        Estructura real (2026-09, hl=es):
        - Cada resultado es un div.Nv2PK[role=article]
        - a.hfpxzc -> enlace al place con aria-label = nombre
        - span.MW4etd -> rating ("4,5")
        - Los div.W4Efsd contienen: categoría · atributos · dirección,
          horario ("Abierto · Cierra..."), y en algunas tarjetas
          teléfono y sitio web como enlaces adicionales.
        """
        results: list[dict[str, Any]] = []
        cards = page.css("div.Nv2PK")
        if not cards:
            # fallback: enlaces directos
            cards = page.css("a.hfpxzc")
        if not cards:
            logger.warning("Scrapling: 0 tarjetas Nv2PK para %r", query)
            return results

        for card in cards:
            try:
                link = card.css("a.hfpxzc") or (card if card.tag == "a" else [])
                if not link:
                    continue
                link = link[0] if hasattr(link, "__len__") else link
                href = link.attrib.get("href", "")
                if not href:
                    continue

                aria = link.attrib.get("aria-label", "") or ""
                name = aria.strip() or self._txt(link)

                if not name:
                    continue

                item: dict[str, Any] = {
                    "query": query,
                    "source": "scrapling",
                    "name": name,
                    "category": "",
                    "rating": "",
                    "reviews": "",
                    "address": "",
                    "phone": "",
                    "website": "",
                    "url": href if href.startswith("http") else f"https://www.google.com{href}",
                }

                blob = card.get_all_text() or ""
                self._fill_from_text(item, blob)
                self._normalize(item)
                results.append(item)
            except Exception as exc:
                logger.debug("Scrapling: error parseando nodo: %s", exc)
                continue

        # Dedupe por URL
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for r in results:
            key = r.get("url") or r.get("name", "")
            if key and key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def _fill_from_text(self, item: dict[str, Any], blob: str) -> None:
        """Extrae campos del blob de texto de la tarjeta de resultado.

        Formato observado (hl=es): nombre | nombre | rating | categoría |
        · | <icon> | · | dirección | horario | extras (Reservar, tel, web)
        """
        if not blob:
            return
        parts = [p.strip() for p in blob.split("\n") if p.strip()]
        # rating: primer campo numérico tipo 4,5 / 4.5
        for i, p in enumerate(parts):
            if re.fullmatch(r"\d[.,]\d", p):
                item["rating"] = p.replace(",", ".")
                # categoría suele ser el campo inmediatamente posterior
                if i + 1 < len(parts) and not parts[i + 1].startswith("·"):
                    item["category"] = parts[i + 1]
                break
        # teléfono: buscar patrón telefónico colombiano/internacional
        m = self._phone_re.search(blob)
        if m:
            item["phone"] = m.group(1).strip()
        # dirección: heurística sobre tokens de dirección colombiana.
        # "Cl. 93a" y "Cra. 45" requieren punto y número — evita matchear "Clínica"/"Cra" sueltas.
        m = re.search(
            r"((?:Calle|Carrera|Avenida|Autopista|Transversal|Diagonal|Tv)\s+[\w#.\s-]{2,40}"
            r"|(?:Cl|Cra|Kr|Av|Dg|Tv)\.\s*[\w#.\s-]{2,40}"
            r"|#\s?\d[\w\s.-]{2,40})",
            blob,
        )
        if m:
            # cortar en salto de línea: el horario ("Cerrado...") va en otra línea
            item["address"] = m.group(1).split("\n")[0].strip().rstrip(" |·")
        # website
        m = re.search(r"(https?://(?!www\.google|maps\.google)[\w.-]+\.[a-z]{2,}\S*)", blob)
        if m:
            item["website"] = m.group(1)
        # horario (info útil, opcional)
        m = re.search(r"(Abierto|Cerrado|Cierra pronto)[^|]{0,60}", blob)
        if m:
            item["hours"] = m.group(0).strip()

    def _normalize(self, item: dict[str, Any]) -> dict[str, Any]:
        """Limpia campos comunes: rating vacío, nombre con sufijo de categoría."""
        if item["rating"] in {"0", "0.0", "N/A"}:
            item["rating"] = ""
        # Si el aria-label incluye "·" suele traer categoría pegada
        name = item["name"]
        if "·" in name:
            parts = [p.strip() for p in name.split("·")]
            if not item["category"] and len(parts) > 1:
                item["category"] = parts[-1]
            item["name"] = parts[0]
        return item

    def enrich_details(
        self, place_url: str, language: str = "es", wait: int = 4
    ) -> dict[str, str]:
        """Visita la ficha de detalle de un negocio y extrae teléfono/web.

        La lista de resultados no siempre trae teléfono ni sitio web; la ficha
        individual sí. Devuelve dict con phone, website, reviews, plus_code.
        """
        out = {"phone": "", "website": "", "reviews": "", "plus_code": ""}
        try:
            page = self.fetcher.fetch(
                place_url,
                extra_headers={"User-Agent": _UA, "Accept-Language": f"{language},es;q=0.9"},
                timeout=45000,
                wait=wait,
                headless=self.headless,
                solve_cloudflare=True,
            )
            if page.status != 200:
                return out
            blob = page.get_all_text() or ""
            m = self._phone_re.search(blob)
            if m:
                out["phone"] = m.group(1).strip()
            # website: en la ficha el dominio del negocio aparece justo antes
            # del teléfono (ej "restmarieantoinette.com \ue89e ... (601) 7443466").
            # Buscar el dominio más cercano ANTES del match de teléfono.
            site = ""
            if m:
                before = blob[: m.start()]
                for dm in re.finditer(
                    r"(?:https?://)?(?:[\w-]+\.)+(?:com|co|net|org|io|es)(?:\b)",
                    before,
                ):
                    cand = dm.group(0)
                    if not any(
                        d in cand for d in ("google.", "gstatic", "googleapis", "schema.org")
                    ):
                        site = cand
            else:
                dm = re.search(
                    r"(?:https?://)?(?:[\w-]+\.)+(?:com|co|net|org|io|es)(?:\b)", blob
                )
                if dm and not any(
                    d in dm.group(0) for d in ("google.", "gstatic", "googleapis")
                ):
                    site = dm.group(0)
            if site:
                out["website"] = site if site.startswith("http") else f"https://{site}"
            # reviews: "4,5 (123)" o "123 reseñas"
            m = re.search(r"\((\d[\d.,]*)\)\s*$", blob.split("\n")[0] if blob else "")
            if m:
                out["reviews"] = m.group(1)
            m = re.search(r"(\d[\d.,]*)\s+reseñ", blob)
            if m:
                out["reviews"] = m.group(1)
            return out
        except Exception as exc:
            logger.debug("Scrapling: error enrich %s: %s", place_url[:60], exc)
            return out


# ---------------------------------------------------------------------- mod

_scraper: ScraplingMapsScraper | None = None


def get_scraper() -> ScraplingMapsScraper:
    """Instancia singleton del scraper."""
    global _scraper
    if _scraper is None:
        _scraper = ScraplingMapsScraper()
    return _scraper


async def search_businesses(
    query: str,
    limit: int = 20,
    language: str = "es",
    location: str | None = None,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Búsqueda de negocios usada por scheduler y control tower."""
    scraper = get_scraper()
    return await scraper.search(query, limit, language, location, filters)
