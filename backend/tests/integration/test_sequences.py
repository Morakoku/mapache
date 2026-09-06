"""Fase 7 — secuencias de seguimiento de punta a punta.

Lo que importa comprobar no es que envíe, sino que **deje de enviar**: al
primer "ya respondió", al primer rebote, al primer "no me interesa". Un
seguimiento automático que no sabe parar es un generador de spam.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActivityType, LeadStatus, ReplyIntent
from app.models.activity import Activity
from app.models.email import EmailMessage, EmailTemplate, SuppressionEntry
from app.models.sequence import (
    FOLLOWUP_CANCELLED,
    FOLLOWUP_PENDING,
    FOLLOWUP_SENT,
    FOLLOWUP_SKIPPED,
    FollowUp,
    Sequence,
    SequenceStep,
)
from app.services.email_svc import EmailService
from app.services.inbox_svc import InboxService
from app.services.lead_svc import LeadService
from app.services.sequence_svc import FollowUpService, SequenceService
from app.workers.followup_worker import _process
from tests.integration.test_email_send import (
    _mailhog_available,
    _seed,
    _seed_account,
    _seed_settings,
)
from tests.integration.test_inbox import _inbound

API = "/api/v1"


async def _template(db: AsyncSession, name: str) -> EmailTemplate:
    template = EmailTemplate(
        name=name,
        category="followup_1",
        subject="¿Lo viste, {{company_name}}?",
        body_text="Hola {{first_name}}, te escribo de nuevo por si se te pasó.\n",
        variables_used=["company_name", "first_name", "unsubscribe_url"],
    )
    db.add(template)
    await db.flush()
    return template


async def _sequence(
    db: AsyncSession, *, steps: int = 2, condition: str | None = None, **kw: object
) -> Sequence:
    sequence = Sequence(name=f"Seguimiento {datetime.now(UTC).timestamp()}", **kw)  # type: ignore[arg-type]
    db.add(sequence)
    await db.flush()

    for number in range(1, steps + 1):
        template = await _template(db, f"Paso {number} {sequence.id}")
        db.add(
            SequenceStep(
                sequence_id=sequence.id,
                step_number=number,
                template_id=template.id,
                delay_days=0,
                delay_hours=0,
                condition={"if": condition} if condition else None,
                skip_weekends=False,
                send_window_start=time(0, 0),
                send_window_end=time(23, 59),
            )
        )
    await db.flush()
    return await SequenceService(db).get_or_404(sequence.id)


async def _due_followup(db: AsyncSession, lead_id) -> FollowUp:  # type: ignore[no-untyped-def]
    """El seguimiento pendiente del lead, con la fecha ya vencida."""
    result = await db.execute(
        select(FollowUp).where(FollowUp.lead_id == lead_id, FollowUp.status == FOLLOWUP_PENDING)
    )
    follow_up = result.scalars().first()
    assert follow_up is not None
    follow_up.scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.flush()
    return follow_up


async def _setup(db: AsyncSession, **kw):  # type: ignore[no-untyped-def]
    lead = await _seed(db, **kw)
    settings = await _seed_settings(db)
    account = await _seed_account(db)
    settings.default_account_id = account.id
    await db.flush()
    return lead, settings, account


# ------------------------------------------------------------------ calendario


@pytest.mark.asyncio
async def test_preview_no_escribe_nada(db: AsyncSession) -> None:
    """Ver el calendario no puede tener efectos: es la pantalla de confirmar."""
    lead, _, _ = await _setup(db)
    sequence = await _sequence(db, steps=3)

    previews = await SequenceService(db).preview(sequence, [lead.id])
    assert len(previews) == 1
    assert len(previews[0].steps) == 3
    assert previews[0].warnings == []
    # Las fechas van en orden y ninguna es pasado.
    fechas = [s.scheduled_at for s in previews[0].steps]
    assert fechas == sorted(fechas)
    assert fechas[0] >= datetime.now(UTC) - timedelta(seconds=5)

    pendientes = (await db.execute(select(FollowUp))).scalars().all()
    assert pendientes == []


@pytest.mark.asyncio
async def test_enroll_programa_solo_el_primer_paso(db: AsyncSession) -> None:
    """Programar los tres de golpe obligaría a cancelar dos al primer 'sí'."""
    lead, _, _ = await _setup(db)
    sequence = await _sequence(db, steps=3)

    created, _ = await SequenceService(db).enroll(sequence, [lead.id])
    assert len(created) == 1
    assert created[0].sequence_step == 1

    pendientes = (await db.execute(select(FollowUp))).scalars().all()
    assert len(pendientes) == 1

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.sequence_id == sequence.id
    assert fresh.next_follow_up_at is not None


@pytest.mark.asyncio
async def test_no_se_inscribe_dos_veces(db: AsyncSession) -> None:
    lead, _, _ = await _setup(db)
    sequence = await _sequence(db)
    service = SequenceService(db)

    await service.enroll(sequence, [lead.id])
    created, previews = await service.enroll(sequence, [lead.id])

    assert created == []
    assert any("ya está inscrito" in w for w in previews[0].warnings)


@pytest.mark.asyncio
async def test_prospecto_sin_email_no_entra(db: AsyncSession) -> None:
    lead, _, _ = await _setup(db)
    lead.contact.email = None
    lead.company.email = None
    await db.flush()

    sequence = await _sequence(db)
    created, previews = await SequenceService(db).enroll(sequence, [lead.id])
    assert created == []
    assert any("no tiene email" in w for w in previews[0].warnings)


# ------------------------------------------------------------------ ejecución


@pytest.mark.asyncio
async def test_la_secuencia_envia_y_encadena_el_siguiente_paso(db: AsyncSession) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, _, _ = await _setup(db)
    sequence = await _sequence(db, steps=2)
    await SequenceService(db).enroll(sequence, [lead.id])

    follow_up = await _due_followup(db, lead.id)
    assert await _process(db, follow_up) == "sent"

    await db.refresh(follow_up)
    assert follow_up.status == FOLLOWUP_SENT
    assert follow_up.sent_email_id is not None

    # El correo enviado sabe de qué seguimiento salió.
    email = await db.get(EmailMessage, follow_up.sent_email_id)
    assert email is not None
    assert email.follow_up_id == follow_up.id

    # Y ya hay un paso 2 programado.
    siguiente = (
        await db.execute(
            select(FollowUp).where(FollowUp.lead_id == lead.id, FollowUp.status == FOLLOWUP_PENDING)
        )
    ).scalar_one()
    assert siguiente.sequence_step == 2

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.sequence_step == 1
    assert fresh.next_follow_up_at == siguiente.scheduled_at

    types = (
        (await db.execute(select(Activity.activity_type).where(Activity.lead_id == lead.id)))
        .scalars()
        .all()
    )
    assert ActivityType.FOLLOWUP_SENT in types


@pytest.mark.asyncio
async def test_la_secuencia_se_detiene_al_responder(db: AsyncSession) -> None:
    """La regla que más importa: nadie recibe un recordatorio después de
    haber contestado."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, _, account = await _setup(db)
    sequence = await _sequence(db, steps=3)
    await SequenceService(db).enroll(sequence, [lead.id])

    # Paso 1 sale.
    follow_up = await _due_followup(db, lead.id)
    assert await _process(db, follow_up) == "sent"

    # El prospecto responde a ese correo.
    email = await db.get(EmailMessage, follow_up.sent_email_id)
    assert email is not None
    await InboxService(db).ingest(account, _inbound(email))
    await db.flush()

    # Los seguimientos pendientes se cancelaron solos.
    pendientes = (
        (
            await db.execute(
                select(FollowUp).where(
                    FollowUp.lead_id == lead.id, FollowUp.status == FOLLOWUP_PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert pendientes == []

    cancelados = (
        (
            await db.execute(
                select(FollowUp).where(
                    FollowUp.lead_id == lead.id, FollowUp.status == FOLLOWUP_CANCELLED
                )
            )
        )
        .scalars()
        .all()
    )
    assert [f.skip_reason for f in cancelados] == ["replied"]

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.next_follow_up_at is None


@pytest.mark.asyncio
async def test_un_seguimiento_vencido_tras_responder_se_descarta(db: AsyncSession) -> None:
    """Cinturón y tirantes: aunque la fila siguiera pendiente, el worker no la
    envía."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, _, _ = await _setup(db)
    sequence = await _sequence(db)
    await SequenceService(db).enroll(sequence, [lead.id])

    lead.replied_at = datetime.now(UTC)
    await db.flush()

    follow_up = await _due_followup(db, lead.id)
    assert await _process(db, follow_up) == "skipped"
    assert follow_up.skip_reason == "replied"


@pytest.mark.asyncio
async def test_no_le_interesa_detiene_la_secuencia(db: AsyncSession) -> None:
    lead, _, _ = await _setup(db)
    sequence = await _sequence(db)
    await SequenceService(db).enroll(sequence, [lead.id])

    lead.reply_intent = ReplyIntent.NEGATIVE
    await db.flush()

    follow_up = await _due_followup(db, lead.id)
    assert await _process(db, follow_up) == "skipped"
    assert follow_up.skip_reason == "not_interested"

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.sequence_paused is True


@pytest.mark.asyncio
async def test_lead_cerrado_no_recibe_seguimiento(db: AsyncSession) -> None:
    lead, _, _ = await _setup(db)
    sequence = await _sequence(db)
    await SequenceService(db).enroll(sequence, [lead.id])

    lead.status = LeadStatus.WON
    await db.flush()

    follow_up = await _due_followup(db, lead.id)
    assert await _process(db, follow_up) == "skipped"
    assert follow_up.skip_reason == "closed"


@pytest.mark.asyncio
async def test_estar_en_la_lista_de_no_contactar_detiene_todo(db: AsyncSession) -> None:
    lead, _, _ = await _setup(db)
    sequence = await _sequence(db)
    await SequenceService(db).enroll(sequence, [lead.id])

    db.add(SuppressionEntry(email=lead.contact.email, reason="unsubscribed"))
    await db.flush()

    follow_up = await _due_followup(db, lead.id)
    assert await _process(db, follow_up) == "skipped"
    assert follow_up.skip_reason == "suppressed"


@pytest.mark.asyncio
async def test_fuera_de_ventana_se_reprograma_no_se_pierde(db: AsyncSession) -> None:
    """Perder un seguimiento porque el worker corrió de madrugada sería un
    fallo del sistema, no una decisión."""
    lead, settings, _ = await _setup(db)
    sequence = await _sequence(db)
    await SequenceService(db).enroll(sequence, [lead.id])

    # Ventana cerrada: un minuto imposible de alcanzar ahora mismo.
    ahora = datetime.now(UTC).time()
    settings.send_window_start = time(23, 58)
    settings.send_window_end = time(23, 59)
    if time(23, 58) <= ahora <= time(23, 59):  # pragma: no cover - franja de un minuto
        settings.send_window_start = time(0, 0)
        settings.send_window_end = time(0, 1)
    await db.flush()

    follow_up = await _due_followup(db, lead.id)
    original = follow_up.scheduled_at
    assert await _process(db, follow_up) == "rescheduled"

    assert follow_up.status == FOLLOWUP_PENDING
    assert follow_up.scheduled_at > original
    assert follow_up.attempts == 1


@pytest.mark.asyncio
async def test_condicion_del_paso_salta_pero_no_corta(db: AsyncSession) -> None:
    """`opened_not_replied`: si no abrió, este paso no aplica, pero el
    siguiente sigue en pie."""
    lead, _, _ = await _setup(db)
    sequence = await _sequence(db, steps=2, condition="opened_not_replied")
    await SequenceService(db).enroll(sequence, [lead.id])

    follow_up = await _due_followup(db, lead.id)
    assert await _process(db, follow_up) == "skipped"
    assert follow_up.skip_reason == "condition_not_met"

    siguiente = (
        await db.execute(
            select(FollowUp).where(FollowUp.lead_id == lead.id, FollowUp.status == FOLLOWUP_PENDING)
        )
    ).scalar_one()
    assert siguiente.sequence_step == 2


@pytest.mark.asyncio
async def test_condicion_se_cumple_cuando_hubo_apertura(db: AsyncSession) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)

    # Un correo previo, abierto.
    outcome = await EmailService(db).send_one(
        lead,
        subject="Primer contacto",
        body_text="Hola\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    email = await db.get(EmailMessage, outcome.email_id)
    assert email is not None
    email.open_count = 1
    email.opened_at = datetime.now(UTC)
    await db.flush()

    sequence = await _sequence(db, steps=1, condition="opened_not_replied")
    await SequenceService(db).enroll(sequence, [lead.id])

    follow_up = await _due_followup(db, lead.id)
    assert await _process(db, follow_up) == "sent"


# ------------------------------------------------------------------ API


@pytest.mark.asyncio
async def test_ciclo_completo_por_api(client: AsyncClient, db: AsyncSession) -> None:
    lead, _, _ = await _setup(db)
    template = await _template(db, "Recordatorio API")

    created = await client.post(
        f"{API}/sequences",
        json={
            "name": "Seguimiento de 2 pasos",
            "max_steps": 2,
            "steps": [
                {"template_id": str(template.id), "delay_days": 3},
                {
                    "template_id": str(template.id),
                    "delay_days": 4,
                    "condition": "opened_not_replied",
                },
            ],
        },
    )
    assert created.status_code == 201
    sequence = created.json()
    assert len(sequence["steps"]) == 2
    assert sequence["steps"][1]["condition_key"] == "opened_not_replied"

    preview = await client.post(
        f"{API}/sequences/{sequence['id']}/preview-schedule",
        json={"lead_ids": [str(lead.id)]},
    )
    assert preview.status_code == 200
    assert len(preview.json()[0]["steps"]) == 2

    # Ver el calendario no inscribe a nadie.
    assert (await client.get(f"{API}/follow-ups")).json()["total"] == 0

    enrolled = await client.post(
        f"{API}/sequences/{sequence['id']}/enroll", json={"lead_ids": [str(lead.id)]}
    )
    assert enrolled.status_code == 200
    body = enrolled.json()
    assert body["enrolled"] == 1
    assert body["first_send_at"] < body["last_send_at"]

    agenda = (await client.get(f"{API}/follow-ups", params={"status": "PENDING"})).json()
    assert agenda["total"] == 1
    follow_up_id = agenda["items"][0]["id"]

    skipped = await client.post(f"{API}/follow-ups/{follow_up_id}/skip")
    assert skipped.json()["status"] == FOLLOWUP_SKIPPED
    assert skipped.json()["skip_label"] is not None


@pytest.mark.asyncio
async def test_condicion_inventada_se_rechaza(client: AsyncClient, db: AsyncSession) -> None:
    template = await _template(db, "Cualquiera")
    response = await client.post(
        f"{API}/sequences",
        json={
            "name": "Rota",
            "steps": [{"template_id": str(template.id), "condition": "si_le_cae_bien"}],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_STEP_CONDITION"


@pytest.mark.asyncio
async def test_borrar_la_secuencia_cancela_lo_programado(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Un seguimiento huérfano se ejecutaría sin reglas de parada."""
    lead, _, _ = await _setup(db)
    sequence = await _sequence(db)
    await SequenceService(db).enroll(sequence, [lead.id])
    await db.commit()

    assert (await client.delete(f"{API}/sequences/{sequence.id}?confirm=true")).status_code == 204

    restantes = (
        (await db.execute(select(FollowUp).where(FollowUp.status == FOLLOWUP_PENDING)))
        .scalars()
        .all()
    )
    assert restantes == []


@pytest.mark.asyncio
async def test_seguimiento_manual_sin_secuencia(client: AsyncClient, db: AsyncSession) -> None:
    """El "recuérdame escribirle el martes" de toda la vida."""
    lead, _, _ = await _setup(db)
    cuando = datetime.now(UTC) + timedelta(days=2)

    created = await client.post(
        f"{API}/follow-ups",
        json={
            "lead_id": str(lead.id),
            "scheduled_at": cuando.isoformat(),
            "note": "Llamarle antes de escribir",
        },
    )
    assert created.status_code == 201
    assert created.json()["is_manual"] is True
    assert created.json()["sequence_id"] is None

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.next_follow_up_at is not None


@pytest.mark.asyncio
async def test_la_baja_cancela_los_seguimientos(client: AsyncClient, db: AsyncSession) -> None:
    """Quien pide no recibir más correos no puede tener envíos en cola."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    sequence = await _sequence(db, steps=3)
    await SequenceService(db).enroll(sequence, [lead.id])

    outcome = await EmailService(db).send_one(
        lead,
        subject="Hola",
        body_text="cuerpo\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    email = await db.get(EmailMessage, outcome.email_id)
    assert email is not None
    await db.commit()

    response = await client.post(f"/tracking/unsubscribe/{email.unsubscribe_token}")
    assert response.status_code == 200

    pendientes = (
        (
            await db.execute(
                select(FollowUp).where(
                    FollowUp.lead_id == lead.id, FollowUp.status == FOLLOWUP_PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert pendientes == []


@pytest.mark.asyncio
async def test_cancelar_a_mano_deja_rastro(db: AsyncSession) -> None:
    lead, _, _ = await _setup(db)
    sequence = await _sequence(db)
    created, _ = await SequenceService(db).enroll(sequence, [lead.id])

    cancelled = await FollowUpService(db).cancel(created[0].id)
    assert cancelled.status == FOLLOWUP_CANCELLED
    assert cancelled.executed_at is not None
