"""Conexión de buzones por OAuth (Gmail y Microsoft).

El código está completo pero **sin credenciales**: mientras no haya client id
y secret en el entorno, `/connect` devuelve un error explicando qué falta y la
UI ofrece solo SMTP. Nada de esto bloquea el envío.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.enums import MailProviderType
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.mail import oauth
from app.routers.security_schemes import SERVICE_BEARER
from app.schemas.email import EmailAccountOut
from app.services.mail_admin_svc import EmailAccountService

router = APIRouter()
logger = get_logger(__name__)

_PROVIDERS = {
    "google": MailProviderType.GMAIL,
    "microsoft": MailProviderType.MICROSOFT,
}


def _provider_or_400(name: str) -> MailProviderType:
    provider = _PROVIDERS.get(name.lower())
    if provider is None:
        raise ValidationError(
            f"Proveedor OAuth desconocido: {name}.",
            code="UNKNOWN_OAUTH_PROVIDER",
            details={"available": sorted(_PROVIDERS)},
        )
    return provider


@router.get("/{provider}/connect", include_in_schema=False)
async def connect(provider: str) -> RedirectResponse:
    """Redirige a la pantalla de consentimiento del proveedor."""
    url = oauth.start_flow(_provider_or_400(provider))
    return RedirectResponse(url=url, status_code=302)


@router.get("/{provider}/callback", include_in_schema=False, response_model=None)
async def callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = Query(default=None),
    db: AsyncSession | None = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Recibe el `code`, lo cambia por tokens y guarda la cuenta."""
    provider_type = _provider_or_400(provider)

    if error:
        logger.warning("oauth_callback_error", provider=provider, error=error)
        return _result_page(
            "No se pudo conectar la cuenta",
            error_description or error,
            ok=False,
        )

    if not code or not state:
        return _result_page(
            "Respuesta incompleta del proveedor",
            "Faltan parámetros en el callback. Vuelve a intentar la conexión.",
            ok=False,
        )

    pending = oauth.consume_state(state)
    if pending.provider is not provider_type:
        # El state pertenece a otro proveedor: o alguien lo manipuló, o se
        # cruzaron dos flujos. En ambos casos, no se sigue.
        return _result_page(
            "Conexión inválida",
            "El estado de la conexión no coincide. Vuelve a intentarlo.",
            ok=False,
        )

    tokens = await oauth.exchange_code(provider_type, code, pending.verifier)
    account = await EmailAccountService(db).upsert_oauth(provider_type, tokens)
    await db.commit()

    frontend = get_settings().frontend_base_url
    if frontend:
        return RedirectResponse(
            url=f"{frontend.rstrip('/')}/settings?connected={account.email}", status_code=302
        )
    return _result_page(
        "Cuenta conectada",
        f"Ya puedes enviar desde {account.email}. Puedes cerrar esta pestaña.",
        ok=True,
    )


@router.post(
    "/{provider}/disconnect/{account_id}",
    response_model=dict,
    dependencies=[Depends(SERVICE_BEARER)],
)
async def disconnect(
    provider: str,
    account_id: uuid.UUID,
    db: AsyncSession | None = Depends(get_db),
) -> dict[str, bool]:
    """Revoca el token **en el proveedor** y borra la cuenta."""
    _provider_or_400(provider)
    await EmailAccountService(db).disconnect(account_id)
    await db.commit()
    return {"disconnected": True}


@router.get(
    "/accounts",
    response_model=list[EmailAccountOut],
    dependencies=[Depends(SERVICE_BEARER)],
)
async def list_connected(db: AsyncSession | None = Depends(get_db)) -> list[EmailAccountOut]:
    accounts = await EmailAccountService(db).list_accounts()
    return [EmailAccountOut.model_validate(a) for a in accounts]


def _result_page(title: str, message: str, *, ok: bool) -> HTMLResponse:
    color = "#059669" if ok else "#dc2626"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #f8fafc; color: #0f172a;
          display: grid; place-items: center; min-height: 100vh; margin: 0; }}
  .card {{ background: #fff; padding: 40px; border-radius: 12px; max-width: 460px;
           text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ font-size: 18px; margin: 0 0 8px; color: {color}; }}
  p {{ margin: 0; color: #475569; font-size: 15px; }}
</style></head>
<body><div class="card"><h1>{title}</h1><p>{message}</p></div></body></html>""",
        status_code=200 if ok else 400,
    )
