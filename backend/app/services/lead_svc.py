"""Módulo 7 — el servicio central del CRM.

Todo movimiento de etapa pasa por `move_stage`: escribe el historial, calcula
cuánto tiempo estuvo en la etapa anterior y deja actividad. Si alguien mueve
un lead sin pasar por aquí, el embudo del Módulo 20 deja de cuadrar.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, Subquery, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import (
    ActivityType,
    ActorType,
    Direction,
    EmailStatus,
    LeadStatus,
    StageType,
)
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.company import Company
from app.models.contact import Contact
from app.models.email import EmailMessage
from app.models.lead import ENGAGEMENT_POINTS, Lead, LeadStageHistory
from app.models.pipeline import AUTO_ADVANCE_CEILING, PipelineStage
from app.repositories.base import BaseRepository
from app.services.activity_svc import ActivityService
from app.services.pipeline_svc import PipelineService

logger = get_logger(__name__)

# Orden del embudo. Se usa para decidir si un movimiento es avance o
# retroceso, y para no auto-avanzar hacia atrás.
_STAGE_ORDER: dict[StageType, int] = {
    stage_type: index for index, stage_type in enumerate(StageType)
}

# Segmentos de seguimiento (Módulo 14). Son la lectura comercial del tracking:
# quién demostró interés y con quién no pasó nada.
SEGMENTS = frozenset({"opened_no_reply", "clicked_no_reply", "sent_no_open", "replied", "bounced"})


def _outbound_stats() -> Subquery:
    """Resumen de correo saliente por prospecto.

    Se agrega en una subconsulta en vez de calcularlo por fila: con 3.000
    prospectos, una consulta por lead son 3.000 viajes a la base.
    """
    return (
        select(
            EmailMessage.lead_id.label("lead_id"),
            func.count().label("emails_sent"),
            func.max(EmailMessage.sent_at).label("last_sent_at"),
            func.coalesce(func.sum(EmailMessage.open_count), 0).label("opens"),
            func.max(EmailMessage.opened_at).label("last_opened_at"),
            func.coalesce(func.sum(EmailMessage.click_count), 0).label("clicks"),
            func.max(EmailMessage.clicked_at).label("last_clicked_at"),
            func.count(EmailMessage.bounced_at).label("bounces"),
        )
        .where(
            EmailMessage.direction == Direction.OUTBOUND,
            EmailMessage.lead_id.is_not(None),
            EmailMessage.status.notin_([EmailStatus.DRAFT, EmailStatus.CANCELLED]),
        )
        .group_by(EmailMessage.lead_id)
        .subquery()
    )


@dataclass(frozen=True, slots=True)
class BulkCreateResult:
    created: list[Lead]
    skipped_existing: int
    skipped_no_company: int


class LeadRepository(BaseRepository[Lead]):
    model = Lead


class LeadService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = LeadRepository(session)
        self.pipeline = PipelineService(session)
        self.activities = ActivityService(session)

    # ------------------------------------------------------------ alta

    async def create(
        self,
        *,
        company_id: uuid.UUID,
        service_id: uuid.UUID,
        contact_id: uuid.UUID | None = None,
        stage_id: uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> Lead:
        company = await self.session.get(Company, company_id)
        if company is None:
            raise NotFoundError.for_entity("company", company_id)

        existing = await self.repo.get_by(company_id=company_id, service_id=service_id)
        if existing is not None:
            raise ConflictError(
                f"'{company.name}' ya tiene un prospecto para este servicio.",
                code="LEAD_ALREADY_EXISTS",
                details={"lead_id": str(existing.id)},
            )

        stage = (
            await self.pipeline.get_or_404(stage_id)
            if stage_id
            else await self._initial_stage(company, contact_id)
        )

        lead = Lead(
            company_id=company_id,
            service_id=service_id,
            contact_id=contact_id or await self._pick_primary_contact(company_id),
            stage_id=stage.id,
            owner_id=owner_id,
            last_activity_at=datetime.now(UTC),
        )
        try:
            await self.repo.add(lead)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                "No se pudo crear el prospecto.", code="LEAD_CREATE_FAILED"
            ) from exc

        await self._record_stage_entry(lead, from_stage=None, actor=ActorType.SYSTEM)
        await self.activities.record(
            ActivityType.LEAD_CREATED,
            title=f"Prospecto creado para {company.name}",
            lead_id=lead.id,
            company_id=company_id,
            owner_id=owner_id,
        )
        return lead

    async def create_bulk(
        self,
        *,
        company_ids: Sequence[uuid.UUID],
        service_id: uuid.UUID,
        owner_id: uuid.UUID | None = None,
    ) -> BulkCreateResult:
        """Alta en lote desde una selección de empresas.

        Los duplicados se saltan en silencio en vez de abortar: seleccionar 50
        empresas de las que 3 ya son prospectos es lo normal, no un error.
        """
        created: list[Lead] = []
        skipped_existing = skipped_no_company = 0

        existing_rows = await self.session.execute(
            select(Lead.company_id).where(
                Lead.company_id.in_(company_ids), Lead.service_id == service_id
            )
        )
        already = {row[0] for row in existing_rows}

        for company_id in company_ids:
            if company_id in already:
                skipped_existing += 1
                continue
            try:
                created.append(
                    await self.create(
                        company_id=company_id, service_id=service_id, owner_id=owner_id
                    )
                )
            except NotFoundError:
                skipped_no_company += 1

        logger.info(
            "leads_bulk_created",
            created=len(created),
            skipped_existing=skipped_existing,
            skipped_no_company=skipped_no_company,
        )
        return BulkCreateResult(created, skipped_existing, skipped_no_company)

    async def _initial_stage(self, company: Company, contact_id: uuid.UUID | None) -> PipelineStage:
        """Entrada al embudo según lo que ya se sabe de la empresa.

        Un prospecto con email conocido no debería empezar en "Prospecto":
        ya tiene el contacto encontrado, y hacer que el usuario lo arrastre a
        mano es trabajo inútil.
        """
        if contact_id or company.email:
            stage = await self.pipeline.get_by_type(StageType.CONTACT_FOUND)
            if stage is not None:
                return stage
        return await self.pipeline.get_default()

    async def _pick_primary_contact(self, company_id: uuid.UUID) -> uuid.UUID | None:
        """Contacto principal: el marcado como tal, o el primero contactable."""
        result = await self.session.execute(
            select(Contact)
            .where(Contact.company_id == company_id, Contact.do_not_contact.is_(False))
            .order_by(Contact.is_primary.desc(), Contact.created_at)
        )
        for contact in result.scalars().all():
            if contact.is_contactable:
                return contact.id
        return None

    # ------------------------------------------------------------ etapas

    async def get_or_404(self, lead_id: uuid.UUID) -> Lead:
        result = await self.session.execute(
            select(Lead)
            .where(Lead.id == lead_id)
            .options(selectinload(Lead.company), selectinload(Lead.contact))
            # La sesión va con `expire_on_commit=False`, así que tras mover un
            # lead la relación `stage` sigue cargada con la etapa vieja y la
            # respuesta del POST devolvería la columna anterior. Con
            # `populate_existing` se relee desde la base.
            .execution_options(populate_existing=True)
        )
        lead = result.scalar_one_or_none()
        if lead is None:
            raise NotFoundError.for_entity("lead", lead_id)
        return lead

    async def move_stage(
        self,
        lead: Lead,
        stage_id: uuid.UUID,
        *,
        actor: ActorType = ActorType.USER,
        reason: str | None = None,
    ) -> Lead:
        """Mueve un lead de etapa y deja rastro.

        El movimiento manual es libre en cualquier dirección: no todos los
        prospectos pasan por todas las fases, y el usuario manda.
        """
        target = await self.pipeline.get_or_404(stage_id)
        if target.id == lead.stage_id:
            return lead

        previous = await self.pipeline.get_or_404(lead.stage_id)
        lead.stage_id = target.id
        lead.last_activity_at = datetime.now(UTC)

        # Ganado y perdido son estados, no solo columnas: cierran el lead.
        if target.is_won:
            lead.status = LeadStatus.WON
            lead.won_at = datetime.now(UTC)
        elif target.is_lost:
            lead.status = LeadStatus.LOST
            lead.lost_at = datetime.now(UTC)
            lead.lost_reason = reason
        elif lead.status in {LeadStatus.WON, LeadStatus.LOST}:
            # Reabrir: si vuelve al tablero, deja de estar cerrado.
            lead.status = LeadStatus.OPEN
            lead.won_at = lead.lost_at = None

        await self._record_stage_entry(lead, from_stage=previous, actor=actor, reason=reason)
        await self.activities.record(
            ActivityType.STAGE_CHANGED,
            title=f"{previous.name} → {target.name}",
            description=reason,
            lead_id=lead.id,
            company_id=lead.company_id,
            actor=actor,
            metadata={"from": previous.stage_key, "to": target.stage_key},
            owner_id=lead.owner_id,
        )
        await self.session.flush()
        return lead

    async def _record_stage_entry(
        self,
        lead: Lead,
        *,
        from_stage: PipelineStage | None,
        actor: ActorType,
        reason: str | None = None,
    ) -> None:
        """Escribe el historial y calcula el tiempo en la etapa anterior."""
        now = datetime.now(UTC)
        duration: int | None = None

        if from_stage is not None:
            # La sesión va con `autoflush=False`, así que la fila de historial
            # anterior puede seguir pendiente en memoria. Sin este flush la
            # consulta no la ve y `duration_seconds` sale siempre NULL, que
            # deja la métrica de velocidad del embudo vacía.
            await self.session.flush()

            last = await self.session.execute(
                select(LeadStageHistory.entered_at)
                .where(LeadStageHistory.lead_id == lead.id)
                .order_by(LeadStageHistory.entered_at.desc())
                .limit(1)
            )
            entered_at = last.scalar_one_or_none()
            if entered_at is not None:
                duration = int((now - entered_at).total_seconds())

        target = await self.pipeline.get_or_404(lead.stage_id)
        self.session.add(
            LeadStageHistory(
                lead_id=lead.id,
                from_stage_id=from_stage.id if from_stage else None,
                to_stage_id=target.id,
                from_stage_type=from_stage.stage_type if from_stage else None,
                to_stage_type=target.stage_type,
                actor=actor,
                reason=reason,
                entered_at=now,
                duration_seconds=duration,
            )
        )

    async def auto_advance(self, lead: Lead, event: str) -> Lead:
        """Avance automático por un evento (apertura, respuesta...).

        Tres reglas que no se rompen: solo avanza (nunca retrocede), nunca
        pasa de CONVERSATION —de ahí en adelante la decisión es comercial y la
        toma el usuario—, y no toca leads ya cerrados.
        """
        if lead.status in {LeadStatus.WON, LeadStatus.LOST, LeadStatus.DISQUALIFIED}:
            return lead

        result = await self.session.execute(
            select(PipelineStage)
            # `contains` genera `auto_advance_on @> ARRAY[...]`, que además es
            # indexable con GIN si algún día hace falta.
            .where(PipelineStage.auto_advance_on.contains([event]))
            .order_by(PipelineStage.position)
        )
        candidates = list(result.scalars().all())
        if not candidates:
            return lead

        current = await self.pipeline.get_or_404(lead.stage_id)
        current_rank = _STAGE_ORDER[current.stage_type]
        ceiling_rank = _STAGE_ORDER[AUTO_ADVANCE_CEILING]

        for target in candidates:
            target_rank = _STAGE_ORDER[target.stage_type]
            if target_rank <= current_rank:
                continue  # no retroceder
            if target_rank > ceiling_rank:
                continue  # más allá de CONVERSACIÓN decide el usuario
            return await self.move_stage(
                lead, target.id, actor=ActorType.SYSTEM, reason=f"Evento automático: {event}"
            )

        return lead

    async def add_engagement(self, lead: Lead, event: str) -> Lead:
        """Suma puntos de engagement (Módulo 14).

        Es un indicador de interacción, no una verdad: la UI muestra siempre
        el detalle que lo compone.
        """
        points = ENGAGEMENT_POINTS.get(event, 0)
        if points:
            lead.engagement_score = min(255, lead.engagement_score + points)
        lead.last_activity_at = datetime.now(UTC)
        await self.session.flush()
        return lead

    # ------------------------------------------------------------ cierre

    async def mark_won(
        self, lead: Lead, *, value: float | None = None, note: str | None = None
    ) -> Lead:
        stage = await self.pipeline.get_by_type(StageType.WON)
        if stage is None:
            raise ValidationError("No hay etapa de tipo GANADO.", code="NO_WON_STAGE")
        if value is not None:
            from decimal import Decimal

            lead.estimated_value = Decimal(str(value))
        lead = await self.move_stage(lead, stage.id, reason=note)
        await self.activities.record(
            ActivityType.WON,
            title="Cliente ganado",
            description=note,
            lead_id=lead.id,
            company_id=lead.company_id,
            actor=ActorType.USER,
            owner_id=lead.owner_id,
        )
        return lead

    async def mark_lost(self, lead: Lead, *, reason: str) -> Lead:
        stage = await self.pipeline.get_by_type(StageType.LOST)
        if stage is None:
            raise ValidationError("No hay etapa de tipo PERDIDO.", code="NO_LOST_STAGE")
        lead = await self.move_stage(lead, stage.id, reason=reason)
        await self.activities.record(
            ActivityType.LOST,
            title="Prospecto perdido",
            description=reason,
            lead_id=lead.id,
            company_id=lead.company_id,
            actor=ActorType.USER,
            owner_id=lead.owner_id,
        )
        return lead

    # ------------------------------------------------------------ segmentos

    def build_segment_query(
        self,
        segment: str,
        *,
        min_days: int = 0,
        service_id: uuid.UUID | None = None,
    ) -> Select[Any]:
        """Prospectos agrupados por lo que hicieron con el correo (Módulo 14).

        El segmento que importa a diario es `opened_no_reply`: abrieron y no
        contestaron. Es interés demostrado sin conversación — exactamente la
        gente a la que hay que volver a escribir, y la que un CRM sin tracking
        no sabe distinguir de quien nunca abrió.
        """
        if segment not in SEGMENTS:
            raise ValidationError(
                f"Segmento desconocido: {segment}.",
                code="UNKNOWN_SEGMENT",
                details={"available": sorted(SEGMENTS)},
            )

        stats = _outbound_stats()
        cutoff = datetime.now(UTC) - timedelta(days=min_days)

        stmt = select(
            Lead,
            stats.c.emails_sent,
            stats.c.last_sent_at,
            stats.c.opens,
            stats.c.last_opened_at,
            stats.c.clicks,
            stats.c.last_clicked_at,
            stats.c.bounces,
        ).join(stats, stats.c.lead_id == Lead.id)

        match segment:
            case "opened_no_reply":
                stmt = stmt.where(
                    stats.c.last_opened_at.is_not(None),
                    stats.c.last_opened_at <= cutoff,
                    Lead.replied_at.is_(None),
                    Lead.status == LeadStatus.OPEN,
                    # Un rebote duro no es un prospecto pendiente, es una
                    # dirección muerta.
                    stats.c.bounces == 0,
                ).order_by(stats.c.opens.desc(), stats.c.last_opened_at.desc())
            case "clicked_no_reply":
                stmt = stmt.where(
                    stats.c.clicks > 0,
                    Lead.replied_at.is_(None),
                    Lead.status == LeadStatus.OPEN,
                ).order_by(stats.c.clicks.desc(), stats.c.last_clicked_at.desc())
            case "sent_no_open":
                stmt = stmt.where(
                    stats.c.emails_sent > 0,
                    stats.c.last_sent_at <= cutoff,
                    stats.c.last_opened_at.is_(None),
                    Lead.replied_at.is_(None),
                    Lead.status == LeadStatus.OPEN,
                    stats.c.bounces == 0,
                ).order_by(stats.c.last_sent_at.desc())
            case "replied":
                stmt = stmt.where(Lead.replied_at.is_not(None)).order_by(Lead.replied_at.desc())
            case _:  # bounced
                stmt = stmt.where(stats.c.bounces > 0).order_by(stats.c.last_sent_at.desc())

        if service_id is not None:
            stmt = stmt.where(Lead.service_id == service_id)

        return stmt

    async def paginate_segment(
        self, stmt: Select[Any], *, page: int, size: int
    ) -> tuple[list[Any], int]:
        """Pagina una consulta de segmento (devuelve filas, no entidades)."""
        total_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self.session.execute(total_stmt)).scalar_one() or 0
        result = await self.session.execute(stmt.limit(size).offset((page - 1) * size))
        return list(result.all()), total

    # ------------------------------------------------------------ consultas

    def build_list_query(
        self,
        *,
        stage_id: uuid.UUID | None = None,
        service_id: uuid.UUID | None = None,
        status: LeadStatus | None = None,
        min_score: int | None = None,
        engagement: str | None = None,
        has_email: bool | None = None,
        q: str | None = None,
        followup_before: datetime | None = None,
    ) -> Select[tuple[Lead]]:
        stmt = (
            select(Lead)
            .join(Company, Company.id == Lead.company_id)
            .options(
                selectinload(Lead.company),
                selectinload(Lead.contact),
                selectinload(Lead.stage),
                selectinload(Lead.service),
            )
        )

        if stage_id is not None:
            stmt = stmt.where(Lead.stage_id == stage_id)
        if service_id is not None:
            stmt = stmt.where(Lead.service_id == service_id)
        if status is not None:
            stmt = stmt.where(Lead.status == status)
        if min_score is not None:
            stmt = stmt.where(Lead.score >= min_score)
        if followup_before is not None:
            stmt = stmt.where(
                Lead.next_follow_up_at.is_not(None),
                Lead.next_follow_up_at <= followup_before,
            )
        if has_email is True:
            stmt = stmt.where(
                or_(
                    Company.email.is_not(None),
                    select(Contact.id)
                    .where(Contact.company_id == Company.id, Contact.email.is_not(None))
                    .exists(),
                )
            )
        if q:
            stmt = stmt.where(Company.name.ilike(f"%{q}%"))
        if engagement:
            bands = {"FRIO": (0, 0), "BAJO": (1, 14), "MEDIO": (15, 39), "ALTO": (40, 255)}
            if engagement.upper() in bands:
                low, high = bands[engagement.upper()]
                stmt = stmt.where(Lead.engagement_score.between(low, high))

        return stmt.order_by(Lead.score.desc(), Lead.last_activity_at.desc())

    async def board(
        self, *, service_id: uuid.UUID | None = None, per_stage: int = 50
    ) -> list[dict[str, Any]]:
        """Tablero Kanban: etapas con sus leads y totales.

        Se pagina por columna: una etapa con 3.000 prospectos no puede
        arrastrar el tablero entero.
        """
        stages = await self.pipeline.list_stages()
        board = []

        for stage in stages:
            stmt = self.build_list_query(stage_id=stage.id, service_id=service_id)
            count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
            total = (await self.session.execute(count_stmt)).scalar_one() or 0
            leads = (await self.session.execute(stmt.limit(per_stage))).scalars().all()

            value_stmt = select(func.coalesce(func.sum(Lead.estimated_value), 0)).where(
                Lead.stage_id == stage.id
            )
            if service_id is not None:
                value_stmt = value_stmt.where(Lead.service_id == service_id)
            total_value = (await self.session.execute(value_stmt)).scalar_one()

            board.append(
                {"stage": stage, "leads": list(leads), "total": total, "total_value": total_value}
            )

        return board
