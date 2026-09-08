"""Fallback de PostgREST para endpoints que usan SQLAlchemy.

Cuando db=None (modo serverless), estos helpers redirigen las operaciones
a PostgREST HTTP API en vez de PostgreSQL directo.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.core.postgrest_client import pg_delete, pg_insert, pg_select, pg_update

logger = get_logger(__name__)


async def fetch_one(
    table: str,
    filters: dict[str, str],
    *,
    columns: str = "*",
) -> dict[str, Any] | None:
    """Equivalente a session.execute(select(...).where(...)).scalar_one_or_none()."""
    results = await pg_select(table, columns=columns, filters=filters, limit=1)
    return results[0] if results else None


async def fetch_all(
    table: str,
    filters: dict[str, str] | None = None,
    *,
    columns: str = "*",
    order: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Equivalente a session.execute(select(...).where(...).all())."""
    return await pg_select(table, columns=columns, filters=filters, order=order, limit=limit)


async def create_one(
    table: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """Equivalente to session.add(obj); session.commit(); session.refresh(obj)."""
    results = await pg_insert(table, data)
    return results[0] if results else None


async def update_one(
    table: str,
    filters: dict[str, str],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Equivalente a session.execute(update(...).where(...))."""
    return await pg_update(table, filters, data)


async def delete_one(
    table: str,
    filters: dict[str, str],
) -> bool:
    """Equivalente a session.execute(delete(...).where(...))."""
    results = await pg_delete(table, filters)
    return len(results) > 0


async def count_records(
    table: str,
    filters: dict[str, str] | None = None,
) -> int:
    """Equivalente to session.execute(select(func.count(...))).scalar()."""
    from app.core.postgrest_client import pg_count
    return await pg_count(table, filters)
