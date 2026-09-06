"""Dedupe de empresas contra Postgres real.

Se prueba con `RawPlace` construidos a mano, no contra Google: el listado de
Maps varía entre consultas, así que un test que dependa de él no distingue un
fallo del dedupe de una diferencia real de resultados.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SourceType
from app.models.company import Company
from app.scrapers.base import RawPlace
from app.services.company_svc import CompanyService


def _place(**overrides) -> RawPlace:
    base = {
        "name": "Restaurante El Sabor",
        "source": SourceType.GOOGLE_MAPS,
        "address": "Cra. 43A #7-50, El Poblado, Medellín, Antioquia",
        "city": "Medellín",
        "phone": "604 444 1234",
        "website": "https://www.elsabor.com",
        "latitude": 6.2088,
        "longitude": -75.5906,
        "rating": 4.5,
        "reviews_count": 120,
    }
    return RawPlace(**{**base, **overrides})


async def test_same_place_twice_creates_one_company(db: AsyncSession) -> None:
    """Nivel 1: mismo FTID -> misma empresa. Es el 90% de los casos."""
    service = CompanyService(db)

    first = await service.upsert_from_raw(_place(ftid="0xabc:0xdef"))
    second = await service.upsert_from_raw(_place(ftid="0xabc:0xdef"))

    assert first.is_new is True
    assert second.is_new is False
    assert first.company.id == second.company.id

    total = await db.execute(select(func.count()).select_from(Company))
    assert total.scalar_one() == 1


async def test_dedupe_key_matches_across_formatting_differences(db: AsyncSession) -> None:
    """Nivel 2: sin FTID, la clave determinista reconoce la misma empresa
    aunque el proveedor la escriba distinto."""
    service = CompanyService(db)

    await service.upsert_from_raw(_place(name="Restaurante El Sabor S.A.S."))
    second = await service.upsert_from_raw(
        _place(name="EL SABOR SAS", website="elsabor.com", phone="+57 604 444 1234")
    )

    assert second.is_new is False

    total = await db.execute(select(func.count()).select_from(Company))
    assert total.scalar_one() == 1


async def test_different_companies_are_not_merged(db: AsyncSession) -> None:
    service = CompanyService(db)

    await service.upsert_from_raw(_place(name="El Sabor", ftid="0x1:0x1"))
    other = await service.upsert_from_raw(
        _place(name="La Fonda Paisa", ftid="0x2:0x2", phone="604 555 9999")
    )

    assert other.is_new is True

    total = await db.execute(select(func.count()).select_from(Company))
    assert total.scalar_one() == 2


async def test_chain_branches_stay_separate(db: AsyncSession) -> None:
    """Dos sedes con el mismo nombre y ciudad son empresas distintas.

    Fusionarlas perdería las conversaciones de una de las dos, que es peor que
    tener un duplicado.
    """
    service = CompanyService(db)

    await service.upsert_from_raw(
        _place(name="Juan Valdez", ftid="0xaa:0xaa", phone="604 111 1111", website=None)
    )
    sede2 = await service.upsert_from_raw(
        _place(name="Juan Valdez", ftid="0xbb:0xbb", phone="604 222 2222", website=None)
    )

    assert sede2.is_new is True


async def test_scraper_and_api_ids_converge_on_one_company(db: AsyncSession) -> None:
    """FTID (scraper) y place_id (Places API) son identificadores distintos.

    Una empresa capturada por ambos proveedores debe reconocerse como la
    misma; si no, cambiar de proveedor duplicaría toda la base.
    """
    service = CompanyService(db)

    scraped = await service.upsert_from_raw(_place(ftid="0xaa:0xbb"))
    assert scraped.is_new is True

    from_api = await service.upsert_from_raw(
        _place(
            source=SourceType.GOOGLE_PLACES_API,
            ftid=None,
            place_id="ChIJxxxxxxxxx",
            name="Restaurante El Sabor",
        )
    )

    assert from_api.is_new is False, "el nivel 2 (dedupe_key) debe atraparlo"
    assert from_api.company.id == scraped.company.id


async def test_merge_does_not_overwrite_good_data_with_empty(db: AsyncSession) -> None:
    """Un scraping que falló al leer el teléfono no debe borrar el que ya
    teníamos."""
    service = CompanyService(db)

    first = await service.upsert_from_raw(_place(ftid="0x1:0x2"))
    assert first.company.phone == "+576044441234"

    await service.upsert_from_raw(_place(ftid="0x1:0x2", phone=None, website=None))

    assert first.company.phone == "+576044441234", "el teléfono debe conservarse"
    assert first.company.website is not None


async def test_merge_refreshes_live_data(db: AsyncSession) -> None:
    """Rating y reseñas sí se sobrescriben: son datos vivos."""
    service = CompanyService(db)

    first = await service.upsert_from_raw(_place(ftid="0x3:0x4", rating=4.1, reviews_count=50))
    await service.upsert_from_raw(_place(ftid="0x3:0x4", rating=4.7, reviews_count=310))

    assert float(first.company.rating) == 4.7
    assert first.company.reviews_count == 310


async def test_social_url_does_not_count_as_own_website(db: AsyncSession) -> None:
    """Un negocio cuyo 'sitio web' es su Instagram no tiene web propia — y es
    justo el prospecto que busca quien vende desarrollo web."""
    service = CompanyService(db)

    result = await service.upsert_from_raw(
        _place(ftid="0x9:0x9", website="https://www.instagram.com/mirestaurante")
    )

    assert result.company.website_domain is None
    assert result.company.has_website is False


async def test_similar_names_nearby_are_flagged_not_merged(db: AsyncSession) -> None:
    """Nivel 3: se avisa del parecido, pero no se fusiona automáticamente."""
    service = CompanyService(db)

    await service.upsert_from_raw(
        _place(name="Panadería La Espiga Dorada", ftid="0xc1:0xc1", phone="604 111 0000")
    )
    await db.flush()

    similar = await service.upsert_from_raw(
        _place(
            name="Panaderia La Espiga Dorada",
            ftid="0xc2:0xc2",
            phone="604 222 0000",
            website=None,
        )
    )

    assert similar.is_new is True, "no se fusiona sin que lo decida el usuario"
    assert len(similar.duplicate_candidates) >= 1, "pero sí se avisa del parecido"
    assert similar.duplicate_candidates[0].similarity > 0.75
