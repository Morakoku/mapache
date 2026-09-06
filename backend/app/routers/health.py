"""Healthcheck. Sin prefijo de versión: lo consumen Docker y los balanceadores."""

from __future__ import annotations

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


@router.get("/health/ready", response_model=HealthOut)
async def readiness(
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> HealthOut:
    """Readiness: ¿puede atender tráfico? Comprueba la conexión a Postgres.

    Devuelve 503 si la base no responde, para que el orquestador saque la
    instancia del balanceo en vez de mandarle peticiones que van a fallar.
    """
    settings = get_settings()
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de BD degrada el readiness
        logger.error("readiness_db_failed", error=str(exc))
        db_status = "error"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthOut(
        status="ok" if db_status == "ok" else "degraded",
        environment=settings.environment,
        database=db_status,
        version=VERSION,
    )
