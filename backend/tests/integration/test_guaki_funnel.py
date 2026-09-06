"""Embudo comercial de Guaki (LOOP-26) contra Postgres real.

Verifica que /guaki/funnel devuelve métricas reales: detectados, oportunidades,
contactados, interesados, registrados, gratuitos, pagos, conversión y MRR.
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


async def _seed(db: AsyncSession, slug: str, phone: str) -> Lead:
    await _seed_stages(db)
    now = datetime.now(UTC)
    service = Service(name=f"Guaki panaderías {slug}", target_industries=["Panadería"])
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
        name=f"Panadería {slug}",
        category="Panadería",
        city="Soacha",
        dedupe_key=slug,
        first_extracted_at=now,
        last_extracted_at=now,
        data_quality_score=45,
        rating=Decimal("4.4"),
        reviews_count=38,
        phone=phone,
        email=f"info@{slug}.co",
    )
    db.add(company)
    await db.flush()
    db.add(CompanySignal(company_id=company.id, signal_key="no_website", source=SourceType.WEBSITE))
    contact = Contact(
        company_id=company.id,
        full_name=f"Contacto {slug}",
        email=f"maria@{slug}.co",
        email_verified=VerificationStatus.MX_OK,
        source=SourceType.WEBSITE,
    )
    db.add(contact)
    await db.flush()
    return await LeadService(db).create(
        company_id=company.id, service_id=service.id, contact_id=contact.id
    )


async def test_guaki_funnel_con_vinculos(db: AsyncSession, client: AsyncClient) -> None:
    l1 = await _seed(db, "esquina", "+573001112233")
    l2 = await _seed(db, "dospan", "+573001112244")
    await db.commit()

    # l1 → registrado con plan PRESENCIA (pago) · l2 → registrado GRATIS.
    await client.post(
        f"/api/v1/guaki/prospects/{l1.id}/link",
        json={
            "guaki_business_id": "biz-1",
            "match_state": "MATCHED",
            "registered_at": "2026-08-10T10:00:00Z",
            "plan": "PRESENCIA",
        },
    )
    await client.post(
        f"/api/v1/guaki/prospects/{l2.id}/link",
        json={
            "guaki_business_id": "biz-2",
            "match_state": "MATCHED",
            "registered_at": "2026-08-11T10:00:00Z",
            "plan": "GRATIS",
        },
    )

    resp = await client.get("/api/v1/guaki/funnel")
    assert resp.status_code == 200
    f = resp.json()

    assert f["detectadas"] == 2
    assert f["high"] >= 2  # sin web + contacto + info parcial → HIGH
    assert f["registrados"] == 2
    assert f["pagos"] == 1
    assert f["gratuitos"] == 1
    assert f["conversion_registro"]["numerator"] == 2
    assert f["conversion_registro"]["denominator"] == 2
    assert f["conversion_registro"]["value"] == 100.0
    assert f["upgrades"] == 0
    assert f["mrr"] == 0
    assert f["mrr_status"] == "PENDING_PAYMENTS"


async def test_guaki_funnel_vacio(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/guaki/funnel")
    assert resp.status_code == 200
    f = resp.json()
    assert f["detectadas"] == 0
    assert f["conversion_registro"]["value"] == 0
    assert f["conversion_registro"]["denominator"] == 0
