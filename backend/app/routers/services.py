"""Módulo 1 — servicios que vende el usuario."""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import JobType, LeadStatus
from app.core.idempotency import require_idempotency
from app.enrichment.signal_detector import ALL_SIGNALS, SIGNAL_LABELS
from app.models.lead import Lead
from app.schemas.prospecting import (
    PlannedSearchOut,
    ProspectPlanIn,
    ProspectPlanOut,
    ProspectPlanRunOut,
    ServiceIn,
    ServiceOut,
    ServiceUpdate,
    SignalOption,
    TopProspectOut,
)
from app.services.catalog_svc import CatalogService
from app.services.job_svc import JobService
from app.services.prospect_plan_svc import ProspectPlan, ProspectPlanService

router = APIRouter(dependencies=[Depends(require_idempotency)])


@router.get("", response_model=list[ServiceOut])
async def list_services(
    only_active: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> list[ServiceOut]:
    services = await CatalogService(db).list_all(only_active=only_active)
    return [ServiceOut.model_validate(s) for s in services]


@router.get("/signals", response_model=list[SignalOption])
async def list_opportunity_signals() -> list[SignalOption]:
    """Catálogo de señales de oportunidad.

    La UI ofrece estas opciones en vez de un campo libre: una señal escrita a
    mano nunca cruzaría con `company_signals` y el servicio no encontraría
    prospectos, sin ninguna pista de por qué.
    """
    return [SignalOption(key=key, label=SIGNAL_LABELS.get(key, key)) for key in ALL_SIGNALS]


@router.post("", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
async def create_service(
    payload: ServiceIn,
    db: AsyncSession = Depends(get_db),
) -> ServiceOut:
    service = await CatalogService(db).create(payload.model_dump())
    await db.commit()
    return ServiceOut.model_validate(service)


@router.get("/{service_id}", response_model=ServiceOut)
async def get_service(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ServiceOut:
    return ServiceOut.model_validate(await CatalogService(db).get_or_404(service_id))


@router.patch("/{service_id}", response_model=ServiceOut)
async def update_service(
    service_id: uuid.UUID,
    payload: ServiceUpdate,
    db: AsyncSession = Depends(get_db),
) -> ServiceOut:
    service = await CatalogService(db).update(service_id, payload.model_dump(exclude_unset=True))
    await db.commit()
    # `updated_at` lo pone Postgres en el UPDATE y queda expirado; serializar
    # sin releer dispararía un lazy load fuera del greenlet (500).
    await db.refresh(service)
    return ServiceOut.model_validate(service)


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    await CatalogService(db).delete(service_id)
    await db.commit()


# --------------------------------------------------------- plan de prospección


@router.get("/{service_id}/prospect-plan/cities", response_model=list[str])
async def suggest_cities(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    """Ciudades donde ya hay empresas, de más a menos.

    Es la mejor pista de dónde trabaja el usuario. Si la base está vacía
    devuelve vacío y la interfaz pide la ciudad a mano.
    """
    await CatalogService(db).get_or_404(service_id)
    return await ProspectPlanService(db).suggest_cities()


@router.post("/{service_id}/prospect-plan", response_model=ProspectPlanOut)
async def build_prospect_plan(
    service_id: uuid.UUID,
    payload: ProspectPlanIn,
    db: AsyncSession = Depends(get_db),
) -> ProspectPlanOut:
    """Propone qué buscar para vender este servicio, sin ejecutar nada.

    Es una propuesta: cada búsqueda tarda varios minutos, así que el usuario
    la revisa antes de lanzarla.
    """
    service = await CatalogService(db).get_or_404(service_id)
    plan = await ProspectPlanService(db).build(
        service, cities=payload.cities, target_per_search=payload.target_per_search
    )
    return _plan_out(plan)


@router.post(
    "/{service_id}/prospect-plan/run",
    response_model=ProspectPlanRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_prospect_plan(
    service_id: uuid.UUID,
    payload: ProspectPlanIn,
    db: AsyncSession = Depends(get_db),
) -> ProspectPlanRunOut:
    """Crea las búsquedas del plan y las lanza todas."""
    service = await CatalogService(db).get_or_404(service_id)

    planner = ProspectPlanService(db)
    plan = await planner.build(
        service, cities=payload.cities, target_per_search=payload.target_per_search
    )
    creadas = await planner.create_searches(service, plan)

    jobs = JobService(db)
    encolar: list[tuple[uuid.UUID, dict[str, object]]] = []
    for search in creadas:
        job_payload: dict[str, object] = {
            "search_id": str(search.id),
            "provider": "google_maps_scraper",
        }
        job = await jobs.create(
            JobType.DISCOVERY, job_payload, progress_total=search.target_count
        )
        encolar.append((job.id, job_payload))

    await db.commit()

    # Se encola fuera de la transacción: si el commit falla, no queda un job
    # corriendo contra una búsqueda que no llegó a guardarse.
    queue = get_job_queue()
    for job_id, job_payload in encolar:
        await queue.enqueue(JobType.DISCOVERY, job_payload, job_id=job_id)

    saltadas = sum(1 for s in plan.searches if s.already_exists)
    return ProspectPlanRunOut(
        created=len(creadas),
        skipped_existing=saltadas,
        job_ids=[job_id for job_id, _ in encolar],
        message=(
            f"{len(creadas)} búsqueda(s) en marcha"
            + (f", {saltadas} ya existían" if saltadas else "")
            + ". Cada una tarda varios minutos."
        ),
    )


def _plan_out(plan: ProspectPlan) -> ProspectPlanOut:
    return ProspectPlanOut(
        service_id=plan.service_id,
        service_name=plan.service_name,
        searches=[PlannedSearchOut(**asdict(s)) for s in plan.searches],
        source=plan.source,
        notes=plan.notes,
        total_target=plan.total_target,
    )


@router.get("/{service_id}/top-prospects", response_model=list[TopProspectOut])
async def top_prospects(
    service_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[TopProspectOut]:
    """Los mejores prospectos de este servicio, con lo que los justifica.

    Ordenados por score, que ya pondera encaje con las industrias objetivo,
    señales de oportunidad y facilidad de contacto. Se devuelven las razones
    del desglose para que la prioridad se pueda discutir en vez de tener que
    creerse un número.
    """
    await CatalogService(db).get_or_404(service_id)

    filas = await db.execute(
        select(Lead)
        .where(Lead.service_id == service_id, Lead.status == LeadStatus.OPEN)
        .order_by(Lead.score.desc(), Lead.created_at.desc())
        .limit(limit)
    )

    prospectos: list[TopProspectOut] = []
    for lead in filas.scalars():
        dimensiones = (lead.score_breakdown or {}).get("dimensions", {})
        # Las razones se ordenan por cuánto aportaron: la primera es la que
        # de verdad explica por qué este prospecto está arriba.
        ordenadas = sorted(
            dimensiones.values(), key=lambda d: d.get("contribution", 0), reverse=True
        )
        razones = [r for d in ordenadas for r in d.get("reasons", [])][:3]

        prospectos.append(
            TopProspectOut(
                lead_id=lead.id,
                company_id=lead.company_id,
                company_name=lead.company.name,
                city=lead.company.city,
                score=lead.score,
                email=(lead.contact.email if lead.contact else None) or lead.company.email,
                phone=(lead.contact.phone if lead.contact else None) or lead.company.phone,
                google_maps_url=lead.company.google_maps_url,
                stage_name=lead.stage.name,
                reasons=razones,
            )
        )
    return prospectos
