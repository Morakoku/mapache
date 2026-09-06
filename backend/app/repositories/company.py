"""Repositorio de empresas, con el dedupe de 4 niveles (§4.3).

Los niveles 1 y 2 son índices únicos; el 3 usa `pg_trgm` + distancia
geográfica. El nivel 4 (fusión manual) es una decisión del usuario, no del
repositorio: fusionar mal es peor que duplicar, porque se pierden
conversaciones.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.company import Company, CompanySignal, CompanySocial, CompanySource
from app.repositories.base import BaseRepository
from app.schemas.prospecting import CompanyFacetsOut, PlatformCountOut
from app.utils.url import is_share_url

logger = get_logger(__name__)

# Umbrales del nivel 3. Por debajo de esto no se propone la fusión: un falso
# positivo cuesta más que un duplicado.
_SIMILARITY_THRESHOLD = 0.75
_MAX_DISTANCE_METERS = 300


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    company: Company
    similarity: float
    distance_m: float | None
    reason: str


class CompanyRepository(BaseRepository[Company]):
    model = Company

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ------------------------------------------------------- dedupe niveles 1-2

    async def find_by_google_ids(
        self, *, ftid: str | None = None, place_id: str | None = None
    ) -> Company | None:
        """Nivel 1: identificadores de Google.

        Se consultan los dos porque proceden de proveedores distintos y no son
        intercambiables: el scraper da FTID y la Places API da place_id. Así,
        una empresa capturada por ambos se reconoce como la misma.
        """
        if not ftid and not place_id:
            return None

        conditions = []
        if ftid:
            conditions.append(Company.google_ftid == ftid)
        if place_id:
            conditions.append(Company.google_place_id == place_id)

        result = await self.session.execute(select(Company).where(or_(*conditions)).limit(1))
        return result.scalar_one_or_none()

    async def find_by_dedupe_key(
        self, dedupe_key: str, owner_id: uuid.UUID | None = None
    ) -> Company | None:
        """Nivel 2: clave determinista de nombre + dominio + teléfono + ciudad."""
        stmt = select(Company).where(
            Company.dedupe_key == dedupe_key,
            Company.owner_id.is_(owner_id) if owner_id is None else Company.owner_id == owner_id,
        )
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none()

    # ------------------------------------------------------- dedupe nivel 3

    async def find_similar(
        self,
        *,
        name: str,
        city: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        limit: int = 5,
    ) -> list[DuplicateCandidate]:
        """Nivel 3: nombres parecidos y cerca físicamente.

        No fusiona nada: devuelve candidatos para que el usuario decida. Dos
        sedes de una cadena tienen el mismo nombre y son empresas distintas,
        y solo una persona puede distinguir ese caso con fiabilidad.
        """
        similarity = func.similarity(Company.name, name).label("sim")
        stmt = select(Company, similarity).where(similarity > _SIMILARITY_THRESHOLD)

        if city:
            stmt = stmt.where(func.lower(Company.city) == city.lower())

        distance_col = None
        if latitude is not None and longitude is not None:
            distance_col = func.earth_distance(
                func.ll_to_earth(Company.latitude, Company.longitude),
                func.ll_to_earth(latitude, longitude),
            ).label("distance_m")
            stmt = stmt.add_columns(distance_col).where(
                Company.latitude.is_not(None),
                or_(distance_col < _MAX_DISTANCE_METERS, Company.latitude.is_(None)),
            )

        stmt = stmt.order_by(similarity.desc()).limit(limit)
        rows = await self.session.execute(stmt)

        candidates: list[DuplicateCandidate] = []
        for row in rows:
            company = row[0]
            sim = float(row[1])
            distance = float(row[2]) if len(row) > 2 and row[2] is not None else None
            candidates.append(
                DuplicateCandidate(
                    company=company,
                    similarity=sim,
                    distance_m=distance,
                    reason=(
                        f"nombre {sim:.0%} similar"
                        + (f" y a {distance:.0f} m" if distance is not None else "")
                    ),
                )
            )
        return candidates

    # ------------------------------------------------------- consultas de la UI

    def build_list_query(
        self,
        *,
        q: str | None = None,
        city: str | None = None,
        category: str | None = None,
        has_email: bool | None = None,
        has_website: bool | None = None,
        has_phone: bool | None = None,
        has_social: bool | None = None,
        platform: str | None = None,
        verified: bool | None = None,
        include_closed: bool = False,
        signal: str | None = None,
        min_rating: float | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> Select[tuple[Company]]:
        stmt = select(Company)

        if owner_id is not None:
            stmt = stmt.where(Company.owner_id == owner_id)
        if q:
            # `ilike` sobre el índice trigram: sirve para búsqueda incremental.
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(Company.name.ilike(pattern), Company.website_domain.ilike(pattern))
            )
        if city:
            stmt = stmt.where(func.lower(Company.city) == city.lower())
        if category:
            stmt = stmt.where(Company.category.ilike(f"%{category}%"))
        if has_email is True:
            stmt = stmt.where(Company.email.is_not(None))
        elif has_email is False:
            stmt = stmt.where(Company.email.is_(None))
        # "Sin web" es el filtro que más se usa para vender desarrollo web, así
        # que tiene que significar lo que el comercial entiende: que no tiene
        # sitio propio. Un negocio cuyo "sitio" es su Instagram cuenta como sin
        # web —`website_domain` solo se rellena con dominios propios—.
        if has_website is True:
            stmt = stmt.where(Company.website_domain.is_not(None))
        elif has_website is False:
            stmt = stmt.where(Company.website_domain.is_(None))
        if has_phone is True:
            stmt = stmt.where(Company.phone.is_not(None))
        elif has_phone is False:
            stmt = stmt.where(Company.phone.is_(None))

        social_exists = select(CompanySocial.id).where(CompanySocial.company_id == Company.id)
        if platform:
            stmt = stmt.where(social_exists.where(CompanySocial.platform == platform).exists())
        elif has_social is True:
            stmt = stmt.where(social_exists.exists())
        elif has_social is False:
            stmt = stmt.where(~social_exists.exists())

        # Verificado = Google emitió un identificador para esa ficha. No se
        # puede fabricar, así que es la garantía dura de que el negocio existe.
        google_id = or_(Company.google_ftid.is_not(None), Company.google_place_id.is_not(None))
        if verified is True:
            stmt = stmt.where(google_id)
        elif verified is False:
            stmt = stmt.where(~google_id)

        # Un negocio cerrado no es un prospecto. Se excluye por defecto y hay
        # que pedirlo expresamente para verlo.
        if not include_closed:
            stmt = stmt.where(Company.is_permanently_closed.is_(False))

        if min_rating is not None:
            stmt = stmt.where(Company.rating >= min_rating)
        if signal:
            stmt = stmt.where(
                select(CompanySignal.id)
                .where(
                    CompanySignal.company_id == Company.id,
                    CompanySignal.signal_key == signal,
                )
                .exists()
            )

        return stmt.order_by(Company.last_extracted_at.desc())

    async def facets(self) -> CompanyFacetsOut:
        """Conteos por cada filtro, en una sola consulta agregada.

        Va en una consulta y no en once: con `count(*) FILTER (WHERE …)` es un
        único recorrido de la tabla, y once viajes a la base para pintar unos
        contadores de filtro no se justifica.
        """
        social_exists = (
            select(CompanySocial.id).where(CompanySocial.company_id == Company.id).exists()
        )

        def contar(condition: Any) -> Any:
            return func.count().filter(condition)

        row = (
            await self.session.execute(
                select(
                    func.count().label("total"),
                    contar(Company.email.is_not(None)).label("with_email"),
                    contar(Company.email.is_(None)).label("without_email"),
                    contar(Company.website_domain.is_not(None)).label("with_website"),
                    contar(Company.website_domain.is_(None)).label("without_website"),
                    contar(Company.phone.is_not(None)).label("with_phone"),
                    contar(Company.phone.is_(None)).label("without_phone"),
                    contar(Company.whatsapp.is_not(None)).label("with_whatsapp"),
                    contar(Company.whatsapp.is_(None)).label("without_whatsapp"),
                    contar(social_exists).label("with_social"),
                    contar(~social_exists).label("without_social"),
                    contar(
                        or_(
                            Company.google_ftid.is_not(None),
                            Company.google_place_id.is_not(None),
                        )
                    ).label("verified"),
                    contar(
                        Company.google_ftid.is_(None) & Company.google_place_id.is_(None)
                    ).label("unverified"),
                    contar(Company.is_permanently_closed.is_(True)).label("closed"),
                ).select_from(Company)
            )
        ).one()

        platforms = (
            await self.session.execute(
                select(CompanySocial.platform, func.count(func.distinct(CompanySocial.company_id)))
                .group_by(CompanySocial.platform)
                .order_by(func.count(func.distinct(CompanySocial.company_id)).desc())
            )
        ).all()

        cities = (
            await self.session.execute(
                select(Company.city)
                .where(Company.city.is_not(None))
                .group_by(Company.city)
                .order_by(func.count().desc())
                .limit(40)
            )
        ).scalars().all()

        return CompanyFacetsOut(
            total=row.total,
            with_email=row.with_email,
            without_email=row.without_email,
            with_website=row.with_website,
            without_website=row.without_website,
            with_phone=row.with_phone,
            without_phone=row.without_phone,
            with_whatsapp=row.with_whatsapp,
            without_whatsapp=row.without_whatsapp,
            with_social=row.with_social,
            without_social=row.without_social,
            verified=row.verified,
            unverified=row.unverified,
            permanently_closed=row.closed,
            platforms=[PlatformCountOut(platform=p, count=c) for p, c in platforms],
            cities=[c for c in cities if c],
        )

    async def pending_enrichment(self, limit: int = 100) -> Sequence[Company]:
        """Empresas que nunca se enriquecieron o cuyo dato está viejo."""
        stmt = (
            select(Company)
            .where(
                or_(
                    Company.last_enriched_at.is_(None),
                    Company.last_enriched_at < text("now() - interval '90 days'"),
                )
            )
            .order_by(Company.created_at)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------- hijos

    async def upsert_source(
        self,
        company_id: uuid.UUID,
        *,
        field_name: str,
        value: str,
        source: str,
        source_url: str | None = None,
        confidence: int = 50,
        verification: str | None = None,
    ) -> CompanySource:
        """Registra la procedencia de un dato, o actualiza la confianza si ya
        se conocía por la misma fuente."""
        existing = await self.session.execute(
            select(CompanySource).where(
                CompanySource.company_id == company_id,
                CompanySource.field_name == field_name,
                CompanySource.value == value,
            )
        )
        record = existing.scalar_one_or_none()

        if record is not None:
            record.confidence = max(record.confidence, confidence)
            if verification is not None:
                record.verification = verification  # type: ignore[assignment]
            if source_url:
                record.source_url = source_url
            return record

        record = CompanySource(
            company_id=company_id,
            field_name=field_name,
            value=value,
            source=source,
            source_url=source_url,
            confidence=confidence,
        )
        if verification is not None:
            record.verification = verification  # type: ignore[assignment]
        self.session.add(record)
        return record

    async def upsert_social(
        self,
        company_id: uuid.UUID,
        *,
        platform: str,
        url: str,
        source: str,
        handle: str | None = None,
    ) -> CompanySocial | None:
        # Red de seguridad: cualquier camino nuevo que traiga redes pasa por
        # aquí, y un botón de compartir guardado se enseña como si fuera la
        # cuenta de la empresa.
        if is_share_url(url):
            logger.debug("social_descartada_por_compartir", company_id=str(company_id), url=url)
            return None

        existing = await self.session.execute(
            select(CompanySocial).where(
                CompanySocial.company_id == company_id,
                CompanySocial.platform == platform,
                CompanySocial.url == url,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None

        record = CompanySocial(
            company_id=company_id,
            platform=platform,
            url=url,
            handle=handle,
            source=source,
        )
        self.session.add(record)
        return record

    async def replace_signals(
        self,
        company_id: uuid.UUID,
        signals: list[tuple[str, dict | None]],
        source: str,
    ) -> None:
        """Sustituye las señales de la empresa por las recién detectadas.

        Se reemplaza en vez de acumular porque una señal es una foto del
        estado actual: si el negocio ya publicó su web, `no_website` debe
        desaparecer, no quedarse para siempre.
        """
        existing = await self.session.execute(
            select(CompanySignal).where(CompanySignal.company_id == company_id)
        )
        by_key = {s.signal_key: s for s in existing.scalars().all()}
        incoming = {key for key, _ in signals}

        for key, value in signals:
            record = by_key.get(key)
            if record is None:
                self.session.add(
                    CompanySignal(
                        company_id=company_id,
                        signal_key=key,
                        value=value,
                        source=source,
                    )
                )
            else:
                record.value = value

        for key, record in by_key.items():
            if key not in incoming:
                await self.session.delete(record)
