"""Redes de la ficha de Google Maps, sin levantar Chromium.

El extractor real necesita un navegador, pero lo que hay que probar es la
decisión: de todos los enlaces del panel, cuáles son perfiles del negocio y
cuáles son ruido de Google. Eso se prueba con una lista de URLs.
"""

from __future__ import annotations

import pytest

from app.scrapers.google_maps.detail_page import _SOCIAL_TEXT_RE, extract_socials
from app.utils.url import is_share_url, social_handle, unwrap_redirect


class _PanelFalso:
    """Doble de `Page` que solo sabe devolver los enlaces del panel."""

    def __init__(self, hrefs: list[str]) -> None:
        self._hrefs = hrefs

    def locator(self, _selector: str) -> _PanelFalso:
        return self

    async def evaluate_all(self, _script: str) -> list[str]:
        return self._hrefs


async def _socials(hrefs: list[str], texto: str = "") -> dict[str, str]:
    return await extract_socials(_PanelFalso(hrefs), texto)  # type: ignore[arg-type]


# ------------------------------------------------------------------ redirecciones


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        (
            "https://www.google.com/url?q=https%3A%2F%2Finstagram.com%2Flafinca",
            "https://instagram.com/lafinca",
        ),
        (
            "https://www.google.com/url?q=https://tiktok.com/@lafinca&sa=D",
            "https://tiktok.com/@lafinca",
        ),
        # Navegación interna de Google: no es un enlace saliente.
        ("https://www.google.com/url?q=/maps/place/x", "https://www.google.com/url?q=/maps/place/x"),
        ("https://instagram.com/directo", "https://instagram.com/directo"),
    ],
)
def test_se_deshacen_las_redirecciones_de_google(url: str, esperado: str) -> None:
    """Sin esto el detector ve `google.com` y descarta el perfil."""
    assert unwrap_redirect(url) == esperado


# ------------------------------------------------------------------ extracción


@pytest.mark.asyncio
async def test_una_ficha_con_varias_redes_las_guarda_todas() -> None:
    """El caso que se perdía: la ficha enlaza cuatro redes y solo se leía una."""
    socials = await _socials(
        [
            "https://lafinca.co/",
            "https://www.instagram.com/lafinca/",
            "https://www.tiktok.com/@lafinca",
            "https://www.linkedin.com/company/la-finca",
            "https://x.com/lafinca",
        ]
    )

    assert set(socials) == {"instagram", "tiktok", "linkedin", "x"}
    assert socials["tiktok"] == "https://www.tiktok.com/@lafinca"


@pytest.mark.asyncio
async def test_los_enlaces_de_google_no_se_cuelan() -> None:
    """El pie de Google enlaza sus propias redes; no son las del negocio."""
    socials = await _socials(
        [
            "https://policies.google.com/terms",
            "https://support.google.com/maps",
            "https://www.google.com/intl/es/about/",
            "https://maps.google.com/maps?cid=123",
        ]
    )

    assert socials == {}


@pytest.mark.asyncio
async def test_la_portada_de_una_red_no_es_un_perfil() -> None:
    """`instagram.com` a secas no es el perfil de nadie."""
    socials = await _socials(["https://www.instagram.com/", "https://facebook.com"])

    assert socials == {}


@pytest.mark.asyncio
async def test_se_queda_con_el_primer_perfil_de_cada_red() -> None:
    socials = await _socials(
        [
            "https://instagram.com/principal",
            "https://instagram.com/secundaria",
        ]
    )

    assert socials == {"instagram": "https://instagram.com/principal"}


@pytest.mark.asyncio
async def test_perfiles_tras_una_redireccion_de_google() -> None:
    socials = await _socials(
        ["https://www.google.com/url?q=https%3A%2F%2Fwww.instagram.com%2Fdonaana&sa=D"]
    )

    assert socials == {"instagram": "https://www.instagram.com/donaana"}
    assert social_handle(socials["instagram"]) == "donaana"


@pytest.mark.asyncio
async def test_si_no_hay_enlaces_se_lee_la_descripcion() -> None:
    """A veces el perfil está escrito en el texto en vez de enlazado."""
    texto = "Panadería artesanal. Escríbenos por https://instagram.com/donaana o al 3001112233."

    socials = await _socials([], texto)

    assert socials == {"instagram": "https://instagram.com/donaana"}


@pytest.mark.asyncio
async def test_el_texto_solo_se_mira_si_no_hubo_enlaces() -> None:
    """Un enlace real vale más que una mención suelta en la descripción."""
    socials = await _socials(
        ["https://instagram.com/de-enlace"],
        "Síguenos en https://instagram.com/del-texto",
    )

    assert socials == {"instagram": "https://instagram.com/de-enlace"}


@pytest.mark.asyncio
async def test_twitter_y_x_son_la_misma_red() -> None:
    socials = await _socials(["https://twitter.com/negocio", "https://x.com/negocio"])

    assert set(socials) == {"x"}


@pytest.mark.asyncio
async def test_un_fallo_leyendo_enlaces_no_tumba_la_ficha() -> None:
    """Las redes son opcionales: un error aquí no puede perder el negocio."""

    class _PanelRoto:
        def locator(self, _selector: str) -> _PanelRoto:
            return self

        async def evaluate_all(self, _script: str) -> list[str]:
            raise RuntimeError("el panel cambió")

    socials = await extract_socials(_PanelRoto(), "sin nada")  # type: ignore[arg-type]

    assert socials == {}


@pytest.mark.parametrize(
    "texto",
    [
        "https://instagram.com/x",
        "https://www.tiktok.com/@x",
        "https://linkedin.com/company/x",
        "https://x.com/x",
        "https://wa.me/573001112233",
    ],
)
def test_el_regex_de_respaldo_reconoce_las_redes_pedidas(texto: str) -> None:
    assert _SOCIAL_TEXT_RE.search(texto) is not None


# URLs reales que se colaron en un scraping de panaderías de Medellín: la web
# del negocio estaba en un directorio lleno de botones de compartir, y cada
# empresa acabó con un «LinkedIn» y un «X» que no eran suyos.
_COMPARTIR = [
    "https://twitter.com/share?url=https%3A%2F%2Fwww.aiyellow.com%2Fx&text=Panader%C3%ADa",
    "https://pinterest.com/pin/create/link?url=https%3A%2F%2Fwww.aiyellow.com%2Fx",
    "https://www.linkedin.com/shareArticle?mini=true&url=https%3A%2F%2Fwww.aiyellow.com%2Fx",
    "https://wa.me/?text=Panader%C3%ADa%3Ahttps%3A%2F%2Fwww.aiyellow.com%2Fx",
    "https://www.facebook.com/sharer/sharer.php?u=https%3A%2F%2Fejemplo.co",
    "https://www.facebook.com/dialog/share?href=https%3A%2F%2Fejemplo.co",
    "https://telegram.me/share/url?url=https%3A%2F%2Fejemplo.co",
]

_PERFILES = [
    "https://www.instagram.com/panaderialaespiga",
    "https://www.instagram.com/panaderia?hl=es",
    "https://www.instagram.com/panaderia?igshid=abc123",
    "https://www.facebook.com/panaderiayreposteria",
    "https://www.linkedin.com/company/panaderia-la-espiga",
    "https://www.tiktok.com/@panaderia",
    "https://wa.me/573001112233",
    "https://x.com/panaderia",
]


@pytest.mark.parametrize("url", _COMPARTIR)
def test_los_botones_de_compartir_no_son_perfiles(url: str) -> None:
    assert is_share_url(url) is True


@pytest.mark.parametrize("url", _PERFILES)
def test_los_perfiles_de_verdad_sobreviven_al_filtro(url: str) -> None:
    assert is_share_url(url) is False


@pytest.mark.asyncio
async def test_la_ficha_no_guarda_botones_de_compartir() -> None:
    """El filtro tiene que actuar donde se leen los enlaces, no después."""
    panel = _PanelFalso([*_COMPARTIR, "https://www.instagram.com/panaderialaespiga"])

    socials = await extract_socials(panel, "")  # type: ignore[arg-type]

    assert socials == {"instagram": "https://www.instagram.com/panaderialaespiga"}
