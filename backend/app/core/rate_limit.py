"""Rate limiting en memoria (LOOP-13).

Ventana deslizante por (categoría, IP). Suficiente para el MVP monoproceso
(mismo espíritu que NonceStore); para multi-proceso se migrará a Redis.

Las categorías cubren las rutas con efecto real: envío de correo, scraping/
enrichment, settings (admin), jobs y tracking público.
"""

from __future__ import annotations

import threading
import time
from typing import Any, ClassVar

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def category_for(method: str, path: str) -> str:
    """Categoría de rate limit para una petición.

    Solo se limita: escrituras de /api/v1 y el tracking público (lectura y
    escritura). El resto pasa sin contador.
    """
    if path.startswith("/tracking"):
        return "tracking"
    if method not in _WRITE_METHODS or not path.startswith("/api/v1"):
        return "none"
    if path.startswith("/api/v1/emails") or path.startswith("/api/v1/conversations"):
        return "email"
    if (
        "/searches/" in path and path.endswith("/run")
    ) or "/prospect-plan/run" in path or "/enrich" in path:
        return "scraping"
    if path.startswith("/api/v1/settings"):
        return "settings"
    if path.startswith("/api/v1/jobs"):
        return "jobs"
    if "follow-ups/run" in path or "/enroll" in path:
        return "email"
    return "default"


class RateLimiter:
    """Ventana deslizante por clave. Thread-safe para el MVP."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def allow(
        self, key: tuple[str, str], limit: int, window_seconds: int, *, now: float | None = None
    ) -> bool:
        current = time.monotonic() if now is None else now
        cutoff = current - window_seconds
        with self._lock:
            times = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(times) >= limit:
                self._hits[key] = times
                return False
            times.append(current)
            self._hits[key] = times
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Aplica límites por categoría + IP.

    `limits` mapea categoría → máx por ventana; `window_seconds` es la ventana.
    Cuando `enabled=False` (config) queda transparente.
    """

    DEFAULT_LIMITS: ClassVar[dict[str, int]] = {
        "email": 120,
        "scraping": 60,
        "settings": 60,
        "jobs": 60,
        "tracking": 300,
        "default": 120,
    }

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        limiter: RateLimiter | None = None,
        limits: dict[str, int] | None = None,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._enabled = enabled
        self._limiter = limiter or _default_limiter
        self._limits = {**self.DEFAULT_LIMITS, **(limits or {})}
        self._window = window_seconds

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if not self._enabled:
            return await call_next(request)
        category = category_for(request.method, request.url.path)
        if category == "none":
            return await call_next(request)
        ip = _client_ip(request)
        limit = self._limits.get(category, self._limits["default"])
        if not self._limiter.allow((category, ip), limit, self._window):
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Demasiadas solicitudes. Inténtelo de nuevo en un momento.",
                        "details": {"category": category},
                    }
                },
            )
        return await call_next(request)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


_default_limiter = RateLimiter()


def reset_default_limiter() -> None:
    _default_limiter.reset()


__all__ = ["RateLimitMiddleware", "RateLimiter", "category_for", "reset_default_limiter"]
