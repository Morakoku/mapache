"""Endpoint público para triggerar el scheduler desde GitHub Actions.

Estos endpoints son públicos (sin auth) porque los llama un scheduler
externo (GitHub Actions / Vercel Cron) que no puede manejar tokens HMAC.
En producción, restringir por IP del servidor de scheduler o exigir
un token simple en query string.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.services.job_svc import JobService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scheduler"])


@router.post("/trigger", response_model=dict[str, Any])
async def scheduler_trigger(action: str = "ping") -> dict[str, Any]:
    """Trigger endpoint para el scheduler externo.

    Acciones:
    - ping: saludar, devuelve ok
    - scrape: dispara un job de SCRAPER_HEALTH
    - process: dispara jobs pendientes (en teoría, en la implementación actual
      la cola inprocess ya los está procesando)
    """
    settings = get_settings()
    logger.info("scheduler_trigger", action=action, environment=settings.environment)

    if action == "ping":
        return {"status": "ok", "message": "Mapache scheduler alive"}

    if action == "scrape":
        async with settings.db_session() as session:
            # Create a SCRAPER_HEALTH job
            job = await JobService(session).create(
                "SCRAPER_HEALTH",
                {"source": "scheduler_trigger", "action": "health_check"}
            )
            await session.commit()
            logger.info("scraper_health_job_created", job_id=str(job.id))
        return {"status": "ok", "message": "Scraper health job enqueued", "job_id": str(job.id)}

    if action == "process":
        # In the current implementation, jobs are processed automatically
        # by the inprocess queue. This endpoint just confirms the system is processing.
        return {"status": "ok", "message": "Processing jobs (inprocess queue active)"}

    return {"status": "error", "message": f"Unknown action: {action}"}


@router.get("/status")
async def scheduler_status() -> dict[str, Any]:
    """Devuelve el estado del scheduler y la cola de jobs."""
    settings = get_settings()
    return {
        "scheduler_enabled": settings.scheduler_enabled,
        "environment": settings.environment,
        "job_queue_backend": settings.job_queue_backend,
        "followup_tick_minutes": settings.followup_tick_minutes,
        "inbox_sync_minutes": settings.inbox_sync_minutes,
    }
