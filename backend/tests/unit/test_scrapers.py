"""Tests del contrato de proveedores y del motor de cascada de selectores.

El motor se prueba con un doble de `TextSource`, sin levantar Chromium: es lo
que permite verificar el comportamiento de fallback de forma determinista.
"""

from __future__ import annotations

import pytest

from app.core.enums import SourceType
from app.core.exceptions import ConfigurationError
from app.scrapers.base import ProviderHealth, RawPlace, SearchQuery
from app.scrapers.google_maps.extraction import extract
from app.scrapers.google_maps.search_page import build_search_url
from app.scrapers.google_maps.selectors import Selector, SelectorChain, build_selector_chains
from app.scrapers.registry import (
    GOOGLE_MAPS_SCRAPER,
    GOOGLE_PLACES_API,
    build_provider,
    source_type_for,
)


class FakeSource:
    """Doble de `TextSource` con un mapa fijo de selector -> valor."""

    def __init__(self, values: dict[str, str | None], text: str = "") -> None:
        self._values = values
        self._text = text
        self.queries: list[str] = []

    async def query_text(self, css: str, attribute: str | None) -> str | None:
        key = f"{css}@{attribute}" if attribute else css
        self.queries.append(key)
        return self._values.get(key)

    async def full_text(self) -> str:
        return self._text


# ------------------------------------------------------------------ cascada


async def test_extract_uses_first_selector_when_available() -> None:
    chain = SelectorChain("phone", [Selector("css", "a"), Selector("css", "b")])
    source = FakeSource({"a": "  604 444 1234  "})

    result = await extract(chain, source)

    assert result == "604 444 1234", "debe recortar espacios"
    assert chain.hits == [1, 0]
    assert chain.fallback_ratio == 0.0


async def test_extract_falls_back_and_records_the_level() -> None:
    """El nivel 1 falla, responde el 2: el contador lo delata."""
    chain = SelectorChain("phone", [Selector("css", "a"), Selector("css", "b")])
    source = FakeSource({"a": None, "b": "604 444 1234"})

    result = await extract(chain, source)

    assert result == "604 444 1234"
    assert chain.hits == [0, 1]
    assert chain.fallback_ratio == 1.0, "no usó el selector semántico"


async def test_extract_uses_regex_as_last_resort() -> None:
    chain = SelectorChain(
        "phone",
        [Selector("css", "a"), Selector("regex", r"(\d{3}[\s-]?\d{4})")],
    )
    source = FakeSource({}, text="Llámanos al 444 1234 hoy")

    assert await extract(chain, source) == "444 1234"
    assert chain.hits == [0, 1]


async def test_extract_returns_none_when_everything_fails() -> None:
    chain = SelectorChain("website", [Selector("css", "a"), Selector("css", "b")])

    assert await extract(chain, FakeSource({})) is None
    assert chain.total_hits == 0


async def test_extract_ignores_empty_strings() -> None:
    """Un nodo presente pero vacío no es un valor: hay que seguir bajando."""
    chain = SelectorChain("name", [Selector("css", "a"), Selector("css", "b")])
    source = FakeSource({"a": "   ", "b": "El Sabor"})

    assert await extract(chain, source) == "El Sabor"
    assert chain.hits == [0, 1]


async def test_extract_survives_a_selector_that_raises() -> None:
    class ExplodingSource(FakeSource):
        async def query_text(self, css: str, attribute: str | None) -> str | None:
            if css == "a":
                raise RuntimeError("selector inválido")
            return "valor"

    chain = SelectorChain("name", [Selector("css", "a"), Selector("css", "b")])

    assert await extract(chain, ExplodingSource({})) == "valor"


async def test_extract_reads_attribute_when_requested() -> None:
    chain = SelectorChain("phone", [Selector("css", "button", attribute="data-item-id")])
    source = FakeSource({"button@data-item-id": "phone:tel:+576044441234"})

    assert await extract(chain, source) == "phone:tel:+576044441234"


def test_selector_chains_cover_every_module_3_field() -> None:
    chains = build_selector_chains()
    esperados = {"name", "category", "address", "phone", "website", "rating", "reviews", "hours"}

    assert esperados.issubset(chains.keys())
    for name, chain in chains.items():
        assert len(chain.selectors) >= 2, f"'{name}' necesita al menos un fallback"


def test_selector_chains_are_independent_per_call() -> None:
    """Los contadores de un run no deben contaminar al siguiente."""
    a = build_selector_chains()
    a["name"].record_hit(0)

    assert build_selector_chains()["name"].total_hits == 0


# ------------------------------------------------------------------ SearchQuery


def test_text_query_prefers_zone_over_city() -> None:
    query = SearchQuery(
        business_type="Restaurantes",
        keywords=["italiano"],
        city="Medellín",
        zone="El Poblado",
    )

    assert query.text_query == "Restaurantes italiano en El Poblado"


def test_text_query_without_zone() -> None:
    query = SearchQuery(business_type="Hoteles", city="Medellín")
    assert query.text_query == "Hoteles en Medellín"


def test_search_url_forces_spanish_and_uses_coordinates() -> None:
    """`hl=es` no es cosmético: los selectores dependen de aria-labels en
    español, y sin forzarlo la extracción varía según la IP de salida."""
    query = SearchQuery(
        business_type="Restaurantes",
        city="Medellín",
        latitude=6.2088,
        longitude=-75.5906,
        radius_km=10,
    )

    url = build_search_url(query)

    assert "hl=es" in url
    assert "@6.2088,-75.5906,13z" in url
    assert "Restaurantes" in url


def test_filters_are_applied_client_side() -> None:
    query = SearchQuery(business_type="x", city="y", min_rating=4.0, max_reviews=500)

    bueno = RawPlace(name="A", source=SourceType.GOOGLE_MAPS, rating=4.5, reviews_count=120)
    mal_rating = RawPlace(name="B", source=SourceType.GOOGLE_MAPS, rating=3.2, reviews_count=10)
    cadena = RawPlace(name="C", source=SourceType.GOOGLE_MAPS, rating=4.8, reviews_count=9000)
    sin_rating = RawPlace(name="D", source=SourceType.GOOGLE_MAPS)

    assert query.matches_filters(bueno) is True
    assert query.matches_filters(mal_rating) is False
    assert query.matches_filters(cadena) is False, "max_reviews descarta cadenas grandes"
    assert query.matches_filters(sin_rating) is False, "sin rating no supera min_rating"


# ------------------------------------------------------------------ RawPlace


def test_raw_place_needs_more_than_a_name() -> None:
    """Un nombre suelto no sirve para prospectar: ni ubicar ni contactar."""
    assert RawPlace(name="El Sabor", source=SourceType.GOOGLE_MAPS).is_usable is False
    assert (
        RawPlace(name="El Sabor", source=SourceType.GOOGLE_MAPS, address="Cra 43").is_usable is True
    )
    assert RawPlace(name="El Sabor", source=SourceType.GOOGLE_MAPS, latitude=6.2).is_usable is True


def test_raw_place_rejects_blank_name() -> None:
    assert RawPlace(name="   ", source=SourceType.GOOGLE_MAPS, phone="+57").is_usable is False


# ------------------------------------------------------------------ registry


def test_registry_builds_scraper_by_default() -> None:
    provider = build_provider(GOOGLE_MAPS_SCRAPER)

    assert provider.name == GOOGLE_MAPS_SCRAPER
    assert provider.source_type is SourceType.GOOGLE_MAPS


def test_registry_builds_places_api_with_key() -> None:
    provider = build_provider(GOOGLE_PLACES_API, google_places_key="AIza-test")

    assert provider.source_type is SourceType.GOOGLE_PLACES_API


def test_registry_explains_missing_places_key() -> None:
    """El mensaje debe ser accionable: es un problema de configuración del
    usuario, no un error interno."""
    with pytest.raises(ConfigurationError) as exc:
        build_provider(GOOGLE_PLACES_API)

    assert exc.value.code == "MISSING_PLACES_API_KEY"
    assert "Configuración" in exc.value.message


def test_registry_rejects_unknown_provider() -> None:
    with pytest.raises(ConfigurationError) as exc:
        build_provider("scraper_inventado")

    assert exc.value.code == "UNKNOWN_DISCOVERY_PROVIDER"
    assert GOOGLE_MAPS_SCRAPER in exc.value.message, "debe listar las opciones válidas"


def test_source_type_for() -> None:
    assert source_type_for(GOOGLE_MAPS_SCRAPER) is SourceType.GOOGLE_MAPS
    assert source_type_for(GOOGLE_PLACES_API) is SourceType.GOOGLE_PLACES_API


# ------------------------------------------------------------------ health


def test_provider_health_lists_degraded_fields() -> None:
    health = ProviderHealth(
        provider="google_maps_scraper",
        healthy=False,
        checked_fields={"name": True, "address": False, "phone": False},
    )

    assert sorted(health.degraded_fields) == ["address", "phone"]
