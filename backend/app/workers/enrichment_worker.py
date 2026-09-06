"""Worker de enriquecimiento: visita el sitio de cada empresa y saca contacto.

Es el paso que hace que el embudo tenga sentido: ni el scraper de Maps ni la
Places API devuelven email, así que sin esto no hay a quién escribirle.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.database import session_scope
from app.core.logging import get_logger
from app.enrichment.website_crawler import DomainRateLimiter, WebsiteCrawler
from app.models.company import Company
from app.repositories.company import CompanyRepository
from app.services.enrichment_svc import EnrichmentService
from app.services.job_svc import JobService

logger = get_logger(__name__)

_COMMIT_EVERY = 3


async def run_enrichment(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    company_ids = [uuid.UUID(cid) for cid in payload.get("company_ids", [])]
    limit = payload.get("limit", 100)
    # La búsqueda exigía email. Solo se puede comprobar aquí: ninguna ficha de
    # Google trae email, sale de rastrear la web.
    require_email = bool(payload.get("require_email"))

    async with session_scope() as session:
        jobs = JobService(session)
        repo = CompanyRepository(session)

        await jobs.mark_running(job_id)

        if company_ids:
            companies = [c for c in [await repo.get(cid) for cid in company_ids] if c]
        else:
            # Sin lista explícita, se enriquece lo que esté pendiente o viejo.
            companies = list(await repo.pending_enrichment(limit=limit))

        total = len(companies)
        await jobs.update_progress(job_id, current=0, total=total, message="Enriqueciendo…")
        await session.commit()

        if total == 0:
            await jobs.mark_completed(job_id, {"processed": 0, "emails_found": 0})
            await session.commit()
            return

        # Un solo limitador para todo el lote: si varias empresas comparten
        # dominio (franquicias, agregadores tipo Cluvi), el ritmo por dominio
        # se respeta igualmente.
        service = EnrichmentService(
            session, crawler=WebsiteCrawler(rate_limiter=DomainRateLimiter())
        )

        processed = emails_found = with_email = failed = 0
        descartadas: list[Company] = []

        for index, company in enumerate(companies, start=1):
            try:
                outcome = await service.enrich(company)
                processed += 1
                emails_found += outcome.emails_found
                if company.email:
                    with_email += 1
                elif require_email:
                    # Se buscó su email y no hay ninguno. Como la búsqueda lo
                    # exigía, la empresa no llega a la base: guardarla sería
                    # dejarle al usuario el trabajo de filtrarla a mano.
                    descartadas.append(company)
            except Exception as exc:  # noqa: BLE001 - un sitio roto no aborta el lote
                failed += 1
                logger.warning(
                    "enrichment_company_failed",
                    company_id=str(company.id),
                    error=f"{type(exc).__name__}: {exc}",
                )

            if index % _COMMIT_EVERY == 0 or index == total:
                await jobs.update_progress(
                    job_id,
                    current=index,
                    message=f"{index}/{total} — {with_email} con email",
                )
                await session.commit()

        for company in descartadas:
            await repo.delete(company)
        if descartadas:
            await session.commit()

        await jobs.mark_completed(
            job_id,
            {
                "processed": processed,
                "failed": failed,
                "emails_found": emails_found,
                "companies_with_email": with_email,
                "discarded_no_email": len(descartadas),
            },
        )
        await session.commit()

    logger.info(
        "enrichment_job_finished",
        job_id=str(job_id),
        processed=processed,
        with_email=with_email,
        failed=failed,
    )
