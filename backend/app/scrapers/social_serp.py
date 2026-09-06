"""Descubrimiento de perfiles de Instagram y LinkedIn desde el índice de un buscador.

**Este proveedor no toca los servidores de Instagram ni de LinkedIn.** Consulta
el índice público de un buscador a través de su API oficial y de pago.

Por qué así y no rastreando las redes directamente: el `robots.txt` de
Instagram declara `User-agent: * / Disallow: /` con una nota expresa de que la
recolección automatizada está prohibida, y el de LinkedIn es igual de
restrictivo. Ahora bien, **ambas permiten a Googlebot y a Bingbot**, y por eso
sus perfiles de empresa están indexados y son consultables. Preguntarle al
buscador por lo que ya indexó legítimamente da el mismo resultado sin saltarse
la decisión de nadie.

Lo que se obtiene es lo que el buscador publica de cada resultado: el nombre
del negocio, el `@handle` y la descripción del perfil. Los seguidores y las
publicaciones no salen de aquí — para eso hace falta la API oficial de Meta.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from app.core.enums import SerpProvider, SourceType
from app.core.exceptions import ConfigurationError, ExternalServiceError
from app.core.logging import get_logger
from app.scrapers.base import ProviderHealth, RawPlace, SearchQuery
from app.scrapers.web_search import WebResult, WebSearchClient, build_web_search, pause
from app.utils.url import detect_social_platform, normalize_url, social_handle

logger = get_logger(__name__)

# Ningún buscador deja pasar de unos 100-200 resultados por consulta. Pedir
# más es tirar cuota: la página siguiente vuelve vacía igualmente.
_MAX_RESULTS = 200

# El título de un perfil viene como "Nombre del negocio (@handle) • Instagram
# photos and videos" o "Nombre | LinkedIn". Se limpia para quedarse con el
# nombre, que es lo que se guarda como empresa.
_TITLE_NOISE = re.compile(
    # El guion largo va escapado como \u2013 para que el linter no lo lea
    # como un guion normal mal escrito.
    "\\s*[\u2022|\u00b7\\-\u2013]\\s*(instagram|linkedin).*$"
    "|\\s*\\(@[\\w.]+\\).*$"
    "|\\s*on instagram.*$",
    re.IGNORECASE,
)


class SocialSerpProvider:
    """Encuentra perfiles de una red social por palabra clave y ciudad.

    Emite una `RawPlace` por perfil, con la red ya rellenada. Encaja en el
    mismo flujo que el resto: el enriquecimiento y el score no saben de dónde
    salió la empresa.
    """

    def __init__(
        self,
        *,
        search_client: WebSearchClient,
        platform: str,
        site: str,
        source_type: SourceType,
        timeout: float = 20.0,
    ) -> None:
        self.name = f"{platform}_serp"
        self.source_type = source_type
        self._search = search_client
        self._platform = platform
        self._site = site
        self._timeout = timeout
        self._last_stats: dict[str, int] = {}

    @property
    def last_stats(self) -> dict[str, int]:
        return dict(self._last_stats)

    def build_terms(self, query: SearchQuery) -> str:
        """La consulta que se le manda al buscador.

        `site:` restringe al dominio de la red; el resto son las palabras del
        usuario. La ciudad va entre comillas para que el buscador la exija en
        vez de tratarla como sugerencia.
        """
        partes = [f"site:{self._site}", query.business_type, *query.keywords]
        if query.city:
            partes.append(f'"{query.city}"')
        return " ".join(p for p in partes if p and p.strip())

    async def search(self, query: SearchQuery) -> AsyncIterator[RawPlace]:
        terms = self.build_terms(query)
        limite = min(query.limit, _MAX_RESULTS)
        stats = {"requested": limite, "returned": 0, "usable": 0, "filtered": 0}
        vistos: set[str] = set()

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for pagina_n in range(self._search.max_pages):
                if pagina_n:
                    # Brave gratis admite una consulta por segundo; sin esperar,
                    # la segunda página vuelve 429 y la búsqueda queda a medias.
                    await pause(self._search.pause_between_pages)

                pagina = await self._search.search(client, terms, page=pagina_n)
                if not pagina:
                    break

                for item in pagina:
                    stats["returned"] += 1
                    place = self._to_place(item, query)
                    if place is None:
                        continue

                    # Se deduplica por `@handle`, no por URL: el buscador
                    # devuelve el mismo perfil como `instagram.com/x` y como
                    # `www.instagram.com/x`, y son el mismo negocio.
                    handle = (place.external_id or "").lower()
                    if handle in vistos:
                        continue
                    vistos.add(handle)

                    if (motivo := query.rejection_reason(place)) is not None:
                        stats["filtered"] += 1
                        stats[f"filtered_{motivo}"] = stats.get(f"filtered_{motivo}", 0) + 1
                        continue

                    stats["usable"] += 1
                    yield place
                    if stats["usable"] >= limite:
                        break

                if stats["usable"] >= limite or len(pagina) < self._search.per_page:
                    break

        self._last_stats = stats
        logger.info("serp_search_finished", provider=self.name, buscador=self._search.name, **stats)

    def _to_place(self, item: WebResult, query: SearchQuery) -> RawPlace | None:
        """Convierte un resultado del buscador en una empresa.

        Descarta lo que no sea un perfil: publicaciones sueltas, etiquetas y
        páginas de ayuda comparten dominio con los perfiles.
        """
        link = normalize_url(item.url)
        if not link or detect_social_platform(link) != self._platform:
            return None

        handle = social_handle(link)
        if not handle:
            return None

        nombre = _clean_title(item.title) or handle
        snippet = item.snippet

        return RawPlace(
            name=nombre,
            source=self.source_type,
            external_id=f"{self._platform}:{handle}",
            description=snippet,
            # La ciudad la pone la búsqueda, no el resultado: el buscador no
            # devuelve dirección. Se marca así para no fingir un dato que no
            # tenemos, y el filtro de ciudad no puede exigirla.
            city=query.city or None,
            socials={self._platform: link},
            raw={"title": item.title, "link": link, "snippet": snippet},
        )

    async def healthcheck(self) -> ProviderHealth:
        """¿Responde el buscador con la clave configurada?"""
        query = SearchQuery(business_type="restaurante", city="Medellín", limit=1)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                items = await self._search.search(client, self.build_terms(query), page=0)
        except ExternalServiceError as exc:
            return ProviderHealth(provider=self.name, healthy=False, message=exc.message)

        return ProviderHealth(
            provider=self.name,
            healthy=bool(items),
            checked_fields={"items": bool(items)},
            message=None if items else "El buscador no devolvió resultados.",
        )


def _clean_title(title: str) -> str:
    """`Panadería Ana (@panana) • Instagram photos` -> `Panadería Ana`."""
    return _TITLE_NOISE.sub("", title).strip(" -|·•").strip()


def build_serp_provider(
    platform: str,
    *,
    provider: SerpProvider,
    api_key: str | None,
    engine_id: str | None,
) -> SocialSerpProvider:
    search_client = build_web_search(provider=provider, api_key=api_key, engine_id=engine_id)

    sitios = {
        "instagram": ("instagram.com", SourceType.INSTAGRAM),
        "linkedin": ("linkedin.com/company", SourceType.LINKEDIN),
    }
    if platform not in sitios:
        raise ConfigurationError(
            f"Red desconocida: '{platform}'. Opciones: {', '.join(sitios)}.",
            code="UNKNOWN_SOCIAL_PLATFORM",
        )

    site, source = sitios[platform]
    return SocialSerpProvider(
        search_client=search_client,
        platform=platform,
        site=site,
        source_type=source,
    )
