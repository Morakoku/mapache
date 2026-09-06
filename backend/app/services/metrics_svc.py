"""Módulo 20 — métricas y embudo.

Todo se calcula agregando en Postgres, no en Python: con 3.000 prospectos y
20.000 correos, traerse las filas para contarlas en un bucle es la diferencia
entre 40 ms y 8 segundos.

Dos decisiones que cambian los números y conviene tener presentes:

  - **Las aperturas de bots no cuentan.** Apple MPP y el proxy de Gmail cargan
    el pixel de todos los correos; contarlas daría un 70% de apertura falso.
    Se usa `open_count`, que ya excluye lo marcado como bot (§11.3).
  - **Las auto-respuestas no son respuestas.** Un "estoy de vacaciones" no es
    interés. Por eso la tasa de respuesta sale de `replied_at`, que el
    `InboxService` solo pone ante una respuesta real.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Direction, LeadStatus, StageType
from app.models.company import Company
from app.models.contact import Contact
from app.models.email import Conversation, EmailMessage, EmailTemplate, SuppressionEntry
from app.models.lead import Lead, LeadStageHistory
from app.models.service import Service

# Umbrales de alarma (§13.2). Por encima de esto no es una métrica más: es un
# aviso de que el dominio se está quemando.
BOUNCE_RATE_ALARM = 5.0
UNSUBSCRIBE_RATE_ALARM = 0.5

# Pasos del embudo, en orden. El primero sale de `companies`; el resto, de los
# correos y del historial de etapas.
FUNNEL_STAGES: tuple[StageType, ...] = (
    StageType.REPLIED,
    StageType.CONVERSATION,
    StageType.INTERESTED,
    StageType.MEETING,
    StageType.OPPORTUNITY,
    StageType.PROPOSAL,
    StageType.WON,
)


@dataclass(frozen=True, slots=True)
class Period:
    """Rango de fechas del informe. Por defecto, los últimos 30 días."""

    start: datetime
    end: datetime

    @classmethod
    def build(cls, start: date | None, end: date | None, *, days: int = 30) -> Period:
        end_dt = datetime.combine(end or datetime.now(UTC).date(), datetime.max.time(), tzinfo=UTC)
        start_dt = datetime.combine(
            start or (end_dt.date() - timedelta(days=days - 1)), datetime.min.time(), tzinfo=UTC
        )
        return cls(start=start_dt, end=end_dt)


@dataclass(frozen=True, slots=True)
class FunnelStep:
    key: str
    label: str
    count: int
    # Contra qué paso se compara. No siempre es el de arriba: una respuesta
    # sale de los entregados, no de los clicks.
    baseline_key: str | None = None
    # Conversión respecto a ese paso: la columna que señala el cuello de
    # botella (§13.3).
    conversion_from_previous: float | None = None
    conversion_from_start: float | None = None


@dataclass(frozen=True, slots=True)
class Rate:
    """Una tasa con su numerador y denominador a la vista.

    Se devuelven los dos porque "45% de apertura" sobre 11 correos no es una
    métrica, es ruido — y sin el denominador nadie puede saberlo.
    """

    value: float
    numerator: int
    denominator: int
    is_alarm: bool = False

    @classmethod
    def of(cls, numerator: int, denominator: int, *, alarm_above: float | None = None) -> Rate:
        value = round(numerator / denominator * 100, 2) if denominator else 0.0
        return cls(
            value=value,
            numerator=numerator,
            denominator=denominator,
            is_alarm=alarm_above is not None and value > alarm_above,
        )


@dataclass(frozen=True, slots=True)
class EmailMetrics:
    sent: int
    delivered: int
    opened: int
    clicked: int
    replied: int
    bounced: int
    unsubscribed: int
    open_rate: Rate
    click_rate: Rate
    click_to_open_rate: Rate
    reply_rate: Rate
    bounce_rate: Rate
    unsubscribe_rate: Rate
    warnings: list[str] = field(default_factory=list)


class MetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------ correo

    def _email_scope(
        self, period: Period, *, service_id: uuid.UUID | None
    ) -> Select[tuple[Any, ...]]:
        """Base común de las métricas de correo: lo saliente del periodo."""
        stmt = select(EmailMessage).where(
            EmailMessage.direction == Direction.OUTBOUND,
            EmailMessage.sent_at.is_not(None),
            EmailMessage.sent_at.between(period.start, period.end),
        )
        if service_id is not None:
            stmt = stmt.join(Lead, Lead.id == EmailMessage.lead_id).where(
                Lead.service_id == service_id
            )
        return stmt

    async def email_metrics(
        self, period: Period, *, service_id: uuid.UUID | None = None
    ) -> EmailMetrics:
        """Tasas de correo del periodo (§13.2)."""
        scope = self._email_scope(period, service_id=service_id).subquery()

        row = (
            await self.session.execute(
                select(
                    func.count().label("sent"),
                    # Entregado = no rebotó. SMTP no confirma entrega, así que
                    # afirmar más que esto sería inventar.
                    func.count().filter(scope.c.bounced_at.is_(None)).label("delivered"),
                    func.count().filter(scope.c.open_count > 0).label("opened"),
                    func.count().filter(scope.c.click_count > 0).label("clicked"),
                    func.count().filter(scope.c.replied_at.is_not(None)).label("replied"),
                    func.count().filter(scope.c.bounced_at.is_not(None)).label("bounced"),
                ).select_from(scope)
            )
        ).one()

        unsubscribed = (
            await self.session.execute(
                select(func.count())
                .select_from(SuppressionEntry)
                .where(
                    SuppressionEntry.reason == "unsubscribed",
                    SuppressionEntry.created_at.between(period.start, period.end),
                )
            )
        ).scalar_one() or 0

        bounce = Rate.of(row.bounced, row.sent, alarm_above=BOUNCE_RATE_ALARM)
        unsubscribe = Rate.of(unsubscribed, row.delivered, alarm_above=UNSUBSCRIBE_RATE_ALARM)

        warnings: list[str] = []
        if bounce.is_alarm:
            warnings.append(
                f"Rebotes al {bounce.value}%: por encima del {BOUNCE_RATE_ALARM}% los "
                "proveedores empiezan a penalizar el dominio. Revisa la calidad de las "
                "direcciones antes de seguir enviando."
            )
        if unsubscribe.is_alarm:
            warnings.append(
                f"Bajas al {unsubscribe.value}%: por encima del {UNSUBSCRIBE_RATE_ALARM}% "
                "el mensaje no está encajando con a quién se envía."
            )
        if row.sent and row.sent < 30:
            warnings.append(
                f"Solo {row.sent} correos en el periodo: los porcentajes con tan pocos "
                "datos no significan gran cosa."
            )

        return EmailMetrics(
            sent=row.sent,
            delivered=row.delivered,
            opened=row.opened,
            clicked=row.clicked,
            replied=row.replied,
            bounced=row.bounced,
            unsubscribed=unsubscribed,
            open_rate=Rate.of(row.opened, row.delivered),
            click_rate=Rate.of(row.clicked, row.delivered),
            click_to_open_rate=Rate.of(row.clicked, row.opened),
            reply_rate=Rate.of(row.replied, row.delivered),
            bounce_rate=bounce,
            unsubscribe_rate=unsubscribe,
            warnings=warnings,
        )

    # ------------------------------------------------------------ embudo

    async def _companies_with_email(self, period: Period) -> int:
        """Empresas a las que se les puede escribir.

        Incluye el email del contacto, no solo el genérico de la empresa: el
        enriquecimiento suele guardarlo ahí, y contar únicamente
        `companies.email` daba cero con datos reales. Vive en un solo sitio
        porque el panel y el embudo enseñan este número a la vez: con dos
        consultas distintas acabaron diciendo 0 y 12 el mismo día.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Company)
            .where(
                Company.first_extracted_at.between(period.start, period.end),
                or_(
                    Company.email.is_not(None),
                    select(Contact.id)
                    .where(Contact.company_id == Company.id, Contact.email.is_not(None))
                    .exists(),
                ),
            )
        )
        return result.scalar_one() or 0

    async def _companies_with_whatsapp(self, period: Period) -> int:
        """Empresas que se pueden atacar por WhatsApp (columna `whatsapp`).

        El número puede venir del enlace `wa.me` de la ficha de Google o del
        sitio propio; se guarda en E.164 y aquí solo se cuenta.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Company)
            .where(
                Company.first_extracted_at.between(period.start, period.end),
                Company.whatsapp.is_not(None),
            )
        )
        return result.scalar_one() or 0

    async def funnel(
        self, period: Period, *, service_id: uuid.UUID | None = None
    ) -> list[FunnelStep]:
        """Embudo completo, de empresa encontrada a cliente (§13.3).

        Los primeros pasos salen de las tablas de prospección y correo; del
        primer contacto en adelante, de `lead_stage_history` — que registra por
        dónde pasó cada prospecto aunque hoy esté en otra columna. Mirar solo
        la etapa actual perdería a todo el que ya avanzó.
        """
        companies = (
            await self.session.execute(
                select(func.count())
                .select_from(Company)
                .where(Company.first_extracted_at.between(period.start, period.end))
            )
        ).scalar_one() or 0

        with_email = await self._companies_with_email(period)

        emails = await self.email_metrics(period, service_id=service_id)

        # Un prospecto cuenta una sola vez por etapa aunque haya pasado dos
        # veces: son personas, no eventos.
        history = (
            select(
                LeadStageHistory.to_stage_type.label("stage_type"),
                func.count(func.distinct(LeadStageHistory.lead_id)).label("leads"),
            )
            .where(LeadStageHistory.entered_at.between(period.start, period.end))
            .group_by(LeadStageHistory.to_stage_type)
        )
        if service_id is not None:
            history = history.join(Lead, Lead.id == LeadStageHistory.lead_id).where(
                Lead.service_id == service_id
            )

        reached = {row.stage_type: row.leads for row in (await self.session.execute(history)).all()}

        # Cada paso declara contra qué se compara, porque el embudo no es una
        # sola cadena: las respuestas salen de los entregados, no de los
        # clicks. Encadenarlo por posición daba un "600% de conversión" en
        # cuanto alguien respondía sin haber pulsado un enlace.
        raw: list[tuple[str, str, int, str | None]] = [
            ("companies", "Empresas encontradas", companies, None),
            ("with_email", "Con email", with_email, "companies"),
            ("sent", "Correos enviados", emails.sent, "with_email"),
            ("delivered", "Entregados", emails.delivered, "sent"),
            ("opened", "Aperturas", emails.opened, "delivered"),
            ("clicked", "Clicks", emails.clicked, "opened"),
        ]
        labels = {
            StageType.REPLIED: "Respuestas",
            StageType.CONVERSATION: "Conversaciones",
            StageType.INTERESTED: "Interesados",
            StageType.MEETING: "Reuniones",
            StageType.OPPORTUNITY: "Oportunidades",
            StageType.PROPOSAL: "Propuestas",
            StageType.WON: "Clientes ganados",
        }
        # Una respuesta no requiere haber pulsado un enlace: su base son los
        # entregados. De ahí en adelante sí es una cadena.
        baselines: dict[StageType, str] = {
            StageType.REPLIED: "delivered",
            StageType.CONVERSATION: "replied",
            StageType.INTERESTED: "conversation",
            StageType.MEETING: "interested",
            StageType.OPPORTUNITY: "meeting",
            StageType.PROPOSAL: "opportunity",
            StageType.WON: "opportunity",
        }
        raw += [
            (
                stage.value.lower(),
                labels[stage],
                reached.get(stage, 0),
                baselines[stage],
            )
            for stage in FUNNEL_STAGES
        ]

        counts = {key: count for key, _, count, _ in raw}
        start_count = raw[0][2] if raw else 0

        steps: list[FunnelStep] = []
        for key, label, count, baseline in raw:
            base = counts.get(baseline or "", 0)
            steps.append(
                FunnelStep(
                    key=key,
                    label=label,
                    count=count,
                    baseline_key=baseline,
                    # Sin base no hay porcentaje que dar: mejor un hueco que un
                    # número inventado.
                    conversion_from_previous=(round(count / base * 100, 2) if base else None),
                    conversion_from_start=(
                        round(count / start_count * 100, 2) if start_count else None
                    ),
                )
            )
        return steps

    def bottleneck(self, steps: list[FunnelStep]) -> str | None:
        """El paso con peor conversión, en una frase.

        Es la lectura que hace útil el embudo: "de entregados a aperturas cae
        al 10%" señala el asunto o la entregabilidad, no el producto.
        """
        worst = min(
            (s for s in steps if s.conversion_from_previous is not None),
            key=lambda s: s.conversion_from_previous or 0.0,
            default=None,
        )
        if worst is None or worst.conversion_from_previous is None:
            return None

        by_key = {s.key: s for s in steps}
        base = by_key.get(worst.baseline_key or "")
        base_label = base.label if base else "el inicio"
        return (
            f"El mayor salto está entre «{base_label}» y «{worst.label}»: "
            f"pasa el {worst.conversion_from_previous}%."
        )

    # ------------------------------------------------------------ resumen

    async def overview(
        self, period: Period, *, service_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        """Contadores del panel principal (§13.1)."""
        leads = select(
            func.count().label("total"),
            func.count().filter(Lead.status == LeadStatus.WON).label("won"),
            func.count().filter(Lead.status == LeadStatus.LOST).label("lost"),
            func.count().filter(Lead.status == LeadStatus.OPEN).label("open"),
            func.coalesce(
                func.sum(Lead.estimated_value).filter(Lead.status == LeadStatus.WON), 0
            ).label("won_value"),
            func.count().filter(Lead.score >= 60).label("qualified"),
        ).where(Lead.created_at.between(period.start, period.end))
        if service_id is not None:
            leads = leads.where(Lead.service_id == service_id)
        lead_row = (await self.session.execute(leads)).one()

        companies_total = (
            await self.session.execute(
                select(func.count())
                .select_from(Company)
                .where(Company.first_extracted_at.between(period.start, period.end))
            )
        ).scalar_one() or 0
        # Misma regla que el embudo: si el panel dice 0 y el embudo 12, el
        # usuario deja de creerse los dos.
        companies_with_email = await self._companies_with_email(period)
        companies_with_whatsapp = await self._companies_with_whatsapp(period)

        conversations = (
            await self.session.execute(
                select(
                    func.count().label("total"),
                    func.count().filter(Conversation.status == "NEEDS_REPLY").label("pending"),
                )
                .select_from(Conversation)
                .where(Conversation.created_at.between(period.start, period.end))
            )
        ).one()

        emails = await self.email_metrics(period, service_id=service_id)

        return {
            "companies_found": companies_total,
            "companies_with_email": companies_with_email,
            "companies_with_whatsapp": companies_with_whatsapp,
            "leads": lead_row.total,
            "leads_qualified": lead_row.qualified,
            "leads_open": lead_row.open,
            "leads_won": lead_row.won,
            "leads_lost": lead_row.lost,
            "won_value": float(lead_row.won_value or 0),
            "conversations": conversations.total,
            "conversations_pending": conversations.pending,
            "emails": emails,
        }

    # ------------------------------------------------------------ cortes

    async def by_template(self, period: Period) -> list[dict[str, Any]]:
        """Rendimiento comparado de plantillas: el test A/B implícito.

        Se calcula sobre los envíos reales, no sobre los contadores
        materializados de `email_templates`: esos son de todo el histórico y
        aquí interesa el periodo.
        """
        scope = self._email_scope(period, service_id=None).subquery()
        rows = (
            await self.session.execute(
                select(
                    EmailTemplate.id,
                    EmailTemplate.name,
                    func.count().label("sent"),
                    func.count().filter(scope.c.bounced_at.is_(None)).label("delivered"),
                    func.count().filter(scope.c.open_count > 0).label("opened"),
                    func.count().filter(scope.c.click_count > 0).label("clicked"),
                    func.count().filter(scope.c.replied_at.is_not(None)).label("replied"),
                )
                .select_from(scope)
                .join(EmailTemplate, EmailTemplate.id == scope.c.template_id)
                .group_by(EmailTemplate.id, EmailTemplate.name)
                .order_by(func.count().desc())
            )
        ).all()

        return [
            {
                "template_id": row.id,
                "name": row.name,
                "sent": row.sent,
                "open_rate": Rate.of(row.opened, row.delivered),
                "click_rate": Rate.of(row.clicked, row.delivered),
                "reply_rate": Rate.of(row.replied, row.delivered),
                # Con menos de 30 envíos la comparación entre plantillas no
                # distingue una mejor de una con suerte.
                "is_significant": row.sent >= 30,
            }
            for row in rows
        ]

    async def by_service(self, period: Period) -> list[dict[str, Any]]:
        """Qué servicio se vende mejor."""
        rows = (
            await self.session.execute(
                select(
                    Service.id,
                    Service.name,
                    func.count(Lead.id).label("leads"),
                    func.count(Lead.id).filter(Lead.replied_at.is_not(None)).label("replied"),
                    func.count(Lead.id).filter(Lead.status == LeadStatus.WON).label("won"),
                    func.coalesce(
                        func.sum(Lead.estimated_value).filter(Lead.status == LeadStatus.WON), 0
                    ).label("won_value"),
                )
                .select_from(Service)
                .join(
                    Lead,
                    and_(
                        Lead.service_id == Service.id,
                        Lead.created_at.between(period.start, period.end),
                    ),
                    isouter=True,
                )
                .group_by(Service.id, Service.name)
                .order_by(func.count(Lead.id).desc())
            )
        ).all()

        return [
            {
                "service_id": row.id,
                "name": row.name,
                "leads": row.leads,
                "reply_rate": Rate.of(row.replied, row.leads),
                "win_rate": Rate.of(row.won, row.leads),
                "won": row.won,
                "won_value": float(row.won_value or 0),
            }
            for row in rows
        ]

    async def by_city(self, period: Period, *, limit: int = 20) -> list[dict[str, Any]]:
        """Dónde está el mercado real, que no siempre es dónde se buscó."""
        rows = (
            await self.session.execute(
                select(
                    Company.city,
                    func.count(func.distinct(Company.id)).label("companies"),
                    func.count(func.distinct(Lead.id)).label("leads"),
                    func.count(func.distinct(Lead.id))
                    .filter(Lead.replied_at.is_not(None))
                    .label("replied"),
                    func.count(func.distinct(Lead.id))
                    .filter(Lead.status == LeadStatus.WON)
                    .label("won"),
                )
                .select_from(Company)
                .join(Lead, Lead.company_id == Company.id, isouter=True)
                .where(
                    Company.city.is_not(None),
                    Company.first_extracted_at.between(period.start, period.end),
                )
                .group_by(Company.city)
                .order_by(func.count(func.distinct(Company.id)).desc())
                .limit(limit)
            )
        ).all()

        return [
            {
                "city": row.city,
                "companies": row.companies,
                "leads": row.leads,
                "reply_rate": Rate.of(row.replied, row.leads),
                "won": row.won,
            }
            for row in rows
        ]

    async def velocity(self, period: Period) -> list[dict[str, Any]]:
        """Días medios en cada etapa, desde `lead_stage_history`.

        Responde "¿dónde se atascan?" con datos, no con intuición. La mediana
        va al lado de la media: un prospecto olvidado seis meses arrastra la
        media y no dice nada del resto.
        """
        rows = (
            await self.session.execute(
                select(
                    LeadStageHistory.from_stage_type.label("stage_type"),
                    func.count().label("transitions"),
                    func.avg(LeadStageHistory.duration_seconds).label("avg_seconds"),
                    func.percentile_cont(0.5)
                    .within_group(LeadStageHistory.duration_seconds)
                    .label("median_seconds"),
                )
                .where(
                    LeadStageHistory.from_stage_type.is_not(None),
                    LeadStageHistory.duration_seconds.is_not(None),
                    LeadStageHistory.entered_at.between(period.start, period.end),
                )
                .group_by(LeadStageHistory.from_stage_type)
            )
        ).all()

        return [
            {
                "stage_type": row.stage_type,
                "transitions": row.transitions,
                "avg_days": round(float(row.avg_seconds or 0) / 86400, 2),
                "median_days": round(float(row.median_seconds or 0) / 86400, 2),
            }
            for row in rows
        ]

    # ------------------------------------------------------------ series

    async def timeseries(
        self,
        period: Period,
        *,
        metric: str = "sent",
        granularity: str = "day",
    ) -> list[dict[str, Any]]:
        """Serie temporal de una métrica.

        `date_trunc` agrupa en Postgres: traerse fila a fila para agrupar en
        Python sería el mismo resultado y diez veces más lento.
        """
        bucket = func.date_trunc(granularity, EmailMessage.sent_at).label("bucket")

        metric_column = {
            "sent": func.count(),
            "delivered": func.count().filter(EmailMessage.bounced_at.is_(None)),
            "opened": func.count().filter(EmailMessage.open_count > 0),
            "clicked": func.count().filter(EmailMessage.click_count > 0),
            "replied": func.count().filter(EmailMessage.replied_at.is_not(None)),
            "bounced": func.count().filter(EmailMessage.bounced_at.is_not(None)),
        }[metric]

        rows = (
            await self.session.execute(
                select(bucket, metric_column.label("value"))
                .where(
                    EmailMessage.direction == Direction.OUTBOUND,
                    EmailMessage.sent_at.between(period.start, period.end),
                )
                .group_by(bucket)
                .order_by(bucket)
            )
        ).all()

        return [{"bucket": row.bucket, "value": row.value} for row in rows]

    async def leads_timeseries(
        self, period: Period, *, granularity: str = "day"
    ) -> list[dict[str, Any]]:
        """Prospectos creados y ganados por periodo."""
        bucket = func.date_trunc(granularity, Lead.created_at).label("bucket")
        rows = (
            await self.session.execute(
                select(
                    bucket,
                    func.count().label("created"),
                    func.count().filter(Lead.status == LeadStatus.WON).label("won"),
                )
                .where(Lead.created_at.between(period.start, period.end))
                .group_by(bucket)
                .order_by(bucket)
            )
        ).all()
        return [{"bucket": row.bucket, "created": row.created, "won": row.won} for row in rows]

    # ------------------------------------------------------------ atención

    async def needs_attention(self, *, limit: int = 20) -> dict[str, Any]:
        """Lo que la pantalla de inicio pone arriba del todo (§13 de la UI).

        No es una métrica: es la lista de lo que hay que hacer hoy.
        """
        now = datetime.now(UTC)

        # Contadores en una única sentencia (un round-trip a Postgres) en vez
        # de tres consultas seriales. Los subselects escalares no se convierten
        # en JOINs; cada uno escanea su propia tabla con sus índices.
        unanswered = (
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.status == "NEEDS_REPLY")
            .scalar_subquery()
        )
        overdue = (
            select(func.count())
            .select_from(Lead)
            .where(
                Lead.next_follow_up_at.is_not(None),
                Lead.next_follow_up_at < now,
                Lead.status == LeadStatus.OPEN,
            )
            .scalar_subquery()
        )
        hot = (
            select(func.count())
            .select_from(Lead)
            .where(
                Lead.engagement_score >= 40,
                Lead.replied_at.is_(None),
                Lead.status == LeadStatus.OPEN,
            )
            .scalar_subquery()
        )

        row = (
            await self.session.execute(
                select(
                    unanswered.label("unanswered"),
                    overdue.label("overdue"),
                    hot.label("hot"),
                )
            )
        ).one()

        unanswered_count = row.unanswered or 0
        overdue_count = row.overdue or 0
        hot_count = row.hot or 0

        return {
            "unanswered_conversations": unanswered_count,
            "overdue_followups": overdue_count,
            "hot_leads_without_reply": hot_count,
            "total": unanswered_count + overdue_count + hot_count,
        }
