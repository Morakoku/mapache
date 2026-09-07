"""Healthcheck. Sin prefijo de versión: lo consumen Docker y los balanceadores."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.schemas.common import HealthOut

router = APIRouter(tags=["health"])
logger = get_logger(__name__)

VERSION = "0.1.0"


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirige la raíz / a la documentación interactiva Swagger /docs."""
    return RedirectResponse(url="/docs")


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    """Liveness: ¿está el proceso vivo? No toca la base de datos."""
    settings = get_settings()
    return HealthOut(
        status="ok",
        environment=settings.environment,
        database="not_checked",
        version=VERSION,
    )


async def _check_supabase_rest() -> bool:
    """Check Supabase connectivity via PostgREST HTTP API.

    This works in serverless environments (Vercel) where direct PostgreSQL
    connections fail due to IPv4/IPv6 mismatch or pooler limits.
    Uses service_role key (required for PostgREST) and tests alembic_version table.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        return False

    rest_url = settings.supabase_url.rstrip("/") + "/rest/v1/alembic_version?select=version_num&limit=1"
    headers = {
        "apikey": settings.supabase_service_role_key_value,
        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(rest_url, headers=headers)
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("readiness_rest_error", error=str(exc))
        return False


async def _check_direct_db(db: AsyncSession) -> bool:
    """Check connectivity via direct PostgreSQL (SQLAlchemy/asyncpg)."""
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("readiness_db_failed", error=str(exc))
        return False


@router.get("/health/ready", response_model=HealthOut)
async def readiness(
    response: Response,
    db: AsyncSession | None = Depends(get_db),
) -> HealthOut:
    """Readiness: ¿puede atender tráfico? Comprueba la conexión a la base de datos.

    Strategy:
    1. First tries Supabase PostgREST API (HTTP) — works in serverless (Vercel)
       where direct PostgreSQL connections fail (IPv4/IPv6, pooler limits).
    2. Falls back to direct DB (SQLAlchemy/asyncpg) for non-Supabase setups.

    Devuelve 503 si la base no responde, para que el orquestador saque la
    instancia del balanceo en vez de mandarle peticiones que van a fallar.
    """
    settings = get_settings()
    db_status = "error"

    # 1) Try Supabase REST API (HTTP) — works in Vercel serverless
    if await _check_supabase_rest():
        db_status = "ok"
    # 2) Fallback to direct PostgreSQL connection
    elif db and await _check_direct_db(db):
        db_status = "ok"

    if db_status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthOut(
        status="ok" if db_status == "ok" else "degraded",
        environment=settings.environment,
        database=db_status,
        version=VERSION,
    )
