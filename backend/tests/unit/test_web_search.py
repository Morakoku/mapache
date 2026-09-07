"""Los dos buscadores soportados, detrás de la misma interfaz.

Lo que se prueba es la traducción: cada API pagina, autentica y nombra sus
campos distinto, y el resto del CRM no debe enterarse. La llamada HTTP va
simulada — un test que gasta cuota de una API no es un test.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.enums import SerpProvider
from app.core.exceptions import ConfigurationError, ExternalServiceError
from app.scrapers.web_search import BraveClient, GoogleCseClient, build_web_search


def _captura(monkeypatch: pytest.MonkeyPatch, cuerpo: dict[str, Any]) -> list[dict[str, Any]]:
    """Guarda cómo se llamó a la API y responde lo que se le diga."""
    llamadas: list[dict[str, Any]] = []

    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        llamadas.append({"url": url, "params": kw.get("params"), "headers": kw.get("headers")})
        return httpx.Response(200, json=cuerpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return llamadas


# ------------------------------------------------------------------ Google


@pytest.mark.asyncio
async def test_google_traduce_la_pagina_a_su_indice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google cuenta resultados, no páginas: la página 2 empieza en el 11."""
    llamadas = _captura(monkeypatch, {"items": []})

    async with httpx.AsyncClient() as client:
        await GoogleCseClient(api_key="k", engine_id="m").search(client, "pan", page=1)

    assert llamadas[0]["params"]["start"] == 11
    assert llamadas[0]["params"]["cx"] == "m"


@pytest.mark.asyncio
async def test_google_normaliza_el_resultado(monkeypatch: pytest.MonkeyPatch) -> None:
    _captura(
        monkeypatch,
        {
            "items": [
                {
                    "title": "Panadería Ana",
                    "link": "https://www.panaderiaana.com/nosotros",
                    "snippet": "Masa madre desde 1998",
                }
            ]
        },
    )

    async with httpx.AsyncClient() as client:
        resultados = await GoogleCseClient(api_key="k", engine_id="m").search(client, "pan", page=0)

    assert resultados[0].title == "Panadería Ana"
    assert resultados[0].snippet == "Masa madre desde 1998"
    # El dominio se calcula de la URL: cada buscador lo llama de otra forma.
    assert resultados[0].domain == "panaderiaana.com"


# ------------------------------------------------------------------ Brave


@pytest.mark.asyncio
async def test_brave_pagina_por_numero_de_pagina(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = _captura(monkeypatch, {"web": {"results": []}})

    async with httpx.AsyncClient() as client:
        await BraveClient(api_key="k").search(client, "pan", page=2)

    assert llamadas[0]["params"]["offset"] == 2
    assert llamadas[0]["params"]["count"] == 20


@pytest.mark.asyncio
async def test_brave_manda_la_clave_en_la_cabecera(monkeypatch: pytest.MonkeyPatch) -> None:
    """No va en la query: una clave en la URL acaba en los logs de todos."""
    llamadas = _captura(monkeypatch, {"web": {"results": []}})

    async with httpx.AsyncClient() as client:
        await BraveClient(api_key="secreta").search(client, "pan", page=0)

    assert llamadas[0]["headers"]["X-Subscription-Token"] == "secreta"
    assert "secreta" not in str(llamadas[0]["params"])


@pytest.mark.asyncio
async def test_brave_normaliza_su_forma(monkeypatch: pytest.MonkeyPatch) -> None:
    """Brave llama `description` a lo que Google llama `snippet`."""
    _captura(
        monkeypatch,
        {
            "web": {
                "results": [
                    {
                        "title": "Panadería Ana",
                        "url": "https://www.panaderiaana.com/",
                        "description": "Masa madre desde 1998",
                    }
                ]
            }
        },
    )

    async with httpx.AsyncClient() as client:
        resultados = await BraveClient(api_key="k").search(client, "pan", page=0)

    assert resultados[0].snippet == "Masa madre desde 1998"
    assert resultados[0].domain == "panaderiaana.com"


@pytest.mark.asyncio
async def test_brave_busca_en_colombia_y_en_espanol(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = _captura(monkeypatch, {"web": {"results": []}})

    async with httpx.AsyncClient() as client:
        await BraveClient(api_key="k").search(client, "pan", page=0)

    assert llamadas[0]["params"]["country"] == "CO"
    assert llamadas[0]["params"]["search_lang"] == "es"


# ------------------------------------------------------------------ errores


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "esperado"),
    [(401, "no es válida"), (429, "cuota"), (403, "permiso")],
)
async def test_cada_codigo_dice_que_hacer(
    monkeypatch: pytest.MonkeyPatch, status: int, esperado: str
) -> None:
    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(status, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(ExternalServiceError) as exc:
        async with httpx.AsyncClient() as client:
            await BraveClient(api_key="k").search(client, "pan", page=0)

    assert esperado in exc.value.message.lower()


# ------------------------------------------------------------------ elección


def test_brave_no_necesita_motor() -> None:
    """Es la razón práctica para elegirlo: un dato de configuración en vez de dos."""
    buscador = build_web_search(provider=SerpProvider.BRAVE, api_key="k", engine_id=None)

    assert buscador.name == "brave"


def test_google_sin_motor_lo_dice_claro() -> None:
    with pytest.raises(ConfigurationError) as exc:
        build_web_search(provider=SerpProvider.GOOGLE_CSE, api_key="k", engine_id=None)

    assert "cx" in exc.value.message


def test_sin_clave_no_hay_buscador() -> None:
    with pytest.raises(ConfigurationError) as exc:
        build_web_search(provider=SerpProvider.BRAVE, api_key=None, engine_id=None)

    assert "Configuración" in exc.value.message


@pytest.mark.asyncio
async def test_brave_dice_que_la_clave_esta_mal_aunque_conteste_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Respuesta real de Brave con una clave inválida.

    A secas, un 422 se lee como «arregla la consulta» y manda a mirar donde no
    es: lo que falla es la clave.
    """
    cuerpo = {
        "type": "ErrorResponse",
        "error": {
            "status": 422,
            "detail": "The provided subscription token is invalid.",
            "meta": {"component": "authentication"},
            "code": "SUBSCRIPTION_TOKEN_INVALID",
        },
    }

    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(422, json=cuerpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(ExternalServiceError) as exc:
        async with httpx.AsyncClient() as client:
            await BraveClient(api_key="mala").search(client, "pan", page=0)

    assert "clave" in exc.value.message.lower()
    assert exc.value.details["api_code"] == "SUBSCRIPTION_TOKEN_INVALID"
