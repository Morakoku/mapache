"""Fase 6 — ingesta de correo entrante y segmentos de seguimiento.

El transporte ya está probado en la Fase 4; aquí se prueba la decisión: qué
hace el CRM con una respuesta, con un "estoy de vacaciones" y con un rebote, y
cómo queda después el segmento de "abrió y no respondió".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    ActivityType,
    Direction,
    EmailStatus,
    LeadStatus,
    StageType,
    VerificationStatus,
)
from app.mail.base import InboundMessage
from app.models.activity import Activity
from app.models.email import (
    Conversation,
    ConversationMessage,
    EmailMessage,
    SuppressionEntry,
)
from app.models.lead import Lead
from app.services.email_svc import EmailService
from app.services.inbox_svc import AUTO_REPLY_RETRY_DAYS, InboxService
from app.services.lead_svc import LeadService
from tests.integration.test_email_send import (
    _mailhog_available,
    _seed,
    _seed_account,
    _seed_settings,
)

API = "/api/v1"


async def _send(db: AsyncSession, lead: Lead, settings, account, subject="Una idea"):  # type: ignore[no-untyped-def]
    outcome = await EmailService(db).send_one(
        lead,
        subject=subject,
        body_text="Hola Camila, ¿hablamos?\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    assert outcome.sent, outcome.message
    await db.flush()
    assert outcome.email_id is not None
    email = await db.get(EmailMessage, outcome.email_id)
    assert email is not None
    return email


def _inbound(email: EmailMessage, **kw) -> InboundMessage:  # type: ignore[no-untyped-def]
    defaults = {
        "provider_message_id": f"<respuesta-{email.id}@lafinca.co>",
        "from_email": email.to_email,
        "to_email": email.from_email,
        "subject": f"Re: {email.subject}",
        "body_text": "Sí me interesa, ¿cuánto costaría?\n",
        "body_html": None,
        "received_at": datetime.now(UTC),
        "in_reply_to": email.provider_message_id,
        "references": email.provider_message_id,
    }
    return InboundMessage(**{**defaults, **kw})


async def _setup(db: AsyncSession, **kw):  # type: ignore[no-untyped-def]
    lead = await _seed(db, **kw)
    settings = await _seed_settings(db)
    account = await _seed_account(db)
    return lead, settings, account


# ------------------------------------------------------------------ respuestas


@pytest.mark.asyncio
async def test_respuesta_avanza_el_prospecto_y_para_la_secuencia(db: AsyncSession) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)

    result = await InboxService(db).ingest(account, _inbound(email))
    assert result.kind == "reply"

    await db.refresh(email)
    assert email.replied_at is not None

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.replied_at is not None
    # Responder detiene la automatización: a partir de aquí escribe una persona.
    assert fresh.sequence_paused is True
    assert fresh.next_follow_up_at is None
    assert fresh.engagement_score >= 40
    assert fresh.stage.stage_type is StageType.REPLIED

    conversation = await db.get(Conversation, email.conversation_id)
    assert conversation is not None
    assert conversation.status == "NEEDS_REPLY"
    assert conversation.is_unread is True
    assert conversation.last_direction is Direction.INBOUND
    assert conversation.message_count == 2

    inbound_row = (
        await db.execute(
            select(EmailMessage).where(
                EmailMessage.lead_id == lead.id, EmailMessage.direction == Direction.INBOUND
            )
        )
    ).scalar_one()
    assert inbound_row.status is EmailStatus.DELIVERED

    types = (
        (await db.execute(select(Activity.activity_type).where(Activity.lead_id == lead.id)))
        .scalars()
        .all()
    )
    assert ActivityType.EMAIL_REPLIED in types


@pytest.mark.asyncio
async def test_el_mismo_mensaje_dos_veces_no_se_duplica(db: AsyncSession) -> None:
    """Gmail reentrega por webhook y el delta de Graph repite: sin esto, una
    respuesta contaría dos veces."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)
    inbound = _inbound(email)

    inbox = InboxService(db)
    assert (await inbox.ingest(account, inbound)).kind == "reply"
    assert (await inbox.ingest(account, inbound)).kind == "duplicate"

    conversation = await db.get(Conversation, email.conversation_id)
    assert conversation is not None
    assert conversation.message_count == 2


@pytest.mark.asyncio
async def test_auto_respuesta_no_cuenta_como_respuesta(db: AsyncSession) -> None:
    """Un "vuelvo el lunes" no es interés: se guarda y se reprograma."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)

    result = await InboxService(db).ingest(
        account,
        _inbound(
            email,
            subject="Fuera de la oficina",
            body_text="Estoy fuera hasta el 5 de agosto.\n",
            is_auto_reply=True,
        ),
    )
    assert result.kind == "auto_reply"

    await db.refresh(email)
    assert email.replied_at is None

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.replied_at is None
    assert fresh.stage.stage_type is not StageType.REPLIED
    assert fresh.next_follow_up_at is not None
    esperado = datetime.now(UTC) + timedelta(days=AUTO_REPLY_RETRY_DAYS)
    assert abs((fresh.next_follow_up_at - esperado).total_seconds()) < 120

    conversation = await db.get(Conversation, email.conversation_id)
    assert conversation is not None
    # No pide atención humana: no ensucia la bandeja de pendientes.
    assert conversation.is_unread is False
    assert conversation.status == "AWAITING_REPLY"


# ------------------------------------------------------------------ rebotes


@pytest.mark.asyncio
async def test_rebote_duro_suprime_la_direccion(db: AsyncSession) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)

    result = await InboxService(db).ingest(
        account,
        _inbound(
            email,
            provider_message_id="<dsn-1@correo.co>",
            from_email="MAILER-DAEMON@correo.co",
            subject="Undelivered Mail Returned to Sender",
            body_text="<gerente@lafinca.co>: 550 5.1.1 User unknown\n",
            is_bounce=True,
            bounce_type="hard",
        ),
    )
    assert result.kind == "bounce"

    await db.refresh(email)
    assert email.status is EmailStatus.BOUNCED
    assert email.bounce_type == "hard"

    suppressed = (
        await db.execute(select(SuppressionEntry).where(SuppressionEntry.email == email.to_email))
    ).scalar_one()
    assert suppressed.reason == "hard_bounce"

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.contact is not None
    assert fresh.contact.email_verified is VerificationStatus.BOUNCED
    assert fresh.sequence_paused is True

    # Y ya no se le puede volver a escribir.
    blocked = await EmailService(db).send_one(
        fresh,
        subject="Otra idea",
        body_text="Hola de nuevo\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    assert blocked.sent is False


@pytest.mark.asyncio
async def test_rebote_blando_no_descarta_al_prospecto(db: AsyncSession) -> None:
    """Un buzón lleno se arregla solo; bloquear por eso perdería clientes."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)

    await InboxService(db).ingest(
        account,
        _inbound(
            email,
            provider_message_id="<dsn-2@correo.co>",
            from_email="postmaster@correo.co",
            body_text="452 4.2.2 Mailbox full\n",
            is_bounce=True,
            bounce_type="soft",
        ),
    )

    assert (
        await db.execute(select(SuppressionEntry).where(SuppressionEntry.email == email.to_email))
    ).scalar_one_or_none() is None

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.sequence_paused is False
    assert fresh.contact is not None
    assert fresh.contact.email_verified is not VerificationStatus.BOUNCED


# ------------------------------------------------------------------ emparejado


@pytest.mark.asyncio
async def test_respuesta_sin_cabeceras_se_empareja_por_remitente(db: AsyncSession) -> None:
    """Hay clientes de correo que rompen el hilo. El remitente sigue siendo
    una pista válida."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)

    result = await InboxService(db).ingest(
        account, _inbound(email, in_reply_to=None, references=None)
    )
    assert result.kind == "reply"
    assert result.lead_id == lead.id


@pytest.mark.asyncio
async def test_correo_de_un_desconocido_no_inventa_un_prospecto(db: AsyncSession) -> None:
    """Podría ser correo personal del usuario: se registra y se ignora."""
    _, _, account = await _setup(db)

    result = await InboxService(db).ingest(
        account,
        InboundMessage(
            provider_message_id="<suelto-1@otra.co>",
            from_email="quien@otra.co",
            to_email=account.email,
            subject="Hola",
            body_text="Mensaje sin relación con el CRM\n",
            body_html=None,
            received_at=datetime.now(UTC),
        ),
    )
    assert result.kind == "unmatched"

    total = (await db.execute(select(ConversationMessage))).scalars().all()
    assert total == []


# ------------------------------------------------------------------ segmentos


@pytest.mark.asyncio
async def test_segmento_abrio_y_no_respondio(client: AsyncClient, db: AsyncSession) -> None:
    """El segmento que se mira cada mañana."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)

    # Aún no ha abierto: no está en el segmento, está en "sin apertura".
    vacio = await client.get(f"{API}/leads/segments/opened_no_reply")
    assert vacio.json()["total"] == 0
    sin_abrir = await client.get(f"{API}/leads/segments/sent_no_open")
    assert sin_abrir.json()["total"] == 1

    # Abre dos veces.
    email.open_count = 2
    email.opened_at = datetime.now(UTC)
    email.first_opened_at = email.opened_at
    await db.flush()

    listado = await client.get(f"{API}/leads/segments/opened_no_reply")
    assert listado.status_code == 200
    body = listado.json()
    assert body["total"] == 1

    fila = body["items"][0]
    assert fila["lead"]["id"] == str(lead.id)
    assert fila["opens"] == 2
    assert fila["emails_sent"] == 1
    assert fila["days_since_open"] == 0
    assert "no respondió" in fila["note"]

    # Con min_days=2 no aparece: abrió hoy, escribirle ya sería prematuro.
    reciente = await client.get(f"{API}/leads/segments/opened_no_reply", params={"min_days": 2})
    assert reciente.json()["total"] == 0

    # Responde y sale del segmento.
    await InboxService(db).ingest(account, _inbound(email))
    await db.flush()

    despues = await client.get(f"{API}/leads/segments/opened_no_reply")
    assert despues.json()["total"] == 0
    respondieron = await client.get(f"{API}/leads/segments/replied")
    assert respondieron.json()["total"] == 1


@pytest.mark.asyncio
async def test_click_pesa_mas_que_apertura_en_el_segmento(
    client: AsyncClient, db: AsyncSession
) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)
    email.open_count = 1
    email.opened_at = datetime.now(UTC)
    email.click_count = 1
    email.clicked_at = datetime.now(UTC)
    await db.flush()

    clicked = await client.get(f"{API}/leads/segments/clicked_no_reply")
    assert clicked.json()["total"] == 1
    assert "enlace" in clicked.json()["items"][0]["note"]


@pytest.mark.asyncio
async def test_resumen_de_segmentos(client: AsyncClient, db: AsyncSession) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)
    email.open_count = 1
    email.opened_at = datetime.now(UTC)
    await db.flush()

    summary = (await client.get(f"{API}/leads/segments/summary")).json()
    assert summary["opened_no_reply"] == 1
    assert summary["sent_no_open"] == 0
    assert summary["replied"] == 0
    assert summary["bounced"] == 0


@pytest.mark.asyncio
async def test_segmento_desconocido_se_rechaza(client: AsyncClient) -> None:
    response = await client.get(f"{API}/leads/segments/inventado")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_SEGMENT"


@pytest.mark.asyncio
async def test_lead_cerrado_no_aparece_en_seguimiento(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Un prospecto ganado o perdido no es trabajo pendiente."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead, settings, account = await _setup(db)
    email = await _send(db, lead, settings, account)
    email.open_count = 1
    email.opened_at = datetime.now(UTC)
    lead.status = LeadStatus.LOST
    await db.flush()

    listado = await client.get(f"{API}/leads/segments/opened_no_reply")
    assert listado.json()["total"] == 0
