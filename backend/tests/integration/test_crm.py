"""Flujo del CRM contra Postgres real: empresa -> lead -> embudo.

Lo que más importa aquí es que `lead_stage_history` quede bien escrito: sin
él, el embudo del Módulo 20 no se puede calcular y el fallo sería silencioso.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActivityType, LeadStatus, StageType
from app.core.exceptions import ConflictError, ValidationError
from app.models.activity import Activity
from app.models.company import Company
from app.models.lead import LeadStageHistory
from app.models.pipeline import DEFAULT_STAGES, PipelineStage
from app.models.service import Service
from app.services.contact_svc import ContactService
from app.services.lead_svc import LeadService
from app.services.pipeline_svc import PipelineService


async def _seed_stages(db: AsyncSession) -> list[PipelineStage]:
    """Siembra las 14 etapas.

    El esquema de tests se crea con `create_all`, que no ejecuta el seed de la
    migración, así que hay que replicarlo aquí.
    """
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
    return stages


async def _seed_company(db: AsyncSession, name: str = "Restaurante El Sabor", **kw) -> Company:
    now = datetime.now(UTC)
    company = Company(
        name=name,
        city="Medellín",
        dedupe_key=f"key-{name}",
        first_extracted_at=now,
        last_extracted_at=now,
        **kw,
    )
    db.add(company)
    await db.flush()
    return company


async def _seed_service(db: AsyncSession) -> Service:
    service = Service(name="Desarrollo Web", opportunity_signals=["no_website"])
    db.add(service)
    await db.flush()
    return service


# ------------------------------------------------------------------ pipeline


async def test_stage_seed_has_fourteen_stages(db: AsyncSession) -> None:
    await _seed_stages(db)
    stages = await PipelineService(db).list_stages()

    assert len(stages) == 14
    assert stages[0].stage_type is StageType.NEW
    assert stages[-1].stage_type is StageType.LOST
    assert [s.position for s in stages] == list(range(1, 15))


async def test_stage_type_is_immutable(db: AsyncSession) -> None:
    """Renombrar una etapa es libre; cambiarle el tipo reescribiría el embudo."""
    stages = await _seed_stages(db)
    service = PipelineService(db)

    renamed = await service.update(stages[0].id, {"name": "Primer toque", "color": "#123456"})
    assert renamed.name == "Primer toque"
    assert renamed.stage_type is StageType.NEW, "el tipo no cambia al renombrar"

    with pytest.raises(ValidationError) as exc:
        await service.update(stages[0].id, {"stage_type": StageType.WON})
    assert exc.value.code == "STAGE_TYPE_IMMUTABLE"


async def test_system_stages_cannot_be_deleted(db: AsyncSession) -> None:
    stages = await _seed_stages(db)

    with pytest.raises(ValidationError) as exc:
        await PipelineService(db).delete(stages[0].id, move_to_stage_id=None)
    assert exc.value.code == "STAGE_IS_SYSTEM"


async def test_deleting_a_stage_with_leads_requires_a_destination(db: AsyncSession) -> None:
    """Nunca se borran prospectos por reorganizar el tablero."""
    stages = await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)

    custom = await PipelineService(db).create(
        {"name": "Mi etapa", "stage_type": StageType.QUALIFIED, "color": "#abcdef"}
    )
    lead = await LeadService(db).create(
        company_id=company.id, service_id=service.id, stage_id=custom.id
    )

    with pytest.raises(ConflictError) as exc:
        await PipelineService(db).delete(custom.id, move_to_stage_id=None)
    assert exc.value.code == "STAGE_HAS_LEADS"
    assert exc.value.details["lead_count"] == 1

    moved = await PipelineService(db).delete(custom.id, move_to_stage_id=stages[0].id)
    assert moved == 1
    assert lead.stage_id == stages[0].id


async def test_reorder_requires_every_stage(db: AsyncSession) -> None:
    stages = await _seed_stages(db)
    service = PipelineService(db)

    with pytest.raises(ValidationError) as exc:
        await service.reorder([stages[0].id, stages[1].id])
    assert exc.value.code == "REORDER_INCOMPLETE"

    reversed_ids = [s.id for s in reversed(stages)]
    result = await service.reorder(reversed_ids)
    assert [s.id for s in result] == reversed_ids
    assert [s.position for s in result] == list(range(1, 15))


# ------------------------------------------------------------------ leads


async def test_lead_starts_in_contact_found_when_email_is_known(db: AsyncSession) -> None:
    """Un prospecto con email ya tiene el contacto encontrado; hacer que el
    usuario lo arrastre a mano sería trabajo inútil."""
    await _seed_stages(db)
    company = await _seed_company(db, email="hola@elsabor.com")
    service = await _seed_service(db)

    lead = await LeadService(db).create(company_id=company.id, service_id=service.id)
    stage = await PipelineService(db).get_or_404(lead.stage_id)

    assert stage.stage_type is StageType.CONTACT_FOUND


async def test_lead_without_email_starts_at_the_beginning(db: AsyncSession) -> None:
    await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)

    lead = await LeadService(db).create(company_id=company.id, service_id=service.id)
    stage = await PipelineService(db).get_or_404(lead.stage_id)

    assert stage.stage_type is StageType.NEW


async def test_one_lead_per_company_and_service(db: AsyncSession) -> None:
    await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    await lead_service.create(company_id=company.id, service_id=service.id)

    with pytest.raises(ConflictError) as exc:
        await lead_service.create(company_id=company.id, service_id=service.id)
    assert exc.value.code == "LEAD_ALREADY_EXISTS"


async def test_same_company_can_have_leads_for_different_services(db: AsyncSession) -> None:
    """El ejemplo del Módulo 7: Restaurante X con lead de web, de fotografía
    y de video."""
    await _seed_stages(db)
    company = await _seed_company(db)
    web = await _seed_service(db)
    foto = Service(name="Fotografía")
    db.add(foto)
    await db.flush()

    lead_service = LeadService(db)
    a = await lead_service.create(company_id=company.id, service_id=web.id)
    b = await lead_service.create(company_id=company.id, service_id=foto.id)

    assert a.id != b.id


async def test_bulk_create_skips_existing_without_aborting(db: AsyncSession) -> None:
    """Seleccionar 50 empresas de las que 3 ya son prospectos es lo normal."""
    await _seed_stages(db)
    service = await _seed_service(db)
    companies = [await _seed_company(db, name=f"Empresa {i}") for i in range(4)]

    lead_service = LeadService(db)
    await lead_service.create(company_id=companies[0].id, service_id=service.id)

    result = await lead_service.create_bulk(
        company_ids=[c.id for c in companies], service_id=service.id
    )

    assert len(result.created) == 3
    assert result.skipped_existing == 1


# ------------------------------------------------------------------ historial


async def test_moving_stage_writes_history(db: AsyncSession) -> None:
    """Sin historial no hay embudo, solo un inventario de dónde está cada lead."""
    stages = await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    await lead_service.move_stage(lead, stages[3].id, reason="Le escribí")

    rows = await db.execute(
        select(LeadStageHistory)
        .where(LeadStageHistory.lead_id == lead.id)
        .order_by(LeadStageHistory.entered_at)
    )
    history = list(rows.scalars().all())

    assert len(history) == 2, "creación + movimiento"
    assert history[0].from_stage_type is None, "la entrada inicial no viene de ninguna etapa"
    assert history[0].to_stage_type is StageType.NEW
    assert history[1].from_stage_type is StageType.NEW
    assert history[1].to_stage_type is StageType.CONTACTED
    assert history[1].reason == "Le escribí"
    assert history[1].duration_seconds is not None, "debe medir el tiempo en la etapa anterior"


async def test_stage_change_creates_activity(db: AsyncSession) -> None:
    stages = await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    await lead_service.move_stage(lead, stages[5].id)

    rows = await db.execute(
        select(Activity).where(
            Activity.lead_id == lead.id,
            Activity.activity_type == ActivityType.STAGE_CHANGED,
        )
    )
    activity = rows.scalar_one()
    assert "Respondió" in activity.title
    assert activity.activity_metadata["to"] == "replied"


async def test_history_survives_stage_deletion(db: AsyncSession) -> None:
    """El tipo se desnormaliza en el historial: si el usuario borra una etapa,
    las métricas del embudo siguen siendo calculables."""
    stages = await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    pipeline = PipelineService(db)

    custom = await pipeline.create({"name": "Temporal", "stage_type": StageType.QUALIFIED})
    lead = await LeadService(db).create(company_id=company.id, service_id=service.id)
    await LeadService(db).move_stage(lead, custom.id)

    await pipeline.delete(custom.id, move_to_stage_id=stages[0].id)
    await db.flush()

    rows = await db.execute(
        select(LeadStageHistory.to_stage_type).where(LeadStageHistory.lead_id == lead.id)
    )
    types = [r[0] for r in rows]
    assert StageType.QUALIFIED in types, "el tipo sobrevive al borrado de la etapa"


# ------------------------------------------------------------------ auto-avance


async def test_auto_advance_moves_forward_only(db: AsyncSession) -> None:
    stages = await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    await lead_service.auto_advance(lead, "EMAIL_OPENED")

    stage = await PipelineService(db).get_or_404(lead.stage_id)
    assert stage.stage_type is StageType.OPENED

    # Un evento de una etapa anterior no debe hacerlo retroceder.
    await lead_service.auto_advance(lead, "EMAIL_SENT")
    stage = await PipelineService(db).get_or_404(lead.stage_id)
    assert stage.stage_type is StageType.OPENED, "nunca retrocede"

    assert stages  # silencia el linter sobre la variable sin usar


async def test_auto_advance_never_decides_commercial_stages(db: AsyncSession) -> None:
    """El sistema llega hasta CONVERSACIÓN. Que alguien abra un correo no lo
    convierte en 'interesado' — esa es una lectura comercial del usuario."""
    await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    pipeline = PipelineService(db)

    interested = await pipeline.get_by_type(StageType.INTERESTED)
    assert interested is not None
    interested.auto_advance_on = ["EMAIL_OPENED"]
    await db.flush()

    lead = await LeadService(db).create(company_id=company.id, service_id=service.id)
    await LeadService(db).auto_advance(lead, "EMAIL_OPENED")

    stage = await pipeline.get_or_404(lead.stage_id)
    assert stage.stage_type is not StageType.INTERESTED
    assert stage.stage_type is StageType.OPENED


async def test_auto_advance_ignores_closed_leads(db: AsyncSession) -> None:
    await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    await lead_service.mark_lost(lead, reason="No están interesados")

    await lead_service.auto_advance(lead, "EMAIL_OPENED")

    assert lead.status is LeadStatus.LOST
    stage = await PipelineService(db).get_or_404(lead.stage_id)
    assert stage.is_lost


# ------------------------------------------------------------------ cierre


async def test_win_sets_status_and_value(db: AsyncSession) -> None:
    await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    await lead_service.mark_won(lead, value=3500000, note="Cerrado por WhatsApp")

    assert lead.status is LeadStatus.WON
    assert lead.won_at is not None
    assert float(lead.estimated_value) == 3500000


async def test_reopening_a_closed_lead_clears_the_closure(db: AsyncSession) -> None:
    stages = await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    await lead_service.mark_lost(lead, reason="Sin presupuesto")
    assert lead.status is LeadStatus.LOST

    await lead_service.move_stage(lead, stages[7].id, reason="Volvieron a escribir")

    assert lead.status is LeadStatus.OPEN
    assert lead.lost_at is None


# ------------------------------------------------------------------ engagement


async def test_engagement_bands(db: AsyncSession) -> None:
    await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    assert lead.engagement_band == "FRIO"

    await lead_service.add_engagement(lead, "EMAIL_OPENED")
    assert lead.engagement_score == 5
    assert lead.engagement_band == "BAJO"

    await lead_service.add_engagement(lead, "EMAIL_CLICKED")
    assert lead.engagement_score == 25
    assert lead.engagement_band == "MEDIO"

    await lead_service.add_engagement(lead, "EMAIL_REPLIED")
    assert lead.engagement_band == "ALTO"


# ------------------------------------------------------------------ contactos


async def test_contact_email_is_verified_on_create(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_mx(domain: str, timeout_s: float = 5.0) -> bool:
        return True

    monkeypatch.setattr("app.enrichment.email_verifier.has_mx_record", fake_mx)

    company = await _seed_company(db)
    contact = await ContactService(db).create(
        {"company_id": company.id, "first_name": "Juan", "email": "Juan.Perez@ElSabor.com"}
    )

    assert contact.email == "juan.perez@elsabor.com", "se normaliza a minúsculas"
    assert contact.is_role_email is False
    assert contact.is_contactable is True


async def test_role_email_is_flagged(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_mx(domain: str, timeout_s: float = 5.0) -> bool:
        return True

    monkeypatch.setattr("app.enrichment.email_verifier.has_mx_record", fake_mx)

    company = await _seed_company(db)
    contact = await ContactService(db).create(
        {"company_id": company.id, "email": "info@elsabor.com"}
    )

    assert contact.is_role_email is True


async def test_duplicate_contact_email_in_same_company_is_rejected(db: AsyncSession) -> None:
    company = await _seed_company(db)
    service = ContactService(db)

    await service.create({"company_id": company.id, "email": "juan@elsabor.com"})

    with pytest.raises(ConflictError) as exc:
        await service.create({"company_id": company.id, "email": "JUAN@elsabor.com"})
    assert exc.value.code == "CONTACT_EMAIL_TAKEN"


async def test_setting_primary_unsets_the_others(db: AsyncSession) -> None:
    company = await _seed_company(db)
    service = ContactService(db)

    a = await service.create({"company_id": company.id, "email": "a@x.com", "is_primary": True})
    b = await service.create({"company_id": company.id, "email": "b@x.com"})

    await service.set_primary(b.id)

    assert b.is_primary is True
    assert a.is_primary is False


async def test_lead_picks_a_contactable_primary_contact(db: AsyncSession) -> None:
    """El lead debe apuntar a alguien a quien se pueda escribir, no al primero
    que aparezca."""
    await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    contacts = ContactService(db)

    bloqueado = await contacts.create({"company_id": company.id, "email": "no@x.com"})
    bloqueado.do_not_contact = True
    await db.flush()
    bueno = await contacts.create({"company_id": company.id, "email": "si@x.com"})

    lead = await LeadService(db).create(company_id=company.id, service_id=service.id)

    assert lead.contact_id == bueno.id


# ------------------------------------------------------------------ tablero


async def test_board_has_one_column_per_stage(db: AsyncSession) -> None:
    await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    await LeadService(db).create(company_id=company.id, service_id=service.id)

    board = await LeadService(db).board()

    assert len(board) == 14
    with_leads = [col for col in board if col["total"] > 0]
    assert len(with_leads) == 1
    assert with_leads[0]["stage"].stage_type is StageType.NEW


async def test_funnel_counts_leads_that_passed_through_each_stage(db: AsyncSession) -> None:
    """La consulta del embudo del Módulo 20 cuenta quién *pasó* por cada
    etapa, no quién está ahí ahora. Es toda la diferencia entre un embudo y un
    inventario."""
    stages = await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    await lead_service.move_stage(lead, stages[3].id)  # CONTACTED
    await lead_service.move_stage(lead, stages[4].id)  # OPENED
    await lead_service.move_stage(lead, stages[5].id)  # REPLIED

    reached = await db.execute(
        select(
            LeadStageHistory.to_stage_type,
            func.count(func.distinct(LeadStageHistory.lead_id)),
        ).group_by(LeadStageHistory.to_stage_type)
    )
    counts = dict(reached.all())

    # Está en REPLIED, pero pasó por las cuatro etapas.
    assert counts[StageType.NEW] == 1
    assert counts[StageType.CONTACTED] == 1
    assert counts[StageType.OPENED] == 1
    assert counts[StageType.REPLIED] == 1


# ------------------------------------------------------------------ regresiones


async def test_moving_a_lead_returns_the_new_stage_not_the_old_one(db: AsyncSession) -> None:
    """Regresión: con `expire_on_commit=False`, la relación `stage` quedaba
    cacheada y la respuesta del POST devolvía la columna anterior. En el
    Kanban eso significa arrastrar una tarjeta y ver cómo vuelve a su sitio.
    """
    stages = await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    await lead_service.move_stage(lead, stages[5].id)  # REPLIED

    releido = await lead_service.get_or_404(lead.id)

    assert releido.stage.stage_type is StageType.REPLIED
    assert releido.stage.name == "Respondió"


async def test_activity_schema_reads_the_column_not_sqlalchemy_metadata(
    db: AsyncSession,
) -> None:
    """Regresión: `ActivityOut` usaba `alias="metadata"`, y Pydantic con
    `from_attributes` leía `Activity.metadata` — el objeto `MetaData` de
    SQLAlchemy— en vez de la columna. El timeline devolvía 500.
    """
    from app.schemas.crm import ActivityOut

    stages = await _seed_stages(db)
    company = await _seed_company(db)
    service = await _seed_service(db)
    lead_service = LeadService(db)

    lead = await lead_service.create(company_id=company.id, service_id=service.id)
    await lead_service.move_stage(lead, stages[3].id)
    await db.flush()

    rows = await db.execute(
        select(Activity).where(
            Activity.lead_id == lead.id,
            Activity.activity_type == ActivityType.STAGE_CHANGED,
        )
    )
    activity = rows.scalar_one()

    out = ActivityOut.model_validate(activity)
    assert out.activity_metadata == {"from": "prospect", "to": "first_contact"}

    # Y en JSON se publica como `metadata`, que es lo que consume la UI.
    assert out.model_dump(by_alias=True)["metadata"] == out.activity_metadata
