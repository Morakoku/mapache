"""Fase 9 — métricas contra números calculados a mano.

Una métrica mal calculada es peor que no tenerla: el usuario toma decisiones
comerciales con ella. Por eso cada tasa se siembra con datos exactos y se
comprueba contra la fracción escrita en el propio test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    ActorType,
    Direction,
    EmailStatus,
    LeadStatus,
    MailProviderType,
    StageType,
)
from app.models.company import Company
from app.models.email import EmailMessage, EmailTemplate, SuppressionEntry
from app.models.lead import Lead, LeadStageHistory
from app.models.pipeline import DEFAULT_STAGES, PipelineStage
from app.models.service import Service
from app.services.metrics_svc import MetricsService, Period

API = "/api/v1"


async def _stages(db: AsyncSession) -> list[PipelineStage]:
    stages = [
        PipelineStage(
            name=s["name"],
            stage_key=s["stage_key"],
            stage_type=s["stage_type"],
            position=index,
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


async def _company(
    db: AsyncSession, name: str, *, city: str = "Medellín", email: str | None = None
):  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    company = Company(
        name=name,
        city=city,
        email=email,
        dedupe_key=f"key-{name}-{uuid.uuid4().hex[:6]}",
        first_extracted_at=now,
        last_extracted_at=now,
    )
    db.add(company)
    await db.flush()
    return company


async def _lead(db: AsyncSession, company: Company, service: Service, stage: PipelineStage) -> Lead:
    lead = Lead(
        company_id=company.id,
        service_id=service.id,
        stage_id=stage.id,
        last_activity_at=datetime.now(UTC),
    )
    db.add(lead)
    await db.flush()
    return lead


def _email(
    lead: Lead,
    *,
    opened: bool = False,
    clicked: bool = False,
    replied: bool = False,
    bounced: bool = False,
    template_id: uuid.UUID | None = None,
) -> EmailMessage:
    """Un correo saliente con el resultado ya escrito.

    Se siembra el estado final en vez de recorrer todo el flujo: aquí lo que
    se prueba es la aritmética, no el envío.
    """
    now = datetime.now(UTC)
    return EmailMessage(
        lead_id=lead.id,
        direction=Direction.OUTBOUND,
        from_email="daniel@midominio.co",
        to_email=f"contacto-{uuid.uuid4().hex[:6]}@empresa.co",
        subject="Una idea",
        status=EmailStatus.BOUNCED if bounced else EmailStatus.SENT,
        provider=MailProviderType.SMTP,
        template_id=template_id,
        sent_at=now,
        # `open_count` ya viene sin las aperturas de bots: el pixel las
        # registra pero no las suma (§11.3).
        open_count=1 if opened else 0,
        opened_at=now if opened else None,
        click_count=1 if clicked else 0,
        clicked_at=now if clicked else None,
        replied_at=now if replied else None,
        bounced_at=now if bounced else None,
        bounce_type="hard" if bounced else None,
    )


async def _seed_emails(db: AsyncSession, lead: Lead, **counts: int) -> None:
    """Siembra correos con los resultados indicados.

    `_seed_emails(db, lead, plain=6, opened=2, clicked=1, replied=1)` = 10
    correos: 6 sin nada, 2 abiertos, 1 con click, 1 respondido.
    """
    for _ in range(counts.get("plain", 0)):
        db.add(_email(lead))
    for _ in range(counts.get("opened", 0)):
        db.add(_email(lead, opened=True))
    for _ in range(counts.get("clicked", 0)):
        db.add(_email(lead, opened=True, clicked=True))
    for _ in range(counts.get("replied", 0)):
        db.add(_email(lead, opened=True, replied=True))
    for _ in range(counts.get("bounced", 0)):
        db.add(_email(lead, bounced=True))
    await db.flush()


# ------------------------------------------------------------------ tasas


@pytest.mark.asyncio
async def test_las_tasas_cuadran_con_la_fraccion(db: AsyncSession) -> None:
    stages = await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    lead = await _lead(db, await _company(db, "Finca"), service, stages[0])

    # 20 enviados: 2 rebotan → 18 entregados.
    # Abiertos: 2 + 3 + 4 = 9. Clicks: 3. Respuestas: 4.
    await _seed_emails(db, lead, plain=9, opened=2, clicked=3, replied=4, bounced=2)

    metrics = await MetricsService(db).email_metrics(Period.build(None, None))

    assert (metrics.sent, metrics.delivered) == (20, 18)
    assert (metrics.opened, metrics.clicked, metrics.replied) == (9, 3, 4)

    assert metrics.open_rate.value == 50.0  # 9/18
    assert metrics.click_rate.value == 16.67  # 3/18
    assert metrics.click_to_open_rate.value == 33.33  # 3/9
    assert metrics.reply_rate.value == 22.22  # 4/18
    assert metrics.bounce_rate.value == 10.0  # 2/20 sobre enviados, no entregados

    # La fracción viaja con la tasa: sin denominador nadie sabe si un 50% son
    # 9 de 18 o 1 de 2.
    assert (metrics.open_rate.numerator, metrics.open_rate.denominator) == (9, 18)


@pytest.mark.asyncio
async def test_sin_datos_las_tasas_son_cero_no_un_error(db: AsyncSession) -> None:
    """Dividir por cero no puede tumbar el panel del primer día."""
    metrics = await MetricsService(db).email_metrics(Period.build(None, None))
    assert metrics.sent == 0
    assert metrics.open_rate.value == 0.0
    assert metrics.open_rate.denominator == 0


@pytest.mark.asyncio
async def test_rebotes_altos_disparan_la_alarma(db: AsyncSession) -> None:
    """Por encima del 5% los proveedores empiezan a penalizar el dominio."""
    stages = await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    lead = await _lead(db, await _company(db, "Finca"), service, stages[0])

    await _seed_emails(db, lead, plain=90, bounced=10)  # 10%

    metrics = await MetricsService(db).email_metrics(Period.build(None, None))
    assert metrics.bounce_rate.is_alarm is True
    assert any("Rebotes" in w for w in metrics.warnings)


@pytest.mark.asyncio
async def test_bajas_altas_disparan_la_alarma(db: AsyncSession) -> None:
    stages = await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    lead = await _lead(db, await _company(db, "Finca"), service, stages[0])
    await _seed_emails(db, lead, plain=100)

    for i in range(3):  # 3/100 = 3%, muy por encima del 0,5%
        db.add(SuppressionEntry(email=f"baja{i}@x.co", reason="unsubscribed"))
    await db.flush()

    metrics = await MetricsService(db).email_metrics(Period.build(None, None))
    assert metrics.unsubscribe_rate.is_alarm is True
    assert any("Bajas" in w for w in metrics.warnings)


@pytest.mark.asyncio
async def test_avisa_cuando_la_muestra_es_pequena(db: AsyncSession) -> None:
    """Un 50% sobre 4 correos no es una métrica; decirlo evita decisiones
    tomadas sobre ruido."""
    stages = await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    lead = await _lead(db, await _company(db, "Finca"), service, stages[0])
    await _seed_emails(db, lead, plain=2, opened=2)

    metrics = await MetricsService(db).email_metrics(Period.build(None, None))
    assert any("no significan gran cosa" in w for w in metrics.warnings)


@pytest.mark.asyncio
async def test_el_periodo_recorta_de_verdad(db: AsyncSession) -> None:
    stages = await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    lead = await _lead(db, await _company(db, "Finca"), service, stages[0])

    viejo = _email(lead)
    viejo.sent_at = datetime.now(UTC) - timedelta(days=90)
    db.add(viejo)
    db.add(_email(lead))
    await db.flush()

    recientes = await MetricsService(db).email_metrics(Period.build(None, None))
    assert recientes.sent == 1

    todo = await MetricsService(db).email_metrics(
        Period.build((datetime.now(UTC) - timedelta(days=120)).date(), None)
    )
    assert todo.sent == 2


# ------------------------------------------------------------------ embudo


@pytest.mark.asyncio
async def test_el_embudo_cuenta_por_donde_paso_cada_prospecto(db: AsyncSession) -> None:
    """Un prospecto que hoy está en "Ganado" pasó antes por "Interesado" y
    cuenta en ambos: mirar solo la etapa actual perdería a los que avanzaron.
    """
    stages = await _stages(db)
    by_type = {s.stage_type: s for s in stages}
    service = Service(name="Web")
    db.add(service)
    await db.flush()

    lead = await _lead(db, await _company(db, "Finca"), service, by_type[StageType.WON])
    now = datetime.now(UTC)
    for index, stage_type in enumerate(
        [StageType.REPLIED, StageType.CONVERSATION, StageType.INTERESTED, StageType.WON]
    ):
        db.add(
            LeadStageHistory(
                lead_id=lead.id,
                to_stage_id=by_type[stage_type].id,
                to_stage_type=stage_type,
                actor=ActorType.USER,
                entered_at=now + timedelta(minutes=index),
            )
        )
    await db.flush()

    steps = await MetricsService(db).funnel(Period.build(None, None))
    counts = {s.key: s.count for s in steps}

    assert counts["replied"] == 1
    assert counts["conversation"] == 1
    assert counts["interested"] == 1
    assert counts["won"] == 1
    # No pasó por reunión ni propuesta: esos quedan en cero, no heredan.
    assert counts["meeting"] == 0
    assert counts["proposal"] == 0


@pytest.mark.asyncio
async def test_pasar_dos_veces_por_una_etapa_cuenta_una(db: AsyncSession) -> None:
    """Son personas, no eventos: un prospecto reabierto no duplica el embudo."""
    stages = await _stages(db)
    by_type = {s.stage_type: s for s in stages}
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    lead = await _lead(db, await _company(db, "Finca"), service, by_type[StageType.REPLIED])

    now = datetime.now(UTC)
    for index in range(2):
        db.add(
            LeadStageHistory(
                lead_id=lead.id,
                to_stage_id=by_type[StageType.REPLIED].id,
                to_stage_type=StageType.REPLIED,
                actor=ActorType.USER,
                entered_at=now + timedelta(minutes=index),
            )
        )
    await db.flush()

    steps = await MetricsService(db).funnel(Period.build(None, None))
    assert {s.key: s.count for s in steps}["replied"] == 1


@pytest.mark.asyncio
async def test_el_embudo_calcula_conversion_paso_a_paso(db: AsyncSession) -> None:
    await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()

    # 4 empresas, 2 con email.
    for i in range(4):
        await _company(db, f"Empresa {i}", email=f"e{i}@x.co" if i < 2 else None)

    steps = await MetricsService(db).funnel(Period.build(None, None))
    by_key = {s.key: s for s in steps}

    assert by_key["companies"].count == 4
    assert by_key["with_email"].count == 2
    assert by_key["with_email"].conversion_from_previous == 50.0
    # El primer paso no tiene anterior con el que comparar.
    assert by_key["companies"].conversion_from_previous is None


@pytest.mark.asyncio
async def test_con_email_cuenta_tambien_el_del_contacto(db: AsyncSession) -> None:
    """El enriquecimiento suele guardar la dirección en el contacto, no en la
    empresa. Contar solo `companies.email` daba cero con datos reales."""
    from app.models.contact import Contact

    await _stages(db)
    company = await _company(db, "Solo contacto")  # sin email propio
    db.add(Contact(company_id=company.id, email="camila@lafinca.co"))
    await _company(db, "Sin nada")
    await db.flush()

    steps = {s.key: s for s in await MetricsService(db).funnel(Period.build(None, None))}
    assert steps["companies"].count == 2
    assert steps["with_email"].count == 1


@pytest.mark.asyncio
async def test_las_respuestas_se_comparan_con_entregados_no_con_clicks(
    db: AsyncSession,
) -> None:
    """Responder no exige haber pulsado un enlace.

    Encadenando por posición, 6 respuestas sobre 1 click daban un «600% de
    conversión» — un número que no significa nada.
    """
    stages = await _stages(db)
    by_type = {s.stage_type: s for s in stages}
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    company = await _company(db, "Finca")
    lead = await _lead(db, company, service, by_type[StageType.REPLIED])

    # 10 entregados, 1 click, 4 respuestas.
    await _seed_emails(db, lead, plain=9, clicked=1)
    for index in range(4):
        otro = await _lead(
            db, await _company(db, f"Otra {index}"), service, by_type[StageType.REPLIED]
        )
        db.add(
            LeadStageHistory(
                lead_id=otro.id,
                to_stage_id=by_type[StageType.REPLIED].id,
                to_stage_type=StageType.REPLIED,
                actor=ActorType.PROSPECT,
                entered_at=datetime.now(UTC),
            )
        )
    await db.flush()

    steps = {s.key: s for s in await MetricsService(db).funnel(Period.build(None, None))}
    respuestas = steps["replied"]

    assert respuestas.baseline_key == "delivered"
    assert respuestas.count == 4
    assert respuestas.conversion_from_previous == 40.0  # 4/10, no 4/1
    assert respuestas.conversion_from_previous <= 100


@pytest.mark.asyncio
async def test_el_cuello_de_botella_se_explica_en_una_frase(db: AsyncSession) -> None:
    await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    for i in range(10):
        await _company(db, f"Empresa {i}", email=f"e{i}@x.co" if i == 0 else None)

    metrics = MetricsService(db)
    steps = await metrics.funnel(Period.build(None, None))
    frase = metrics.bottleneck(steps)

    assert frase is not None
    assert "%" in frase


# ------------------------------------------------------------------ cortes


@pytest.mark.asyncio
async def test_comparativa_de_plantillas_marca_lo_que_no_es_concluyente(
    db: AsyncSession,
) -> None:
    stages = await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    lead = await _lead(db, await _company(db, "Finca"), service, stages[0])

    buena = EmailTemplate(
        name="Buena", category="first_contact", subject="s", body_text="b", variables_used=[]
    )
    poca = EmailTemplate(
        name="Poca muestra",
        category="first_contact",
        subject="s",
        body_text="b",
        variables_used=[],
    )
    db.add_all([buena, poca])
    await db.flush()

    for _ in range(40):
        db.add(_email(lead, opened=True, template_id=buena.id))
    for _ in range(5):
        db.add(_email(lead, opened=True, template_id=poca.id))
    await db.flush()

    rows = {r["name"]: r for r in await MetricsService(db).by_template(Period.build(None, None))}

    assert rows["Buena"]["sent"] == 40
    assert rows["Buena"]["open_rate"].value == 100.0
    assert rows["Buena"]["is_significant"] is True
    # Con 5 envíos, comparar plantillas es comparar suerte.
    assert rows["Poca muestra"]["is_significant"] is False


@pytest.mark.asyncio
async def test_velocidad_por_etapa_en_dias(db: AsyncSession) -> None:
    stages = await _stages(db)
    by_type = {s.stage_type: s for s in stages}
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    lead = await _lead(db, await _company(db, "Finca"), service, by_type[StageType.CONTACTED])

    now = datetime.now(UTC)
    for seconds in (86400, 3 * 86400):  # 1 día y 3 días → media 2, mediana 2
        db.add(
            LeadStageHistory(
                lead_id=lead.id,
                from_stage_id=by_type[StageType.CONTACTED].id,
                from_stage_type=StageType.CONTACTED,
                to_stage_id=by_type[StageType.OPENED].id,
                to_stage_type=StageType.OPENED,
                actor=ActorType.SYSTEM,
                entered_at=now,
                duration_seconds=seconds,
            )
        )
    await db.flush()

    rows = await MetricsService(db).velocity(Period.build(None, None))
    contacted = next(r for r in rows if r["stage_type"] is StageType.CONTACTED)
    assert contacted["transitions"] == 2
    assert contacted["avg_days"] == 2.0
    assert contacted["median_days"] == 2.0


# ------------------------------------------------------------------ API


@pytest.mark.asyncio
async def test_el_panel_responde_con_el_periodo_por_defecto(
    client: AsyncClient, db: AsyncSession
) -> None:
    stages = await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    company = await _company(db, "Finca", email="x@finca.co")
    lead = await _lead(db, company, service, stages[0])
    lead.status = LeadStatus.WON
    lead.estimated_value = 1_500_000  # type: ignore[assignment]
    await _seed_emails(db, lead, plain=8, opened=2)
    await db.commit()

    response = await client.get(f"{API}/metrics/overview")
    assert response.status_code == 200
    body = response.json()

    assert body["companies_found"] == 1
    assert body["companies_with_email"] == 1
    # El panel y el embudo cuentan lo mismo: si uno dice 0 y el otro 1, el
    # usuario deja de creerse los dos.
    funnel = (await client.get(f"{API}/metrics/funnel")).json()
    with_email = next(s for s in funnel["steps"] if s["key"] == "with_email")
    assert with_email["count"] == body["companies_with_email"]
    assert body["leads"] == 1
    assert body["leads_won"] == 1
    assert body["won_value"] == 1_500_000.0
    assert body["emails"]["sent"] == 10
    assert body["emails"]["open_rate"]["value"] == 20.0
    # El periodo devuelto es explícito: nadie tiene que adivinar qué se está mirando.
    assert body["period"]["start"] < body["period"]["end"]


@pytest.mark.asyncio
async def test_el_embudo_por_api_trae_la_lectura(client: AsyncClient, db: AsyncSession) -> None:
    await _stages(db)
    for i in range(3):
        await _company(db, f"Empresa {i}", email="x@y.co" if i == 0 else None)
    await db.commit()

    body = (await client.get(f"{API}/metrics/funnel")).json()
    assert body["steps"][0]["label"] == "Empresas encontradas"
    assert body["steps"][0]["count"] == 3
    assert isinstance(body["bottleneck"], str)


@pytest.mark.asyncio
async def test_la_lista_de_atencion_es_lo_que_hay_que_hacer_hoy(
    client: AsyncClient, db: AsyncSession
) -> None:
    stages = await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()

    vencido = await _lead(db, await _company(db, "Vencido"), service, stages[0])
    vencido.next_follow_up_at = datetime.now(UTC) - timedelta(days=1)

    caliente = await _lead(db, await _company(db, "Caliente"), service, stages[0])
    caliente.engagement_score = 45
    await db.commit()

    body = (await client.get(f"{API}/metrics/attention")).json()
    assert body["overdue_followups"] == 1
    assert body["hot_leads_without_reply"] == 1
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_serie_temporal_agrupada_por_dia(client: AsyncClient, db: AsyncSession) -> None:
    stages = await _stages(db)
    service = Service(name="Web")
    db.add(service)
    await db.flush()
    lead = await _lead(db, await _company(db, "Finca"), service, stages[0])

    hoy = _email(lead)
    ayer = _email(lead)
    ayer.sent_at = datetime.now(UTC) - timedelta(days=1)
    db.add_all([hoy, ayer])
    await db.commit()

    puntos = (await client.get(f"{API}/metrics/timeseries", params={"metric": "sent"})).json()
    assert len(puntos) == 2
    assert sum(p["value"] for p in puntos) == 2


@pytest.mark.asyncio
async def test_metrica_inventada_se_rechaza(client: AsyncClient) -> None:
    response = await client.get(f"{API}/metrics/timeseries", params={"metric": "karma"})
    assert response.status_code == 422
