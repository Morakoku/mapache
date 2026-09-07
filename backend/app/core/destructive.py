"""Guardia de operaciones destructivas (LOOP-13).

Las operaciones que destruyen datos (DELETE) o que afectan a jobs ajenos
(cancelación) exigen `?confirm=true` explícito. Sin confirmación se responde
409 sin ejecutar nada. Se aplica por middleware para no depender de que cada
router recuerde la regla.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


def is_destructive(method: str, path: str) -> bool:
    return (method == "DELETE" and path.startswith("/api/v1")) or (
        method == "POST" and path.startswith("/api/v1/jobs/") and path.endswith("/cancel")
    )


class DestructiveGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, *, enabled: bool = True) -> None:
        super().__init__(app)
        self._enabled = enabled

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if (
            self._enabled
            and is_destructive(request.method, request.url.path)
            and (request.query_params.get("confirm") != "true")
        ):
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "REQUIRES_CONFIRMATION",
                        "message": "Operación destructiva. Confirme con ?confirm=true.",
                        "details": {},
                    }
                },
            )
        return await call_next(request)


__all__ = ["DestructiveGuardMiddleware", "is_destructive"]
