"""Worker de scoring: recalcula el prospect score en lote.

Se ejecuta como job porque recalcular todo el embudo tras cambiar los pesos
puede tardar, y bloquear la petición HTTP mientras tanto sería un timeout.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.database import session_scope
from app.core.logging import get_logger
from app.services.job_svc import JobService
from app.services.scoring_svc import ScoringService

logger = get_logger(__name__)

# Tamaño del lote entre commits. Cada lote es una transacción corta: si el
# proceso muere a mitad, lo ya calculado queda guardado.
_BATCH = 50


async def run_scoring(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    lead_ids = [uuid.UUID(lid) for lid in payload.get("lead_ids", [])]

    async with session_scope() as session:
        jobs = JobService(session)
        await jobs.mark_running(job_id)

        service = ScoringService(session)
        if not lead_ids:
            # Sin lista explícita se recalcula todo: es lo que hace falta al
            # tocar los pesos, porque cambian todos los números a la vez.
            lead_ids = await service.pending_ids()

        total = len(lead_ids)
        await jobs.update_progress(job_id, current=0, total=total, message="Calculando score…")
        await session.commit()

        scored = 0
        for start in range(0, total, _BATCH):
            batch = lead_ids[start : start + _BATCH]
            result = await service.score_leads(batch)
            scored += result.scored

            await jobs.update_progress(
                job_id,
                current=min(start + _BATCH, total),
                message=f"{scored}/{total} prospectos",
            )
            await session.commit()

        await jobs.mark_completed(job_id, {"scored": scored, "requested": total})
        await session.commit()

    logger.info("scoring_job_finished", job_id=str(job_id), scored=scored)
