"""Selección del proveedor de correo según la cuenta.

`EmailService` pide "el proveedor de esta cuenta" y recibe una implementación
del Protocol. Renueva el token OAuth por el camino si hace falta, para que el
código de envío no tenga que preocuparse por la caducidad.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccountStatus, MailProviderType
from app.core.logging import get_logger
from app.core.security import decrypt_optional, encrypt
from app.mail import oauth
from app.mail.base import MailAuthError, MailProvider
from app.mail.gmail import GmailProvider
from app.mail.graph import GraphProvider
from app.mail.smtp import SmtpProvider
from app.models.email_account import EmailAccount

logger = get_logger(__name__)

# Margen antes de la caducidad. Renovar justo al expirar deja carreras en las
# que el token muere entre la comprobación y la petición.
_REFRESH_MARGIN = timedelta(minutes=5)


async def provider_for(account: EmailAccount, session: AsyncSession | None = None) -> MailProvider:
    """Instancia el proveedor de una cuenta, renovando el token si toca."""
    if account.provider is MailProviderType.SMTP:
        return SmtpProvider(account)

    token = await ensure_fresh_token(account, session)

    if account.provider is MailProviderType.GMAIL:
        return GmailProvider(account, token)
    return GraphProvider(account, token)


async def ensure_fresh_token(account: EmailAccount, session: AsyncSession | None = None) -> str:
    """Devuelve un access token válido, renovándolo si está por caducar.

    Si el refresh falla, la cuenta se marca `TOKEN_EXPIRED` en vez de dejarla
    fallando en silencio en cada envío: así la UI puede pedir que se reconecte.
    """
    access = decrypt_optional(account.oauth_access_token_enc)
    refresh = decrypt_optional(account.oauth_refresh_token_enc)

    still_valid = (
        access
        and account.oauth_expires_at
        and account.oauth_expires_at - _REFRESH_MARGIN > datetime.now(UTC)
    )
    if still_valid:
        return access  # type: ignore[return-value]

    if not refresh:
        account.status = AccountStatus.TOKEN_EXPIRED
        raise MailAuthError(
            f"La cuenta {account.email} no tiene token de renovación. Vuelve a conectarla.",
            code="NO_REFRESH_TOKEN",
            details={"account_id": str(account.id), "email": account.email},
        )

    try:
        tokens = await oauth.refresh_tokens(account.provider, refresh)
    except MailAuthError:
        account.status = AccountStatus.TOKEN_EXPIRED
        if session is not None:
            await session.flush()
        raise

    account.oauth_access_token_enc = encrypt(tokens.access_token)
    if tokens.refresh_token:
        account.oauth_refresh_token_enc = encrypt(tokens.refresh_token)
    account.oauth_expires_at = tokens.expires_at
    account.status = AccountStatus.ACTIVE
    if session is not None:
        await session.flush()

    logger.info("oauth_token_refreshed", account=str(account.id), provider=account.provider.value)
    return tokens.access_token
