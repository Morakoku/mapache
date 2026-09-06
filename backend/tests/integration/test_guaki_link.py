"""Vínculo Prospecto↔Negocio (LOOP-24) contra Postgres real.

Cubre: registrar/actualizar el vínculo de un prospecto, verlo en el endpoint de
prospectos, y buscar el prospecto que corresponde a un negocio de Guaki.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SourceType, VerificationStatus
from app.models.company import Company, CompanySignal
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.pipeline import DEFAULT_STAGES, PipelineStage
from app.models.search import Search
from app.models.service import Service
from app.services.lead_svc import LeadService


async def _seed_stages(db: AsyncSession) -> None:
    existing = await db.scalar(select(PipelineStage.id).limit(1))
    if existing is not None:
        return
    db.add_all(
        [
            PipelineStage(
                name=s["name"],
                stage_key=s["stage_key"],
                stage_type=s["stage_type"],
                position=index,
                color=s.get("color", "#64748b"),
                is_default=s.get("is_default", False),
                is_won=s.get("is_won", False),
                is_lost=s.get("is_lost", False),
                is_system=True,
                auto_advance_on=s.get("auto_advance_on", []),
            )
            for index, s in enumerate(DEFAULT_STAGES, start=1)
        ]
    )
    await db.flush()


async def _seed(db: AsyncSession) -> Lead:
    await _seed_stages(db)
    now = datetime.now(UTC)

    service = Service(name="Guaki panaderías", target_industries=["Panadería"])
    db.add(service)
    await db.flush()

    db.add(
        Search(
            service_id=service.id,
            name="Panaderías Soacha",
            business_type="panaderia",
            city="Soacha",
            source=SourceType.GOOGLE_MAPS,
        )
    )

    company = Company(
        name="Panadería La Esquina",
        category="Panadería",
        city="Soacha",
        dedupe_key="la-esquina",
        first_extracted_at=now,
        last_extracted_at=now,
        data_quality_score=60,
        rating=Decimal("4.4"),
        reviews_count=38,
        phone="+573001112233",
        website="laesquina.co",
        email="info@laesquina.co",
    )
    db.add(company)
    await db.flush()

    db.add(CompanySignal(company_id=company.id, signal_key="no_website", source=SourceType.WEBSITE))

    contact = Contact(
        company_id=company.id,
        full_name="María Pérez",
        email="maria@laesquina.co",
        email_verified=VerificationStatus.MX_OK,
        source=SourceType.WEBSITE,
    )
    db.add(contact)
    await db.flush()

    return await LeadService(db).create(
        company_id=company.id, service_id=service.id, contact_id=contact.id
    )


async def test_link_prospect_upsert_y_se_ve_en_prospectos(
    db: AsyncSession, client: AsyncClient
) -> None:
    lead = await _seed(db)
    await db.commit()

    resp = await client.post(
        f"/api/v1/guaki/prospects/{lead.id}/link",
        json={
            "guaki_business_id": "guaki-biz-abc",
            "match_state": "MATCHED",
            "confidence": 95,
            "signals": ["email", "telefono"],
            "registered_at": "2026-08-10T10:00:00Z",
            "plan": "PRESENCIA",
            "status": "active",
        },
    )
    assert resp.status_code == 200
    vinculo = resp.json()["vinculo"]
    assert vinculo["state"] == "MATCHED"
    assert vinculo["guaki_business_id"] == "guaki-biz-abc"
    assert vinculo["plan"] == "PRESENCIA"
    assert vinculo["registered_at"]

    prospects = await client.get("/api/v1/guaki/prospects")
    item = prospects.json()["items"][0]
    assert item["vinculo"] is not None
    assert item["vinculo"]["state"] == "MATCHED"
    assert item["vinculo"]["guaki_business_id"] == "guaki-biz-abc"


async def test_link_404_lead_inexistente(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/guaki/prospects/00000000-0000-0000-0000-000000000001/link",
        json={"guaki_business_id": "x"},
    )
    assert resp.status_code == 404


async def test_link_match_encuentra_prospecto(db: AsyncSession, client: AsyncClient) -> None:
    await _seed(db)
    await db.commit()

    resp = await client.post(
        "/api/v1/guaki/link/match",
        json={
            "name": "Panadería La Esquina",
            "domain": "www.laesquina.co",
            "email": "info@laesquina.co",
            "phone": "+573001112233",
            "city": "Soacha",
        },
    )
    assert resp.status_code == 200
    candidates = resp.json()["candidates"]
    assert candidates
    top = candidates[0]
    assert top["match_state"] == "MATCHED"
    assert top["negocio"] == "Panadería La Esquina"
