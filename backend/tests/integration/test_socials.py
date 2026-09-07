"""Redes sociales: extraerlas, guardarlas y poder filtrar por ellas.

En Colombia hay muchísimo negocio pequeño que **no tiene web**: su presencia
es el Instagram, y en la ficha de Google ese perfil aparece en el campo "sitio
web". Si el CRM lo descarta por no ser un sitio propio, tira justo el único
canal de contacto que tenía esa empresa.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SourceType
from app.enrichment.website_crawler import CrawlResult
from app.models.company import Company, CompanySocial
from app.scrapers.base import RawPlace
from app.services.company_svc import CompanyService
from app.services.enrichment_svc import EnrichmentService
from app.utils.dedupe import build_dedupe_key
from app.utils.url import social_handle

API = "/api/v1"


async def _company(db: AsyncSession, name: str, website: str | None, **kw: object) -> Company:
    now = datetime.now(UTC)
    company = Company(
        name=name,
        city=kw.pop("city", "Medellín"),  # type: ignore[arg-type]
        website=website,
        website_domain=kw.pop("website_domain", None),  # type: ignore[arg-type]
        dedupe_key=build_dedupe_key(name=name, website=website, phone_e164=None, city="Medellín"),
        first_extracted_at=now,
        last_extracted_at=now,
        **kw,  # type: ignore[arg-type]
    )
    db.add(company)
    await db.flush()
    return company


# ------------------------------------------------------------------ handles


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("https://instagram.com/lafinca", "lafinca"),
        ("https://www.instagram.com/lafinca/", "lafinca"),
        ("https://tiktok.com/@lafinca", "lafinca"),
        ("https://linkedin.com/company/la-finca", "la-finca"),
        ("https://www.linkedin.com/in/daniel-ruiz", "daniel-ruiz"),
        ("https://x.com/lafinca", "lafinca"),
        ("https://facebook.com/pages/La-Finca", "La-Finca"),
        ("https://instagram.com/", None),
    ],
)
def test_el_handle_sale_de_la_url(url: str, esperado: str | None) -> None:
    """`@lafinca` cabe en una tabla; una URL de 80 caracteres, no."""
    assert social_handle(url) == esperado


# ------------------------------------------------------------------ extracción


@pytest.mark.asyncio
async def test_instagram_como_sitio_web_se_guarda_como_red(db: AsyncSession) -> None:
    """El caso que se perdía: el "sitio web" del negocio es su Instagram.

    Antes `is_social_url` lo descartaba por no ser un sitio propio y nadie
    guardaba el perfil, así que la empresa quedaba sin ningún canal.
    """
    company = await _company(db, "Panadería Doña Ana", "https://instagram.com/donaana")

    outcome = await EnrichmentService(db).enrich(company)

    assert outcome.socials_found == 1
    redes = (
        (await db.execute(select(CompanySocial).where(CompanySocial.company_id == company.id)))
        .scalars()
        .all()
    )
    assert len(redes) == 1
    assert redes[0].platform == "instagram"
    assert redes[0].handle == "donaana"
    # Vino de la ficha de Google, no de rastrear una web.
    assert redes[0].source is SourceType.GOOGLE_MAPS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "plataforma", "handle"),
    [
        ("https://www.tiktok.com/@barberia.co", "tiktok", "barberia.co"),
        ("https://www.linkedin.com/company/consultora-x", "linkedin", "consultora-x"),
        ("https://x.com/estudio_legal", "x", "estudio_legal"),
        ("https://twitter.com/estudio_legal", "x", "estudio_legal"),
        ("https://www.facebook.com/restaurante", "facebook", "restaurante"),
    ],
)
async def test_las_cuatro_redes_que_importan(
    db: AsyncSession, url: str, plataforma: str, handle: str
) -> None:
    company = await _company(db, f"Negocio {plataforma}", url)

    await EnrichmentService(db).enrich(company)

    red = (
        (await db.execute(select(CompanySocial).where(CompanySocial.company_id == company.id)))
        .scalars()
        .one()
    )
    assert red.platform == plataforma
    assert red.handle == handle


class _CrawlerMudo:
    """Crawler que no llega a la web. Evita salir a internet en los tests."""

    async def crawl(self, url: str) -> CrawlResult:
        return CrawlResult(url=url, reachable=False, error="sin red")


@pytest.mark.asyncio
async def test_una_web_propia_no_se_confunde_con_una_red(db: AsyncSession) -> None:
    """Un dominio propio no genera perfil social por su cuenta.

    Y si la web no responde, el enriquecimiento no puede reventar: antes
    intentaba leer las redes ya guardadas con una carga perezosa y saltaba
    `MissingGreenlet`.
    """
    company = await _company(
        db, "Constructora Real", "https://constructorareal.co", website_domain="constructorareal.co"
    )

    service = EnrichmentService(db, crawler=_CrawlerMudo())  # type: ignore[arg-type]
    outcome = await service.enrich(company)

    assert outcome.socials_found == 0
    assert outcome.crawled is False


@pytest.mark.asyncio
async def test_las_redes_de_la_ficha_se_guardan_al_descubrir(db: AsyncSession) -> None:
    """El descubrimiento ya las guarda; no espera al enriquecimiento.

    El enriquecimiento solo entra cuando hay una web propia que rastrear, así
    que el negocio que únicamente tiene Instagram nunca llegaría allí.
    """
    place = RawPlace(
        name="Pastelería Dulce",
        source=SourceType.GOOGLE_MAPS,
        city="Medellín",
        address="Calle 10 #43-20",
        maps_url="https://www.google.com/maps/place/?q=place_id:ChIJabc",
        website="https://instagram.com/dulce",
        socials={
            "tiktok": "https://www.tiktok.com/@dulce",
            "linkedin": "https://www.linkedin.com/company/dulce",
            "x": "https://x.com/dulce",
        },
    )

    result = await CompanyService(db).upsert_from_raw(place)
    await db.flush()

    redes = (
        (
            await db.execute(
                select(CompanySocial).where(CompanySocial.company_id == result.company.id)
            )
        )
        .scalars()
        .all()
    )

    # Las tres de la ficha más la del campo "sitio web".
    assert {r.platform for r in redes} == {"tiktok", "linkedin", "x", "instagram"}
    assert {r.handle for r in redes} == {"dulce"}
    # Y la URL de la ficha de Google queda guardada.
    assert result.company.google_maps_url == place.maps_url


@pytest.mark.asyncio
async def test_un_negocio_solo_con_redes_sigue_siendo_util(db: AsyncSession) -> None:
    """Sin dirección ni teléfono, pero con Instagram: es contactable."""
    place = RawPlace(
        name="Marca Digital",
        source=SourceType.GOOGLE_MAPS,
        socials={"instagram": "https://instagram.com/marcadigital"},
    )

    assert place.is_usable is True


# ------------------------------------------------------------------ API


@pytest.mark.asyncio
async def test_el_listado_trae_las_redes(client: AsyncClient, db: AsyncSession) -> None:
    """Sin esto había que abrir empresa por empresa para ver su Instagram."""
    company = await _company(db, "Café del Parque", "https://instagram.com/cafeparque")
    await EnrichmentService(db).enrich(company)
    await db.commit()

    response = await client.get(f"{API}/companies", params={"q": "Café del Parque"})
    assert response.status_code == 200
    fila = response.json()["items"][0]
    assert fila["socials"][0]["platform"] == "instagram"
    assert fila["socials"][0]["handle"] == "cafeparque"


@pytest.mark.asyncio
async def test_filtrar_por_plataforma(client: AsyncClient, db: AsyncSession) -> None:
    con_tiktok = await _company(db, "Barbería TikTok", "https://tiktok.com/@barberia")
    con_insta = await _company(db, "Café Insta", "https://instagram.com/cafe")
    service = EnrichmentService(db)
    await service.enrich(con_tiktok)
    await service.enrich(con_insta)
    await db.commit()

    response = await client.get(f"{API}/companies", params={"platform": "tiktok"})
    nombres = [c["name"] for c in response.json()["items"]]
    assert "Barbería TikTok" in nombres
    assert "Café Insta" not in nombres


@pytest.mark.asyncio
async def test_filtrar_sin_web_y_con_redes(client: AsyncClient, db: AsyncSession) -> None:
    """El filtro estrella para vender desarrollo web: no tiene sitio, tiene Instagram."""
    sin_web = await _company(db, "Solo Instagram", "https://instagram.com/solo")
    await _company(db, "Con Web", "https://conweb.co", website_domain="conweb.co")
    await EnrichmentService(db).enrich(sin_web)
    await db.commit()

    response = await client.get(
        f"{API}/companies", params={"has_website": "false", "has_social": "true"}
    )
    nombres = [c["name"] for c in response.json()["items"]]
    assert nombres == ["Solo Instagram"]


@pytest.mark.asyncio
async def test_filtrar_sin_redes(client: AsyncClient, db: AsyncSession) -> None:
    await _company(db, "Sin nada", None)
    con_red = await _company(db, "Con red", "https://instagram.com/conred")
    await EnrichmentService(db).enrich(con_red)
    await db.commit()

    response = await client.get(f"{API}/companies", params={"has_social": "false"})
    nombres = [c["name"] for c in response.json()["items"]]
    assert "Sin nada" in nombres
    assert "Con red" not in nombres


@pytest.mark.asyncio
async def test_facetas_cuentan_lo_que_hay(client: AsyncClient, db: AsyncSession) -> None:
    """Un filtro que puede devolver cero sin avisar se prueba una vez y ya."""
    con_red = await _company(db, "Con Instagram", "https://instagram.com/x")
    await _company(db, "Con web propia", "https://propia.co", website_domain="propia.co")
    await _company(db, "Pelada", None, phone="+573001112233")
    await EnrichmentService(db).enrich(con_red)
    await db.commit()

    response = await client.get(f"{API}/companies/facets")
    assert response.status_code == 200
    facetas = response.json()

    assert facetas["total"] == 3
    assert facetas["with_website"] == 1
    assert facetas["without_website"] == 2
    assert facetas["with_social"] == 1
    assert facetas["without_social"] == 2
    assert facetas["with_phone"] == 1
    assert {p["platform"] for p in facetas["platforms"]} == {"instagram"}
    assert "Medellín" in facetas["cities"]


@pytest.mark.asyncio
async def test_la_paginacion_no_repite_ni_se_salta_empresas(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Con 3.000 empresas la lista se pagina; el orden tiene que ser estable."""
    for index in range(25):
        await _company(db, f"Empresa {index:02d}", None)
    await db.commit()

    vistos: list[str] = []
    for page in (1, 2, 3):
        response = await client.get(f"{API}/companies", params={"page": page, "size": 10})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 25
        assert body["pages"] == 3
        vistos.extend(c["id"] for c in body["items"])

    assert len(vistos) == 25
    assert len(set(vistos)) == 25
