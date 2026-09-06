"""Fase 4 — endpoints de configuración, cuentas, plantillas y correo.

`/emails/send` no se prueba aquí: encola un job que corre con su propia sesión
y no vería la transacción del test. Su camino completo se verifica en el
ensayo end-to-end contra la base de desarrollo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.mail.guardrails import local_now
from app.models.email import SuppressionEntry
from tests.integration.test_email_send import (
    _mailhog_available,
    _mailhog_find,
    _mailhog_reset,
    _seed,
    _seed_account,
    _seed_settings,
)

API = "/api/v1"


@pytest.mark.asyncio
async def test_settings_muestra_el_limite_real_con_warmup(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Durante la rampa, el límite configurado y el real no coinciden.

    La UI enseña el real; si no, el usuario no entiende por qué se paró en 20.
    """
    settings = await _seed_settings(db)
    settings.warmup_enabled = True
    settings.warmup_started_on = datetime.now(UTC).date()
    await db.flush()

    response = await client.get(f"{API}/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["daily_send_limit"] == 100
    assert body["effective_daily_limit"] == 20
    assert body["country_code"] == "CO"
    assert body["timezone"] == "America/Bogota"


@pytest.mark.asyncio
async def test_patch_settings_arranca_la_rampa(client: AsyncClient, db: AsyncSession) -> None:
    settings = await _seed_settings(db, warmup_enabled=False)

    response = await client.patch(
        f"{API}/settings", json={"warmup_enabled": True, "sender_name": "Daniel Ruiz"}
    )
    assert response.status_code == 200
    assert response.json()["warmup_started_on"] == local_now(settings).date().isoformat()


@pytest.mark.asyncio
async def test_sin_claves_oauth_la_ui_solo_ofrece_smtp(client: AsyncClient) -> None:
    response = await client.get(f"{API}/settings/oauth-providers")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] == []
    assert body["smtp_always_available"] is True
    assert "GOOGLE_CLIENT_ID" in body["setup_hint"]


@pytest.mark.asyncio
async def test_connect_sin_credenciales_explica_que_falta(client: AsyncClient) -> None:
    response = await client.get("/auth/google/connect", follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OAUTH_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_alta_smtp_no_devuelve_credenciales(client: AsyncClient) -> None:
    """Las contraseñas se cifran y no salen por la API, ni cifradas."""
    response = await client.post(
        f"{API}/settings/email-accounts/smtp",
        json={
            "email": "daniel@midominio.co",
            "display_name": "Daniel Ruiz",
            "smtp_host": "localhost",
            "smtp_port": 1025,
            "smtp_password": "secreto-que-no-debe-salir",
            "smtp_use_tls": False,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["provider"] == "SMTP"
    assert body["is_default"] is True  # la primera cuenta pasa a serlo
    assert body["is_oauth"] is False
    assert "secreto-que-no-debe-salir" not in response.text
    assert not any("password" in key or "_enc" in key for key in body)

    listed = await client.get(f"{API}/settings/email-accounts")
    assert [a["email"] for a in listed.json()] == ["daniel@midominio.co"]


@pytest.mark.asyncio
async def test_plantilla_con_variable_inventada_se_rechaza(client: AsyncClient) -> None:
    response = await client.post(
        f"{API}/templates",
        json={
            "name": "Rota",
            "subject": "Hola {{compnay_name}}",
            "body_text": "cuerpo",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_TEMPLATE_VARIABLE"


@pytest.mark.asyncio
async def test_ciclo_de_plantillas(client: AsyncClient) -> None:
    variables = await client.get(f"{API}/templates/variables")
    keys = {v["key"] for v in variables.json()}
    assert "company_name" in keys and "unsubscribe_url" in keys

    created = await client.post(
        f"{API}/templates",
        json={
            "name": "Primer contacto",
            "category": "first_contact",
            "subject": "Una idea para {{company_name}}",
            "body_text": "Hola {{first_name}}, ¿hablamos?\n",
        },
    )
    assert created.status_code == 201
    template = created.json()
    # La baja se registra siempre como dependencia, aunque no esté en el texto:
    # el pie legal la inyecta al enviar.
    assert "unsubscribe_url" in template["variables_used"]
    assert "company_name" in template["variables_used"]

    patched = await client.patch(
        f"{API}/templates/{template['id']}",
        json={"subject": "Idea para {{company_name}} en {{city}}"},
    )
    assert patched.status_code == 200
    assert "city" in patched.json()["variables_used"]

    deleted = await client.delete(f"{API}/templates/{template['id']}?confirm=true")
    assert deleted.status_code == 204
    assert (await client.get(f"{API}/templates/{template['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_nombre_de_plantilla_repetido_da_409(client: AsyncClient) -> None:
    """Insensible a mayúsculas.

    El índice lleva `NULLS NOT DISTINCT`: sin eso, con `owner_id` nulo —el caso
    del MVP— Postgres consideraría distintas ambas filas y no habría unicidad.
    """
    first = await client.post(
        f"{API}/templates",
        json={"name": "Primer contacto", "subject": "Hola", "body_text": "cuerpo"},
    )
    assert first.status_code == 201

    duplicated = await client.post(
        f"{API}/templates",
        json={"name": "primer contacto", "subject": "x", "body_text": "y"},
    )
    assert duplicated.status_code == 409
    assert duplicated.json()["error"]["code"] == "TEMPLATE_NAME_TAKEN"


@pytest.mark.asyncio
async def test_preview_avisa_de_variables_sin_dato(client: AsyncClient, db: AsyncSession) -> None:
    lead = await _seed(db)
    await _seed_settings(db)
    await _seed_account(db)
    lead.company.city = None
    await db.flush()

    template = (
        await client.post(
            f"{API}/templates",
            json={
                "name": "Con ciudad",
                "subject": "Hola {{company_name}}",
                "body_text": "Trabajáis en {{city}}, ¿verdad?\n",
            },
        )
    ).json()

    response = await client.post(
        f"{API}/emails/preview",
        json={"lead_ids": [str(lead.id)], "template_id": template["id"]},
    )
    assert response.status_code == 200
    draft = response.json()[0]
    assert draft["can_send"] is True
    assert draft["subject"] == "Hola Restaurante La Finca"
    assert "{{" not in draft["body_text"]
    assert any("city" in w for w in draft["warnings"])


@pytest.mark.asyncio
async def test_preview_marca_lo_que_los_guardrails_bloquearian(
    client: AsyncClient, db: AsyncSession
) -> None:
    """El usuario ve el problema antes de pulsar enviar, no después."""
    lead = await _seed(db, email="baja@lafinca.co")
    await _seed_settings(db)
    await _seed_account(db)

    db.add(SuppressionEntry(email="baja@lafinca.co", reason="unsubscribed"))
    await db.flush()

    response = await client.post(
        f"{API}/emails/preview",
        json={
            "lead_ids": [str(lead.id)],
            "subject": "Hola {{company_name}}",
            "body_text": "cuerpo\n",
        },
    )
    draft = response.json()[0]
    assert draft["can_send"] is False
    assert "no contactar" in draft["blocked_reason"]


@pytest.mark.asyncio
async def test_preview_sin_plantilla_ni_cuerpo_falla(client: AsyncClient, db: AsyncSession) -> None:
    lead = await _seed(db)
    await _seed_settings(db)
    await _seed_account(db)

    response = await client.post(f"{API}/emails/preview", json={"lead_ids": [str(lead.id)]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TEMPLATE_OR_BODY_REQUIRED"


@pytest.mark.asyncio
async def test_enviar_sin_cuenta_conectada_lo_dice_claro(
    client: AsyncClient, db: AsyncSession
) -> None:
    lead = await _seed(db)
    await _seed_settings(db)

    response = await client.post(
        f"{API}/emails/send",
        json={"drafts": [{"lead_id": str(lead.id), "subject": "Hola", "body_text": "cuerpo"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "NO_EMAIL_ACCOUNT"


@pytest.mark.asyncio
async def test_supresion_crud_y_csv(client: AsyncClient) -> None:
    created = await client.post(
        f"{API}/suppression", json={"email": "Nope@Empresa.CO", "reason": "manual"}
    )
    assert created.status_code == 201
    # Normalizado a minúsculas: la comparación en el envío es case-insensitive.
    assert created.json()["email"] == "nope@empresa.co"

    repeated = await client.post(
        f"{API}/suppression", json={"email": "nope@empresa.co", "reason": "manual"}
    )
    assert repeated.json()["id"] == created.json()["id"]

    csv_body = "email,domain,reason\nuno@x.co,,manual\n,competencia.co,competitor\n,,\n"
    imported = await client.post(
        f"{API}/suppression/import",
        files={"file": ("lista.csv", csv_body.encode(), "text/csv")},
    )
    assert imported.status_code == 201
    assert imported.json() == {"imported": 2, "skipped": 1}

    listed = await client.get(f"{API}/suppression", params={"q": "competencia"})
    assert listed.json()["total"] == 1

    deleted = await client.delete(f"{API}/suppression/{created.json()['id']}?confirm=true")
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_csv_sin_columnas_utiles_se_rechaza(client: AsyncClient) -> None:
    response = await client.post(
        f"{API}/suppression/import",
        files={"file": ("mal.csv", b"nombre,telefono\nJuan,123\n", "text/csv")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CSV_MISSING_COLUMNS"


@pytest.mark.asyncio
async def test_conversacion_completa_con_respuesta(client: AsyncClient, db: AsyncSession) -> None:
    """Enviar, listar el hilo y responder desde la bandeja."""
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")
    await _mailhog_reset()

    from app.services.email_svc import EmailService

    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    outcome = await EmailService(db).send_one(
        lead,
        subject="Una idea para La Finca",
        body_text="Hola Camila, ¿hablamos?\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    assert outcome.sent
    await db.flush()

    listed = await client.get(f"{API}/conversations")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    conversation = listed.json()["items"][0]
    assert conversation["last_direction"] == "OUTBOUND"
    assert conversation["status"] == "AWAITING_REPLY"

    # El filtro "sin respuesta" es el que usa el comercial cada mañana.
    unanswered = await client.get(f"{API}/conversations", params={"filter": "unanswered"})
    assert unanswered.json()["total"] == 1
    answered = await client.get(f"{API}/conversations", params={"filter": "answered"})
    assert answered.json()["total"] == 0

    detail = await client.get(f"{API}/conversations/{conversation['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["messages"]) == 1

    reply = await client.post(
        f"{API}/conversations/{conversation['id']}/reply",
        json={"body_text": "Te dejo el enlace: https://midominio.co/casos\n"},
    )
    assert reply.status_code == 200
    assert reply.json()["direction"] == "OUTBOUND"

    after = await client.get(f"{API}/conversations/{conversation['id']}")
    assert len(after.json()["messages"]) == 2

    # La respuesta llegó de verdad y mantiene el hilo.
    received = await _mailhog_find("gerente@lafinca.co")
    assert received is not None

    closed = await client.post(f"{API}/conversations/{conversation['id']}/close")
    assert closed.json()["status"] == "CLOSED"


@pytest.mark.asyncio
async def test_conversacion_inexistente_da_404(client: AsyncClient) -> None:
    response = await client.get(f"{API}/conversations/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_listado_de_correos_por_lead(client: AsyncClient, db: AsyncSession) -> None:
    if not await _mailhog_available():
        pytest.skip("Mailhog no está levantado (make up)")

    from app.services.email_svc import EmailService

    lead = await _seed(db)
    settings = await _seed_settings(db)
    account = await _seed_account(db)

    outcome = await EmailService(db).send_one(
        lead,
        subject="Hola",
        body_text="cuerpo con enlace https://midominio.co/casos\n",
        body_html=None,
        template=None,
        settings=settings,
        account=account,
    )
    await db.flush()

    listed = await client.get(f"{API}/emails", params={"lead_id": str(lead.id)})
    assert listed.json()["total"] == 1

    detail = await client.get(f"{API}/emails/{outcome.email_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "SENT"
    assert [e["event_type"] for e in body["events"]] == ["SENT"]
    assert len(body["links"]) == 1

    # Un correo ya enviado no se cancela: mentiría al usuario.
    cancelled = await client.post(f"{API}/emails/{outcome.email_id}/cancel")
    assert cancelled.status_code == 400
    assert cancelled.json()["error"]["code"] == "EMAIL_NOT_CANCELLABLE"
