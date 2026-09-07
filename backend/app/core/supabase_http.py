"""Cliente de base de datos vía Supabase PostgREST API.

Usa httpx (ya en dependencias) para hacer llamadas HTTP directas a PostgREST.
Funciona en Vercel serverless donde asyncpg no puede conectar.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


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
    import httpx

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return []

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}"
    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
    }
    params: dict[str, str] = {"select": columns}
    if filters:
        for key, value in filters.items():
            params[key] = f"eq.{value}"
    if order:
        params["order"] = order
    if limit:
        params["limit"] = str(limit)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("postgrest_select_failed", table=table, status=resp.status_code)
            return []
    except Exception as exc:
        logger.error("postgrest_select_error", table=table, error=str(exc))
        return []


async def insert(
    table: str,
    data: dict[str, Any] | list[dict[str, Any]],
    *,
    upsert: bool = False,
) -> list[dict[str, Any]]:
    """INSERT vía PostgREST."""
    import httpx

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return []

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}"
    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    # Si data no tiene id o dedupe_key, generarlos
    if isinstance(data, dict):
        if "id" not in data:
            data["id"] = str(uuid.uuid4())
        if "dedupe_key" not in data and table == "companies":
            data["dedupe_key"] = data.get("name", "").lower().strip()
        # Campos NOT NULL de companies con defaults
        if table == "companies":
            if "owner_id" not in data:
                data["owner_id"] = data["id"]  # owner_id = id del creador
            from datetime import datetime, UTC
            now = datetime.now(UTC).isoformat()
            if "first_extracted_at" not in data:
                data["first_extracted_at"] = now
            if "last_extracted_at" not in data:
                data["last_extracted_at"] = now

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if upsert:
                headers["Prefer"] = "return=representation,resolution=merge-duplicates"
            resp = await client.post(url, headers=headers, json=data)
            if resp.status_code in (200, 201):
                return resp.json()
            logger.warning("postgrest_insert_failed", table=table, status=resp.status_code, detail=resp.text)
            return []
    except Exception as exc:
        logger.error("postgrest_insert_error", table=table, error=str(exc))
        return []


async def update(
    table: str,
    filters: dict[str, str],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """UPDATE vía PostgREST."""
    import httpx

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return []

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}"
    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    params: dict[str, str] = {}
    for key, value in filters.items():
        params[key] = f"eq.{value}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(url, headers=headers, json=data, params=params)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("postgrest_update_failed", table=table, status=resp.status_code)
            return []
    except Exception as exc:
        logger.error("postgrest_update_error", table=table, error=str(exc))
        return []


async def delete(
    table: str,
    filters: dict[str, str],
) -> list[dict[str, Any]]:
    """DELETE vía PostgREST."""
    import httpx

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return []

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}"
    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
    }
    params: dict[str, str] = {}
    for key, value in filters.items():
        params[key] = f"eq.{value}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(url, headers=headers, params=params)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("postgrest_delete_failed", table=table, status=resp.status_code)
            return []
    except Exception as exc:
        logger.error("postgrest_delete_error", table=table, error=str(exc))
        return []


async def health_check() -> bool:
    """Verifica que la conexión a Supabase funciona."""
    result = await select("alembic_version", columns="version_num", limit=1)
    return len(result) > 0
