"""Fase 4 — envío real por SMTP contra Mailhog.

No se simula el transporte: el correo sale de verdad, Mailhog lo recibe y el
test lee las cabeceras del mensaje entregado. Es la única forma de comprobar
que el `List-Unsubscribe`, el pixel y los links reescritos llegan de verdad al
destinatario y no solo a la base de datos.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActivityType, EmailEventType, EmailStatus, MailProviderType
from app.mail.guardrails import SkipReason
from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.email import (
    Conversation,
    ConversationMessage,
    EmailEvent,
    EmailLink,
    EmailMessage,
)
from app.models.email_account import EmailAccount
from app.models.lead import Lead
from app.models.pipeline import DEFAULT_STAGES, PipelineStage
from app.models.service import Service
from app.models.settings import AppSettings
from app.services.email_svc import EmailService
from app.services.lead_svc import LeadService
from app.services.mail_admin_svc import EmailAccountService, SuppressionService, TemplateService

MAILHOG_API = "http://localhost:8025/api"
SMTP_HOST = "localhost"
SMTP_PORT = 1025


async def _mailhog_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{MAILHOG_API}/v2/messages?limit=1")
            return response.status_code == 200
    except Exception:  # noqa: BLE001 - si no responde, se salta el test
        return False


async def _mailhog_reset() -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await client.delete(f"{MAILHOG_API}/v1/messages")


async def _mailhog_find(to_email: str) -> dict:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(
            f"{MAILHOG_API}/v2/search", params={"kind": "to", "query": to_email}
        )
        response.raise_for_status()
        items = response.json()["items"]
    assert items, f"Mailhog no recibió ningún correo para {to_email}"
    return items[0]


def _header(message: dict, name: str) -> str | None:
    values = message["Content"]["Headers"].get(name)
    return values[0] if values else None


def _raw_body(message: dict) -> str:
    """Cuerpo completo tal cual se transmitió, con todas sus partes MIME."""
    return message["Content"]["Body"]


# ------------------------------------------------------------------ semilla


async def _seed(db: AsyncSession, *, email: str = "gerente@lafinca.co") -> Lead:
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

    now = datetime.now(UTC)
    company = Company(
        name="Restaurante La Finca",
        city="Medellín",
        category="Restaurante",
        website="https://lafinca.co",
        dedupe_key="lafinca-medellin",
        first_extracted_at=now,
        last_extracted_at=now,
    )
    service = Service(name="Páginas web", value_proposition="Sitios que venden")
    db.add_all([company, service])
    await db.flush()

    contact = Contact(
        company_id=company.id,
        full_name="Camila Restrepo",
        email=email,
        is_primary=True,
    )
    db.add(contact)
    await db.flush()

    leads = LeadService(db)
    lead = await leads.create(company_id=company.id, service_id=service.id, contact_id=contact.id)
    await db.flush()
    # Igual que en la API: el envío necesita `company` y `contact` ya cargados.
    return await leads.get_or_404(lead.id)


async def _seed_settings(db: AsyncSession, **kw: object) -> AppSettings:
    defaults: dict[str, object] = {
        "id": 1,
        "sender_name": "Daniel Ruiz",
        "address_of_sender": "Calle 10 #43-20, Medellín, Colombia",
        # Ventana abierta y sin fin de semana: el test no puede depender de la
        # hora a la que se ejecute. Hasta 23:59:59 y no 23:59:00 — la ventana
        # se compara con la hora completa, así que el minuto final del día
        # quedaba fuera y estos tests fallaban 60 segundos cada noche.
        "send_window_start": time(0, 0),
        "send_window_end": time(23, 59, 59),
        "skip_weekends": False,
        "warmup_enabled": False,
        "min_seconds_between": 0,
    }
    settings = AppSettings(**{**defaults, **kw})  # type: ignore[arg-type]
    db.add(settings)
    await db.flush()
    return settings


async def _seed_account(db: AsyncSession) -> EmailAccount:
    return await EmailAccountService(db).create_smtp(
        {
            "email": "daniel@midominio.co",
            "display_name": "Daniel Ruiz",
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "smtp_use_tls": False,
            "smtp_user": None,
            "smtp_password": None,
            "imap_host": None,
            "imap_port": None,
            "imap_user": None,
            "imap_password": None,
            "is_default": True,
        }
    )


# ------------------------------------------------------------------ envío real


@pytest.mark.asyncio
async def test_envio_real_por_smtp_llega_completo(db: AsyncSession) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")
    await _mailhog_reset()

    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    template = await TemplateService(db).create(
        {
            "name": "Primer contacto web",
            "category": "first_contact",
            "subject": "Una idea para {{company_name}}",
            "body_text": (
                "Hola {{first_name}},\n\n"
                "Vi {{company_name}} en {{city}} y noté algo sobre su web.\n"
                "Aquí un ejemplo: https://midominio.co/casos\n\n"
                "¿Te sirve que te lo cuente en dos líneas?\n"
            ),
        }
    )

    emails = EmailService(db)
    draft = await emails.build_draft(lead, template=template, settings=settings, account=account)
    assert draft.can_send, draft.blocked_reason
    assert "Restaurante La Finca" in draft.subject
    assert "Camila" in draft.body_text

    outcome = await emails.send_one(
        lead,
        subject=draft.subject,
        body_text=draft.body_text,
        body_html=draft.body_html,
        template=template,
        settings=settings,
        account=account,
    )
    assert outcome.sent, outcome.message
    await db.flush()

    # --- lo que quedó en la base ---
    assert outcome.email_id is not None
    email_row = await db.get(EmailMessage, outcome.email_id)
    assert email_row is not None
    assert email_row.status is EmailStatus.SENT
    assert email_row.provider is MailProviderType.SMTP
    assert email_row.sent_at is not None
    assert email_row.provider_message_id

    links = (
        (await db.execute(select(EmailLink).where(EmailLink.email_message_id == email_row.id)))
        .scalars()
        .all()
    )
    assert len(links) == 1, "el enlace del cuerpo debe quedar registrado para el tracking"

    events = (
        (await db.execute(select(EmailEvent).where(EmailEvent.email_message_id == email_row.id)))
        .scalars()
        .all()
    )
    assert [e.event_type for e in events] == [EmailEventType.SENT]

    conversation = (
        await db.execute(select(Conversation).where(Conversation.lead_id == lead.id))
    ).scalar_one()
    assert conversation.message_count == 1
    assert conversation.status == "AWAITING_REPLY"

    message = (
        await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id
            )
        )
    ).scalar_one()
    assert message.snippet

    assert account.sent_today == 1
    assert template.times_used == 1
    assert lead.first_contact_at is not None

    activity_types = (
        (await db.execute(select(Activity.activity_type).where(Activity.lead_id == lead.id)))
        .scalars()
        .all()
    )
    assert ActivityType.EMAIL_SENT in activity_types

    # --- lo que recibió el destinatario ---
    received = await _mailhog_find("gerente@lafinca.co")

    assert _header(received, "From") == "Daniel Ruiz <daniel@midominio.co>"
    assert _header(received, "List-Unsubscribe") == (
        f"<http://localhost:8000/tracking/unsubscribe/{email_row.unsubscribe_token}>"
    )
    assert _header(received, "List-Unsubscribe-Post") == "List-Unsubscribe=One-Click"
    assert _header(received, "Auto-Submitted") == "no"

    body = _raw_body(received)
    assert f"/tracking/open/{email_row.tracking_token}.gif" in body, "falta el pixel"
    assert f"/tracking/click/{links[0].tracking_token}" in body, "el link no se reescribió"
    assert "https://midominio.co/casos" not in body.replace("=\r\n", ""), (
        "el enlace original no debería viajar sin reescribir"
    )
    assert "Calle 10 #43-20" in body.replace("=\r\n", ""), "falta la dirección del remitente"
    assert str(email_row.unsubscribe_token) in body


# ------------------------------------------------------------------ barreras


@pytest.mark.asyncio
async def test_supresion_bloquea_el_envio(db: AsyncSession) -> None:
    lead = await _seed(db, email="baja@lafinca.co")
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    await SuppressionService(db).add(email="baja@lafinca.co", reason="unsubscribed")

    outcome = await EmailService(db).send_one(
        lead,
        subject="Hola",
        body_text="cuerpo",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    assert outcome.sent is False
    assert outcome.skip_reason is SkipReason.SUPPRESSED


@pytest.mark.asyncio
async def test_supresion_por_dominio_bloquea_a_toda_la_empresa(db: AsyncSession) -> None:
    lead = await _seed(db, email="alguien@lafinca.co")
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    await SuppressionService(db).add(domain="lafinca.co", reason="competitor")

    outcome = await EmailService(db).send_one(
        lead,
        subject="Hola",
        body_text="cuerpo",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    assert outcome.skip_reason is SkipReason.SUPPRESSED


@pytest.mark.asyncio
async def test_do_not_contact_bloquea_el_envio(db: AsyncSession) -> None:
    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    lead.contact.do_not_contact = True
    await db.flush()

    outcome = await EmailService(db).send_one(
        lead,
        subject="Hola",
        body_text="cuerpo",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    assert outcome.skip_reason is SkipReason.DO_NOT_CONTACT


@pytest.mark.asyncio
async def test_limite_diario_detiene_el_envio(db: AsyncSession) -> None:
    lead = await _seed(db)
    settings = await _seed_settings(db, daily_send_limit=1)
    account = await _seed_account(db)
    account.sent_today = 1
    account.counters_reset_at = datetime.now(UTC)
    await db.flush()

    outcome = await EmailService(db).send_one(
        lead,
        subject="Hola",
        body_text="cuerpo",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    assert outcome.skip_reason is SkipReason.DAILY_LIMIT


@pytest.mark.asyncio
async def test_misma_plantilla_dos_veces_al_mismo_lead_se_bloquea(db: AsyncSession) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)
    template = await TemplateService(db).create(
        {
            "name": "Seguimiento",
            "category": "followup_1",
            "subject": "Sobre {{company_name}}",
            "body_text": "Hola {{first_name}}, ¿lo viste?\n",
        }
    )

    emails = EmailService(db)
    first = await emails.send_one(
        lead,
        subject="Sobre La Finca",
        body_text="Hola Camila",
        body_html=None,
        template=template,
        settings=settings,
        account=account,
    )
    assert first.sent

    second = await emails.send_one(
        lead,
        subject="Sobre La Finca",
        body_text="Hola Camila",
        body_html=None,
        template=template,
        settings=settings,
        account=account,
    )
    assert second.skip_reason is SkipReason.DUPLICATE


@pytest.mark.asyncio
async def test_pausa_global_detiene_todo(db: AsyncSession) -> None:
    lead = await _seed(db)
    settings = await _seed_settings(db, automations_paused=True)
    account = await _seed_account(db)

    outcome = await EmailService(db).send_one(
        lead,
        subject="Hola",
        body_text="cuerpo",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    assert outcome.skip_reason is SkipReason.AUTOMATIONS_PAUSED


@pytest.mark.asyncio
async def test_lead_sin_email_no_se_intenta(db: AsyncSession) -> None:
    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    lead.contact.email = None
    lead.company.email = None
    await db.flush()

    outcome = await EmailService(db).send_one(
        lead,
        subject="Hola",
        body_text="cuerpo",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    assert outcome.skip_reason is SkipReason.NO_EMAIL
