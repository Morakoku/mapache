"""Middleware de autenticación servicio-a-servicio (L1).

Capa mínima que implementa REQUEST → AUTHENTICATION → ALLOW/DENY:

- Extrae el header `Authorization: Bearer <clien...n>`.
- Valida firma HMAC-SHA256 con `SERVICE_TOKEN_KEY`, ventana temporal (TTL) y
  nonce único (replay) vía `NonceStore` en memoria.
- Si `settings.service_auth_enabled` está en False (default local) la capa queda
  transparente: **no** se rompe ningún flujo actual. Activarla es un cambio de
  configuración deliberado (PATCH-03).
- Rutas públicas de siempre quedan exentas: `/health*`, `/tracking*`, `/docs`,
  `/redoc`, `/openapi.json` y **solo** los flujos OAuth `/auth/{provider}/connect`
  y `/auth/{provider}/callback`. Los publican `public_router` (healthchecks,
  pixel de tracking, callbacks OAuth) y los consumen terceros (Google/Microsoft),
  no la SPA.
- El resto de `/auth/*` — `GET /auth/accounts` y `POST /auth/{provider}/disconnect/...`
  (gestión de buzones desde la UI) — quedan **protegidos** (LOOP-06): no hay un
  consumidor legítimo tercero y `disconnect` escribe/elimina sobre el subsistema
  de correo.

Cuando la capa está activa y el token no es válido, responde 401 con el mismo
formato `{"error": {...}}` que el resto de la app (code/message/details).

Autorización (L2, LOOP-14): una vez autenticado, se comprueba el scope exigido
por la operación (`app.core.scopes`). El mapa solo declara el contrato Hermes
(dispatch + jobs); cualquier otra operación de los 141 endpoints queda sin
declarar → DENY con 403 (least privilege). Cuando `service_auth_enabled=True` y
el token es válido pero el scope falta → 403 (code FORBIDDEN/SCOPE_*).
"""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import Settings, get_settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.scopes import scope_for, validate_scope_set
from app.core.security import NonceStore, ServiceTokenError, verify_service_token

# Rutas que siguen públicas (sin Auth de servicio). El prefijo de health está
# en el `public_router`; el `tracking` y los callbacks OAuth viven fuera de
# /api/v1. NO se incluye `/auth` genérico: entre los dos flujos de consentimiento
# (connect/callback) y los endpoints de gestión (accounts/disconnect) solo los
# primeros pertenecen a terceros.
_PUBLIC_PREFIXES = (
    "/health",
    "/api/health",  # Vercel rewrite
    "/tracking",
    "/api/tracking",  # Vercel rewrite
    "/docs",
    "/redoc",
    "/openapi.json",
    "/torre-control",
    "/torre-control/",
    "/tc-api",
    "/tc-api/",
    "/email-simple-public",
    "/dashboard",
    "/dashboard/",
    "/api/v1/tc",
)

# Únicos endpoints `/auth/*` públicos: los que ejecutan Google/Microsoft durante
# el flujo OAuth. `accounts` y `disconnect` quedan fuera (protegidos).
_OAUTH_PUBLIC_PATH_RE = re.compile(r"^/api/auth/[^/]+/(connect|callback)$|^/auth/[^/]+/(connect|callback)$")

# Instancia única de nonces del proceso. Se comparte entre peticiones; se vacía
# al reiniciar Mapache (ver NonceStore). No es thread-perfect ni persiste — se
# documenta como límite de L1 en LOOP-04.
_nonce_store = NonceStore()


def is_public_service_path(path: str) -> bool:
    """Clasificación de rutas exentas de la autenticación de servicio.

    Helpers públicos para tests: esta es la definición canónica de
    PUBLICA vs PROTEGIDA; debe mantenerse en sincronía con la declaración
    OpenAPI (`SERVICE_BEARER` en rutas o routers).
    """
    if path.startswith(_PUBLIC_PREFIXES):
        return True
    return bool(_OAUTH_PUBLIC_PATH_RE.match(path))


class ServiceAuthMiddleware(BaseHTTPMiddleware):
    """Autentica (L1) y autoriza por scope (L2). No decide permisos finos de UI
    (multiusuario): ese es el dominio de la web app, no del API service layer."""

    def __init__(
        self,
        app: Any,
        *,
        settings: Settings | None = None,
        nonce_store: NonceStore | None = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings or get_settings()
        self._nonce_store = nonce_store or _nonce_store

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        settings = self._settings

        # Exención de rutas públicas y de la capa desactivada (default local).
        path = request.url.path
        if not settings.service_auth_enabled:
            return await call_next(request)
        if is_public_service_path(path):
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        if not authorization.lower().startswith("bearer "):
            return _deny(
                UnauthorizedError(
                    "Falta token de servicio.",
                    code="SERVICE_TOKEN_MISSING",
                    details={},
                )
            )
        token = authorization.split(maxsplit=1)[1].strip()

        try:
            client_id = verify_service_token(
                token,
                key=(
                    settings.service_token_key.get_secret_value()
                    if settings.service_token_key
                    else None
                ),
                ttl_seconds=settings.service_token_ttl_seconds,
                nonce_store=self._nonce_store,
                now=int(time.time()),
                allowed_client_ids=settings.trusted_client_ids,
            )
        except ServiceTokenError as exc:
            return _deny(UnauthorizedError(exc.message, code=exc.code, details=exc.details))

        # Autenticado (L1). Resolvemos la identidad y pasamos a autorizar (L2).
        request.state.service_client = client_id

        # ------------------------------------------------------- L2 (LOOP-14)
        # Autorización: "¿qué puedes hacer?". Política explícita por operación
        # (solo el contrato Hermes está declarado); todo lo no declarado se
        # deniega (403). El set de scopes concedido a la identidad lo valida
        # `validate_scope_set` (sin wildcards, sin scopes fuera de la whitelist
        # del contrato). Con `service_auth_enabled=False` (default local) esta
        # rama no se ejecuta: la capa sigue transparente.
        granted = settings.service_scopes_for(client_id)
        reason = validate_scope_set(granted)
        if reason is not None:
            return _deny(
                ForbiddenError(
                    "Identidad con scopes no válidos.",
                    code="SCOPE_INVALID_SET",
                    details={"reason": reason},
                )
            )

        required_scope = scope_for(request.method, path)
        if required_scope is None:
            return _deny(
                ForbiddenError(
                    "Operación no habilitada para clientes de servicio.",
                    code="SCOPE_NOT_ASSIGNED",
                    details={},
                )
            )
        if required_scope not in granted:
            return _deny(
                ForbiddenError(
                    "La identidad no tiene el alcance necesario.",
                    code="SCOPE_MISSING",
                    details={"scope": required_scope},
                )
            )

        request.state.service_scopes = frozenset(granted)
        request.state.required_scope = required_scope
        return await call_next(request)


def _deny(error: UnauthorizedError | ForbiddenError) -> JSONResponse:
    return JSONResponse(status_code=error.http_status, content=error.to_dict())


__all__ = ["ServiceAuthMiddleware", "is_public_service_path"]