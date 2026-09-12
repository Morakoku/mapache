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

# Owner determinista del pipeline de scraping. La constraint única de
# companies es (owner_id, dedupe_key): si cada insert genera owner_id
# nuevo, el mismo negocio se duplica en cada ciclo. Este valor es el
# namespace fijo bajo el cual el scheduler hace upsert — los inserts
# de otras fuentes (csv_import, API) traen su propio owner_id.
_SCHEDULER_OWNER_ID = "00000000-0000-4000-8000-000000000001"


# --------------------------------------------------------------- esquema PostgREST
# La app vive en el schema `crm` (separado de `public`, que mezcla otros sistemas).
# PostgREST selecciona el schema con Accept-Profile (lectura) / Content-Profile (escritura).
_PG_CRM = "crm"
_CRM_TABLES = frozenset({
    "activities", "app_settings", "audit_log", "call_logs", "call_scripts",
    "companies", "company_signals", "company_socials", "company_sources",
    "contacts", "conversation_messages", "conversations", "email_accounts",
    "email_events", "email_links", "email_messages", "email_templates",
    "follow_ups", "idempotency_events", "jobs", "lead_stage_history", "leads",
    "pipeline_stages", "search_results", "search_runs", "searches",
    "sequence_steps", "sequences", "services", "suppression_list", "tasks",
})


def _apply_profile(headers: dict[str, str], table: str) -> None:
    """Crm para las tablas de la app; public para el resto (ej. veyra_intakes)."""
    if table in _CRM_TABLES:
        headers["Accept-Profile"] = _PG_CRM
        headers["Content-Profile"] = _PG_CRM


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
    _apply_profile(headers, table)
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


async def pg_count_exact(
    table: str,
    *,
    filters: dict[str, str] | None = None,
) -> int:
    """Conteo exacto de filas vía Prefer: count=exact (Content-Range).

    A diferencia de un select con limit (que satura en el limit), este
    devuelve el total real de la tabla aunque haya miles de filas. Los
    filtros aceptan la sintaxis PostgREST completa, ej.:
    ``{"last_enriched_at": "not.is.null"}``.
    """
    import httpx

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return 0

    url = settings.supabase_url.rstrip("/") + f"/rest/v1/{table}"
    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
        "Prefer": "count=exact",
    }
    _apply_profile(headers, table)
    params: dict[str, str] = {"select": "id", "limit": "1"}
    if filters:
        for key, value in filters.items():
            params[key] = value

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code in (200, 206):
                # Content-Range: "0-0/9688" (o "*/9688" si no hay filas)
                content_range = resp.headers.get("content-range", "")
                total = content_range.rsplit("/", 1)[-1]
                return int(total) if total.isdigit() else 0
            logger.warning(
                "postgrest_count_exact_failed",
                table=table,
                status=resp.status_code,
            )
            return 0
    except Exception as exc:
        logger.error("postgrest_count_exact_error", table=table, error=str(exc))
        return 0


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
    _apply_profile(headers, table)
    # PostgREST necesita el query param on_conflict para saber sobre qué
    # constraint hacer el merge; sin él, merge-duplicates responde 409
    # en vez de fusionar. companies deduplica por (owner_id, dedupe_key).
    on_conflict = "owner_id,dedupe_key" if table == "companies" else None

    # Si data no tiene id o dedupe_key, generarlos
    if isinstance(data, dict):
        if "id" not in data:
            data["id"] = str(uuid.uuid4())
        if "dedupe_key" not in data and table == "companies":
            data["dedupe_key"] = data.get("name", "").lower().strip()[:40]
        # Campos NOT NULL de companies con defaults
        if table == "companies":
            if "owner_id" not in data:
                # owner_id FIJO y determinista: la unique (owner_id, dedupe_key)
                # solo deduplica si owner_id es constante entre ciclos. Un uuid4
                # nuevo por fila hacía que merge-duplicates nunca fusionara.
                data["owner_id"] = _SCHEDULER_OWNER_ID
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
                params: dict[str, str] = {}
                if on_conflict:
                    params["on_conflict"] = on_conflict
                resp = await client.post(url, headers=headers, json=data, params=params)
            else:
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
    _apply_profile(headers, table)
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
    _apply_profile(headers, table)
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
