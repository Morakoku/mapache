"""Cálculo y persistencia del prospect score (Módulo 8).

El motor (`app/scoring`) es puro; aquí está lo aburrido pero necesario: leer de
la base todo lo que necesita **en pocas consultas**, guardar el resultado y
dejar rastro cuando el número cambia de forma relevante.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ActivityType, ActorType, EmailStatus, VerificationStatus
from app.core.logging import get_logger
from app.models.company import CompanySignal
from app.models.email import EmailMessage
from app.models.lead import Lead
from app.models.search import Search
from app.scoring import ScoreInput, ScoreResult, compute_score
from app.scoring.guaki import guaki_opportunity
from app.scoring.vevra import bant_score, commercial_intelligence, segment_classify
from app.services.activity_svc import ActivityService
from app.services.mail_admin_svc import SettingsService
from app.utils.text import normalize_city

logger = get_logger(__name__)

# Un cambio menor de score no merece una línea en el historial: el desglose ya
# lo explica y llenar la actividad de ruido hace que nadie la lea.
NOTABLE_DELTA = 15


@dataclass(frozen=True, slots=True)
class ScoringResult:
    scored: int
    skipped: int


class ScoringService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.activities = ActivityService(session)

    async def score_leads(self, lead_ids: Sequence[uuid.UUID]) -> ScoringResult:
        """Recalcula el score de una lista de prospectos."""
        if not lead_ids:
            return ScoringResult(scored=0, skipped=0)

        leads = await self._load_leads(lead_ids)
        if not leads:
            return ScoringResult(scored=0, skipped=0)

        weights = (await SettingsService(self.session).get()).score_weights
        signals = await self._signals_by_company([lead.company_id for lead in leads])
        cities = await self._target_cities([lead.service_id for lead in leads])
        bounced = await self._bounced_leads([lead.id for lead in leads])
        now = datetime.now(UTC)

        scored = 0
        for lead in leads:
            data = self._to_input(
                lead,
                signals=signals.get(lead.company_id, frozenset()),
                target_cities=cities.get(lead.service_id, frozenset()),
                has_bounced=lead.id in bounced,
            )
            result = compute_score(data, weights, now)
            bant = bant_score(data)
            segment = segment_classify(data)
            result.breakdown["vevra"] = self._vevra_block(lead, data, result, bant, segment)
            result.breakdown["guaki"] = self._guaki_block(data)
            await self._apply(lead, result)
            scored += 1

        await self.session.flush()
        logger.info("scoring_done", scored=scored)
        return ScoringResult(scored=scored, skipped=len(lead_ids) - scored)

    async def pending_ids(self) -> list[uuid.UUID]:
        """Todos los prospectos. Es lo que hay que recalcular al tocar los pesos."""
        result = await self.session.execute(select(Lead.id))
        return list(result.scalars().all())

    async def score_all(self) -> ScoringResult:
        """Recalcula todo de una vez. Cómodo en tests y en lotes pequeños."""
        return await self.score_leads(await self.pending_ids())

    # ------------------------------------------------------------- lectura

    async def _load_leads(self, lead_ids: Sequence[uuid.UUID]) -> list[Lead]:
        stmt = (
            select(Lead)
            .where(Lead.id.in_(lead_ids))
            .options(
                selectinload(Lead.company),
                selectinload(Lead.contact),
                selectinload(Lead.service),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _signals_by_company(
        self, company_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, frozenset[str]]:
        stmt = select(CompanySignal.company_id, CompanySignal.signal_key).where(
            CompanySignal.company_id.in_(company_ids)
        )
        rows = (await self.session.execute(stmt)).all()

        grouped: dict[uuid.UUID, set[str]] = {}
        for company_id, key in rows:
            grouped.setdefault(company_id, set()).add(key)
        return {company_id: frozenset(keys) for company_id, keys in grouped.items()}

    async def _target_cities(
        self, service_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, frozenset[str]]:
        """Ciudades objetivo de cada servicio.

        No hay un campo "ciudades objetivo": las ciudades donde prospectas un
        servicio son, literalmente, aquellas donde has lanzado búsquedas para
        él. Se lee de ahí en vez de pedir que se configuren dos veces.
        """
        stmt = select(Search.service_id, Search.city).where(
            Search.service_id.in_(service_ids), Search.is_active.is_(True)
        )
        rows = (await self.session.execute(stmt)).all()

        grouped: dict[uuid.UUID, set[str]] = {}
        for service_id, city in rows:
            if service_id is None:
                continue
            grouped.setdefault(service_id, set()).add(normalize_city(city))
        return {service_id: frozenset(cities) for service_id, cities in grouped.items()}

    async def _bounced_leads(self, lead_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        stmt = (
            select(EmailMessage.lead_id)
            .where(
                EmailMessage.lead_id.in_(lead_ids),
                EmailMessage.status == EmailStatus.BOUNCED,
            )
            .distinct()
        )
        result = await self.session.execute(stmt)
        return {lead_id for lead_id in result.scalars().all() if lead_id is not None}

    # ------------------------------------------------------------ escritura

    @staticmethod
    def _vevra_block(
        lead: Lead,
        data: ScoreInput,
        result: ScoreResult,
        bant: Any,
        segment: Any,
    ) -> dict[str, Any]:
        """Vista comercial VEYRA que se guarda dentro de `score_breakdown` (sin
        migración): BANT, segmento A/B, Lead Intelligence Record y próxima
        acción. El Command Center la consume para explicar el prospecto."""
        name = lead.company.name if lead.company else None
        city = lead.company.city if lead.company else None
        lir = commercial_intelligence(
            data, result, bant, segment, company_name=name, city_label=city
        )
        return {
            "bant_total": bant.total,
            "is_sql": bant.is_sql,
            "is_close": bant.is_close,
            "segment": segment.segment,
            "segment_reason": segment.segment_reason,
            "segment_confidence": segment.confidence,
            "lir": lir,
        }

    @staticmethod
    def _guaki_block(data: ScoreInput) -> dict[str, Any]:
        """Clasificación Guaki (oportunidad de presencia digital) para el lead."""
        g = guaki_opportunity(data)
        return {"opportunity": g.opportunity, "reasons": g.reasons}

    @staticmethod
    def _to_input(
        lead: Lead,
        *,
        signals: frozenset[str],
        target_cities: frozenset[str],
        has_bounced: bool,
    ) -> ScoreInput:
        company = lead.company
        contact = lead.contact
        service = lead.service

        return ScoreInput(
            company_category=company.category,
            company_categories=tuple(company.categories or ()),
            company_city=company.city,
            company_email=company.email,
            company_phone=company.phone,
            company_website=company.website,
            rating=company.rating,
            reviews_count=company.reviews_count,
            employee_range=company.employee_range,
            signals=signals,
            data_quality_score=company.data_quality_score,
            extracted_at=company.first_extracted_at,
            service_name=service.name,
            target_industries=tuple(service.target_industries or ()),
            opportunity_signals=tuple(service.opportunity_signals or ()),
            problems_solved=tuple(service.problems_solved or ()),
            target_cities=target_cities,
            contact_email=contact.email if contact else None,
            contact_email_status=(
                contact.email_verified if contact else VerificationStatus.UNVERIFIED
            ),
            contact_is_role_email=contact.is_role_email if contact else False,
            contact_phone=contact.phone if contact else None,
            contact_linkedin=contact.linkedin_url if contact else None,
            engagement_score=lead.engagement_score,
            last_activity_at=lead.last_activity_at,
            has_bounced=has_bounced,
        )

    async def _apply(self, lead: Lead, result: ScoreResult) -> None:
        previous = lead.score
        lead.score = result.total
        lead.score_breakdown = result.breakdown
        lead.score_computed_at = result.computed_at

        if abs(result.total - previous) >= NOTABLE_DELTA:
            direction = "subió" if result.total > previous else "bajó"
            await self.activities.record(
                activity_type=ActivityType.NOTE,
                actor=ActorType.SYSTEM,
                title=f"El score {direction} de {previous} a {result.total}",
                description=result.summary,
                lead_id=lead.id,
                company_id=lead.company_id,
            )
