"""Cliente de base de datos vía Supabase PostgREST API.

Reemplaza a SQLAlchemy/asyncpg en entornos serverless (Vercel) donde
la conexión directa a PostgreSQL no funciona (IPv4 vs IPv6, pooler limits).

Usa la API REST de Supabase (PostgREST) que es HTTP-based y funciona
en cualquier entorno serverless.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SupabaseRestClient:
    """Cliente HTTP para Supabase PostgREST API.

    Soporta SELECT, INSERT, UPDATE, DELETE vía REST.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = f"{settings.supabase_url}/rest/v1"
        self.headers = {
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ CRUD
    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, str] | None = None,
        order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """SELECT sobre una tabla."""
        client = await self._get_client()
        params: dict[str, str] = {"select": columns}
        if filters:
            for key, value in filters.items():
                params[key] = f"eq.{value}"
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)

        response = await client.get(f"/{table}", params=params)
        response.raise_for_status()
        return response.json()

    async def insert(
        self,
        table: str,
        data: dict[str, Any] | list[dict[str, Any]],
        *,
        upsert: bool = False,
        on_conflict: str = "",
    ) -> list[dict[str, Any]]:
        """INSERT (o upsert) en una tabla."""
        client = await self._get_client()
        headers = dict(self.headers)
        if upsert:
            headers["Prefer"] = f"return=representation,resolution=merge-duplicates"
            if on_conflict:
                params = {"on_conflict": on_conflict}
        else:
            params = None

        response = await client.post(
            f"/{table}",
            json=data,
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()

    async def update(
        self,
        table: str,
        filters: dict[str, str],
        data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """UPDATE con filtros."""
        client = await self._get_client()
        params = {k: f"eq.{v}" for k, v in filters.items()}
        response = await client.patch(
            f"/{table}",
            json=data,
            headers=self.headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()

    async def delete(
        self,
        table: str,
        filters: dict[str, str],
    ) -> list[dict[str, Any]]:
        """DELETE con filtros."""
        client = await self._get_client()
        params = {k: f"eq.{v}" for k, v in filters.items()}
        response = await client.delete(
            f"/{table}",
            headers=self.headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()

    async def rpc(
        self,
        function_name: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Llamada a función RPC (PostgreSQL function via REST)."""
        client = await self._get_client()
        response = await client.post(
            f"/rpc/{function_name}",
            json=params or {},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()


# Singleton
_client: SupabaseRestClient | None = None


def get_supabase_client() -> SupabaseRestClient:
    global _client
    if _client is None:
        _client = SupabaseRestClient()
    return _client


async def close_supabase_client() -> None:
    global _client
    if _client:
        await _client.close()
        _client = None
