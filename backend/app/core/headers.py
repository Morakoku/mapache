"""Security headers (LOOP-13).

Cabeceras defensivas para toda la API. HSTS solo se activa cuando de verdad hay
TLS delante (config `hsts_enabled`); aquí no se asume. La CSP se omite en los
endpoints de documentación (Swagger/ReDoc cargan JS de CDN).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

_CSP = "default-src 'none'; frame-ancestors 'none'; sandbox; base-uri 'none'"
_DOC_PATHS = ("/docs", "/redoc", "/openapi.json", "/api/v1/openapi.json", "/torre-control", "/torre-control/", "/tc-api", "/tc-api/")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Añade cabeceras de seguridad a todas las respuestas."""

    def __init__(self, app: Any, *, hsts: bool = False) -> None:
        super().__init__(app)
        self._hsts = hsts

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        path = request.url.path
        if not path.startswith(_DOC_PATHS):
            response.headers["Content-Security-Policy"] = _CSP
        if self._hsts:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


__all__ = ["SecurityHeadersMiddleware"]
