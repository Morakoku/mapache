"""Supabase REST API client for serverless environments.

This module provides a lightweight HTTP client for Supabase's PostgREST API,
designed to work in Vercel serverless functions where direct PostgreSQL
connections (asyncpg) fail due to connection pooling exhaustion.

Usage:
    client = SupabaseRestClient()
    data = await client.select("businesses", filters={"city": "bogota"})
    await client.insert("inquiries", {"business_id": "...", "email": "..."})
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.core.config import get_settings


class SupabaseRestError(Exception):
    """Raised when Supabase REST API returns an error."""

    def __init__(self, message: str, status_code: int, details: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class SupabaseRestClient:
    """Async HTTP client for Supabase PostgREST API.

    Uses httpx with connection pooling suitable for serverless environments.
    Each instance maintains its own client; reuse across requests in the same
    function invocation for connection reuse.
    """

    def __init__(
        self,
        base_url: str | None = None,
        anon_key: str | None = None,
        service_role_key: str | None = None,
        timeout: float = 30.0,
        max_connections: int = 10,
    ):
        settings = get_settings()

        self.base_url = base_url or settings.supabase_url
        self.anon_key = anon_key or (settings.supabase_anon_key_value or "")
        self.service_role_key = service_role_key or (settings.supabase_service_role_key_value or "")

        if not self.base_url or not self.anon_key:
            raise ValueError(
                "Supabase URL and anon key required. Set SUPABASE_URL and SUPABASE_ANON_KEY."
            )

        # Ensure base_url ends with /rest/v1/
        if not self.base_url.endswith("/rest/v1/"):
            self.base_url = self.base_url.rstrip("/") + "/rest/v1/"

        # Headers for anon key (respects RLS)
        self._anon_headers = {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.anon_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

        # Headers for service role (bypasses RLS - use carefully)
        self._service_headers = None
        if self.service_role_key:
            self._service_headers = {
                "apikey": self.service_role_key,
                "Authorization": f"Bearer {self.service_role_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }

        # Connection pool limits for serverless
        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=5,
            keepalive_expiry=30.0,
        )

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            limits=limits,
            headers=self._anon_headers,
        )

    async def __aenter__(self) -> SupabaseRestClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client and release connections."""
        await self._client.aclose()

    def _get_headers(self, use_service_role: bool = False) -> dict[str, str]:
        """Get headers for the request."""
        if use_service_role and self._service_headers:
            return self._service_headers
        return self._anon_headers

    def _build_url(self, table: str, query_params: dict[str, Any] | None = None) -> str:
        """Build the full URL for a table endpoint."""
        url = table
        if query_params:
            # Filter out None values
            filtered = {k: v for k, v in query_params.items() if v is not None}
            if filtered:
                url += "?" + urlencode(filtered, doseq=True)
        return url

    async def _request(
        self,
        method: str,
        table: str,
        *,
        query_params: dict[str, Any] | None = None,
        json_data: dict | list | None = None,
        headers: dict[str, str] | None = None,
        use_service_role: bool = False,
    ) -> httpx.Response:
        """Make an HTTP request to the PostgREST API."""
        url = self._build_url(table, query_params)
        request_headers = self._get_headers(use_service_role)
        if headers:
            request_headers = {**request_headers, **headers}

        response = await self._client.request(
            method=method,
            url=url,
            json=json_data,
            headers=request_headers,
        )

        if response.is_success:
            return response

        # Parse error response
        try:
            error_data = response.json()
            message = error_data.get("message", response.text)
            details = error_data.get("details", {})
            code = error_data.get("code", "")
        except Exception:
            message = response.text
            details = {}
            code = ""

        raise SupabaseRestError(
            message=f"Supabase API error ({response.status_code}): {message}",
            status_code=response.status_code,
            details={"code": code, **details},
        )

    # ================================================================
    # SELECT (GET)
    # ================================================================

    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        count: bool = False,
        use_service_role: bool = False,
    ) -> list[dict[str, Any]]:
        """Select rows from a table.

        Args:
            table: Table name
            columns: Comma-separated column names or "*"
            filters: Dict of filter conditions (e.g., {"city": "eq.bogota", "status": "neq.archived"})
            order: Order clause (e.g., "created_at.desc" or "name.asc.nulls_last")
            limit: Maximum rows to return
            offset: Number of rows to skip
            count: Include total count in response headers
            use_service_role: Use service role key (bypasses RLS)

        Returns:
            List of row dictionaries
        """
        params: dict[str, Any] = {"select": columns}

        if filters:
            params.update(filters)

        if order:
            params["order"] = order

        if limit is not None:
            params["limit"] = limit

        if offset is not None:
            params["offset"] = offset

        headers = {}
        if count:
            headers["Prefer"] = "count=exact"

        response = await self._request(
            "GET",
            table,
            query_params=params,
            headers=headers,
            use_service_role=use_service_role,
        )

        return response.json()

    async def select_one(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, Any] | None = None,
        use_service_role: bool = False,
    ) -> dict[str, Any] | None:
        """Select a single row (limit=1)."""
        results = await self.select(
            table,
            columns=columns,
            filters=filters,
            limit=1,
            use_service_role=use_service_role,
        )
        return results[0] if results else None

    # ================================================================
    # INSERT (POST)
    # ================================================================

    async def insert(
        self,
        table: str,
        data: dict[str, Any] | list[dict[str, Any]],
        *,
        returning: str = "representation",
        use_service_role: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Insert one or more rows.

        Args:
            table: Table name
            data: Single row dict or list of row dicts
            returning: "representation" (return inserted rows) or "minimal" (return only PK)
            use_service_role: Use service role key (bypasses RLS)

        Returns:
            Inserted row(s) if returning=representation, else empty list
        """
        headers = {"Prefer": f"return={returning}"}
        response = await self._request(
            "POST",
            table,
            json_data=data,
            headers=headers,
            use_service_role=use_service_role,
        )

        if returning == "minimal":
            return []

        result = response.json()
        # Single row insert returns a list with one element
        return result if isinstance(result, list) else [result]

    # ================================================================
    # UPDATE (PATCH)
    # ================================================================

    async def update(
        self,
        table: str,
        data: dict[str, Any],
        *,
        filters: dict[str, Any],
        returning: str = "representation",
        use_service_role: bool = False,
    ) -> list[dict[str, Any]]:
        """Update rows matching filters.

        Args:
            table: Table name
            data: Columns to update
            filters: Filter conditions to identify rows (required for safety)
            returning: "representation" or "minimal"
            use_service_role: Use service role key (bypasses RLS)

        Returns:
            Updated row(s)
        """
        if not filters:
            raise ValueError("Update requires at least one filter for safety")

        headers = {"Prefer": f"return={returning}"}
        response = await self._request(
            "PATCH",
            table,
            query_params=filters,
            json_data=data,
            headers=headers,
            use_service_role=use_service_role,
        )

        if returning == "minimal":
            return []

        return response.json()

    # ================================================================
    # UPSERT (POST with on_conflict)
    # ================================================================

    async def upsert(
        self,
        table: str,
        data: dict[str, Any] | list[dict[str, Any]],
        *,
        on_conflict: str,
        returning: str = "representation",
        ignore_duplicates: bool = False,
        use_service_role: bool = False,
    ) -> list[dict[str, Any]]:
        """Upsert (insert or update) rows.

        Args:
            table: Table name
            data: Row(s) to upsert (must include primary key columns)
            on_conflict: Comma-separated column names for ON CONFLICT clause
            returning: "representation" or "minimal"
            ignore_duplicates: If true, ignore duplicate key errors
            use_service_role: Use service role key (bypasses RLS)

        Returns:
            Upserted row(s)
        """
        headers = {
            "Prefer": f"return={returning},resolution=merge-duplicates",
        }
        if ignore_duplicates:
            headers["Prefer"] += ",ignore-duplicates"

        query_params = {"on_conflict": on_conflict}
        response = await self._request(
            "POST",
            table,
            query_params=query_params,
            json_data=data,
            headers=headers,
            use_service_role=use_service_role,
        )

        if returning == "minimal":
            return []

        return response.json()

    # ================================================================
    # DELETE
    # ================================================================

    async def delete(
        self,
        table: str,
        *,
        filters: dict[str, Any],
        returning: str = "representation",
        use_service_role: bool = False,
    ) -> list[dict[str, Any]]:
        """Delete rows matching filters.

        Args:
            table: Table name
            filters: Filter conditions to identify rows (required for safety)
            returning: "representation" or "minimal"
            use_service_role: Use service role key (bypasses RLS)

        Returns:
            Deleted row(s)
        """
        if not filters:
            raise ValueError("Delete requires at least one filter for safety")

        headers = {"Prefer": f"return={returning}"}
        response = await self._request(
            "DELETE",
            table,
            query_params=filters,
            headers=headers,
            use_service_role=use_service_role,
        )

        if returning == "minimal":
            return []

        return response.json()

    # ================================================================
    # RPC (PostgreSQL functions)
    # ================================================================

    async def rpc(
        self,
        function_name: str,
        params: dict[str, Any] | None = None,
        *,
        use_service_role: bool = False,
        head: bool = False,
        get: bool = False,
        count: str | None = None,
    ) -> Any:
        """Call a PostgreSQL function via RPC endpoint.

        Args:
            function_name: Name of the function in public schema
            params: Function parameters
            use_service_role: Use service role key
            head: HEAD request (no body returned)
            get: GET request (read-only)
            count: Count method for set-returning functions

        Returns:
            Function result
        """
        headers = {}
        if head:
            headers["Prefer"] = "head=true"
        if get:
            headers["Prefer"] = "get=true"
        if count:
            headers["Prefer"] = f"count={count}"

        response = await self._request(
            "POST",
            f"rpc/{function_name}",
            json_data=params or {},
            headers=headers,
            use_service_role=use_service_role,
        )

        if head or get:
            return None

        return response.json()

    # ================================================================
    # Convenience methods for common patterns
    # ================================================================

    async def get_by_id(
        self,
        table: str,
        id_value: str | int,
        *,
        columns: str = "*",
        id_column: str = "id",
        use_service_role: bool = False,
    ) -> dict[str, Any] | None:
        """Get a single row by primary key."""
        return await self.select_one(
            table,
            columns=columns,
            filters={f"{id_column}": f"eq.{id_value}"},
            use_service_role=use_service_role,
        )

    async def filter_eq(
        self,
        table: str,
        column: str,
        value: Any,
        *,
        columns: str = "*",
        limit: int | None = None,
        use_service_role: bool = False,
    ) -> list[dict[str, Any]]:
        """Filter by equality (column = value)."""
        return await self.select(
            table,
            columns=columns,
            filters={column: f"eq.{value}"},
            limit=limit,
            use_service_role=use_service_role,
        )

    async def filter_ilike(
        self,
        table: str,
        column: str,
        pattern: str,
        *,
        columns: str = "*",
        limit: int | None = None,
        use_service_role: bool = False,
    ) -> list[dict[str, Any]]:
        """Filter by case-insensitive LIKE pattern."""
        return await self.select(
            table,
            columns=columns,
            filters={column: f"ilike.{pattern}"},
            limit=limit,
            use_service_role=use_service_role,
        )

    async def filter_in(
        self,
        table: str,
        column: str,
        values: list[Any],
        *,
        columns: str = "*",
        limit: int | None = None,
        use_service_role: bool = False,
    ) -> list[dict[str, Any]]:
        """Filter by IN clause."""
        if not values:
            return []
        # PostgREST uses parentheses for IN: column=in.(val1,val2,val3)
        formatted = ",".join(str(v) for v in values)
        return await self.select(
            table,
            columns=columns,
            filters={column: f"in.({formatted})"},
            limit=limit,
            use_service_role=use_service_role,
        )


# ================================================================
# Module-level singleton for reuse across requests
# ================================================================

_client_instance: SupabaseRestClient | None = None


def get_supabase_rest_client() -> SupabaseRestClient:
    """Get or create the singleton Supabase REST client.

    In serverless environments, this allows connection reuse across
    invocations within the same warm container.
    """
    global _client_instance
    if _client_instance is None:
        _client_instance = SupabaseRestClient()
    return _client_instance


async def close_supabase_rest_client() -> None:
    """Close the singleton client (for testing or shutdown)."""
    global _client_instance
    if _client_instance is not None:
        await _client_instance.aclose()
        _client_instance = None