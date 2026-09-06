"""Middleware de auditoría (LOOP-13).

Registra POST/PATCH/PUT/DELETE de /api/v1, el tracking público y las lecturas
de /settings (sensibles). Nunca guarda el cuerpo, ni tokens, ni datos de
negocio: solo método, ruta, estado, duración, IP, identidad de servicio e
idempotency key. Escritura best-effort: un fallo de BD se loguea y no rompe la
petición.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.database import session_scope
from app.core.logging import get_logger
from app.models.audit import AuditLog

logger = get_logger(__name__)

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_AUDIT_GET_PREFIXES = ("/api/v1/settings",)


def action_for(method: str, path: str) -> str:
    if path.startswith("/tracking"):
        return "tracking"
    if method == "DELETE":
        return "delete"
    if path.startswith("/api/v1/settings"):
        return "admin"
    if path.startswith("/api/v1/emails") or path.startswith("/api/v1/conversations"):
        return "email"
    if (
        "/searches/" in path and path.endswith("/run")
    ) or "/prospect-plan/run" in path or "/enrich" in path:
        return "scraping"
    if path.startswith("/api/v1/jobs"):
        return "jobs"
    if path.startswith("/auth"):
        return "oauth"
    return "write"


def should_audit(method: str, path: str) -> bool:
    # El contrato Hermes (LOOP-16) audita en su propio handler (con identidad
    # y key); aquí se omite para no duplicar la fila.
    if path.startswith("/api/v1/hermes"):
        return False
    if method in _WRITE_METHODS and (path.startswith("/api/v1") or path.startswith("/auth")):
        return True
    if method == "GET" and path.startswith(_AUDIT_GET_PREFIXES):
        return True
    return path.startswith("/tracking")


class AuditMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, *, enabled: bool = True) -> None:
        super().__init__(app)
        self._enabled = enabled

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        path = request.url.path
        method = request.method
        if not self._enabled or not should_audit(method, path):
            return await call_next(request)

        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            await _write_audit(
                method=method,
                path=path,
                status=500,
                duration_ms=int((time.monotonic() - start) * 1000),
                request=request,
                action=action_for(method, path),
            )
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        await _write_audit(
            method=method,
            path=path,
            status=response.status_code,
            duration_ms=duration_ms,
            request=request,
            action=action_for(method, path),
        )
        return response


async def _write_audit(
    *,
    method: str,
    path: str,
    status: int,
    duration_ms: int,
    request: Request,
    action: str,
) -> None:
    try:
        client_id = getattr(request.state, "service_client", None)
        async with session_scope() as session:
            session.add(
                AuditLog(
                    method=method,
                    path=path,
                    status=status,
                    duration_ms=duration_ms,
                    client_ip=_client_ip(request),
                    client_id=str(client_id) if client_id else None,
                    idempotency_key=request.headers.get("idempotency-key"),
                    action=action,
                )
            )
    except Exception:
        logger.exception("audit_write_failed", method=method, path=path)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


__all__ = ["AuditMiddleware", "action_for", "should_audit"]
