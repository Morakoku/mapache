"""Del servicio al plan de búsquedas.

El usuario describe qué vende y a quién, y el CRM propone a quién buscar y
dónde. Lo que hay que blindar es que la propuesta sea **suya**: las industrias
que él escribió mandan siempre sobre lo que deduzca un modelo, y el plan se
enseña antes de gastar seis minutos de scraping por búsqueda.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.search import Search
from app.models.service import Service
from app.services.prospect_plan_svc import ProspectPlanService

API = "/api/v1"


@pytest.fixture
def cola_falsa(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, dict]]:
    """Intercepta el encolado.

    Los tests comprueban que el plan crea las búsquedas y encola un job por
    cada una, no que el scraper corra: eso tarda minutos y sale a internet.
    """
    encolados: list[tuple[object, dict]] = []

    class _Cola:
        async def enqueue(self, job_type: object, payload: dict, *, job_id: object) -> None:
            encolados.append((job_type, payload))

    monkeypatch.setattr("app.routers.services.get_job_queue", lambda: _Cola())
    return encolados


async def _service(db: AsyncSession, **kw: object) -> Service:
    service = Service(
        name=kw.pop("name", "Páginas web"),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )
    db.add(service)
    await db.flush()
    return service


# ------------------------------------------------------------------ plan


@pytest.mark.asyncio
async def test_una_busqueda_por_industria_y_ciudad(db: AsyncSession) -> None:
    service = await _service(db, target_industries=["panadería", "restaurante"])

    plan = await ProspectPlanService(db).build(service, cities=["Medellín", "Bogotá"])

    assert len(plan.searches) == 4
    assert {(s.business_type, s.city) for s in plan.searches} == {
        ("panadería", "Medellín"),
        ("restaurante", "Medellín"),
        ("panadería", "Bogotá"),
        ("restaurante", "Bogotá"),
    }
    assert plan.source == "rules"


@pytest.mark.asyncio
async def test_cada_busqueda_explica_sus_filtros(db: AsyncSession) -> None:
    """Un filtro sin motivo no se puede discutir ni corregir."""
    service = await _service(db, target_industries=["panadería"])

    plan = await ProspectPlanService(db).build(service, cities=["Medellín"])

    assert plan.searches[0].reason
    assert "medellín" in plan.searches[0].reason.lower()


@pytest.mark.asyncio
async def test_si_vendes_webs_no_se_exige_que_tengan_web(db: AsyncSession) -> None:
    """El caso que importa: el prospecto ideal es justo el que no tiene web.

    Exigir sitio web propio en esa búsqueda descartaría a todos los buenos.
    """
    service = await _service(
        db, target_industries=["panadería"], opportunity_signals=["no_website"]
    )

    plan = await ProspectPlanService(db).build(service, cities=["Medellín"])

    propuesta = plan.searches[0]
    assert propuesta.require_website is False
    # Sin web, el teléfono es el único canal: se exige para no traer fantasmas.
    assert propuesta.require_phone is True
    assert "sitio web" in propuesta.reason.lower()


@pytest.mark.asyncio
async def test_se_descarta_la_morralla_de_calificacion(db: AsyncSession) -> None:
    service = await _service(db, target_industries=["restaurante"])

    plan = await ProspectPlanService(db).build(service, cities=["Cali"])

    assert plan.searches[0].min_rating == 3.5


@pytest.mark.asyncio
async def test_si_buscas_calificacion_baja_no_se_filtra_por_ella(db: AsyncSession) -> None:
    """Quien vende reputación busca precisamente a los mal calificados."""
    service = await _service(
        db, target_industries=["restaurante"], opportunity_signals=["low_rating"]
    )

    plan = await ProspectPlanService(db).build(service, cities=["Cali"])

    assert plan.searches[0].min_rating is None


@pytest.mark.asyncio
async def test_no_se_propone_lo_que_ya_existe(db: AsyncSession) -> None:
    service = await _service(db, target_industries=["panadería"])
    db.add(
        Search(
            service_id=service.id,
            name="Ya la tenía",
            business_type="panadería",
            city="Medellín",
        )
    )
    await db.flush()

    plan = await ProspectPlanService(db).build(service, cities=["Medellín"])

    assert plan.searches[0].already_exists is True
    assert plan.total_target == 0


@pytest.mark.asyncio
async def test_el_plan_se_corta_para_no_ser_una_tarde_entera(db: AsyncSession) -> None:
    """Doce búsquedas ya son más de una hora de scraping."""
    service = await _service(db, target_industries=[f"tipo{i}" for i in range(10)])

    plan = await ProspectPlanService(db).build(service, cities=["Medellín", "Bogotá"])

    assert len(plan.searches) == 12
    assert any("se corta" in n for n in plan.notes)


@pytest.mark.asyncio
async def test_sin_industrias_ni_ia_se_dice_que_falta(db: AsyncSession) -> None:
    """No se inventa un público que el usuario no declaró."""
    service = await _service(db, ideal_customer="negocios que quieren vender más")

    with pytest.raises(ValidationError) as exc:
        await ProspectPlanService(db).build(service, cities=["Medellín"])

    assert exc.value.code == "SERVICE_HAS_NO_TARGET"


@pytest.mark.asyncio
async def test_sin_ciudad_no_hay_plan(db: AsyncSession) -> None:
    service = await _service(db, target_industries=["panadería"])

    with pytest.raises(ValidationError) as exc:
        await ProspectPlanService(db).build(service, cities=[])

    assert exc.value.code == "CITIES_REQUIRED"


# ------------------------------------------------------------------ API


@pytest.mark.asyncio
async def test_api_devuelve_el_plan_sin_ejecutar_nada(
    client: AsyncClient, db: AsyncSession
) -> None:
    service = await _service(db, target_industries=["panadería"])
    await db.commit()

    response = await client.post(
        f"{API}/services/{service.id}/prospect-plan",
        json={"cities": ["Medellín"], "target_per_search": 50},
    )
    assert response.status_code == 200
    plan = response.json()
    assert plan["searches"][0]["target_count"] == 50
    assert plan["total_target"] == 50

    # Proponer no crea nada.
    creadas = await db.execute(select(Search))
    assert list(creadas.scalars()) == []


@pytest.mark.asyncio
async def test_api_ejecutar_crea_las_busquedas(
    client: AsyncClient, db: AsyncSession, cola_falsa: list[tuple[object, dict]]
) -> None:
    service = await _service(db, target_industries=["panadería", "cafetería"])
    await db.commit()

    response = await client.post(
        f"{API}/services/{service.id}/prospect-plan/run",
        json={"cities": ["Medellín"], "target_per_search": 30},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["created"] == 2
    assert len(body["job_ids"]) == 2

    creadas = (await db.execute(select(Search))).scalars().all()
    assert {s.business_type for s in creadas} == {"panadería", "cafetería"}
    assert all(s.service_id == service.id for s in creadas)
    # Una búsqueda, un job.
    assert len(cola_falsa) == 2


@pytest.mark.asyncio
async def test_api_no_duplica_al_ejecutar_dos_veces(
    client: AsyncClient, db: AsyncSession, cola_falsa: list[tuple[object, dict]]
) -> None:
    service = await _service(db, target_industries=["panadería"])
    await db.commit()

    ruta = f"{API}/services/{service.id}/prospect-plan/run"
    cuerpo = {"cities": ["Medellín"]}

    primera = await client.post(ruta, json=cuerpo)
    segunda = await client.post(ruta, json=cuerpo)

    assert primera.json()["created"] == 1
    assert segunda.json()["created"] == 0
    assert segunda.json()["skipped_existing"] == 1


@pytest.mark.asyncio
async def test_api_sin_publico_objetivo_explica_que_falta(
    client: AsyncClient, db: AsyncSession
) -> None:
    service = await _service(db, name="Servicio vago")
    await db.commit()

    response = await client.post(
        f"{API}/services/{service.id}/prospect-plan", json={"cities": ["Medellín"]}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SERVICE_HAS_NO_TARGET"
    assert "industrias objetivo" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_api_sugiere_ciudades_donde_ya_hay_empresas(
    client: AsyncClient, db: AsyncSession
) -> None:
    from datetime import UTC, datetime

    from app.models.company import Company

    now = datetime.now(UTC)
    for ciudad, cuantas in (("Medellín", 3), ("Bogotá", 1)):
        for i in range(cuantas):
            db.add(
                Company(
                    name=f"{ciudad} {i}",
                    city=ciudad,
                    dedupe_key=f"{ciudad}-{i}",
                    first_extracted_at=now,
                    last_extracted_at=now,
                )
            )
    service = await _service(db, target_industries=["panadería"])
    await db.commit()

    response = await client.get(f"{API}/services/{service.id}/prospect-plan/cities")

    assert response.status_code == 200
    # Ordenadas de más a menos: es la pista de dónde trabaja de verdad.
    assert response.json()[0] == "Medellín"
