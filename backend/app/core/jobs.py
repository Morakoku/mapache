"""Abstracción de la cola de trabajos (decisión D2).

La API no ejecuta scraping ni envío masivo dentro de un request. Encola un
job y devuelve 202 con su id.

En el MVP la implementación es `InProcessQueue`: asyncio en el mismo proceso,
con el estado persistido en la tabla `jobs` para que un reinicio no pierda la
traza. Cuando haga falta (Fase 9), se cambia por `ArqQueue` sin tocar ni un
worker — de eso va el Protocol.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from app.core.enums import JobType
from app.core.logging import get_logger

logger = get_logger(__name__)

JobHandler = Callable[[uuid.UUID, dict[str, Any]], Awaitable[None]]


@runtime_checkable
class JobQueue(Protocol):
    """Contrato de la cola. Las implementaciones no comparten nada más."""

    async def enqueue(
        self,
        job_type: JobType,
        payload: dict[str, Any],
        *,
        job_id: uuid.UUID | None = None,
    ) -> uuid.UUID: ...

    async def cancel(self, job_id: uuid.UUID) -> bool: ...

    async def shutdown(self) -> None: ...


class JobRegistry:
    """Mapea `JobType` -> handler.

    Los workers se registran al arrancar la app. Encolar un tipo sin handler
    registrado es un error de programación, así que falla fuerte y temprano.
    """

    def __init__(self) -> None:
        self._handlers: dict[JobType, JobHandler] = {}

    def register(self, job_type: JobType, handler: JobHandler) -> None:
        if job_type in self._handlers:
            raise RuntimeError(f"Handler duplicado para {job_type}")
        self._handlers[job_type] = handler

    def get(self, job_type: JobType) -> JobHandler:
        try:
            return self._handlers[job_type]
        except KeyError as exc:
            raise RuntimeError(f"Sin handler registrado para {job_type}") from exc

    def is_registered(self, job_type: JobType) -> bool:
        return job_type in self._handlers


class InProcessQueue:
    """Cola en memoria sobre asyncio, para el MVP.

    Limita la concurrencia con un semáforo: dos scrapers de Playwright a la
    vez ya saturan una máquina de desarrollo, y el objetivo de 100 correos
    diarios no necesita más.

    Limitaciones conocidas, asumidas a este volumen:
      - Un reinicio deja los jobs en RUNNING; al arrancar se rescatan y
        vuelven a QUEUED (`JobService.recover_stale`, Fase 2).
      - No hay reparto entre varios procesos.
    """

    def __init__(self, registry: JobRegistry, *, concurrency: int = 2) -> None:
        self._registry = registry
        self._semaphore = asyncio.Semaphore(concurrency)
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    async def enqueue(
        self,
        job_type: JobType,
        payload: dict[str, Any],
        *,
        job_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        job_id = job_id or uuid.uuid4()
        handler = self._registry.get(job_type)

        task = asyncio.create_task(
            self._run(job_id, job_type, payload, handler),
            name=f"job:{job_type}:{job_id}",
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))

        logger.info("job_enqueued", job_id=str(job_id), job_type=job_type)
        return job_id

    async def _run(
        self,
        job_id: uuid.UUID,
        job_type: JobType,
        payload: dict[str, Any],
        handler: JobHandler,
    ) -> None:
        async with self._semaphore:
            log = logger.bind(job_id=str(job_id), job_type=job_type)
            log.info("job_started")
            try:
                await handler(job_id, payload)
                log.info("job_completed")
            except asyncio.CancelledError:
                log.warning("job_cancelled")
                raise
            except Exception:
                # El handler es responsable de marcar el job como FAILED en BD;
                # aquí solo dejamos rastro para que un fallo no muera en silencio.
                log.exception("job_failed")

    async def cancel(self, job_id: uuid.UUID) -> bool:
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        """Cancela lo pendiente y espera. Se llama en el shutdown de la app."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("job_queue_shutdown", cancelled=len(tasks))
