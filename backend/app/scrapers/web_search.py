"""Buscar en la web pública, con el buscador que esté configurado.

El CRM nunca rastrea un buscador ni una red social: le pregunta a un índice
que ya existe, por su API oficial. Aquí viven los dos que soporta —Google
Custom Search y Brave— detrás de una interfaz única, para que quien busca
(perfiles sociales, información de una empresa) no sepa cuál está puesto.

Las diferencias que sí importan y por eso están modeladas:

- **Paginación**: Google cuenta resultados (`start` = 1, 11, 21…) y Brave
  cuenta páginas (`offset` = 0, 1, 2…). Fuera se pide «página N» y cada uno
  traduce.
- **Ritmo**: el plan gratuito de Brave admite una consulta por segundo. Sin
  esperar entre páginas, la segunda vuelve 429 y la búsqueda se queda a medias.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.enums import SerpProvider
from app.core.exceptions import ConfigurationError, ExternalServiceError
from app.core.logging import get_logger
from app.utils.url import extract_domain

logger = get_logger(__name__)

_GOOGLE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


@dataclass(frozen=True, slots=True)
class WebResult:
    """Un resultado, ya normalizado.

    `domain` se calcula de la URL en vez de leerlo de la respuesta: cada
    buscador lo llama de una forma y el dato es el mismo.
    """

    title: str
    url: str
    snippet: str | None
    domain: str | None


class WebSearchClient(Protocol):
    """Lo que necesita saber quien busca, sea cual sea el buscador."""

    name: str
    per_page: int
    max_pages: int
    pause_between_pages: float

    async def search(self, client: httpx.AsyncClient, terms: str, *, page: int) -> list[WebResult]:
        ...


# Lo que dice Brave cuando la clave está mal. Llega como 422, que a secas se
# leería como «consulta inválida» y mandaría a corregir la búsqueda en vez de
# la clave.
_CODIGOS_DE_CLAVE = frozenset({"SUBSCRIPTION_TOKEN_INVALID", "INVALID_API_KEY"})
_CODIGOS_DE_CUOTA = frozenset({"RATE_LIMITED", "QUOTA_EXCEEDED", "PLAN_LIMIT_REACHED"})


def _error(status: int, buscador: str, cuerpo: dict | None = None) -> ExternalServiceError:
    """Traduce la respuesta del buscador a algo sobre lo que se pueda actuar.

    «0 resultados» haría pensar que no hay negocios cuando lo que pasa es que
    la clave está mal o se acabó la cuota. Cuando el buscador manda un código
    propio se usa ese, porque es más preciso que el HTTP.
    """
    detalle_api = cuerpo.get("error") if isinstance(cuerpo, dict) else None
    codigo = str(detalle_api.get("code") or "") if isinstance(detalle_api, dict) else ""

    if codigo in _CODIGOS_DE_CLAVE:
        detalle = "La clave del buscador no es válida."
    elif codigo in _CODIGOS_DE_CUOTA:
        detalle = "Se agotó la cuota del buscador."
    else:
        detalle = {
            400: "La clave o el identificador del motor no son válidos.",
            401: "La clave del buscador no es válida.",
            403: "La clave no tiene permiso para usar este buscador.",
            422: "El buscador rechazó la consulta.",
            429: "Se agotó la cuota del buscador.",
        }.get(status, "El buscador devolvió un error.")

    return ExternalServiceError(
        f"{detalle} (HTTP {status})",
        code="SERP_ERROR",
        details={"status": status, "provider": buscador, "api_code": codigo or None},
    )


async def _get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str | int],
    headers: dict[str, str] | None,
    buscador: str,
) -> dict:
    try:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            cuerpo_error = exc.response.json()
        except ValueError:
            cuerpo_error = None
        raise _error(
            exc.response.status_code,
            buscador,
            cuerpo_error if isinstance(cuerpo_error, dict) else None,
        ) from exc
    except httpx.HTTPError as exc:
        raise ExternalServiceError(
            "No se pudo contactar con el buscador.", code="SERP_UNREACHABLE"
        ) from exc

    data = response.json()
    return data if isinstance(data, dict) else {}


class GoogleCseClient:
    """Google Programmable Search. 100 consultas gratis al día."""

    name = "google_cse"
    per_page = 10
    max_pages = 10  # `start` no puede pasar de 91.
    pause_between_pages = 0.0

    def __init__(self, *, api_key: str, engine_id: str) -> None:
        self._api_key = api_key
        self._engine_id = engine_id

    async def search(self, client: httpx.AsyncClient, terms: str, *, page: int) -> list[WebResult]:
        cuerpo = await _get(
            client,
            _GOOGLE_ENDPOINT,
            params={
                "key": self._api_key,
                "cx": self._engine_id,
                "q": terms,
                "start": page * self.per_page + 1,
                "num": self.per_page,
            },
            headers=None,
            buscador=self.name,
        )

        resultados = []
        for item in cuerpo.get("items") or []:
            url = str(item.get("link") or "")
            if not url:
                continue
            resultados.append(
                WebResult(
                    title=str(item.get("title") or "").strip(),
                    url=url,
                    snippet=str(item.get("snippet") or "").strip() or None,
                    domain=extract_domain(url),
                )
            )
        return resultados


class BraveClient:
    """Brave Search API. 2.000 consultas gratis al mes, sin motor que crear."""

    name = "brave"
    per_page = 20
    max_pages = 10  # `offset` va de 0 a 9.
    # El plan gratuito admite una consulta por segundo.
    pause_between_pages = 1.1

    def __init__(self, *, api_key: str, country: str = "CO", lang: str = "es") -> None:
        self._api_key = api_key
        self._country = country
        self._lang = lang

    async def search(self, client: httpx.AsyncClient, terms: str, *, page: int) -> list[WebResult]:
        cuerpo = await _get(
            client,
            _BRAVE_ENDPOINT,
            params={
                "q": terms,
                "count": self.per_page,
                "offset": page,
                "country": self._country,
                "search_lang": self._lang,
            },
            headers={
                "X-Subscription-Token": self._api_key,
                "Accept": "application/json",
            },
            buscador=self.name,
        )

        web = cuerpo.get("web") or {}
        resultados = []
        for item in web.get("results") or []:
            url = str(item.get("url") or "")
            if not url:
                continue
            resultados.append(
                WebResult(
                    title=str(item.get("title") or "").strip(),
                    url=url,
                    snippet=str(item.get("description") or "").strip() or None,
                    domain=extract_domain(url),
                )
            )
        return resultados


def build_web_search(
    *,
    provider: SerpProvider,
    api_key: str | None,
    engine_id: str | None,
) -> WebSearchClient:
    """Cliente del buscador configurado, o un error que dice qué falta."""
    if not api_key:
        raise ConfigurationError(
            "Buscar en la web necesita la clave de un buscador. Se configura en "
            "Configuración.",
            code="MISSING_SERP_CREDENTIALS",
        )

    if provider == SerpProvider.BRAVE:
        return BraveClient(api_key=api_key)

    if not engine_id:
        raise ConfigurationError(
            "Google Custom Search necesita además el identificador del motor (cx). "
            "Con Brave no hace falta.",
            code="MISSING_SERP_ENGINE_ID",
        )
    return GoogleCseClient(api_key=api_key, engine_id=engine_id)


async def verify_credentials(buscador: WebSearchClient, *, timeout: float = 20.0) -> None:
    """Una consulta de prueba, para fallar al guardar y no tres días después."""
    async with httpx.AsyncClient(timeout=timeout) as http:
        await buscador.search(http, "panadería Medellín", page=0)


async def pause(seconds: float) -> None:
    """Espera entre páginas. Existe para poder saltársela en los tests."""
    if seconds > 0:
        await asyncio.sleep(seconds)
