"""Registro de workers en la cola de jobs.

Se llama una vez en el arranque de la app. Encolar un `JobType` sin handler
registrado falla de inmediato y a propósito: es un error de programación, no
un caso a tolerar en tiempo de ejecución.
"""

from __future__ import annotations

from app.core.enums import JobType
from app.core.jobs import JobRegistry
from app.workers.discovery_worker import run_discovery, run_provider_health
from app.workers.enrichment_worker import run_enrichment
from app.workers.followup_worker import run_followup_tick
from app.workers.inbox_worker import run_inbox_sync
from app.workers.scoring_worker import run_scoring
from app.workers.send_worker import run_send_batch


def register_workers(registry: JobRegistry) -> None:
    registry.register(JobType.DISCOVERY, run_discovery)
    registry.register(JobType.ENRICHMENT, run_enrichment)
    registry.register(JobType.SCORING, run_scoring)
    registry.register(JobType.SCRAPER_HEALTH, run_provider_health)
    registry.register(JobType.SEND_BATCH, run_send_batch)
    registry.register(JobType.INBOX_SYNC, run_inbox_sync)
    registry.register(JobType.FOLLOWUP_TICK, run_followup_tick)


__all__ = [
    "register_workers",
    "run_discovery",
    "run_enrichment",
    "run_followup_tick",
    "run_inbox_sync",
    "run_provider_health",
    "run_scoring",
    "run_send_batch",
]
