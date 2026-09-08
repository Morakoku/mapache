"""Métricas simples via PostgREST.

Usa select con limit para contar - más compatible que Prefer: count=exact.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def pg_count(table: str, filters: dict[str, str] | None = None) -> int:
    """Contar registros en una tabla."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return 0

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}?select=id&limit=9999"
    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
    }
    if filters:
        for key, value in filters.items():
            url += f"&{key}=eq.{value}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return len(data) if isinstance(data, list) else 0
            logger.warning("pg_count_failed", table=table, status=resp.status_code)
            return 0
    except Exception as e:
        logger.error("pg_count_error", table=table, error=str(e))
        return 0


async def pg_select(
    table: str,
    columns: str = "*",
    filters: dict[str, str] | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order: str | None = None,
) -> list[dict[str, Any]]:
    """SELECT via PostgREST."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return []

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}?select={columns}"
    if filters:
        for key, value in filters.items():
            url += f"&{key}=eq.{value}"
    if order:
        url += f"&order={order}"
    if limit:
        url += f"&limit={limit}"
    if offset:
        url += f"&offset={offset}"

    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json() if isinstance(resp.json(), list) else []
            logger.warning("pg_select_failed", table=table, status=resp.status_code)
            return []
    except Exception as e:
        logger.error("pg_select_error", table=table, error=str(e))
        return []


async def pg_insert(table: str, data: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """INSERT via PostgREST."""
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

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=data)
            if resp.status_code in (200, 201):
                return resp.json() if isinstance(resp.json(), list) else [resp.json()]
            logger.warning("pg_insert_failed", table=table, status=resp.status_code)
            return []
    except Exception as e:
        logger.error("pg_insert_error", table=table, error=str(e))
        return []


async def pg_update(table: str, filters: dict[str, str], data: dict[str, Any]) -> list[dict[str, Any]]:
    """UPDATE via PostgREST."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return []

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}?"
    for key, value in filters.items():
        url += f"{key}=eq.{value}&"
    url = url.rstrip("&")

    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(url, headers=headers, json=data)
            if resp.status_code == 200:
                return resp.json() if isinstance(resp.json(), list) else [resp.json()]
            logger.warning("pg_update_failed", table=table, status=resp.status_code)
            return []
    except Exception as e:
        logger.error("pg_update_error", table=table, error=str(e))
        return []


async def pg_delete(table: str, filters: dict[str, str]) -> bool:
    """DELETE via PostgREST."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return False

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}?"
    for key, value in filters.items():
        url += f"{key}=eq.{value}&"
    url = url.rstrip("&")

    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(url, headers=headers)
            return resp.status_code == 204
    except Exception as e:
        logger.error("pg_delete_error", table=table, error=str(e))
        return False
