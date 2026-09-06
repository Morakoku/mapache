"""Fase 8 — la IA vista desde la API.

El caso que se prueba es el que Daniel tiene hoy: **sin clave de Anthropic**.
Todo tiene que seguir funcionando — plantillas, clasificación por reglas,
sugerencias de intención — y la UI tiene que poder saber que no debe ofrecer
el botón de generar.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ReplyIntent, StageType
from app.models.email import Conversation, EmailMessage, EmailTemplate
from app.services.email_svc import EmailService
from app.services.inbox_svc import InboxService
from app.services.lead_svc import LeadService
from tests.integration.test_email_send import (
    _mailhog_available,
    _seed,
    _seed_account,
    _seed_settings,
)
from tests.integration.test_inbox import _inbound

API = "/api/v1"


async def _template(db: AsyncSession) -> EmailTemplate:
    template = EmailTemplate(
        name=f"Primer contacto {datetime.now(UTC).timestamp()}",
        category="first_contact",
        subject="Una idea para {{company_name}}",
        body_text="Hola {{first_name}}, ¿hablamos?\n",
        variables_used=["company_name", "first_name", "unsubscribe_url"],
    )
    db.add(template)
    await db.flush()
    return template


# ------------------------------------------------------------------ estado


@pytest.mark.asyncio
async def test_sin_clave_la_ui_sabe_que_no_debe_ofrecer_ia(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_settings(db)

    response = await client.get(f"{API}/settings/ai")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["enabled"] is False
    # La pista ya no manda a una variable de entorno: la clave se pega en la
    # pantalla de Configuración, y hay cuatro proveedores donde elegir.
    assert body["setup_hint"]
    assert body["has_stored_key"] is False
    assert {p["provider"] for p in body["providers"]} == {
        "ANTHROPIC",
        "OPENAI",
        "DEEPSEEK",
        "KIMI",
    }
    # Sin clave no se estima coste: no hay nada que cobrar.
    assert body["estimated_cost_per_email_usd"] is None
    # El modelo configurado se sigue informando, para la pantalla de ajustes.
    assert body["model"] == "claude-opus-5"


# ------------------------------------------------------------------ borradores


@pytest.mark.asyncio
async def test_personalizar_sin_ia_devuelve_la_plantilla(
    client: AsyncClient, db: AsyncSession
) -> None:
    """El usuario nunca se queda sin borrador porque la IA no esté."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead = await _seed(db)
    await _seed_settings(db)
    await _seed_account(db)
    template = await _template(db)

    response = await client.post(
        f"{API}/emails/personalize",
        json={"lead_id": str(lead.id), "template_id": str(template.id)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_ai_generated"] is False
    assert body["subject"] == "Una idea para Restaurante La Finca"
    assert "Camila" in body["body_text"]
    assert body["estimated_cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_personalizar_sin_ia_ni_plantilla_lo_dice_claro(
    client: AsyncClient, db: AsyncSession
) -> None:
    lead = await _seed(db)
    await _seed_settings(db)

    response = await client.post(f"{API}/emails/personalize", json={"lead_id": str(lead.id)})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "AI_UNAVAILABLE_NO_TEMPLATE"


# ------------------------------------------------------------------ intención


@pytest.mark.asyncio
async def test_la_respuesta_se_clasifica_aunque_no_haya_ia(db: AsyncSession) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    outcome = await EmailService(db).send_one(
        lead,
        subject="Una idea",
        body_text="Hola\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    email = await db.get(EmailMessage, outcome.email_id)
    assert email is not None

    await InboxService(db).ingest(
        account, _inbound(email, body_text="¿Cuánto costaría el servicio?\n")
    )
    await db.flush()

    conversation = await db.get(Conversation, email.conversation_id)
    assert conversation is not None
    assert conversation.reply_intent is ReplyIntent.PRICING
    assert conversation.intent_source == "rules"
    assert conversation.intent_suggested_stage is StageType.OPPORTUNITY
    assert conversation.intent_reviewed is False

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.reply_intent is ReplyIntent.PRICING
    # Pedir precio suma engagement, pero la etapa avanzada la decide el usuario.
    assert fresh.stage.stage_type is not StageType.OPPORTUNITY


@pytest.mark.asyncio
async def test_pedir_la_baja_por_respuesta_suprime_sin_preguntar(db: AsyncSession) -> None:
    """La única acción automática de la clasificación (§9).

    Equivocarse al otro lado —seguir escribiendo a quien pidió parar— es
    mucho peor que suprimir de más.
    """
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    from sqlalchemy import select

    from app.models.email import SuppressionEntry

    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    outcome = await EmailService(db).send_one(
        lead,
        subject="Una idea",
        body_text="Hola\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    email = await db.get(EmailMessage, outcome.email_id)
    assert email is not None

    await InboxService(db).ingest(
        account, _inbound(email, body_text="Por favor no me escriban más.\n")
    )
    await db.flush()

    suppressed = (
        await db.execute(select(SuppressionEntry).where(SuppressionEntry.email == email.to_email))
    ).scalar_one()
    assert suppressed.reason == "unsubscribed"

    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.status.value == "DISQUALIFIED"
    assert fresh.contact is not None
    assert fresh.contact.do_not_contact is True


@pytest.mark.asyncio
async def test_el_hilo_expone_la_intencion_como_sugerencia(
    client: AsyncClient, db: AsyncSession
) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    outcome = await EmailService(db).send_one(
        lead,
        subject="Una idea",
        body_text="Hola\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    email = await db.get(EmailMessage, outcome.email_id)
    assert email is not None
    await InboxService(db).ingest(
        account, _inbound(email, body_text="Agendemos una llamada esta semana.\n")
    )
    await db.commit()

    detail = (await client.get(f"{API}/conversations/{email.conversation_id}")).json()
    intent = detail["intent"]
    assert intent["reply_intent"] == "MEETING_REQUEST"
    assert intent["suggested_stage"] == "MEETING"
    assert intent["is_confident"] is True
    assert intent["reviewed"] is False
    assert intent["source"] == "rules"


@pytest.mark.asyncio
async def test_el_usuario_corrige_la_intencion_y_manda_su_criterio(
    client: AsyncClient, db: AsyncSession
) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    outcome = await EmailService(db).send_one(
        lead,
        subject="Una idea",
        body_text="Hola\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    email = await db.get(EmailMessage, outcome.email_id)
    assert email is not None
    await InboxService(db).ingest(account, _inbound(email, body_text="Ok, gracias.\n"))
    await db.commit()

    corrected = await client.patch(
        f"{API}/conversations/{email.conversation_id}/intent",
        json={"reply_intent": "POSITIVE", "apply_suggested_stage": True},
    )
    assert corrected.status_code == 200
    body = corrected.json()
    assert body["reply_intent"] == "POSITIVE"
    assert body["source"] == "user"
    assert body["reviewed"] is True
    assert body["confidence"] == 1.0

    # Con `apply_suggested_stage` la etapa sí se mueve — porque lo pidió.
    fresh = await LeadService(db).get_or_404(lead.id)
    assert fresh.stage.stage_type is StageType.INTERESTED

    # Y una clasificación posterior ya no pisa la corrección del usuario.
    await InboxService(db).ingest(
        account,
        _inbound(
            email,
            provider_message_id="<segunda@lafinca.co>",
            body_text="No nos interesa, gracias.\n",
        ),
    )
    await db.flush()
    conversation = await db.get(Conversation, email.conversation_id)
    assert conversation is not None
    assert conversation.reply_intent is ReplyIntent.POSITIVE


@pytest.mark.asyncio
async def test_corregir_la_intencion_no_mueve_la_etapa_por_defecto(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Cambiar una etiqueta no debería mover un prospecto de columna por
    sorpresa."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    outcome = await EmailService(db).send_one(
        lead,
        subject="Una idea",
        body_text="Hola\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    email = await db.get(EmailMessage, outcome.email_id)
    assert email is not None
    await InboxService(db).ingest(account, _inbound(email, body_text="Ok.\n"))
    await db.commit()

    antes = (await LeadService(db).get_or_404(lead.id)).stage.stage_type
    await client.patch(
        f"{API}/conversations/{email.conversation_id}/intent",
        json={"reply_intent": "PRICING"},
    )
    despues = (await LeadService(db).get_or_404(lead.id)).stage.stage_type
    assert antes is despues
