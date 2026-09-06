"""Wiring de dependencias.

Un único sitio donde se decide qué implementación concreta se usa. Cambiar de
`InProcessQueue` a `ArqQueue` (Fase 9) o de proveedor de descubrimiento es
editar aquí, no perseguir imports por todo el código.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.core.jobs import InProcessQueue, JobQueue, JobRegistry


@lru_cache(maxsize=1)
def get_job_registry() -> JobRegistry:
    return JobRegistry()


@lru_cache(maxsize=1)
def get_job_queue() -> JobQueue:
    settings = get_settings()
    registry = get_job_registry()

    if settings.job_queue_backend == "inprocess":
        return InProcessQueue(registry, concurrency=settings.job_worker_concurrency)

    # Fase 9: ArqQueue(registry, redis_url=settings.redis_url)
    raise NotImplementedError(
        f"Backend de cola '{settings.job_queue_backend}' aún no implementado. "
        "Ver docs/ARQUITECTURA.md §16, Fase 7/9."
    )


def reset_container() -> None:
    """Limpia las cachés. Solo para tests."""
    get_job_registry.cache_clear()
    get_job_queue.cache_clear()
