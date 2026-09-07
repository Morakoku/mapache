"""Idempotencia (LOOP-13).

Reintentos con la misma `Idempotency-Key` sobre la misma ruta (POST) devuelven
409 y no vuelven a crear el job ni a enviar el correo. La fila se crea en la
misma transacción que el endpoint: si la operación falla y se revierte, el
reintento con la misma key queda permitido.

Se aplica como dependencia a nivel de router en las operaciones con efecto
(email, jobs, scraping). Cuando el cliente no envía la key, no se exige nada
(compatibilidad total con el comportamiento actual).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import ConflictError, ValidationError
from app.models.idempotency import IdempotencyEvent

_MIN_KEY = 8
_MAX_KEY = 128


class IdempotencyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def begin(self, key: str, method: str, path: str) -> None:
        existing = await self._find(key, method, path)
        if existing is not None:
            raise ConflictError(
                "Operación duplicada para esta Idempotency-Key.",
                code="IDEMPOTENCY_CONFLICT",
                details={"job_id": str(existing.job_id) if existing.job_id else None},
            )
        self.session.add(IdempotencyEvent(idempotency_key=key, method=method, path=path))
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                "Operación duplicada para esta Idempotency-Key.",
                code="IDEMPOTENCY_CONFLICT",
                details={"job_id": None},
            ) from exc

    async def attach_job(self, key: str, method: str, path: str, job_id: uuid.UUID) -> None:
        row = await self._find(key, method, path)
        if row is not None and row.job_id is None:
            row.job_id = job_id

    async def _find(self, key: str, method: str, path: str) -> IdempotencyEvent | None:
        result = await self.session.execute(
            select(IdempotencyEvent).where(
                IdempotencyEvent.idempotency_key == key,
                IdempotencyEvent.method == method,
                IdempotencyEvent.path == path,
            )
        )
        return result.scalar_one_or_none()


async def require_idempotency(
    request: Request,
    db: Annotated[AsyncSession | None, Depends(get_db)],
) -> None:
    """Dependencia de router: aplica idempotencia solo si hay Idempotency-Key."""
    if not get_settings().idempotency_enabled:
        return
    # En modo PostgREST (db=None), no aplicar idempotencia a nivel de SQLAlchemy
    if db is None:
        return
    key = request.headers.get("idempotency-key")
    if request.method != "POST" or not key:
        return
    if not (_MIN_KEY <= len(key) <= _MAX_KEY):
        raise ValidationError(
            f"Idempotency-Key inválida (debe tener entre {_MIN_KEY} y {_MAX_KEY} caracteres).",
            code="IDEMPOTENCY_KEY_INVALID",
        )
    await IdempotencyService(db).begin(key, request.method, request.url.path)


__all__ = ["IdempotencyService", "require_idempotency"]
