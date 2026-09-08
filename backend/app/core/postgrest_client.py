"""Cliente PostgREST para operaciones CRUD en serverless."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def pg_select(
    table: str,
    *,
    columns: str = "*",
    filters: dict[str, str] | None = None,
    order: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict[str, Any]]:
    """SELECT vía PostgREST."""
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
    if offset:
        params["offset"] = str(offset)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("pg_select_failed", table=table, status=resp.status_code)
            return []
    except Exception as exc:
        logger.error("pg_select_error", table=table, error=str(exc))
        return []


async def pg_insert(
    table: str,
    data: dict[str, Any] | list[dict[str, Any]],
    *,
    upsert: bool = False,
) -> list[dict[str, Any]]:
    """INSERT vía PostgREST."""
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
            if upsert:
                headers["Prefer"] = "return=representation,resolution=merge-duplicates"
            resp = await client.post(url, headers=headers, json=data)
            if resp.status_code in (200, 201):
                return resp.json()
            logger.warning("pg_insert_failed", table=table, status=resp.status_code, detail=resp.text)
            return []
    except Exception as exc:
        logger.error("pg_insert_error", table=table, error=str(exc))
        return []


async def pg_update(
    table: str,
    filters: dict[str, str],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """UPDATE vía PostgREST."""
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
            logger.warning("pg_update_failed", table=table, status=resp.status_code)
            return []
    except Exception as exc:
        logger.error("pg_update_error", table=table, error=str(exc))
        return []


async def pg_delete(
    table: str,
    filters: dict[str, str],
) -> list[dict[str, Any]]:
    """DELETE vía PostgREST."""
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
            logger.warning("pg_delete_failed", table=table, status=resp.status_code)
            return []
    except Exception as exc:
        logger.error("pg_delete_error", table=table, error=str(exc))
        return []


async def pg_count(
    table: str,
    filters: dict[str, str] | None = None,
) -> int:
    """COUNT vía PostgREST."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return 0

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}"
    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
        "Prefer": "count=exact",
        "Range-Unit": "items",
        "Range": "0-0",
    }
    params: dict[str, str] = {"select": "id"}
    if filters:
        for key, value in filters.items():
            params[key] = f"eq.{value}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                content_range = resp.headers.get("content-range", "")
                if "/" in content_range:
                    return int(content_range.split("/")[-1])
                return len(resp.json())
            return 0
    except Exception as exc:
        logger.error("pg_count_error", table=table, error=str(exc))
        return 0
