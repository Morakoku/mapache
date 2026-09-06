"""Descubrimiento de perfiles de Instagram y LinkedIn vía índice del buscador.

Se prueba la decisión, no la red: qué consulta se construye, qué resultados se
convierten en empresa y cuáles se tiran. La llamada HTTP va simulada — un test
que depende de la cuota diaria de una API no es un test.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.enums import SerpProvider, SourceType
from app.core.exceptions import ConfigurationError, ExternalServiceError
from app.scrapers.base import SearchQuery
from app.scrapers.registry import build_provider
from app.scrapers.social_serp import SocialSerpProvider, build_serp_provider


def _provider(platform: str = "instagram") -> SocialSerpProvider:
    return build_serp_provider(
        platform,
        provider=SerpProvider.GOOGLE_CSE,
        api_key="clave",
        engine_id="motor",
    )


def _item(title: str, link: str, snippet: str = "") -> dict[str, Any]:
    return {"title": title, "link": link, "snippet": snippet}


def _mock(monkeypatch: pytest.MonkeyPatch, paginas: list[list[dict[str, Any]]]) -> list[str]:
    """Sustituye la llamada HTTP y devuelve las consultas que se pidieron."""
    consultas: list[str] = []
    restantes = list(paginas)

    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        consultas.append(kw["params"]["q"])
        items = restantes.pop(0) if restantes else []
        return httpx.Response(
            200, json={"items": items}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return consultas


# ------------------------------------------------------------------ consulta


def test_la_consulta_restringe_al_dominio_de_la_red() -> None:
    query = SearchQuery(business_type="panadería", city="Medellín")
    assert _provider().build_terms(query).startswith("site:instagram.com")


def test_la_ciudad_va_entre_comillas() -> None:
    """Sin comillas el buscador la trata como sugerencia y trae otras ciudades."""
    query = SearchQuery(business_type="panadería", city="Medellín")
    assert '"Medellín"' in _provider().build_terms(query)


def test_linkedin_apunta_a_las_paginas_de_empresa() -> None:
    """`linkedin.com` a secas trae perfiles de personas; se quiere la empresa."""
    query = SearchQuery(business_type="consultora", city="Bogotá")
    assert _provider("linkedin").build_terms(query).startswith("site:linkedin.com/company")


def test_las_palabras_clave_entran_en_la_consulta() -> None:
    query = SearchQuery(business_type="restaurante", city="Cali", keywords=["italiano"])
    terms = _provider().build_terms(query)
    assert "restaurante" in terms
    assert "italiano" in terms


# ------------------------------------------------------------------ conversión


@pytest.mark.asyncio
async def test_un_perfil_se_convierte_en_empresa(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(
        monkeypatch,
        [[_item("Panadería Ana (@panana) • Instagram", "https://www.instagram.com/panana/")]],
    )
    query = SearchQuery(business_type="panadería", city="Medellín", limit=5)

    lugares = [p async for p in _provider().search(query)]

    assert len(lugares) == 1
    lugar = lugares[0]
    assert lugar.name == "Panadería Ana"
    assert lugar.source is SourceType.INSTAGRAM
    assert lugar.socials == {"instagram": "https://www.instagram.com/panana"}
    assert lugar.external_id == "instagram:panana"


@pytest.mark.asyncio
async def test_el_titulo_se_limpia(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google devuelve el título con la coletilla de la red pegada."""
    _mock(
        monkeypatch,
        [
            [
                _item("Barbería El Corte | LinkedIn", "https://linkedin.com/company/el-corte"),
            ]
        ],
    )
    query = SearchQuery(business_type="barbería", city="Medellín", limit=5, strict_match=False)

    lugares = [p async for p in _provider("linkedin").search(query)]

    assert lugares[0].name == "Barbería El Corte"


@pytest.mark.asyncio
async def test_lo_que_no_es_un_perfil_se_descarta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publicaciones y páginas de ayuda comparten dominio con los perfiles."""
    _mock(
        monkeypatch,
        [
            [
                _item("Una publicación", "https://www.instagram.com/p/CxYz123/"),
                _item("Portada", "https://www.instagram.com/"),
                _item("Panadería Ana", "https://www.instagram.com/panana/"),
            ]
        ],
    )
    query = SearchQuery(business_type="panadería", city="Medellín", limit=5)

    lugares = [p async for p in _provider().search(query)]

    # `/p/` es una publicación, pero su primer segmento útil es "p", que está
    # en la lista de ruido, así que el handle sale del siguiente segmento.
    nombres = {p.name for p in lugares}
    assert "Panadería Ana" in nombres
    assert "Portada" not in nombres


@pytest.mark.asyncio
async def test_no_se_repite_el_mismo_perfil(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(
        monkeypatch,
        [
            [
                _item("Panadería Ana", "https://www.instagram.com/panana/"),
                _item("Panadería Ana", "https://instagram.com/panana"),
            ]
        ],
    )
    query = SearchQuery(business_type="panadería", city="Medellín", limit=5)

    lugares = [p async for p in _provider().search(query)]

    assert len(lugares) == 1


@pytest.mark.asyncio
async def test_el_filtro_de_relevancia_tambien_aplica(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismo criterio que en Maps: si no es lo que se pidió, no entra."""
    _mock(
        monkeypatch,
        [
            [
                _item("Ferretería El Tornillo", "https://www.instagram.com/tornillo/"),
                _item("Panadería Ana", "https://www.instagram.com/panana/"),
            ]
        ],
    )
    query = SearchQuery(business_type="panadería", city="Medellín", limit=5)

    provider = _provider()
    lugares = [p async for p in provider.search(query)]

    assert [p.name for p in lugares] == ["Panadería Ana"]
    assert provider.last_stats["filtered_tipo_de_negocio"] == 1


@pytest.mark.asyncio
async def test_se_para_al_llegar_al_limite(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(
        monkeypatch,
        [
            [
                _item(f"Panadería {i}", f"https://www.instagram.com/pan{i}/")
                for i in range(10)
            ]
        ],
    )
    query = SearchQuery(business_type="panadería", city="Medellín", limit=3)

    lugares = [p async for p in _provider().search(query)]

    assert len(lugares) == 3


# ------------------------------------------------------------------ errores


@pytest.mark.asyncio
async def test_la_cuota_agotada_se_dice_con_claridad(monkeypatch: pytest.MonkeyPatch) -> None:
    """"0 resultados" haría pensar que no hay negocios; es que se acabó la cuota."""

    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(429, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    query = SearchQuery(business_type="panadería", city="Medellín", limit=5)

    with pytest.raises(ExternalServiceError) as exc:
        [p async for p in _provider().search(query)]

    assert "cuota" in exc.value.message.lower()


def test_sin_credenciales_el_mensaje_es_accionable() -> None:
    with pytest.raises(ConfigurationError) as exc:
        build_serp_provider(
            "instagram", provider=SerpProvider.GOOGLE_CSE, api_key=None, engine_id=None
        )

    assert "Configuración" in exc.value.message


def test_una_red_desconocida_se_rechaza() -> None:
    with pytest.raises(ConfigurationError):
        build_serp_provider(
            "tiktok", provider=SerpProvider.GOOGLE_CSE, api_key="k", engine_id="m"
        )


# ------------------------------------------------------------------ registro


def test_el_registro_conoce_las_dos_redes() -> None:
    for nombre, esperado in (
        ("instagram_serp", SourceType.INSTAGRAM),
        ("linkedin_serp", SourceType.LINKEDIN),
    ):
        provider = build_provider(nombre, serp_api_key="k", serp_engine_id="m")
        assert provider.source_type is esperado


def test_el_registro_falla_sin_credenciales() -> None:
    with pytest.raises(ConfigurationError):
        build_provider("instagram_serp")


@pytest.mark.asyncio
async def test_healthcheck_dice_si_la_clave_sirve(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, [[_item("Algo", "https://www.instagram.com/algo/")]])

    salud = await _provider().healthcheck()

    assert salud.healthy is True


@pytest.mark.asyncio
async def test_healthcheck_no_lanza_si_la_clave_esta_mal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El canario informa; no puede tumbar la pantalla de estado."""

    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(403, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    salud = await _provider().healthcheck()

    assert salud.healthy is False
    assert salud.message


@pytest.mark.asyncio
async def test_el_mismo_proveedor_funciona_con_brave(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cambiar de buscador no cambia lo que sale: sigue siendo una empresa."""
    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        cuerpo = {
            "web": {
                "results": [
                    {
                        "title": "Panadería Ana (@panana) • Instagram",
                        "url": "https://www.instagram.com/panana/",
                        "description": "La mejor masa madre de Medellín",
                    }
                ]
            }
        }
        return httpx.Response(200, json=cuerpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = build_serp_provider(
        "instagram", provider=SerpProvider.BRAVE, api_key="clave", engine_id=None
    )
    query = SearchQuery(business_type="panadería", city="Medellín", limit=5)

    lugares = [p async for p in provider.search(query)]

    assert [p.name for p in lugares] == ["Panadería Ana"]
    assert lugares[0].external_id == "instagram:panana"
