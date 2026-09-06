"""Schemas del Módulo 20 — métricas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from app.core.enums import StageType
from app.schemas.common import APIModel


class RateOut(APIModel):
    """Una tasa con su fracción a la vista.

    El numerador y el denominador se devuelven siempre: "45% de apertura" sobre
    11 correos no es una métrica, y sin la fracción nadie puede saberlo.
    """

    value: float
    numerator: int
    denominator: int
    is_alarm: bool = False


class EmailMetricsOut(APIModel):
    sent: int
    delivered: int
    opened: int
    clicked: int
    replied: int
    bounced: int
    unsubscribed: int

    open_rate: RateOut
    click_rate: RateOut
    click_to_open_rate: RateOut
    reply_rate: RateOut
    bounce_rate: RateOut
    unsubscribe_rate: RateOut

    # Avisos en lenguaje llano: rebotes altos, bajas altas, muestra pequeña.
    warnings: list[str]


class PeriodOut(APIModel):
    start: date
    end: date


class OverviewOut(APIModel):
    period: PeriodOut

    companies_found: int
    companies_with_email: int
    leads: int
    leads_qualified: int
    leads_open: int
    leads_won: int
    leads_lost: int
    won_value: float
    conversations: int
    conversations_pending: int

    emails: EmailMetricsOut


class FunnelStepOut(APIModel):
    key: str
    label: str
    count: int
    # Contra qué paso se compara. No siempre es el de arriba: una respuesta
    # sale de los entregados, no de los clicks.
    baseline_key: str | None
    conversion_from_previous: float | None
    conversion_from_start: float | None


class FunnelOut(APIModel):
    period: PeriodOut
    steps: list[FunnelStepOut]
    # Lectura del embudo en una frase, para no dejarle al usuario la tarea de
    # comparar catorce porcentajes a ojo.
    bottleneck: str | None


class TemplatePerformanceOut(APIModel):
    template_id: uuid.UUID
    name: str
    sent: int
    open_rate: RateOut
    click_rate: RateOut
    reply_rate: RateOut
    # Con menos de 30 envíos, comparar plantillas es comparar suerte.
    is_significant: bool


class ServicePerformanceOut(APIModel):
    service_id: uuid.UUID
    name: str
    leads: int
    reply_rate: RateOut
    win_rate: RateOut
    won: int
    won_value: float


class CityPerformanceOut(APIModel):
    city: str
    companies: int
    leads: int
    reply_rate: RateOut
    won: int


class VelocityOut(APIModel):
    stage_type: StageType
    transitions: int
    avg_days: float
    # La mediana al lado de la media: un prospecto olvidado seis meses arrastra
    # la media y no dice nada del resto.
    median_days: float


class TimeseriesPointOut(APIModel):
    bucket: datetime
    value: int


class LeadsTimeseriesPointOut(APIModel):
    bucket: datetime
    created: int
    won: int


class AttentionOut(APIModel):
    """Lo que hay que hacer hoy. No es una métrica, es una lista de tareas."""

    unanswered_conversations: int
    overdue_followups: int
    hot_leads_without_reply: int
    total: int
