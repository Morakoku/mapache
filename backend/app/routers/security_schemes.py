"""Esquema OpenAPI de la autenticación servicio-a-servicio (compartido).

Definición única de `ServiceBearerAuth` para que `api_router`
(`app/routers/__init__.py`) y las rutas OAuth de gestión de buzones
(`app/routers/auth.py`) declaren la MISMA instancia en OpenAPI.

Es puramente declarativo: `HTTPBearer(auto_error=False)` devuelve `None` si no
hay header en runtime. El enforcement real lo hace
`app.middleware.ServiceAuthMiddleware` cuando `SERVICE_AUTH_ENABLED=true`.
"""

from __future__ import annotations

from fastapi.security import HTTPBearer

SERVICE_BEARER = HTTPBearer(
    scheme_name="ServiceBearerAuth",
    bearerFormat="<client_id>.<timestamp>.<nonce>.<signature>",
    # Nunca rechaza en runtime: solo se registra la declaración en OpenAPI. El
    # enforcement real lo hace ServiceAuthMiddleware cuando está activo.
    auto_error=False,
    description=(
        "Autenticación servicio-a-servicio (L1). Header "
        "`Authorization: Bearer <client_id>.<timestamp>.<nonce>.<signature>`:\n"
        "- client_id: identidad del consumidor.\n"
        "- timestamp: ventana de expiración (TTL 300s).\n"
        "- nonce: único e irrepetible (anti-replay en memoria).\n"
        "- signature: HMAC-SHA256 del cuerpo con SERVICE_TOKEN_KEY.\n"
        "Autorización (L2, PATCH-04): separada de la autenticación. Los scopes "
        "viven en la identidad del cliente (`HERMES_SCOPES`), NO en este "
        "esquema: el mapeo operación→scope lo hace `app.core.scopes`. Los "
        "scopes no se exponen aquí porque son opacos a OpenAPI (solo el "
        "consumidor y Mapache los conocen). El enforcement lo hace el "
        "middleware cuando SERVICE_AUTH_ENABLED=true; con false (default "
        "local) la capa queda pasiva y este esquema es meramente declarativo."
    ),
)

__all__ = ["SERVICE_BEARER"]
