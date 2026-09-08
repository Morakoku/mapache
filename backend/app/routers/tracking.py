"""Módulos 12 y 13 — endpoints públicos de tracking.

Sin autenticación por necesidad: los llaman clientes de correo y navegadores
de los prospectos. Los tokens son UUID v4 (122 bits), no enumerables, y las
respuestas nunca revelan si el token existe — siempre devuelven el GIF o la
redirección.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import ActivityType, ActorType, EmailEventType
from app.core.logging import get_logger
from app.models.email import EmailEvent, EmailLink, EmailMessage
from app.services.activity_svc import ActivityService
from app.services.lead_svc import LeadService
from app.services.mail_admin_svc import SuppressionService

router = APIRouter()
logger = get_logger(__name__)

# GIF transparente de 1x1.
_PIXEL = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)

# Proxies que precargan imágenes. Sus aperturas se registran pero no cuentan:
# Apple MPP carga el pixel de todos los correos, respondan o no.
_BOT_AGENTS = (
    "googleimageproxy",
    "yahoomailproxy",
    "bingpreview",
    "barracuda",
    "proofpoint",
    "mimecast",
    "symantec",
    "microsoft office",
    "outlook-ios",
)
# Una apertura en los primeros segundos es un escáner, no una persona.
_BOT_WINDOW = timedelta(seconds=3)


def _looks_like_bot(user_agent: str | None, sent_at: datetime | None) -> bool:
    if user_agent and any(marker in user_agent.lower() for marker in _BOT_AGENTS):
        return True
    return bool(sent_at and datetime.now(UTC) - sent_at < _BOT_WINDOW)


@router.get("/open/{token}.gif", include_in_schema=False)
async def track_open(
    token: uuid.UUID,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
) -> Response:
    """Pixel de apertura.

    Devuelve el GIF siempre, exista o no el token: confirmar la existencia
    permitiría enumerar envíos desde fuera.
    """
    response = Response(
        content=_PIXEL,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )

    result = await db.execute(select(EmailMessage).where(EmailMessage.tracking_token == token))
    email = result.scalar_one_or_none()
    if email is None:
        return response

    user_agent = request.headers.get("user-agent")
    is_bot = _looks_like_bot(user_agent, email.sent_at)
    now = datetime.now(UTC)

    # LOOP-13: límite a la creación de EmailEvent — una sola fila de apertura
    # por correo cada 60 s; evita inundar la tabla ante proxy/refresh.
    already_logged = await db.execute(
        select(EmailEvent.id)
        .where(
            EmailEvent.email_message_id == email.id,
            EmailEvent.event_type == EmailEventType.OPENED,
            EmailEvent.occurred_at >= now - timedelta(seconds=60),
        )
        .limit(1)
    )
    if already_logged.scalar_one_or_none() is None:
        db.add(
            EmailEvent(
                email_message_id=email.id,
                event_type=EmailEventType.OPENED,
                user_agent=user_agent,
                ip_address=request.client.host if request.client else None,
                is_likely_bot=is_bot,
            )
        )

    if not is_bot:
        first_open = email.opened_at is None
        email.open_count += 1
        email.opened_at = now
        if first_open:
            email.first_opened_at = now
        # Una apertura implica que el correo llegó.
        if email.delivered_at is None:
            email.delivered_at = now

        if email.lead_id:
            leads = LeadService(db)
            lead = await leads.repo.get(email.lead_id)
            if lead is not None:
                await leads.add_engagement(
                    lead, "EMAIL_OPENED" if first_open else "EMAIL_OPENED_MULTIPLE"
                )
                await leads.auto_advance(lead, "EMAIL_OPENED")
                await ActivityService(db).record(
                    ActivityType.EMAIL_OPENED,
                    # "Detectada", no "el usuario leyó": el tracking de
                    # apertura no da certeza (§11.2).
                    title="Apertura detectada",
                    lead_id=lead.id,
                    company_id=lead.company_id,
                    actor=ActorType.PROSPECT,
                    metadata={"open_count": email.open_count},
                    owner_id=lead.owner_id,
                )

    await db.commit()
    return response


@router.get("/click/{token}", include_in_schema=False)
async def track_click(
    token: uuid.UUID,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
) -> Response:
    """Registra el click y redirige al destino real.

    Un click es dato duro: requiere acción humana deliberada, a diferencia de
    la apertura. Por eso puntúa cuatro veces más en el engagement.
    """
    result = await db.execute(select(EmailLink).where(EmailLink.tracking_token == token))
    link = result.scalar_one_or_none()
    if link is None:
        return RedirectResponse(url="/", status_code=302)

    # LOOP-13: nunca redirigir a esquemas no http(s) (javascript:, data:…).
    original = link.original_url or ""
    if not (original.startswith("http://") or original.startswith("https://")):
        return RedirectResponse(url="/", status_code=302)

    email = await db.get(EmailMessage, link.email_message_id)
    now = datetime.now(UTC)

    link.click_count += 1
    link.last_clicked_at = now
    if link.first_clicked_at is None:
        link.first_clicked_at = now

    if email is not None:
        db.add(
            EmailEvent(
                email_message_id=email.id,
                email_link_id=link.id,
                event_type=EmailEventType.CLICKED,
                user_agent=request.headers.get("user-agent"),
                ip_address=request.client.host if request.client else None,
            )
        )
        email.click_count += 1
        email.clicked_at = now
        # Un click implica apertura, aunque el pixel no se haya cargado.
        if email.opened_at is None:
            email.opened_at = now
            email.first_opened_at = now
            email.open_count = max(email.open_count, 1)
        if email.delivered_at is None:
            email.delivered_at = now

        if email.lead_id:
            leads = LeadService(db)
            lead = await leads.repo.get(email.lead_id)
            if lead is not None:
                await leads.add_engagement(lead, "EMAIL_CLICKED")
                await leads.auto_advance(lead, "EMAIL_OPENED")
                await ActivityService(db).record(
                    ActivityType.EMAIL_CLICKED,
                    title="Link visitado",
                    description=link.original_url,
                    lead_id=lead.id,
                    company_id=lead.company_id,
                    actor=ActorType.PROSPECT,
                    owner_id=lead.owner_id,
                )

    await db.commit()
    return RedirectResponse(url=original, status_code=302)


@router.get("/unsubscribe/{token}", response_class=HTMLResponse, include_in_schema=False)
async def unsubscribe_page(token: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> HTMLResponse:
    """Página de confirmación de baja."""
    result = await db.execute(select(EmailMessage).where(EmailMessage.unsubscribe_token == token))
    email = result.scalar_one_or_none()
    if email is None:
        return HTMLResponse(_page("Este enlace ya no es válido."), status_code=200)

    return HTMLResponse(
        _page(
            "¿Quieres dejar de recibir correos nuestros?",
            form_action=f"/tracking/unsubscribe/{token}",
        )
    )


@router.post("/unsubscribe/{token}", include_in_schema=False)
async def unsubscribe(token: uuid.UUID, db: AsyncSession | None = Depends(get_db)) -> HTMLResponse:
    """Procesa la baja.

    Acepta también el POST automático de `List-Unsubscribe-Post` (RFC 8058),
    que es como Gmail y Outlook ejecutan la baja en un clic.
    """
    result = await db.execute(select(EmailMessage).where(EmailMessage.unsubscribe_token == token))
    email = result.scalar_one_or_none()
    if email is None:
        return HTMLResponse(_page("Este enlace ya no es válido."), status_code=200)

    await SuppressionService(db).add(
        email=email.to_email,
        reason="unsubscribed",
        source_email_id=email.id,
    )
    db.add(EmailEvent(email_message_id=email.id, event_type=EmailEventType.UNSUBSCRIBED))

    if email.contact_id:
        from app.models.contact import Contact

        contact = await db.get(Contact, email.contact_id)
        if contact is not None:
            contact.do_not_contact = True

    if email.lead_id:
        from app.core.enums import LeadStatus

        lead = await LeadService(db).repo.get(email.lead_id)
        if lead is not None:
            lead.status = LeadStatus.DISQUALIFIED
            lead.sequence_paused = True
            # Quien pidió no recibir más correos no puede seguir teniendo
            # seguimientos programados esperando su turno.
            from app.services.sequence_svc import FollowUpService

            await FollowUpService(db).cancel_pending_for_lead(lead.id, reason="unsubscribed")
            await ActivityService(db).record(
                ActivityType.NOTE,
                title="El prospecto canceló la suscripción",
                lead_id=lead.id,
                company_id=lead.company_id,
                actor=ActorType.PROSPECT,
                owner_id=lead.owner_id,
            )

    await db.commit()
    logger.info("unsubscribed", email=email.to_email)
    return HTMLResponse(_page("Listo. No volverás a recibir correos nuestros."))


def _page(message: str, form_action: str | None = None) -> str:
    button = (
        f'<form method="post" action="{form_action}">'
        '<button type="submit">Cancelar suscripción</button></form>'
        if form_action
        else ""
    )
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Suscripción</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #f8fafc; color: #0f172a;
          display: grid; place-items: center; min-height: 100vh; margin: 0; }}
  .card {{ background: #fff; padding: 40px; border-radius: 12px; max-width: 420px;
           text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  button {{ background: #4f46e5; color: #fff; border: 0; border-radius: 8px;
            padding: 12px 20px; font-size: 15px; cursor: pointer; margin-top: 16px; }}
</style></head>
<body><div class="card"><p>{message}</p>{button}</div></body></html>"""
