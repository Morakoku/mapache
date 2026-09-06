"""Módulos 1 y 2: catálogo de servicios que vende el usuario, y búsquedas.

Se llama `catalog_svc` y no `service_svc` para no arrastrar la ambigüedad de
"servicio" (capa de aplicación) contra "servicio" (lo que el usuario vende),
que aparece por todo el dominio.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobStatus, SourceType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.enrichment.signal_detector import ALL_SIGNALS
from app.models.search import Search, SearchResult, SearchRun
from app.models.service import Service
from app.repositories.base import BaseRepository
from app.scrapers.base import SearchQuery


class ServiceRepository(BaseRepository[Service]):
    model = Service


class SearchRepository(BaseRepository[Search]):
    model = Search


class CatalogService:
    """Servicios que vende el usuario (Módulo 1)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ServiceRepository(session)

    async def create(self, data: dict, owner_id: uuid.UUID | None = None) -> Service:
        self._validate_signals(data.get("opportunity_signals") or [])

        service = Service(owner_id=owner_id, **data)
        try:
            await self.repo.add(service)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"Ya existe un servicio llamado '{data.get('name')}'.",
                code="SERVICE_NAME_TAKEN",
            ) from exc
        return service

    async def get_or_404(self, service_id: uuid.UUID) -> Service:
        service = await self.repo.get(service_id)
        if service is None:
            raise NotFoundError.for_entity("service", service_id)
        return service

    async def update(self, service_id: uuid.UUID, data: dict) -> Service:
        service = await self.get_or_404(service_id)
        if "opportunity_signals" in data:
            self._validate_signals(data["opportunity_signals"] or [])

        for key, value in data.items():
            setattr(service, key, value)
        await self.session.flush()
        return service

    async def delete(self, service_id: uuid.UUID) -> None:
        service = await self.get_or_404(service_id)
        await self.repo.delete(service)

    async def list_all(self, *, only_active: bool = False) -> list[Service]:
        stmt = select(Service).order_by(Service.name)
        if only_active:
            stmt = stmt.where(Service.is_active.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _validate_signals(signals: list[str]) -> None:
        """Las señales son claves del catálogo, no texto libre.

        Aceptar cualquier cadena rompería el cruce con `company_signals` en
        silencio: el servicio nunca encontraría prospectos y no habría forma
        de saber por qué.
        """
        unknown = [s for s in signals if s not in ALL_SIGNALS]
        if unknown:
            raise ValidationError(
                f"Señales de oportunidad desconocidas: {', '.join(unknown)}.",
                code="UNKNOWN_OPPORTUNITY_SIGNAL",
                details={"unknown": unknown, "valid": list(ALL_SIGNALS)},
            )


class SearchService:
    """Configuración y ejecución de búsquedas (Módulo 2)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SearchRepository(session)

    async def create(self, data: dict, owner_id: uuid.UUID | None = None) -> Search:
        search = Search(owner_id=owner_id, **data)
        await self.repo.add(search)
        return search

    async def get_or_404(self, search_id: uuid.UUID) -> Search:
        search = await self.repo.get(search_id)
        if search is None:
            raise NotFoundError.for_entity("search", search_id)
        return search

    async def update(self, search_id: uuid.UUID, data: dict) -> Search:
        search = await self.get_or_404(search_id)
        for key, value in data.items():
            setattr(search, key, value)
        await self.session.flush()
        return search

    async def delete(self, search_id: uuid.UUID) -> None:
        await self.repo.delete(await self.get_or_404(search_id))

    async def list_all(self) -> list[Search]:
        result = await self.session.execute(select(Search).order_by(Search.created_at.desc()))
        return list(result.scalars().all())

    def to_query(self, search: Search) -> SearchQuery:
        """Traduce la configuración guardada al DTO que consumen los proveedores."""
        return SearchQuery(
            business_type=search.business_type,
            city=search.city,
            keywords=list(search.keywords or []),
            zone=search.zone,
            country=search.country,
            region=search.region,
            latitude=search.latitude,
            longitude=search.longitude,
            radius_km=float(search.radius_km),
            limit=search.target_count,
            min_rating=float(search.min_rating) if search.min_rating is not None else None,
            max_reviews=search.max_reviews,
            strict_match=search.strict_match,
        )

    async def start_run(
        self, search: Search, *, provider: SourceType, job_id: uuid.UUID | None = None
    ) -> SearchRun:
        run = SearchRun(
            search_id=search.id,
            job_id=job_id,
            provider=provider,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish_run(
        self,
        run: SearchRun,
        *,
        status: JobStatus,
        found: int = 0,
        new: int = 0,
        duplicate: int = 0,
        error: str | None = None,
    ) -> None:
        run.status = status
        run.results_found = found
        run.results_new = new
        run.results_duplicate = duplicate
        run.error_message = error
        run.finished_at = datetime.now(UTC)
        await self.session.flush()

    async def link_result(
        self,
        run: SearchRun,
        company_id: uuid.UUID,
        *,
        is_new: bool,
        position: int | None,
        raw_payload: dict | None,
    ) -> None:
        """Enlaza empresa y ejecución, guardando el payload crudo.

        El payload permite reprocesar sin volver a llamar al proveedor: si
        mañana se extrae un campo nuevo, sale de aquí en vez de repetir el
        scraping entero.
        """
        exists = await self.session.execute(
            select(SearchResult).where(
                SearchResult.search_run_id == run.id,
                SearchResult.company_id == company_id,
            )
        )
        if exists.scalar_one_or_none() is not None:
            return

        self.session.add(
            SearchResult(
                search_run_id=run.id,
                company_id=company_id,
                is_new=is_new,
                position=position,
                raw_payload=raw_payload,
            )
        )

    async def list_runs(self, search_id: uuid.UUID, limit: int = 20) -> list[SearchRun]:
        result = await self.session.execute(
            select(SearchRun)
            .where(SearchRun.search_id == search_id)
            .order_by(SearchRun.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_companies(self, search_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count(func.distinct(SearchResult.company_id)))
            .select_from(SearchResult)
            .join(SearchRun, SearchRun.id == SearchResult.search_run_id)
            .where(SearchRun.search_id == search_id)
        )
        return result.scalar_one() or 0
