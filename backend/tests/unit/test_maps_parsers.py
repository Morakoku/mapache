"""Tests del parseo del DOM de Google Maps.

Son funciones puras, así que se prueban con strings literales tomados de la
forma real de las URLs y atributos. Cuando Google cambie un formato, esto
falla aquí y no en mitad de un scraping de 100 fichas.
"""

from __future__ import annotations

import pytest

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
    zoom_for_radius,
)

# URL real de ficha (acortada), con FTID y coordenadas del negocio.
FICHA_URL = (
    "https://www.google.com/maps/place/Restaurante+El+Sabor/"
    "@6.2088,-75.5906,17z/data=!3m1!4b1!4m6!3m5!"
    "1s0x8e4428dc1d1b2a7f:0x9f1b3c4d5e6f7a8b!8m2!3d6.2091!4d-75.5688!16s%2Fg%2F11abc"
)


# ------------------------------------------------------------------ FTID


def test_parse_ftid_from_place_url() -> None:
    assert parse_ftid(FICHA_URL) == "0x8e4428dc1d1b2a7f:0x9f1b3c4d5e6f7a8b"


def test_parse_ftid_is_lowercase() -> None:
    url = "https://maps.google.com/?cid=x&data=!1s0x8E44AB:0x9F1B"
    assert parse_ftid(url) == "0x8e44ab:0x9f1b"


@pytest.mark.parametrize("url", [None, "", "https://www.google.com/maps", "no-es-url"])
def test_parse_ftid_returns_none_without_match(url: str | None) -> None:
    assert parse_ftid(url) is None


# ------------------------------------------------------------------ coordenadas


def test_coordinates_prefer_business_over_viewport() -> None:
    """`/@6.2088,-75.5906` es el centro del mapa; `!3d/!4d` es el negocio.

    Confundirlos hace que todas las empresas de una búsqueda caigan en el
    mismo punto del mapa.
    """
    lat, lng = parse_coordinates(FICHA_URL)

    assert (lat, lng) == (6.2091, -75.5688)
    assert lat != 6.2088, "no debe usar el centro del viewport"


def test_coordinates_fall_back_to_viewport() -> None:
    lat, lng = parse_coordinates("https://www.google.com/maps/search/x/@6.25,-75.57,13z")
    assert (lat, lng) == (6.25, -75.57)


def test_coordinates_none_when_absent() -> None:
    assert parse_coordinates("https://www.google.com/maps") == (None, None)


# ------------------------------------------------------------------ rating y reseñas


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("4,5", 4.5),
        ("4.5", 4.5),
        ("4,5 estrellas", 4.5),
        ("Calificación: 3,8 de 5", 3.8),
        ("5,0", 5.0),
    ],
)
def test_parse_rating(entrada: str, esperado: float) -> None:
    assert parse_rating(entrada) == esperado


@pytest.mark.parametrize("entrada", [None, "", "sin calificación", "9,9"])
def test_parse_rating_rejects_invalid(entrada: str | None) -> None:
    assert parse_rating(entrada) is None


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("1.234 reseñas", 1234),  # punto como separador de miles en es-CO
        ("(1.234)", 1234),
        ("87 reseñas", 87),
        ("12,345 reviews", 12345),
    ],
)
def test_parse_reviews_count(entrada: str, esperado: int) -> None:
    assert parse_reviews_count(entrada) == esperado


# ------------------------------------------------------------------ teléfono y dirección


def test_parse_phone_from_data_item_id() -> None:
    """El propio atributo lleva el número: es la fuente más fiable."""
    assert parse_phone("phone:tel:+576044441234") == "+576044441234"


def test_parse_phone_strips_aria_prefix() -> None:
    assert parse_phone("Teléfono: 604 444 1234") == "604 444 1234"


def test_parse_address_strips_prefix() -> None:
    assert parse_address("Dirección: Cra. 43A #7-50") == "Cra. 43A #7-50"


def test_split_address_colombian_format() -> None:
    result = split_address("Cra. 43A #7-50, El Poblado, Medellín, Antioquia")

    assert result["city"] == "Medellín"
    assert result["region"] == "Antioquia"


def test_split_address_extracts_postal_code() -> None:
    result = split_address("Calle 10 #43-15, Medellín, Antioquia 050021")

    assert result["postal_code"] == "050021"
    assert result["region"] == "Antioquia", "el código postal no debe quedar en la región"


def test_split_address_handles_empty() -> None:
    assert split_address(None) == {"city": None, "region": None, "postal_code": None}


# ------------------------------------------------------------------ horarios


def test_parse_opening_hours_basic() -> None:
    result = parse_opening_hours(
        [("lunes", "9:00 a 18:00"), ("sábado", "10:00 a 14:00"), ("domingo", "Cerrado")]
    )

    assert result == {
        "mon": [["09:00", "18:00"]],
        "sat": [["10:00", "14:00"]],
        "sun": [],
    }


def test_parse_opening_hours_distinguishes_closed_from_unknown() -> None:
    """Lista vacía = cerrado. Clave ausente = no tenemos el dato."""
    result = parse_opening_hours([("domingo", "Cerrado")])

    assert result is not None
    assert result["sun"] == []
    assert "mon" not in result


def test_parse_opening_hours_am_pm() -> None:
    result = parse_opening_hours([("lunes", "8:30 a. m. a 6:00 p. m.")])

    assert result == {"mon": [["08:30", "18:00"]]}


def test_parse_opening_hours_24h() -> None:
    assert parse_opening_hours([("lunes", "Abierto 24 horas")]) == {"mon": [["00:00", "23:59"]]}


def test_parse_opening_hours_split_shift() -> None:
    """Jornada partida, muy común en restaurantes."""
    result = parse_opening_hours([("martes", "12:00 a 15:00, 19:00 a 23:00")])

    assert result == {"tue": [["12:00", "15:00"], ["19:00", "23:00"]]}


def test_parse_opening_hours_empty_returns_none() -> None:
    assert parse_opening_hours([]) is None


# ------------------------------------------------------------------ varios


def test_canonical_maps_url_drops_query() -> None:
    result = canonical_maps_url("https://www.google.com/maps/place/X/@1,2,17z?hl=es&entry=ttu")
    assert result == "https://www.google.com/maps/place/X/@1,2,17z"


def test_canonical_maps_url_absolutises_relative() -> None:
    assert canonical_maps_url("/maps/place/X").startswith("https://www.google.com/")


def test_challenge_detection() -> None:
    assert is_challenge_page("https://www.google.com/sorry/index?continue=x", "") is True
    assert is_challenge_page("https://x.com", "Our systems have detected unusual traffic") is True
    assert is_challenge_page("https://www.google.com/maps/place/X", "<html>ok</html>") is False


@pytest.mark.parametrize(
    ("radius", "zoom"), [(1, 16), (2, 15), (5, 14), (10, 13), (25, 12), (100, 10)]
)
def test_zoom_for_radius_is_monotonic(radius: float, zoom: int) -> None:
    assert zoom_for_radius(radius) == zoom


# ------------------------------------------------------------------ regresión: duplicados


def test_parse_address_removes_google_duplicated_segments() -> None:
    """Google emite la dirección con segmentos repetidos. Verificado en la
    ficha real de MAL DE OJO ROOFTOP (aria-label capturado literalmente)."""
    crudo = "Dirección: Cl. 8 #43C - 68, El Poblado, Medellín, El Poblado, Medellín, Antioquia "

    assert parse_address(crudo) == "Cl. 8 #43C - 68, El Poblado, Medellín, Antioquia"


def test_parse_address_leaves_clean_addresses_untouched() -> None:
    crudo = "Dirección: Carrera 35 No: 8A-52, Medellín, Antioquia"

    assert parse_address(crudo) == "Carrera 35 No: 8A-52, Medellín, Antioquia"


def test_parse_address_strips_material_icons() -> None:
    """El texto del nodo trae el icono del área de uso privado de Unicode."""
    assert parse_address("\nCl. 8 #43C - 68, Medellín") == "Cl. 8 #43C - 68, Medellín"


def test_split_address_after_dedupe_finds_city_and_region() -> None:
    limpio = parse_address(
        "Dirección: Cl. 8 #43C - 68, El Poblado, Medellín, El Poblado, Medellín, Antioquia"
    )
    result = split_address(limpio)

    assert result["city"] == "Medellín"
    assert result["region"] == "Antioquia"
