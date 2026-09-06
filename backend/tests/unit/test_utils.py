"""Tests de los utils de normalización. Son la base del dedupe: si fallan,
la base se llena de empresas duplicadas."""

from __future__ import annotations

import pytest

from app.utils.dedupe import build_dedupe_key, name_similarity
from app.utils.phone import format_national, is_mobile, to_e164, to_whatsapp_id
from app.utils.text import normalize_city, normalize_company_name, slugify, snippet
from app.utils.url import (
    detect_social_platform,
    extract_domain,
    is_directory_url,
    is_own_website,
    is_social_url,
    normalize_url,
    same_domain,
)

# ------------------------------------------------------------------ texto


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Restaurante El Sabor S.A.S.", "el sabor"),
        ("EL SABOR SAS", "el sabor"),
        ("El Sabor", "el sabor"),
        ("Panadería La Espiga Ltda", "la espiga"),
        ("Clínica Dental Sonrisa", "dental sonrisa"),
        ("Hotel  Poblado   Plaza", "poblado plaza"),
    ],
)
def test_normalize_company_name_collapses_variants(entrada: str, esperado: str) -> None:
    assert normalize_company_name(entrada) == esperado


def test_normalize_company_name_keeps_generic_only_names() -> None:
    """Si el negocio se llama solo "Panadería", quitar el prefijo lo dejaría
    vacío y colisionaría con cualquier otra panadería."""
    assert normalize_company_name("Panadería") == "panaderia"


def test_normalize_company_name_never_returns_empty() -> None:
    assert normalize_company_name("S.A.S.") != ""


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Medellín", "medellin"),
        ("Medellín, Antioquia", "medellin"),
        ("BOGOTÁ D.C.", "bogota d.c."),
        (None, ""),
    ],
)
def test_normalize_city(entrada: str | None, esperado: str) -> None:
    assert normalize_city(entrada) == esperado


def test_slugify() -> None:
    assert slugify("Restaurante El Sabor S.A.S.") == "restaurante-el-sabor-s-a-s"


def test_snippet_does_not_cut_words() -> None:
    texto = "Somos una empresa dedicada a la gastronomía italiana en el corazón de El Poblado"
    result = snippet(texto, length=30)

    assert result is not None
    assert len(result) <= 31  # +1 por el carácter de elipsis
    assert not result.replace("…", "").endswith(" ")
    assert "gastro" in result or "empresa" in result


# ------------------------------------------------------------------ teléfono


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("300 123 4567", "+573001234567"),
        ("+57 300 123 4567", "+573001234567"),
        ("(604) 444 1234", "+576044441234"),
        ("3001234567", "+573001234567"),
    ],
)
def test_to_e164_colombia(entrada: str, esperado: str) -> None:
    assert to_e164(entrada) == esperado


@pytest.mark.parametrize("entrada", ["", "   ", "no es un teléfono", "123", None])
def test_to_e164_rejects_invalid(entrada: str | None) -> None:
    assert to_e164(entrada) is None


def test_mobile_detection() -> None:
    assert is_mobile("+573001234567") is True
    assert is_mobile("+576044441234") is False, "un fijo de Medellín no es móvil"


def test_whatsapp_id_strips_plus() -> None:
    assert to_whatsapp_id("+573001234567") == "573001234567"


def test_format_national_is_readable() -> None:
    assert format_national("+573001234567") == "300 1234567"


# ------------------------------------------------------------------ url


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("empresa.com", "https://empresa.com/"),
        ("https://www.Empresa.com/inicio/", "https://www.empresa.com/inicio"),
        ("https://empresa.com/x?utm_source=maps&id=7", "https://empresa.com/x?id=7"),
        ("https://empresa.com/x#seccion", "https://empresa.com/x"),
    ],
)
def test_normalize_url(entrada: str, esperado: str) -> None:
    assert normalize_url(entrada) == esperado


@pytest.mark.parametrize("entrada", [None, "", "  ", "mailto:a@b.com", "ftp://x.com"])
def test_normalize_url_rejects_non_http(entrada: str | None) -> None:
    assert normalize_url(entrada) is None


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("https://www.empresa.com/x", "empresa.com"),
        ("https://sub.empresa.com.co", "empresa.com.co"),
        ("EMPRESA.COM", "empresa.com"),
    ],
)
def test_extract_domain(entrada: str, esperado: str) -> None:
    assert extract_domain(entrada) == esperado


def test_social_urls_are_not_own_website() -> None:
    """Un negocio cuyo "sitio web" es su Instagram no tiene web propia — y eso
    es justo la señal de oportunidad que buscamos."""
    assert is_social_url("https://www.instagram.com/mirestaurante") is True
    assert is_social_url("https://wa.me/573001234567") is True
    assert is_social_url("https://mirestaurante.com") is False


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("https://instagram.com/x", "instagram"),
        ("https://www.facebook.com/x", "facebook"),
        ("https://wa.me/573001234567", "whatsapp"),
        ("https://empresa.com", None),
    ],
)
def test_detect_social_platform(entrada: str, esperado: str | None) -> None:
    assert detect_social_platform(entrada) == esperado


def test_same_domain_ignores_subdomain_and_scheme() -> None:
    assert same_domain("http://www.empresa.com/a", "https://empresa.com/b") is True
    assert same_domain("https://empresa.com", "https://otra.com") is False


# ------------------------------------------------------------------ dedupe


def test_dedupe_key_is_stable_across_formatting() -> None:
    a = build_dedupe_key(
        name="Restaurante El Sabor S.A.S.",
        website="https://www.elsabor.com/inicio",
        phone_e164="+573001234567",
        city="Medellín, Antioquia",
    )
    b = build_dedupe_key(
        name="EL SABOR SAS",
        website="elsabor.com",
        phone_e164="+573001234567",
        city="Medellin",
    )
    assert a == b, "las mismas empresas escritas distinto deben colapsar"


def test_dedupe_key_separates_branches_of_a_chain() -> None:
    """Dos sedes con el mismo nombre y ciudad, distinto teléfono: son
    empresas distintas y deben poder coexistir."""
    sede_1 = build_dedupe_key(name="Juan Valdez", phone_e164="+576041111111", city="Medellín")
    sede_2 = build_dedupe_key(name="Juan Valdez", phone_e164="+576042222222", city="Medellín")

    assert sede_1 != sede_2


def test_dedupe_key_separates_different_companies() -> None:
    a = build_dedupe_key(name="El Sabor", city="Medellín")
    b = build_dedupe_key(name="La Fonda", city="Medellín")

    assert a != b


def test_name_similarity() -> None:
    assert name_similarity("Restaurante El Sabor SAS", "El Sabor") == 1.0
    assert name_similarity("El Sabor Italiano", "El Sabor") == pytest.approx(2 / 3)
    assert name_similarity("El Sabor", "La Fonda Paisa") == 0.0


def test_una_ficha_de_directorio_no_es_web_propia() -> None:
    """El caso real: `aiyellow.com/elpandelagloria` no es la web de la panadería.

    Contarla como web propia esconde la señal «sin web» —la que justifica la
    llamada— y al rastrearla se importan las redes del directorio.
    """
    ficha = "https://www.aiyellow.com/elpandelagloria"

    assert is_directory_url(ficha) is True
    assert is_own_website(ficha) is False
    # No es una red social: si lo fuera, se guardaría como perfil de la empresa.
    assert is_social_url(ficha) is False

    assert is_own_website("https://panaderialaespiga.com") is True
    assert is_own_website("https://www.instagram.com/panaderia") is False
    assert is_own_website(None) is False
