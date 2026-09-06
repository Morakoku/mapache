"""Configuración y cuentas de correo (decisión D13)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import client as ai_client
from app.ai.providers import SPECS
from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import AIProvider, JobType, SerpProvider
from app.core.exceptions import ValidationError
from app.core.security import decrypt, encrypt
from app.mail import oauth
from app.mail.base import OutboundMessage
from app.mail.factory import provider_for
from app.mail.guardrails import effective_daily_limit
from app.models.settings import AppSettings
from app.schemas.common import JobAcceptedOut
from app.schemas.email import (
    AccountUpdate,
    AIKeyIn,
    AIProviderOptionOut,
    AIStatusOut,
    EmailAccountOut,
    OAuthProvidersOut,
    SerpKeyIn,
    SettingsOut,
    SettingsUpdate,
    SmtpAccountIn,
)
from app.scrapers.web_search import build_web_search, verify_credentials
from app.services.job_svc import JobService
from app.services.mail_admin_svc import EmailAccountService, SettingsService

router = APIRouter()

_AI_SETUP_HINT = (
    "Sin clave la IA está apagada: las plantillas se siguen renderizando y las "
    "respuestas se clasifican por reglas. Pega la clave de tu proveedor aquí "
    "abajo para activar la personalización."
)

_SETUP_HINT = (
    "Sin credenciales OAuth solo se puede conectar por SMTP. Para habilitar Gmail y "
    "Outlook, crea las apps en Google Cloud y Azure y define GOOGLE_CLIENT_ID / "
    "GOOGLE_CLIENT_SECRET / MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET."
)


def _to_out(settings: AppSettings) -> SettingsOut:
    out = SettingsOut.model_validate(settings)
    # Brave no necesita motor; Google sin `cx` no puede buscar nada.
    out.serp_configured = bool(settings.serp_api_key_enc) and (
        settings.serp_provider != SerpProvider.GOOGLE_CSE or bool(settings.serp_engine_id)
    )
    # El límite configurado y el real difieren durante la rampa de warm-up.
    # La UI muestra el real para que nadie se pregunte por qué se paró en 20.
    out.effective_daily_limit = effective_daily_limit(settings)
    return out


@router.get("", response_model=SettingsOut)
async def get_settings_row(db: AsyncSession = Depends(get_db)) -> SettingsOut:
    return _to_out(await SettingsService(db).get())


@router.patch("", response_model=SettingsOut)
async def update_settings(
    payload: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> SettingsOut:
    settings = await SettingsService(db).update(payload.model_dump(exclude_unset=True))
    await db.commit()
    return _to_out(settings)


@router.get("/ai", response_model=AIStatusOut)
async def ai_status(db: AsyncSession = Depends(get_db)) -> AIStatusOut:
    """Qué puede hacer la IA ahora mismo.

    Sin clave, `configured` viene en falso y la UI esconde el botón de generar
    en vez de ofrecer algo que fallaría. Todo lo demás del CRM sigue igual.
    """
    settings = await SettingsService(db).get()
    configured = ai_client.is_configured(settings)

    # El coste solo se estima para Claude: son los únicos precios que este CRM
    # conoce. Inventar una cifra para otro proveedor sería peor que no darla.
    per_email: float | None = None
    if configured and settings.ai_provider is AIProvider.ANTHROPIC:
        precios = ai_client.PRICING.get(settings.ai_model)
        if precios:
            price_in, price_out = precios
            # Estimación del diseño (§12.1): ~1.500 tokens de entrada y ~400 de
            # salida por correo, con el bloque de sistema ya cacheado.
            per_email = round((1500 * 0.1 * price_in + 400 * price_out) / 1_000_000, 4)

    return AIStatusOut(
        configured=configured,
        enabled=settings.ai_enabled and configured,
        provider=settings.ai_provider,
        model=settings.ai_model,
        tone=settings.ai_tone,
        has_stored_key=bool(settings.ai_api_key_enc),
        setup_hint=None if configured else _AI_SETUP_HINT,
        estimated_cost_per_email_usd=per_email,
        providers=[
            AIProviderOptionOut(
                provider=provider,
                label=spec.label,
                suggested_model=spec.suggested_model,
                api_keys_url=spec.api_keys_url,
            )
            for provider, spec in SPECS.items()
        ],
    )


@router.put("/ai", response_model=AIStatusOut)
async def set_ai_key(payload: AIKeyIn, db: AsyncSession = Depends(get_db)) -> AIStatusOut:
    """Guarda el proveedor, la clave y el modelo.

    La clave se cifra antes de tocar la base y no vuelve a salir por la API.
    Se prueba contra el proveedor antes de guardarla: una clave mal pegada que
    se descubre tres días después, en el primer correo que no se personalizó,
    es exactamente lo que hay que evitar.
    """
    service = SettingsService(db)
    settings = await service.get()

    provider = payload.provider
    model = payload.model or SPECS[provider].suggested_model

    if payload.api_key:
        api_key = payload.api_key.strip()
    elif settings.ai_api_key_enc and provider == settings.ai_provider:
        api_key = decrypt(settings.ai_api_key_enc)
    else:
        raise ValidationError(
            f"Falta la clave de {SPECS[provider].label}.",
            code="AI_KEY_REQUIRED",
        )

    await ai_client.verify_credentials(
        ai_client.AICredentials(provider=provider, api_key=api_key, model=model)
    )

    settings.ai_provider = provider
    settings.ai_model = model
    settings.ai_api_key_enc = encrypt(api_key)
    await db.commit()

    return await ai_status(db)


@router.delete("/ai", response_model=AIStatusOut)
async def clear_ai_key(db: AsyncSession = Depends(get_db)) -> AIStatusOut:
    """Borra la clave guardada. El CRM sigue funcionando sin IA."""
    settings = await SettingsService(db).get()
    settings.ai_api_key_enc = None
    await db.commit()
    return await ai_status(db)


@router.put("/serp", response_model=SettingsOut)
async def set_serp_key(payload: SerpKeyIn, db: AsyncSession = Depends(get_db)) -> SettingsOut:
    """Guarda las credenciales del buscador web.

    Es lo que permite buscar empresas en Instagram y LinkedIn sin rastrear
    esas redes —se consulta el índice que el buscador ya construyó— y lo que
    alimenta el panel de información pública de cada empresa.

    Antes de guardar se hace una consulta de prueba: una clave mal copiada
    tiene que doler ahora y no dentro de tres días, cuando falle una búsqueda
    de 100 empresas a mitad de camino.
    """
    settings = await SettingsService(db).get()

    api_key = payload.api_key.strip() if payload.api_key else None
    if not api_key and settings.serp_api_key_enc:
        api_key = decrypt(settings.serp_api_key_enc)
    engine_id = (payload.engine_id or "").strip() or None

    buscador = build_web_search(
        provider=payload.provider, api_key=api_key, engine_id=engine_id
    )
    await verify_credentials(buscador)

    if api_key:
        settings.serp_api_key_enc = encrypt(api_key)
    settings.serp_provider = payload.provider
    settings.serp_engine_id = engine_id
    await db.commit()
    return _to_out(settings)


@router.delete("/serp", response_model=SettingsOut)
async def clear_serp_key(db: AsyncSession = Depends(get_db)) -> SettingsOut:
    settings = await SettingsService(db).get()
    settings.serp_api_key_enc = None
    settings.serp_engine_id = None
    await db.commit()
    return _to_out(settings)


@router.get("/oauth-providers", response_model=OAuthProvidersOut)
async def list_oauth_providers() -> OAuthProvidersOut:
    """Qué botones de conexión tiene sentido pintar.

    Sin claves configuradas la lista viene vacía y la UI ofrece solo SMTP, en
    vez de un botón que llevaría a un error del proveedor.
    """
    available = oauth.available_providers()
    return OAuthProvidersOut(
        available=available,
        setup_hint=None if available else _SETUP_HINT,
    )


# ------------------------------------------------------------------ cuentas


@router.get("/email-accounts", response_model=list[EmailAccountOut])
async def list_accounts(db: AsyncSession = Depends(get_db)) -> list[EmailAccountOut]:
    accounts = await EmailAccountService(db).list_accounts()
    return [EmailAccountOut.model_validate(a) for a in accounts]


@router.post(
    "/email-accounts/smtp",
    response_model=EmailAccountOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_smtp_account(
    payload: SmtpAccountIn,
    db: AsyncSession = Depends(get_db),
) -> EmailAccountOut:
    """Alta manual SMTP/IMAP.

    Es el camino que funciona sin credenciales OAuth: cualquier buzón con
    contraseña de aplicación sirve.
    """
    account = await EmailAccountService(db).create_smtp(payload.model_dump())
    await db.commit()
    return EmailAccountOut.model_validate(account)


@router.patch("/email-accounts/{account_id}", response_model=EmailAccountOut)
async def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> EmailAccountOut:
    account = await EmailAccountService(db).update(
        account_id, payload.model_dump(exclude_unset=True)
    )
    await db.commit()
    return EmailAccountOut.model_validate(account)


@router.delete("/email-accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(account_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    """Desconecta la cuenta y revoca el token en el proveedor."""
    await EmailAccountService(db).disconnect(account_id)
    await db.commit()


@router.post("/email-accounts/{account_id}/verify", response_model=EmailAccountOut)
async def verify_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> EmailAccountOut:
    """Comprueba credenciales contra el proveedor y actualiza el estado."""
    service = EmailAccountService(db)
    try:
        account = await service.verify(account_id)
    finally:
        # El estado de error también se guarda: si la cuenta dejó de
        # funcionar, la UI tiene que enterarse.
        await db.commit()
    return EmailAccountOut.model_validate(account)


@router.post(
    "/email-accounts/{account_id}/resync",
    response_model=JobAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resync_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedOut:
    """Fuerza la lectura del buzón.

    Con webhook activo no hace falta; sirve cuando el push se cae o cuando la
    cuenta es SMTP/IMAP, que no tiene notificación push.
    """
    account = await EmailAccountService(db).get_or_404(account_id)

    payload = {"account_id": str(account.id)}
    job = await JobService(db).create(JobType.INBOX_SYNC, payload, progress_total=1)
    await db.commit()

    await get_job_queue().enqueue(JobType.INBOX_SYNC, payload, job_id=job.id)
    return JobAcceptedOut(job_id=job.id, message=f"Revisando el buzón de {account.email}.")


@router.post("/email-accounts/{account_id}/test", response_model=dict)
async def send_test_email(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Envía un correo de prueba a la propia cuenta.

    No pasa por los guardrails ni deja rastro de tracking: no es prospección,
    es una comprobación de que el transporte funciona.
    """
    service = EmailAccountService(db)
    account = await service.get_or_404(account_id)
    settings = await SettingsService(db).get()

    provider = await provider_for(account, db)
    result = await provider.send(
        OutboundMessage(
            from_email=account.email,
            from_name=settings.sender_name or account.display_name or "",
            to_email=account.email,
            subject="Prueba de conexión del CRM",
            body_text=(
                "Si lees esto, la cuenta está bien conectada y el CRM puede enviar "
                "correo desde ella.\n"
            ),
        )
    )
    await db.commit()
    return {"sent_to": account.email, "provider_message_id": result.provider_message_id}
