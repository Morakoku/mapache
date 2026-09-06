"""El dossier: todo lo que se sabe de una empresa en un solo sitio.

Lo que hay que blindar: que un fallo del buscador no deje sin panel —lo demás
ya está en base y es lo que de verdad se usa para decidir— y que no se gaste
cuota preguntando lo mismo dos veces.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt
from app.models.company import Company
from app.models.settings import AppSettings
from app.services.dossier_svc import DossierService
from app.utils.dedupe import build_dedupe_key

API = "/api/v1"


async def _company(db: AsyncSession, name: str = "Panadería Ana", **kw: Any) -> Company:
    now = datetime.now(UTC)
    company = Company(
        name=name,
        city=kw.pop("city", "Medellín"),
        dedupe_key=build_dedupe_key(name=name, website=None, phone_e164=None, city="Medellín"),
        first_extracted_at=now,
        last_extracted_at=now,
        **kw,
    )
    db.add(company)
    await db.flush()
    return company


async def _serp_configured(db: AsyncSession) -> AppSettings:
    settings = AppSettings(
        id=1, serp_api_key_enc=encrypt("clave-de-prueba"), serp_engine_id="motor"
    )
    db.add(settings)
    await db.flush()
    return settings


def _mock_serp(monkeypatch: pytest.MonkeyPatch, items: list[dict[str, Any]]) -> list[str]:
    """Sustituye la llamada al buscador y devuelve las consultas pedidas."""
    consultas: list[str] = []

    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        consultas.append(kw["params"]["q"])
        return httpx.Response(200, json={"items": items}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return consultas


def _item(title: str, link: str, domain: str, snippet: str = "") -> dict[str, Any]:
    return {"title": title, "link": link, "displayLink": domain, "snippet": snippet}


# ------------------------------------------------------------------ sin buscador


@pytest.mark.asyncio
async def test_sin_buscador_el_dossier_sigue_sirviendo(db: AsyncSession) -> None:
    """Lo que hay en base es lo que de verdad se usa para decidir."""
    db.add(AppSettings(id=1))
    company = await _company(db, phone="+573001112233")

    dossier = await DossierService(db).build(company)

    assert dossier.web_available is False
    assert dossier.web_findings == []
    assert any("Configuración" in n for n in dossier.notes)
    assert dossier.company.phone == "+573001112233"


# ------------------------------------------------------------------ búsqueda web


@pytest.mark.asyncio
async def test_la_consulta_lleva_nombre_y_ciudad_entre_comillas(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin comillas, "Panadería Ana" devuelve todas las del país."""
    await _serp_configured(db)
    company = await _company(db)
    consultas = _mock_serp(monkeypatch, [])

    await DossierService(db).build(company)

    assert consultas == ['"Panadería Ana" "Medellín"']


@pytest.mark.asyncio
async def test_los_hallazgos_se_guardan(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    await _serp_configured(db)
    company = await _company(db)
    _mock_serp(
        monkeypatch,
        [
            _item(
                "Panadería Ana - Directorio", "https://paginas.co/ana", "paginas.co", "Abre a las 6"
            )
        ],
    )

    dossier = await DossierService(db).build(company)

    assert len(dossier.web_findings) == 1
    assert dossier.web_findings[0].source_domain == "paginas.co"
    # Guardado en la empresa para no volver a gastar cuota.
    assert company.web_findings is not None
    assert company.web_findings_at is not None


@pytest.mark.asyncio
async def test_no_se_vuelve_a_preguntar_si_esta_fresco(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cada consulta cuesta: dos aperturas del panel no son dos consultas."""
    await _serp_configured(db)
    company = await _company(db)
    consultas = _mock_serp(monkeypatch, [_item("Algo", "https://x.co/a", "x.co")])

    service = DossierService(db)
    await service.build(company)
    await service.build(company)

    assert len(consultas) == 1


@pytest.mark.asyncio
async def test_lo_viejo_se_vuelve_a_buscar(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _serp_configured(db)
    company = await _company(db)
    company.web_findings = []
    company.web_findings_at = datetime.now(UTC) - timedelta(days=40)
    await db.flush()
    consultas = _mock_serp(monkeypatch, [_item("Algo", "https://x.co/a", "x.co")])

    await DossierService(db).build(company)

    assert len(consultas) == 1


@pytest.mark.asyncio
async def test_refrescar_a_mano_ignora_la_caché(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _serp_configured(db)
    company = await _company(db)
    consultas = _mock_serp(monkeypatch, [_item("Algo", "https://x.co/a", "x.co")])

    service = DossierService(db)
    await service.build(company)
    await service.build(company, refresh_web=True)

    assert len(consultas) == 2


@pytest.mark.asyncio
async def test_se_descarta_el_ruido_de_google(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los propios enlaces de Google no dicen nada del negocio."""
    await _serp_configured(db)
    company = await _company(db)
    _mock_serp(
        monkeypatch,
        [
            _item("Maps", "https://maps.google.com/x", "maps.google.com"),
            _item("Real", "https://paginas.co/ana", "paginas.co"),
        ],
    )

    dossier = await DossierService(db).build(company)

    assert [f.source_domain for f in dossier.web_findings] == ["paginas.co"]


@pytest.mark.asyncio
async def test_un_dominio_solo_aparece_una_vez(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ocho resultados del mismo directorio no son ocho hallazgos."""
    await _serp_configured(db)
    company = await _company(db)
    _mock_serp(
        monkeypatch,
        [
            _item("Uno", "https://paginas.co/a", "paginas.co"),
            _item("Dos", "https://paginas.co/b", "www.paginas.co"),
            _item("Tres", "https://otro.co/c", "otro.co"),
        ],
    )

    dossier = await DossierService(db).build(company)

    assert [f.source_domain for f in dossier.web_findings] == ["paginas.co", "otro.co"]


@pytest.mark.asyncio
async def test_un_fallo_del_buscador_no_tumba_el_panel(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El resto del dossier es lo que se usa para decidir; tiene que llegar."""
    await _serp_configured(db)
    company = await _company(db, phone="+573001112233")

    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        return httpx.Response(429, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    dossier = await DossierService(db).build(company)

    assert dossier.company.phone == "+573001112233"
    assert any("cuota" in n.lower() for n in dossier.notes)


# ------------------------------------------------------------------ API


@pytest.mark.asyncio
async def test_api_devuelve_el_dossier(client: AsyncClient, db: AsyncSession) -> None:
    db.add(AppSettings(id=1))
    company = await _company(db, phone="+573001112233", google_maps_url="https://maps/x")
    await db.commit()

    response = await client.get(f"{API}/companies/{company.id}/dossier")

    assert response.status_code == 200
    body = response.json()
    assert body["company"]["name"] == "Panadería Ana"
    assert body["company"]["google_maps_url"] == "https://maps/x"
    assert body["web_available"] is False


@pytest.mark.asyncio
async def test_api_dossier_de_empresa_inexistente_da_404(client: AsyncClient) -> None:
    response = await client.get(f"{API}/companies/{'0' * 8}-0000-0000-0000-{'0' * 12}/dossier")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_incluye_contactos_y_senales(client: AsyncClient, db: AsyncSession) -> None:
    from app.core.enums import SourceType
    from app.models.company import CompanySignal
    from app.models.contact import Contact

    db.add(AppSettings(id=1))
    company = await _company(db)
    db.add(CompanySignal(company_id=company.id, signal_key="no_website", source=SourceType.WEBSITE))
    db.add(
        Contact(
            company_id=company.id,
            full_name="Camila Restrepo",
            email="camila@ana.co",
            is_primary=True,
        )
    )
    await db.commit()

    response = await client.get(f"{API}/companies/{company.id}/dossier")

    body = response.json()
    assert body["contacts"][0]["display_name"] == "Camila Restrepo"
    assert "No tiene sitio web" in body["signal_labels"]
