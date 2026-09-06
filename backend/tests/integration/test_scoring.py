"""Scoring contra Postgres real.

Lo que el test unitario no puede cubrir: que el servicio junte bien los datos
de cuatro tablas (empresa, señales, contacto, búsquedas) y que el número acabe
guardado con su desglose.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Direction, EmailStatus, SourceType, VerificationStatus
from app.models.activity import Activity
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
    """Siembra las 14 etapas una sola vez por test.

    Varios tests siembran dos prospectos para compararlos; las etapas son
    globales, así que hay que comprobar antes de insertar.
    """
    existing = await db.scalar(select(PipelineStage.id).limit(1))
    if existing is not None:
        return

    stages = [
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
    db.add_all(stages)
    await db.flush()


async def _seed(
    db: AsyncSession,
    *,
    city: str = "Medellín",
    slug: str = "espiga",
    **company_kw: object,
) -> Lead:
    await _seed_stages(db)
    now = datetime.now(UTC)

    service = Service(
        name=f"Diseño de páginas web ({slug})",
        target_industries=["Panadería"],
        opportunity_signals=["no_website"],
    )
    db.add(service)
    await db.flush()

    db.add(
        Search(
            service_id=service.id,
            name="Panaderías Medellín",
            business_type="panaderia",
            city="Medellín",
            source=SourceType.GOOGLE_MAPS,
        )
    )

    company = Company(
        name=f"Panadería {slug}",
        category="Panadería",
        city=city,
        dedupe_key=slug,
        first_extracted_at=now,
        last_extracted_at=now,
        data_quality_score=80,
        **company_kw,  # type: ignore[arg-type]
    )
    db.add(company)
    await db.flush()

    db.add(CompanySignal(company_id=company.id, signal_key="no_website", source=SourceType.WEBSITE))
    contact = Contact(
        company_id=company.id,
        full_name="Ana Restrepo",
        email=f"ana@{slug}.co",
        email_verified=VerificationStatus.MX_OK,
        source=SourceType.WEBSITE,
    )
    db.add(contact)
    await db.flush()

    return await LeadService(db).create(
        company_id=company.id, service_id=service.id, contact_id=contact.id
    )


async def test_scoring_guarda_numero_y_desglose(db: AsyncSession) -> None:
    lead = await _seed(db, rating=Decimal("4.6"), reviews_count=140)

    result = await ScoringService(db).score_leads([lead.id])
    await db.refresh(lead)

    assert result.scored == 1
    assert lead.score > 0
    assert lead.score_computed_at is not None
    dimensions = lead.score_breakdown["dimensions"]
    assert set(dimensions) == {
        "fit",
        "opportunity",
        "contactability",
        "data_quality",
        "intent",
        "timing",
    }
    assert all(detail["reasons"] for detail in dimensions.values())


async def test_la_ciudad_objetivo_sale_de_las_busquedas_del_servicio(db: AsyncSession) -> None:
    """No hay campo "ciudades objetivo": son las ciudades donde has buscado."""
    en_ciudad = await _seed(db, city="Medellín", slug="cerca")
    await ScoringService(db).score_leads([en_ciudad.id])
    await db.refresh(en_ciudad)

    fuera = await _seed(db, city="Barranquilla", slug="lejos")
    await ScoringService(db).score_leads([fuera.id])
    await db.refresh(fuera)

    assert (
        en_ciudad.score_breakdown["dimensions"]["fit"]["value"]
        > (fuera.score_breakdown["dimensions"]["fit"]["value"])
    )


async def test_un_rebote_anula_la_contactabilidad(db: AsyncSession) -> None:
    """El estado real del envío pesa más que el estado del email en la ficha."""
    lead = await _seed(db)
    db.add(
        EmailMessage(
            lead_id=lead.id,
            direction=Direction.OUTBOUND,
            from_email="yo@midominio.co",
            to_email="ana@espiga.co",
            subject="Hola",
            status=EmailStatus.BOUNCED,
        )
    )
    await db.flush()

    await ScoringService(db).score_leads([lead.id])
    await db.refresh(lead)

    contactabilidad = lead.score_breakdown["dimensions"]["contactability"]
    assert contactabilidad["value"] == 0
    assert "rebotó" in contactabilidad["reasons"][0]


async def test_un_cambio_grande_de_score_deja_actividad(db: AsyncSession) -> None:
    """Si el número se mueve mucho, hay que poder saber cuándo y por qué."""
    lead = await _seed(db, rating=Decimal("4.8"), reviews_count=200)
    await ScoringService(db).score_leads([lead.id])
    await db.flush()

    result = await db.execute(select(Activity).where(Activity.lead_id == lead.id))
    titles = [a.title for a in result.scalars().all()]
    assert any("score" in title for title in titles)


async def test_score_all_recalcula_todo_el_embudo(db: AsyncSession) -> None:
    lead = await _seed(db)
    result = await ScoringService(db).score_all()
    assert result.scored >= 1

    await db.refresh(lead)
    assert lead.score_computed_at is not None


async def test_un_dato_viejo_puntua_menos_en_frescura(db: AsyncSession) -> None:
    reciente = await _seed(db, slug="nueva")
    await ScoringService(db).score_leads([reciente.id])
    await db.refresh(reciente)

    viejo = await _seed(db, slug="vieja")
    viejo_company = await db.get(Company, viejo.company_id)
    assert viejo_company is not None
    viejo_company.first_extracted_at = datetime.now(UTC) - timedelta(days=120)
    await db.flush()

    await ScoringService(db).score_leads([viejo.id])
    await db.refresh(viejo)

    assert (
        viejo.score_breakdown["dimensions"]["timing"]["value"]
        < reciente.score_breakdown["dimensions"]["timing"]["value"]
    )
