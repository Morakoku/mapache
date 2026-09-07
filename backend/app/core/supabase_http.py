"""Cliente de base de datos vía Supabase PostgREST API.

Usa el cliente oficial de Supabase (HTTP-based) en lugar de conexión
directa a PostgreSQL. Funciona en Vercel serverless donde asyncpg
no puede conectar (IPv4 vs IPv6, pooler limits).
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: Any = None  # Supabase client singleton


def get_supabase() -> Any:
    """Obtiene el cliente Supabase (singleton)."""
    global _client
    if _client is None:
        try:
            from supabase import create_client
            settings = get_settings()
            _client = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key,
            )
            logger.info("supabase_client_created", url=settings.supabase_url)
        except ImportError:
            logger.error("supabase_package_not_installed")
            raise
    return _client


def reset_supabase() -> None:
    """Resetea el cliente (útil en tests)."""
    global _client
    _client = None


# ------------------------------------------------------------------ CRUD helpers
async def select(
    table: str,
    *,
    columns: str = "*",
    filters: dict[str, str] | None = None,
    order: str | None = None,
    limit: int | None = None,
    count: bool = False,
) -> list[dict[str, Any]]:
    """SELECT vía PostgREST."""
    client = get_supabase()
    query = client.table(table).select(columns, count="exact" if count else None)
    if filters:
        for key, value in filters.items():
            query = query.eq(key, value)
    if order:
        query = query.order(order)
    if limit:
        query = query.limit(limit)
    result = query.execute()
    return result.data


async def insert(
    table: str,
    data: dict[str, Any] | list[dict[str, Any]],
    *,
    upsert: bool = False,
) -> list[dict[str, Any]]:
    """INSERT vía PostgREST."""
    client = get_supabase()
    if upsert:
        result = client.table(table).upsert(data).execute()
    else:
        result = client.table(table).insert(data).execute()
    return result.data


async def update(
    table: str,
    filters: dict[str, str],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """UPDATE vía PostgREST."""
    client = get_supabase()
    query = client.table(table).update(data)
    for key, value in filters.items():
        query = query.eq(key, value)
    result = query.execute()
    return result.data


async def delete(
    table: str,
    filters: dict[str, str],
) -> list[dict[str, Any]]:
    """DELETE vía PostgREST."""
    client = get_supabase()
    query = client.table(table).delete()
    for key, value in filters.items():
        query = query.eq(key, value)
    result = query.execute()
    return result.data


async def rpc(function_name: str, params: dict[str, Any] | None = None) -> Any:
    """Llamada a función RPC."""
    client = get_supabase()
    result = client.rpc(function_name, params or {}).execute()
    return result.data


async def health_check() -> bool:
    """Verifica que la conexión a Supabase funciona."""
    try:
        client = get_supabase()
        # Intentar una query simple
        result = client.table("alembic_version").select("version_num").limit(1).execute()
        return True
    except Exception as e:
        logger.error("supabase_health_check_failed", error=str(e))
        return False
