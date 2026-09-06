"""Crawler del sitio web de cada empresa.

Respeta `robots.txt`, se identifica con un User-Agent honesto y limita el
ritmo a una petición por segundo por dominio. Visita la home y como mucho
tres páginas internas de contacto: el email casi siempre está ahí y rastrear
el sitio entero sería desproporcionado.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.parser import HTMLParser

from app.core.logging import get_logger
from app.enrichment.extractors import ExtractedContact, extract_all
from app.utils.url import extract_domain, normalize_url

logger = get_logger(__name__)

# Solo ASCII: las cabeceras HTTP no admiten otra cosa y httpx lanza
# UnicodeEncodeError al construir el cliente si se cuela una tilde.
USER_AGENT = "CRM-Prospeccion/0.1 (+https://github.com/; contacto en la configuracion del CRM)"

# Rutas típicas donde vive el email en sitios de PYMEs colombianas.
_CONTACT_PATH_RE = re.compile(
    r"/(contacto|contactenos|contact|contact-us|about|about-us|nosotros|"
    r"quienes-somos|equipo|team|sobre-nosotros|atencion)",
    flags=re.IGNORECASE,
)

_MAX_INTERNAL_PAGES = 3
_MAX_HTML_BYTES = 2_000_000


@dataclass(slots=True)
class CrawlResult:
    url: str
    reachable: bool
    contact: ExtractedContact = field(default_factory=ExtractedContact)
    pages_visited: list[str] = field(default_factory=list)
    final_url: str | None = None
    status_code: int | None = None
    uses_https: bool = False
    response_ms: int | None = None
    robots_blocked: bool = False
    error: str | None = None


class DomainRateLimiter:
    """Un acceso por segundo y dominio.

    No es solo cortesía: un sitio de PYME suele estar en hosting compartido y
    una ráfaga puede tumbarlo, lo que además nos deja sin el dato.
    """

    def __init__(self, min_interval_s: float = 1.0) -> None:
        self._min_interval = min_interval_s
        self._last_access: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, domain: str) -> None:
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            last = self._last_access.get(domain)
            now = time.monotonic()
            if last is not None:
                wait = self._min_interval - (now - last)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_access[domain] = time.monotonic()


class WebsiteCrawler:
    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        rate_limiter: DomainRateLimiter | None = None,
        respect_robots: bool = True,
    ) -> None:
        self._timeout = timeout_s
        self._limiter = rate_limiter or DomainRateLimiter()
        self._respect_robots = respect_robots
        self._robots_cache: dict[str, RobotFileParser | None] = {}

    async def crawl(self, website: str) -> CrawlResult:
        url = normalize_url(website)
        if not url:
            return CrawlResult(url=website, reachable=False, error="URL inválida")

        domain = extract_domain(url) or url
        result = CrawlResult(url=url, reachable=False)

        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es-CO,es;q=0.9"},
        ) as client:
            if self._respect_robots and not await self._robots_allow(client, url, domain):
                logger.info("crawl_robots_blocked", domain=domain)
                result.robots_blocked = True
                result.error = "robots.txt no permite el rastreo"
                return result

            home = await self._fetch(client, url, domain)
            if home is None:
                result.error = "El sitio no respondió"
                return result

            html, final_url, status, elapsed_ms = home
            result.reachable = True
            result.final_url = final_url
            result.status_code = status
            result.uses_https = final_url.startswith("https://")
            result.response_ms = elapsed_ms
            result.pages_visited.append(final_url)
            result.contact = extract_all(html, final_url)

            for link in self._contact_links(html, final_url)[:_MAX_INTERNAL_PAGES]:
                page = await self._fetch(client, link, domain)
                if page is None:
                    continue
                page_html, page_url, _status, _ms = page
                result.pages_visited.append(page_url)
                result.contact.merge(extract_all(page_html, page_url))

        logger.info(
            "crawl_finished",
            domain=domain,
            pages=len(result.pages_visited),
            emails=len(result.contact.emails),
            phones=len(result.contact.phones),
            socials=len(result.contact.socials),
        )
        return result

    async def _robots_allow(self, client: httpx.AsyncClient, url: str, domain: str) -> bool:
        if domain in self._robots_cache:
            parser = self._robots_cache[domain]
            return parser.can_fetch(USER_AGENT, url) if parser else True

        robots_url = urljoin(url, "/robots.txt")
        try:
            await self._limiter.acquire(domain)
            response = await client.get(robots_url, timeout=5.0)
        except Exception:  # noqa: BLE001
            # Sin robots.txt accesible se asume permitido, que es el
            # comportamiento estándar.
            self._robots_cache[domain] = None
            return True

        if response.status_code != 200:
            self._robots_cache[domain] = None
            return True

        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        self._robots_cache[domain] = parser
        return parser.can_fetch(USER_AGENT, url)

    async def _fetch(
        self, client: httpx.AsyncClient, url: str, domain: str
    ) -> tuple[str, str, int, int] | None:
        try:
            await self._limiter.acquire(domain)
            started = time.monotonic()
            response = await client.get(url)
            elapsed_ms = int((time.monotonic() - started) * 1000)
        except Exception as exc:  # noqa: BLE001
            logger.debug("crawl_fetch_failed", url=url, error=str(exc))
            return None

        if response.status_code >= 400:
            return None

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            return None

        html = response.text[:_MAX_HTML_BYTES]
        return html, str(response.url), response.status_code, elapsed_ms

    @staticmethod
    def _contact_links(html: str, base_url: str) -> list[str]:
        """Enlaces internos que probablemente lleven a datos de contacto."""
        tree = HTMLParser(html)
        base_domain = extract_domain(base_url)
        found: list[str] = []
        seen: set[str] = set()

        for node in tree.css("a"):
            href = node.attributes.get("href") or ""
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = normalize_url(urljoin(base_url, href))
            if not absolute or absolute in seen:
                continue
            if extract_domain(absolute) != base_domain:
                continue
            if _CONTACT_PATH_RE.search(absolute):
                seen.add(absolute)
                found.append(absolute)

        return found
