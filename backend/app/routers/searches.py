"""Módulo 2 — búsquedas y su ejecución."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import JobType
from app.core.exceptions import ValidationError
from app.core.idempotency import require_idempotency
from app.schemas.common import JobAcceptedOut
from app.schemas.prospecting import SearchIn, SearchOut, SearchRunOut, SearchUpdate
from app.scrapers.registry import AVAILABLE_PROVIDERS
from app.services.catalog_svc import SearchService
from app.services.job_svc import JobService
from app.services.mail_admin_svc import SettingsService

router = APIRouter(dependencies=[Depends(require_idempotency)])


@router.get("", response_model=list[SearchOut])
async def list_searches(db: AsyncSession = Depends(get_db)) -> list[SearchOut]:
    return [SearchOut.model_validate(s) for s in await SearchService(db).list_all()]


@router.post("", response_model=SearchOut, status_code=status.HTTP_201_CREATED)
async def create_search(
    payload: SearchIn,
    db: AsyncSession = Depends(get_db),
) -> SearchOut:
    search = await SearchService(db).create(payload.model_dump())
    await db.commit()
    return SearchOut.model_validate(search)


@router.get("/{search_id}", response_model=SearchOut)
async def get_search(
    search_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SearchOut:
    return SearchOut.model_validate(await SearchService(db).get_or_404(search_id))


@router.patch("/{search_id}", response_model=SearchOut)
async def update_search(
    search_id: uuid.UUID,
    payload: SearchUpdate,
    db: AsyncSession = Depends(get_db),
) -> SearchOut:
    search = await SearchService(db).update(search_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    # Ver nota en `services.update_service`: `updated_at` queda expirado tras
    # el UPDATE y hay que releerlo antes de serializar.
    await db.refresh(search)
    return SearchOut.model_validate(search)


@router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search(
    search_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    await SearchService(db).delete(search_id)
    await db.commit()


@router.post(
    "/{search_id}/run",
    response_model=JobAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_search(
    search_id: uuid.UUID,
    provider: str | None = Query(
        default=None,
        description="google_maps_scraper | google_places_api | instagram_serp | linkedin_serp",
    ),
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedOut:
    """Lanza la búsqueda en background.

    Devuelve 202 de inmediato: un scraping de 100 fichas tarda entre 6 y 8
    minutos y ninguna petición HTTP debe quedarse esperando eso.

    `provider` permite lanzar la misma búsqueda contra otra fuente: los mismos
    términos en Google Maps, en Instagram o en LinkedIn.
    """
    searches = SearchService(db)
    search = await searches.get_or_404(search_id)
    settings = await SettingsService(db).get()

    elegido = provider or settings.discovery_provider
    if elegido not in AVAILABLE_PROVIDERS:
        raise ValidationError(
            f"Fuente desconocida: '{elegido}'. Opciones: {', '.join(AVAILABLE_PROVIDERS)}.",
            code="UNKNOWN_DISCOVERY_PROVIDER",
        )

    payload: dict[str, object] = {
        "search_id": str(search.id),
        "provider": elegido,
    }
    # LOOP-13: las claves NO viajan en el payload del job (se persistirían en
    # claro en `jobs.payload`). El worker las lee y descifra del AppSettings en
    # su propia sesión. Aquí solo van knobs no sensibles.
    payload["scraper_concurrency"] = settings.scraper_concurrency
    payload["scraper_delay_min"] = settings.scraper_delay_ms_min
    payload["scraper_delay_max"] = settings.scraper_delay_ms_max
    payload["scraper_headless"] = settings.scraper_headless
    job = await JobService(db).create(
        JobType.DISCOVERY, payload, progress_total=search.target_count
    )
    await db.commit()

    await get_job_queue().enqueue(JobType.DISCOVERY, payload, job_id=job.id)

    return JobAcceptedOut(
        job_id=job.id,
        message=f"Buscando hasta {search.target_count} empresas. Sigue el progreso en /jobs.",
    )


@router.get("/{search_id}/runs", response_model=list[SearchRunOut])
async def list_runs(
    search_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[SearchRunOut]:
    service = SearchService(db)
    await service.get_or_404(search_id)
    return [SearchRunOut.model_validate(r) for r in await service.list_runs(search_id)]
