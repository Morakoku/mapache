"""Puente Mapache → Guaki (LOOP-23): el endpoint de prospectos devuelve las
empresas con su clasificación del Guaki Filter y un resumen del embudo, sobre
datos reales en Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EmailStatus, SourceType, VerificationStatus
from app.models.company import Company, CompanySignal
from app.models.contact import Contact
from app.models.email import EmailMessage
from app.models.lead import Lead
from app.models.pipeline import DEFAULT_STAGES, PipelineStage
from app.models.search import Search
from app.models.service import Service
from app.services.lead_svc import LeadService
from app.services.scoring_svc import ScoringService


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


async def _seed(db: AsyncSession, *, email: bool = True) -> Lead:
    """Panadería en Soacha sin web, con redes, contacto y fricción → HIGH."""
    await _seed_stages(db)
    now = datetime.now(UTC)

    service = Service(
        name="VEYRA clínicas",
        target_industries=["Panadería"],
        opportunity_signals=["no_website"],
    )
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
        data_quality_score=45,
        rating=Decimal("4.4"),
        reviews_count=38,
        phone="+573001112233",
        email="info@laesquina.co" if email else None,
    )
    db.add(company)
    await db.flush()

    db.add(CompanySignal(company_id=company.id, signal_key="no_website", source=SourceType.WEBSITE))
    db.add(
        CompanySignal(
            company_id=company.id,
            signal_key="instagram",
            source=SourceType.INSTAGRAM,
        )
    )

    contact = Contact(
        company_id=company.id,
        full_name="María Pérez",
        email="maria@laesquina.co" if email else None,
        email_verified=VerificationStatus.MX_OK,
        source=SourceType.WEBSITE,
    )
    db.add(contact)
    await db.flush()

    return await LeadService(db).create(
        company_id=company.id, service_id=service.id, contact_id=contact.id
    )


async def test_guaki_prospects_devuelve_prospecto_clasificado(
    db: AsyncSession, client: AsyncClient
) -> None:
    lead = await _seed(db)
    await ScoringService(db).score_leads([lead.id])
    await db.commit()

    resp = await client.get("/api/v1/guaki/prospects")
    assert resp.status_code == 200
    body = resp.json()

    assert body["summary"]["detectadas"] >= 1
    assert body["items"]

    prospect = body["items"][0]
    assert prospect["negocio"] == "Panadería La Esquina"
    assert prospect["categoria"] == "Panadería"
    assert prospect["ciudad"] == "Soacha"
    assert prospect["web"] is None
    assert prospect["redes"] == ["instagram"]
    assert prospect["email"] == "maria@laesquina.co"
    assert prospect["telefono"] == "+573001112233"
    assert prospect["oportunidad"] == "HIGH"
    assert prospect["razones"]
    assert any("no se encontró sitio web" in r for r in prospect["razones"])
    assert prospect["contacto_realizado"] is False
    assert prospect["respuesta"] is False
    assert prospect["registro_en_guaki"] is None
    assert prospect["plan_actual"] is None
    assert "fecha_deteccion" in prospect
    assert prospect["estado_comercial"]


async def test_guaki_summary_cuenta_oportunidades(db: AsyncSession, client: AsyncClient) -> None:
    await _seed(db)
    await db.commit()

    resp = await client.get("/api/v1/guaki/prospects")
    body = resp.json()
    summary = body["summary"]

    assert summary["detectadas"] >= 1
    assert summary["alta"] >= 1
    assert summary["con_email"] >= 1
    assert summary["redes_sin_web"] >= 1
    assert summary["registrados"] == 0
    assert summary["conversiones"] == 0


async def test_guaki_contacto_realizado_cuenta(db: AsyncSession, client: AsyncClient) -> None:
    lead = await _seed(db)
    db.add(
        EmailMessage(
            lead_id=lead.id,
            from_email="hola@guaki.co",
            to_email="maria@laesquina.co",
            subject="Hola",
            status=EmailStatus.SENT,
            direction="OUTBOUND",
        )
    )
    await db.commit()

    resp = await client.get("/api/v1/guaki/prospects")
    body = resp.json()
    prospect = body["items"][0]

    assert prospect["contacto_realizado"] is True
    assert body["summary"]["contactados"] >= 1
