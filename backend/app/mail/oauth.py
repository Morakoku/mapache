"""Flujo OAuth de Gmail y Microsoft (decisión D13).

El código está completo pero **sin credenciales**: hay que crear un proyecto
en Google Cloud Console y registrar una app en Azure, y poner los client
id/secret en el entorno. Sin ellos, `is_configured()` devuelve False y la UI
ofrece solo SMTP.

Los scopes son el mínimo que permite enviar y leer respuestas: nada de acceso
completo al buzón.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.enums import MailProviderType
from app.core.exceptions import ConfigurationError
from app.core.logging import get_logger
from app.mail.base import MailAuthError

logger = get_logger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

MS_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MS_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MS_ME_URL = "https://graph.microsoft.com/v1.0/me"

# Mínimo imprescindible: enviar y leer. Nada de borrar ni de gestionar
# etiquetas — cuanto menor el scope, menos fricción en la pantalla de consentimiento.
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "email",
)
MS_SCOPES = ("Mail.Send", "Mail.Read", "User.Read", "offline_access")


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: list[str]
    email: str | None = None
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    client_id: str | None
    client_secret: str | None
    redirect_uri: str

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def google_config() -> ProviderConfig:
    settings = get_settings()
    return ProviderConfig(
        client_id=settings.google_client_id,
        client_secret=(
            settings.google_client_secret.get_secret_value()
            if settings.google_client_secret
            else None
        ),
        redirect_uri=f"{settings.public_base_url.rstrip('/')}/auth/google/callback",
    )


def microsoft_config() -> ProviderConfig:
    settings = get_settings()
    return ProviderConfig(
        client_id=settings.microsoft_client_id,
        client_secret=(
            settings.microsoft_client_secret.get_secret_value()
            if settings.microsoft_client_secret
            else None
        ),
        redirect_uri=f"{settings.public_base_url.rstrip('/')}/auth/microsoft/callback",
    )


def config_for(provider: MailProviderType) -> ProviderConfig:
    if provider is MailProviderType.GMAIL:
        return google_config()
    if provider is MailProviderType.MICROSOFT:
        return microsoft_config()
    raise ConfigurationError(f"{provider} no usa OAuth.", code="PROVIDER_NOT_OAUTH")


def available_providers() -> list[str]:
    """Proveedores OAuth con credenciales puestas.

    La UI usa esto para decidir qué botones enseñar: sin claves, solo SMTP.
    """
    available = []
    if google_config().is_configured:
        available.append(MailProviderType.GMAIL.value)
    if microsoft_config().is_configured:
        available.append(MailProviderType.MICROSOFT.value)
    return available


def build_authorization_url(
    provider: MailProviderType, state: str, code_challenge: str | None = None
) -> str:
    """URL de consentimiento a la que se redirige al usuario."""
    config = config_for(provider)
    if not config.is_configured:
        raise ConfigurationError(
            f"Falta configurar las credenciales OAuth de {provider.value}. "
            "Añade el client id y el secret en el entorno.",
            code="OAUTH_NOT_CONFIGURED",
            details={"provider": provider.value},
        )

    if provider is MailProviderType.GMAIL:
        params = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            # `offline` + `consent` fuerzan la entrega del refresh_token:
            # sin él la cuenta deja de funcionar en una hora.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": " ".join(MS_SCOPES),
        "response_mode": "query",
        "state": state,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{MS_AUTH_URL}?{urlencode(params)}"


def new_state() -> str:
    """Nonce de un solo uso contra CSRF en el callback."""
    return secrets.token_urlsafe(32)


def new_pkce_pair() -> tuple[str, str]:
    """`(verifier, challenge)` para PKCE S256 (§15 del diseño).

    Con PKCE, un `code` interceptado no sirve sin el verificador, que nunca
    sale de este proceso.
    """
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


@dataclass(frozen=True, slots=True)
class PendingAuth:
    provider: MailProviderType
    verifier: str
    created_at: datetime


# Flujos abiertos, en memoria: el consentimiento dura segundos y es un solo
# usuario. Un reinicio a mitad del flujo hace fallar el callback, que es el
# comportamiento correcto —se vuelve a pulsar "conectar"— y no deja estados
# reutilizables por ahí.
_STATE_TTL = timedelta(minutes=10)
_pending: dict[str, PendingAuth] = {}


def start_flow(provider: MailProviderType) -> str:
    """Prepara el estado y devuelve la URL de consentimiento."""
    state = new_state()
    verifier, challenge = new_pkce_pair()
    _prune_states()
    _pending[state] = PendingAuth(provider, verifier, datetime.now(UTC))
    return build_authorization_url(provider, state, challenge)


def consume_state(state: str) -> PendingAuth:
    """Valida y quema el `state` del callback.

    De un solo uso: sin esto, un `state` filtrado permitiría repetir el
    callback (CSRF).
    """
    _prune_states()
    pending = _pending.pop(state, None)
    if pending is None:
        raise MailAuthError(
            "El enlace de conexión caducó o ya se usó. Vuelve a intentarlo.",
            code="OAUTH_STATE_INVALID",
        )
    return pending


def _prune_states() -> None:
    cutoff = datetime.now(UTC) - _STATE_TTL
    for state in [s for s, p in _pending.items() if p.created_at < cutoff]:
        _pending.pop(state, None)


async def exchange_code(
    provider: MailProviderType, code: str, code_verifier: str | None = None
) -> OAuthTokens:
    """Cambia el `code` del callback por tokens."""
    config = config_for(provider)
    token_url = GOOGLE_TOKEN_URL if provider is MailProviderType.GMAIL else MS_TOKEN_URL

    data = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code": code,
        "redirect_uri": config.redirect_uri,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    if provider is MailProviderType.MICROSOFT:
        data["scope"] = " ".join(MS_SCOPES)

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(token_url, data=data)
        if response.status_code != 200:
            raise MailAuthError(
                f"El proveedor rechazó el código de autorización: {response.text[:200]}",
                code="OAUTH_EXCHANGE_FAILED",
            )
        payload = response.json()
        identity = await _fetch_identity(client, provider, payload["access_token"])

    return OAuthTokens(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600))),
        scopes=payload.get("scope", "").split(),
        email=identity.get("email"),
        external_id=identity.get("id"),
    )


async def refresh_tokens(provider: MailProviderType, refresh_token: str) -> OAuthTokens:
    """Renueva el access token.

    Google no siempre devuelve un refresh_token nuevo: si falta, se conserva
    el que ya teníamos en vez de dejar la cuenta sin él.
    """
    config = config_for(provider)
    token_url = GOOGLE_TOKEN_URL if provider is MailProviderType.GMAIL else MS_TOKEN_URL

    data = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if provider is MailProviderType.MICROSOFT:
        data["scope"] = " ".join(MS_SCOPES)

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(token_url, data=data)
        if response.status_code != 200:
            raise MailAuthError(
                "No se pudo renovar el acceso al buzón. Vuelve a conectar la cuenta.",
                code="OAUTH_REFRESH_FAILED",
                details={"provider": provider.value},
            )
        payload = response.json()

    return OAuthTokens(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token") or refresh_token,
        expires_at=datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600))),
        scopes=payload.get("scope", "").split(),
    )


async def revoke(provider: MailProviderType, token: str) -> None:
    """Revoca el token **en el proveedor**, no solo en nuestra base.

    Desconectar una cuenta y dejar el token vivo en Google sería un problema
    de seguridad: el usuario cree que revocó el acceso y no es así.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        if provider is MailProviderType.GMAIL:
            await client.post(GOOGLE_REVOKE_URL, data={"token": token})
        else:
            # Microsoft no expone endpoint de revocación por token; la
            # desconexión se hace desde el portal de la cuenta del usuario.
            logger.info("microsoft_revoke_manual", note="revocar desde el portal de Microsoft")


async def _fetch_identity(
    client: httpx.AsyncClient, provider: MailProviderType, access_token: str
) -> dict[str, str]:
    """Obtiene el email de la cuenta recién conectada."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        if provider is MailProviderType.GMAIL:
            response = await client.get(GOOGLE_USERINFO_URL, headers=headers)
            data = response.json()
            return {"email": data.get("email", ""), "id": data.get("sub", "")}

        response = await client.get(MS_ME_URL, headers=headers)
        data = response.json()
        return {
            "email": data.get("mail") or data.get("userPrincipalName", ""),
            "id": data.get("id", ""),
        }
    except Exception as exc:  # noqa: BLE001 - la identidad es informativa
        logger.warning("oauth_identity_failed", provider=provider.value, error=str(exc))
        return {}
