"""Módulo 20 — métricas y embudo.

Todos los endpoints aceptan `from` y `to`; sin ellos, los últimos 30 días.
Las tasas se devuelven con numerador y denominador para que la interfaz pueda
decir "45% (9 de 20)" en vez de un porcentaje suelto que invita a leer de más.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.metrics import (
    AttentionOut,
    CityPerformanceOut,
    EmailMetricsOut,
    FunnelOut,
    FunnelStepOut,
    LeadsTimeseriesPointOut,
    OverviewOut,
    PeriodOut,
    RateOut,
    ServicePerformanceOut,
    TemplatePerformanceOut,
    TimeseriesPointOut,
    VelocityOut,
)
from app.services.metrics_svc import EmailMetrics, MetricsService, Period, Rate

router = APIRouter()


def _period(start: date | None, end: date | None) -> Period:
    return Period.build(start, end)


def _period_out(period: Period) -> PeriodOut:
    return PeriodOut(start=period.start.date(), end=period.end.date())


def _rate(rate: Rate) -> RateOut:
    return RateOut(
        value=rate.value,
        numerator=rate.numerator,
        denominator=rate.denominator,
        is_alarm=rate.is_alarm,
    )


def _emails(metrics: EmailMetrics) -> EmailMetricsOut:
    return EmailMetricsOut(
        sent=metrics.sent,
        delivered=metrics.delivered,
        opened=metrics.opened,
        clicked=metrics.clicked,
        replied=metrics.replied,
        bounced=metrics.bounced,
        unsubscribed=metrics.unsubscribed,
        open_rate=_rate(metrics.open_rate),
        click_rate=_rate(metrics.click_rate),
        click_to_open_rate=_rate(metrics.click_to_open_rate),
        reply_rate=_rate(metrics.reply_rate),
        bounce_rate=_rate(metrics.bounce_rate),
        unsubscribe_rate=_rate(metrics.unsubscribe_rate),
        warnings=metrics.warnings,
    )


@router.get("/overview", response_model=OverviewOut)
async def overview(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    service_id: uuid.UUID | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> OverviewOut:
    """Contadores del panel principal (§13.1)."""
    period = _period(from_, to)
    data = await MetricsService(db).overview(period, service_id=service_id)
    return OverviewOut(
        period=_period_out(period),
        emails=_emails(data.pop("emails")),
        **data,
    )


@router.get("/funnel", response_model=FunnelOut)
async def funnel(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    service_id: uuid.UUID | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> FunnelOut:
    """Embudo de empresa encontrada a cliente, con conversión paso a paso.

    Se calcula sobre `lead_stage_history`, no sobre la etapa actual: quien ya
    pasó por "Interesado" y hoy está en "Ganado" cuenta en ambos. Mirar solo la
    columna de hoy perdería a todos los que avanzaron.
    """
    period = _period(from_, to)
    service = MetricsService(db)
    steps = await service.funnel(period, service_id=service_id)
    return FunnelOut(
        period=_period_out(period),
        # `asdict`, no `vars`: los dataclass con `slots` no tienen `__dict__`.
        steps=[FunnelStepOut(**asdict(s)) for s in steps],
        bottleneck=service.bottleneck(steps),
    )


@router.get("/email", response_model=EmailMetricsOut)
async def email_metrics(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    service_id: uuid.UUID | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> EmailMetricsOut:
    """Tasas de correo (§13.2).

    Las aperturas de bots no cuentan y las auto-respuestas tampoco: los
    números salen más bajos que en otras herramientas y son los reales.
    """
    period = _period(from_, to)
    return _emails(await MetricsService(db).email_metrics(period, service_id=service_id))


@router.get("/by-template", response_model=list[TemplatePerformanceOut])
async def by_template(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> list[TemplatePerformanceOut]:
    """Rendimiento comparado de plantillas: el test A/B implícito."""
    rows = await MetricsService(db).by_template(_period(from_, to))
    return [
        TemplatePerformanceOut(
            template_id=row["template_id"],
            name=row["name"],
            sent=row["sent"],
            open_rate=_rate(row["open_rate"]),
            click_rate=_rate(row["click_rate"]),
            reply_rate=_rate(row["reply_rate"]),
            is_significant=row["is_significant"],
        )
        for row in rows
    ]


@router.get("/by-service", response_model=list[ServicePerformanceOut])
async def by_service(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> list[ServicePerformanceOut]:
    """Qué servicio se vende mejor."""
    rows = await MetricsService(db).by_service(_period(from_, to))
    return [
        ServicePerformanceOut(
            service_id=row["service_id"],
            name=row["name"],
            leads=row["leads"],
            reply_rate=_rate(row["reply_rate"]),
            win_rate=_rate(row["win_rate"]),
            won=row["won"],
            won_value=row["won_value"],
        )
        for row in rows
    ]


@router.get("/by-city", response_model=list[CityPerformanceOut])
async def by_city(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession | None = Depends(get_db),
) -> list[CityPerformanceOut]:
    """Dónde está el mercado real, que no siempre es dónde se buscó."""
    rows = await MetricsService(db).by_city(_period(from_, to), limit=limit)
    return [
        CityPerformanceOut(
            city=row["city"],
            companies=row["companies"],
            leads=row["leads"],
            reply_rate=_rate(row["reply_rate"]),
            won=row["won"],
        )
        for row in rows
    ]


@router.get("/velocity", response_model=list[VelocityOut])
async def velocity(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> list[VelocityOut]:
    """Días medios en cada etapa. Responde "¿dónde se atascan?" con datos."""
    rows = await MetricsService(db).velocity(_period(from_, to))
    return [VelocityOut(**row) for row in rows]


@router.get("/timeseries", response_model=list[TimeseriesPointOut])
async def timeseries(
    metric: Literal["sent", "delivered", "opened", "clicked", "replied", "bounced"] = "sent",
    granularity: Literal["day", "week", "month"] = "day",
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> list[TimeseriesPointOut]:
    rows: list[dict[str, Any]] = await MetricsService(db).timeseries(
        _period(from_, to), metric=metric, granularity=granularity
    )
    return [TimeseriesPointOut(**row) for row in rows]


@router.get("/leads-timeseries", response_model=list[LeadsTimeseriesPointOut])
async def leads_timeseries(
    granularity: Literal["day", "week", "month"] = "day",
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    db: AsyncSession | None = Depends(get_db),
) -> list[LeadsTimeseriesPointOut]:
    rows = await MetricsService(db).leads_timeseries(_period(from_, to), granularity=granularity)
    return [LeadsTimeseriesPointOut(**row) for row in rows]


@router.get("/attention", response_model=AttentionOut)
async def attention(db: AsyncSession | None = Depends(get_db)) -> AttentionOut:
    """Lo que requiere acción hoy: sin contestar, vencido y caliente sin tocar."""
    return AttentionOut(**await MetricsService(db).needs_attention())
