"""Módulos 3, 4 y 5 — empresas descubiertas y su enriquecimiento."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import get_job_queue
from app.core.database import get_db
from app.core.enums import JobType
from app.core.exceptions import NotFoundError
from app.core.idempotency import require_idempotency
from app.models.company import Company
from app.repositories.company import CompanyRepository
from app.schemas.common import JobAcceptedOut, Page
from app.schemas.prospecting import (
    CompanyDetailOut,
    CompanyDossierOut,
    CompanyFacetsOut,
    CompanyManualIn,
    CompanySummaryOut,
    DossierContactOut,
    DuplicateCandidateOut,
    WebFindingOut,
)
from app.services.company_svc import CompanyService
from app.services.dossier_svc import DossierService
from app.services.job_svc import JobService
from app.utils.dedupe import build_dedupe_key
from app.utils.phone import to_e164
from app.utils.url import extract_domain, normalize_url

router = APIRouter(dependencies=[Depends(require_idempotency)])


@router.get("", response_model=Page[CompanySummaryOut])
async def list_companies(
    q: str | None = Query(default=None, description="Nombre o dominio"),
    city: str | None = None,
    category: str | None = None,
    has_email: bool | None = None,
    has_website: bool | None = None,
    has_phone: bool | None = None,
    has_social: bool | None = None,
    platform: str | None = Query(default=None, description="instagram, tiktok, linkedin, x…"),
    verified: bool | None = Query(
        default=None, description="Solo las que tienen ficha real en Google"
    ),
    include_closed: bool = Query(default=False, description="Incluir cerradas permanentemente"),
    signal: str | None = Query(default=None, description="Clave de señal de oportunidad"),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[CompanySummaryOut]:
    repo = CompanyRepository(db)
    stmt = repo.build_list_query(
        q=q,
        city=city,
        category=category,
        has_email=has_email,
        has_website=has_website,
        has_phone=has_phone,
        has_social=has_social,
        platform=platform,
        verified=verified,
        include_closed=include_closed,
        signal=signal,
        min_rating=min_rating,
    )
    items, total = await repo.paginate(stmt, page=page, size=size)
    return Page.build([CompanySummaryOut.model_validate(c) for c in items], total, page, size)


@router.get("/facets", response_model=CompanyFacetsOut)
async def company_facets(db: AsyncSession = Depends(get_db)) -> CompanyFacetsOut:
    """Cuántas empresas hay de cada cosa.

    El filtro "sin web" solo sirve si antes se sabe que hay 340 sin web. Un
    filtro que puede devolver cero sin avisar se prueba una vez y no se vuelve
    a tocar.
    """
    return await CompanyRepository(db).facets()


@router.post("", response_model=CompanyDetailOut, status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CompanyManualIn,
    db: AsyncSession = Depends(get_db),
) -> CompanyDetailOut:
    """Alta manual, para empresas que llegan fuera de una búsqueda."""
    website = normalize_url(payload.website)
    phone = to_e164(payload.phone)
    whatsapp = to_e164(payload.whatsapp) or (
        f"+{payload.whatsapp.lstrip('+')}" if payload.whatsapp else None
    )
    now = datetime.now(UTC)

    company = Company(
        name=payload.name.strip(),
        category=payload.category,
        address=payload.address,
        city=payload.city,
        phone=phone,
        phone_raw=payload.phone,
        whatsapp=whatsapp,
        email=payload.email,
        website=website,
        website_domain=extract_domain(website),
        dedupe_key=build_dedupe_key(
            name=payload.name, website=website, phone_e164=phone, city=payload.city
        ),
        first_extracted_at=now,
        last_extracted_at=now,
    )
    # Sin esto la ficha salía siempre al 0%, aunque se hubieran rellenado
    # todos los campos a mano.
    company.data_quality_score = CompanyService(db).quality_score(company)

    await CompanyRepository(db).add(company)
    await db.commit()
    await db.refresh(company)
    return CompanyDetailOut.model_validate(company)


@router.get("/{company_id}", response_model=CompanyDetailOut)
async def get_company(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CompanyDetailOut:
    company = await CompanyRepository(db).get(company_id)
    if company is None:
        raise NotFoundError.for_entity("company", company_id)
    return CompanyDetailOut.model_validate(company)


@router.get("/{company_id}/dossier", response_model=CompanyDossierOut)
async def company_dossier(
    company_id: uuid.UUID,
    refresh_web: bool = Query(
        default=False,
        description="Volver a buscar en la web aunque lo guardado siga fresco",
    ),
    db: AsyncSession = Depends(get_db),
) -> CompanyDossierOut:
    """Todo lo que se sabe de la empresa, en un solo sitio.

    La búsqueda web se guarda un mes: cada consulta cuesta cuota y la
    información pública de un negocio pequeño no cambia de un día para otro.
    """
    repo = CompanyRepository(db)
    company = await repo.get(company_id)
    if company is None:
        raise NotFoundError.for_entity("company", company_id)

    dossier = await DossierService(db).build(company, refresh_web=refresh_web)
    await db.commit()
    # Las relaciones se recargan a mano: si la empresa ya estaba en la sesión,
    # `get()` devuelve la instancia del mapa de identidad sin volver a leerlas,
    # y serializarlas dispararía una carga perezosa fuera del greenlet.
    await db.refresh(company, ["sources", "socials", "signals"])

    return CompanyDossierOut(
        company=CompanyDetailOut.model_validate(dossier.company),
        lead_id=dossier.lead_id,
        lead_score=dossier.lead_score,
        stage_name=dossier.stage_name,
        contacts=[
            DossierContactOut(
                display_name=c.display_name,
                job_title=c.job_title,
                email=c.email,
                phone=c.phone,
                is_contactable=c.is_contactable,
            )
            for c in dossier.contacts
        ],
        signal_labels=dossier.signal_labels,
        web_findings=[WebFindingOut(**asdict(f)) for f in dossier.web_findings],
        web_findings_at=dossier.web_findings_at,
        web_available=dossier.web_available,
        notes=dossier.notes,
    )


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Borrado en cascada de la empresa y todos sus datos.

    Es también el mecanismo de supresión que exige la Ley 1581/2012: elimina
    fuentes, redes y señales asociadas.
    """
    repo = CompanyRepository(db)
    company = await repo.get(company_id)
    if company is None:
        raise NotFoundError.for_entity("company", company_id)
    await repo.delete(company)
    await db.commit()


@router.get("/{company_id}/duplicates", response_model=list[DuplicateCandidateOut])
async def find_duplicates(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[DuplicateCandidateOut]:
    """Posibles duplicados de esta empresa.

    Solo propone: fusionar es decisión del usuario. Dos sedes de una cadena
    comparten nombre y ciudad, y equivocarse al fusionar pierde conversaciones.
    """
    repo = CompanyRepository(db)
    company = await repo.get(company_id)
    if company is None:
        raise NotFoundError.for_entity("company", company_id)

    candidates = await repo.find_similar(
        name=company.name,
        city=company.city,
        latitude=company.latitude,
        longitude=company.longitude,
    )
    return [
        DuplicateCandidateOut(
            company=CompanySummaryOut.model_validate(c.company),
            similarity=c.similarity,
            distance_m=c.distance_m,
            reason=c.reason,
        )
        for c in candidates
        if c.company.id != company_id
    ]


@router.post(
    "/{company_id}/enrich",
    response_model=JobAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enrich_company(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedOut:
    company = await CompanyRepository(db).get(company_id)
    if company is None:
        raise NotFoundError.for_entity("company", company_id)

    payload = {"company_ids": [str(company_id)]}
    job = await JobService(db).create(JobType.ENRICHMENT, payload, progress_total=1)
    await db.commit()

    await get_job_queue().enqueue(JobType.ENRICHMENT, payload, job_id=job.id)
    return JobAcceptedOut(job_id=job.id, message="Enriqueciendo la empresa.")


@router.post(
    "/bulk-enrich",
    response_model=JobAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def bulk_enrich(
    company_ids: list[uuid.UUID],
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedOut:
    payload = {"company_ids": [str(cid) for cid in company_ids]}
    job = await JobService(db).create(JobType.ENRICHMENT, payload, progress_total=len(company_ids))
    await db.commit()

    await get_job_queue().enqueue(JobType.ENRICHMENT, payload, job_id=job.id)
    return JobAcceptedOut(job_id=job.id, message=f"Enriqueciendo {len(company_ids)} empresas.")
