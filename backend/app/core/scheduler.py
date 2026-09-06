"""Planificador periódico en proceso.

Lo que la Fase 7 necesita para que una secuencia se ejecute sola: alguien que
mire el reloj. Encola `FOLLOWUP_TICK` e `INBOX_SYNC` cada N minutos, sin
ejecutar nada él mismo — el trabajo sigue siendo de los workers y sigue
quedando registrado en la tabla `jobs`.

Es deliberadamente simple, igual que `InProcessQueue`: para un usuario y 100
correos al día, un `asyncio.sleep` en bucle sobra. Cuando haga falta reparto
entre procesos se cambia por el scheduler de ARQ sin tocar los workers.

Limitación asumida: si el proceso está parado a la hora de un tick, ese tick no
ocurre. No se pierde nada — los seguimientos vencidos siguen vencidos y salen
en la siguiente vuelta.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.core.enums import JobType
from app.core.jobs import JobQueue
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PeriodicTask:
    job_type: JobType
    every_seconds: int
    payload: dict[str, Any]
    # Retraso inicial: arrancar todo a la vez en el segundo cero del proceso
    # solo sirve para pelearse con la migración y el warm-up de conexiones.
    initial_delay: int = 30


class Scheduler:
    def __init__(self, queue: JobQueue) -> None:
        self._queue = queue
        self._tasks: list[asyncio.Task[None]] = []

    def start(self, tasks: list[PeriodicTask]) -> None:
        for task in tasks:
            self._tasks.append(
                asyncio.create_task(self._loop(task), name=f"scheduler:{task.job_type}")
            )
        logger.info("scheduler_started", tasks=[t.job_type.value for t in tasks])

    async def _loop(self, task: PeriodicTask) -> None:
        await asyncio.sleep(task.initial_delay)
        while True:
            try:
                await self._tick(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - un tick fallido no mata el bucle
                logger.warning(
                    "scheduler_tick_failed", job_type=task.job_type.value, error=str(exc)
                )
            await asyncio.sleep(task.every_seconds)

    async def _tick(self, task: PeriodicTask) -> None:
        """Crea la fila del job y lo encola.

        La fila se crea aquí, igual que cuando el disparo es manual: un trabajo
        automático que no aparece en `/jobs` es un trabajo que nadie puede
        auditar cuando algo sale mal.
        """
        # Import local: `app.core.database` importa la configuración, y esta
        # módulo se carga durante el arranque de la app.
        from app.core.database import session_scope
        from app.services.job_svc import JobService

        payload = dict(task.payload)
        async with session_scope() as session:
            job = await JobService(session).create(task.job_type, payload)
            await session.commit()
            job_id = job.id

        await self._queue.enqueue(task.job_type, payload, job_id=job_id)

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("scheduler_stopped")
