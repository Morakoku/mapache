"""Barreras previas a cada envío (§14 del diseño).

Esto no es un filtro de la interfaz: es una comprobación en el servicio, y
`EmailService.send()` no envía nada sin pasarla. Si alguien añade una ruta de
envío que se la salta, el CRM deja de cumplir la normativa.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, tzinfo
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccountStatus, VerificationStatus
from app.models.contact import Contact
from app.models.email import EmailMessage, SuppressionEntry
from app.models.email_account import EmailAccount
from app.models.settings import AppSettings, warmup_limit


class SkipReason(StrEnum):
    """Motivos de descarte. Se muestran tal cual al usuario, así que hay uno
    por causa en vez de un genérico."""

    AUTOMATIONS_PAUSED = "automations_paused"
    SUPPRESSED = "suppressed"
    DO_NOT_CONTACT = "do_not_contact"
    INVALID_EMAIL = "invalid_email"
    DAILY_LIMIT = "daily_limit"
    HOURLY_LIMIT = "hourly_limit"
    OUTSIDE_WINDOW = "outside_window"
    DUPLICATE = "duplicate"
    ACCOUNT_ERROR = "account_error"
    NO_EMAIL = "no_email"


SKIP_MESSAGES: dict[SkipReason, str] = {
    SkipReason.AUTOMATIONS_PAUSED: "Los envíos están pausados en Configuración.",
    SkipReason.SUPPRESSED: "El destinatario está en la lista de no contactar.",
    SkipReason.DO_NOT_CONTACT: "El contacto está marcado como 'no contactar'.",
    SkipReason.INVALID_EMAIL: "El email es inválido o rebotó anteriormente.",
    SkipReason.DAILY_LIMIT: "Se alcanzó el límite de envíos de hoy.",
    SkipReason.HOURLY_LIMIT: "Se alcanzó el límite de envíos de esta hora.",
    SkipReason.OUTSIDE_WINDOW: "Fuera de la ventana de envío configurada.",
    SkipReason.DUPLICATE: "Ya se envió esta plantilla a este prospecto.",
    SkipReason.ACCOUNT_ERROR: "La cuenta de correo necesita reconectarse.",
    SkipReason.NO_EMAIL: "El prospecto no tiene email.",
}


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    allowed: bool
    reason: SkipReason | None = None

    @property
    def message(self) -> str | None:
        return SKIP_MESSAGES.get(self.reason) if self.reason else None


ALLOWED = GuardrailVerdict(allowed=True)


def _blocked(reason: SkipReason) -> GuardrailVerdict:
    return GuardrailVerdict(allowed=False, reason=reason)


def local_now(settings: AppSettings, now: datetime | None = None) -> datetime:
    """El instante actual en la zona horaria configurada.

    Todo lo que el usuario configura en horas —la ventana de envío, el corte
    del contador diario, el día de la rampa— es hora local. Evaluarlo en UTC
    convertiría una ventana de 08:00-18:00 en Colombia en 03:00-13:00 reales.
    """
    now = now or datetime.now(UTC)
    try:
        # `timezone` puede venir sin valor si la fila se construyó en memoria:
        # el default lo pone Postgres, no el modelo.
        tz: tzinfo = ZoneInfo(settings.timezone) if settings.timezone else UTC
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        tz = UTC
    return now.astimezone(tz)


def effective_daily_limit(settings: AppSettings, today: date | None = None) -> int:
    """Límite del día, aplicando la curva de warm-up si está activa.

    Arrancar en 100 con un dominio nuevo es la vía rápida al spam permanente,
    así que la rampa está activada por defecto.
    """
    if not settings.warmup_enabled or settings.warmup_started_on is None:
        return settings.daily_send_limit

    today = today or local_now(settings).date()
    day = (today - settings.warmup_started_on).days + 1
    return min(warmup_limit(max(day, 1)), settings.daily_send_limit)


def is_within_window(settings: AppSettings, now: datetime) -> bool:
    """¿Estamos dentro de la ventana horaria y en día hábil?

    `now` se interpreta en la zona horaria configurada, venga en la que venga.
    """
    local = local_now(settings, now)
    if settings.skip_weekends and local.weekday() >= 5:
        return False
    current: time = local.time()
    return settings.send_window_start <= current <= settings.send_window_end


class Guardrails:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check(
        self,
        *,
        settings: AppSettings,
        account: EmailAccount,
        to_email: str | None,
        contact: Contact | None,
        lead_id: uuid.UUID | None,
        template_id: uuid.UUID | None,
        now: datetime | None = None,
        manual: bool = False,
    ) -> GuardrailVerdict:
        """Comprueba todas las barreras en orden. La primera que falla decide.

        `manual=True` es una respuesta escrita a mano dentro de una conversación
        abierta. Ahí no aplican el ritmo ni la ventana horaria —el prospecto
        acaba de escribir y esperar a mañana sería absurdo—, pero las barreras
        de cumplimiento (supresión, no contactar, email inválido) siguen
        aplicándose sin excepción.
        """
        now = now or datetime.now(UTC)

        if settings.automations_paused and not manual:
            return _blocked(SkipReason.AUTOMATIONS_PAUSED)

        if not account.is_usable or account.status != AccountStatus.ACTIVE:
            return _blocked(SkipReason.ACCOUNT_ERROR)

        if not to_email:
            return _blocked(SkipReason.NO_EMAIL)

        if contact is not None and contact.do_not_contact:
            return _blocked(SkipReason.DO_NOT_CONTACT)

        if contact is not None and contact.email_verified in {
            VerificationStatus.INVALID,
            VerificationStatus.BOUNCED,
        }:
            return _blocked(SkipReason.INVALID_EMAIL)

        if await self.is_suppressed(to_email, owner_id=account.owner_id):
            return _blocked(SkipReason.SUPPRESSED)

        if manual:
            return ALLOWED

        if not is_within_window(settings, now):
            return _blocked(SkipReason.OUTSIDE_WINDOW)

        if account.sent_today >= effective_daily_limit(settings, local_now(settings, now).date()):
            return _blocked(SkipReason.DAILY_LIMIT)

        if account.sent_this_hour >= settings.hourly_send_limit:
            return _blocked(SkipReason.HOURLY_LIMIT)

        if lead_id and template_id and await self._already_sent(lead_id, template_id):
            return _blocked(SkipReason.DUPLICATE)

        return ALLOWED

    async def is_suppressed(self, email: str, *, owner_id: uuid.UUID | None = None) -> bool:
        """¿Está el email o su dominio en la lista de no contactar?

        Se comprueba también el dominio: bloquear `@competencia.com` entero es
        una petición razonable y frecuente.
        """
        domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        stmt = select(SuppressionEntry.id).where(
            or_(
                func.lower(SuppressionEntry.email) == email.lower(),
                func.lower(SuppressionEntry.domain) == domain,
            )
        )
        if owner_id is not None:
            stmt = stmt.where(SuppressionEntry.owner_id == owner_id)
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    async def _already_sent(self, lead_id: uuid.UUID, template_id: uuid.UUID) -> bool:
        """Evita mandar dos veces la misma plantilla al mismo prospecto."""
        result = await self.session.execute(
            select(EmailMessage.id)
            .where(
                EmailMessage.lead_id == lead_id,
                EmailMessage.template_id == template_id,
                EmailMessage.status.notin_(["FAILED", "CANCELLED"]),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    def reset_counters_if_needed(
        account: EmailAccount, now: datetime, *, settings: AppSettings | None = None
    ) -> None:
        """Pone a cero los contadores al cambiar de hora o de día.

        Se hace al leer, no con un cron: evita depender de un scheduler para
        algo que solo importa en el momento de enviar. El corte diario es a
        medianoche local, no UTC: si no, el usuario en Colombia vería
        reiniciarse su cuota a las siete de la tarde.
        """
        if settings is not None:
            now = local_now(settings, now)

        last = account.counters_reset_at
        if last is not None and last.tzinfo is not None and now.tzinfo is not None:
            last = last.astimezone(now.tzinfo)

        if last is None:
            account.sent_today = 0
            account.sent_this_hour = 0
            account.counters_reset_at = now
            return

        if last.date() != now.date():
            account.sent_today = 0
            account.sent_this_hour = 0
        elif last.hour != now.hour:
            account.sent_this_hour = 0

        account.counters_reset_at = now
