"""Módulo 18 — secuencias de seguimiento.

El calendario se calcula **antes** de inscribir a nadie y se le enseña al
usuario: "3 correos a 12 prospectos entre el 28/07 y el 05/08". Nada se envía
solo hasta que confirma. Esa es la diferencia entre una herramienta de
seguimiento y una máquina de spam.

Las horas se calculan en la zona configurada (`America/Bogota` por defecto):
un paso programado "a las 9:00" tiene que caer a las 9:00 del usuario.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ActivityType, ActorType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.mail.guardrails import local_now
from app.models.email import EmailTemplate
from app.models.lead import Lead
from app.models.sequence import (
    FOLLOWUP_CANCELLED,
    FOLLOWUP_PENDING,
    FOLLOWUP_SKIPPED,
    STEP_CONDITIONS,
    FollowUp,
    Sequence,
    SequenceStep,
)
from app.models.settings import AppSettings
from app.repositories.base import BaseRepository
from app.services.activity_svc import ActivityService
from app.services.lead_svc import LeadService

logger = get_logger(__name__)

# Tope de días que se desplaza un envío buscando hueco válido. Si en dos
# semanas no cabe, la configuración está mal y es mejor fallar que arrastrar
# el seguimiento hasta el infinito.
_MAX_SLOT_SHIFT_DAYS = 14


@dataclass(frozen=True, slots=True)
class PlannedStep:
    """Un envío previsto, antes de existir en la base."""

    step_number: int
    template_id: uuid.UUID
    template_name: str
    scheduled_at: datetime
    condition: str


@dataclass(frozen=True, slots=True)
class SchedulePreview:
    """Calendario que se le enseña al usuario antes de inscribir."""

    lead_id: uuid.UUID
    company_name: str
    steps: list[PlannedStep]
    warnings: list[str]


class SequenceRepository(BaseRepository[Sequence]):
    model = Sequence


class FollowUpRepository(BaseRepository[FollowUp]):
    model = FollowUp


def next_valid_slot(
    desired: datetime,
    *,
    settings: AppSettings,
    window_start: time | None = None,
    window_end: time | None = None,
    skip_weekends: bool = True,
) -> datetime:
    """Primer hueco válido a partir de `desired`.

    Empuja hacia adelante hasta caer dentro de la ventana horaria y en día
    hábil. Nunca hacia atrás: adelantar un envío que el usuario vio en el
    calendario sería mentirle.

    Devuelve un instante en UTC, aunque razona en hora local.
    """
    start = window_start or settings.send_window_start
    end = window_end or settings.send_window_end

    local = local_now(settings, desired)
    limit = local + timedelta(days=_MAX_SLOT_SHIFT_DAYS)

    while local <= limit:
        if skip_weekends and local.weekday() >= 5:
            local = _at(local + timedelta(days=1), start)
            continue
        if local.time() < start:
            local = _at(local, start)
            continue
        if local.time() > end:
            local = _at(local + timedelta(days=1), start)
            continue
        return local.astimezone(UTC)

    raise ValidationError(
        "No hay ningún hueco de envío válido en las próximas dos semanas. "
        "Revisa la ventana horaria en Configuración.",
        code="NO_VALID_SEND_SLOT",
    )


def _at(moment: datetime, at: time) -> datetime:
    return moment.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)


class SequenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SequenceRepository(session)
        self.leads = LeadService(session)
        self.activities = ActivityService(session)

    # ------------------------------------------------------------ CRUD

    async def get_or_404(self, sequence_id: uuid.UUID) -> Sequence:
        result = await self.session.execute(
            select(Sequence)
            .where(Sequence.id == sequence_id)
            .options(selectinload(Sequence.steps))
            .execution_options(populate_existing=True)
        )
        sequence = result.scalar_one_or_none()
        if sequence is None:
            raise NotFoundError.for_entity("sequence", sequence_id)
        return sequence

    def build_list_query(self, *, active_only: bool = False) -> Select[tuple[Sequence]]:
        stmt = select(Sequence).options(selectinload(Sequence.steps)).order_by(Sequence.name)
        if active_only:
            stmt = stmt.where(Sequence.is_active.is_(True))
        return stmt

    async def create(self, data: dict) -> Sequence:
        steps_data = data.pop("steps", [])
        if not steps_data:
            raise ValidationError(
                "Una secuencia necesita al menos un paso.", code="SEQUENCE_WITHOUT_STEPS"
            )

        sequence = Sequence(**data)
        try:
            await self.repo.add(sequence)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"Ya existe una secuencia llamada '{data.get('name')}'.",
                code="SEQUENCE_NAME_TAKEN",
            ) from exc

        await self._replace_steps(sequence, steps_data)
        return await self.get_or_404(sequence.id)

    async def update(self, sequence_id: uuid.UUID, data: dict) -> Sequence:
        sequence = await self.get_or_404(sequence_id)
        steps_data = data.pop("steps", None)

        for key, value in data.items():
            setattr(sequence, key, value)

        if steps_data is not None:
            if not steps_data:
                raise ValidationError(
                    "Una secuencia necesita al menos un paso.", code="SEQUENCE_WITHOUT_STEPS"
                )
            # Editar los pasos no toca los seguimientos ya programados: el
            # calendario que el usuario confirmó se respeta.
            for step in list(sequence.steps):
                await self.session.delete(step)
            await self.session.flush()
            await self._replace_steps(sequence, steps_data)

        await self.session.flush()
        return await self.get_or_404(sequence_id)

    async def delete(self, sequence_id: uuid.UUID) -> None:
        sequence = await self.get_or_404(sequence_id)
        # Los seguimientos pendientes se cancelan: dejarlos huérfanos los haría
        # ejecutarse sin secuencia y sin reglas de parada.
        cancelled = await self.session.execute(
            select(FollowUp).where(
                FollowUp.sequence_id == sequence_id, FollowUp.status == FOLLOWUP_PENDING
            )
        )
        for follow_up in cancelled.scalars().all():
            follow_up.status = FOLLOWUP_CANCELLED
            follow_up.skip_reason = "sequence_deleted"

        await self.repo.delete(sequence)

    async def _replace_steps(self, sequence: Sequence, steps_data: list[dict]) -> None:
        for index, raw in enumerate(steps_data, start=1):
            condition = raw.get("condition")
            key = condition.get("if") if isinstance(condition, dict) else condition
            if key and key not in STEP_CONDITIONS:
                raise ValidationError(
                    f"Condición desconocida en el paso {index}: {key}.",
                    code="UNKNOWN_STEP_CONDITION",
                    details={"available": sorted(STEP_CONDITIONS)},
                )

            template = await self.session.get(EmailTemplate, raw["template_id"])
            if template is None:
                raise NotFoundError.for_entity("template", raw["template_id"])

            self.session.add(
                SequenceStep(
                    sequence_id=sequence.id,
                    # `or index`, no `get(..., index)`: la clave llega presente
                    # y a None cuando el cliente no numera los pasos, y el
                    # default de `get` no se aplicaría.
                    step_number=raw.get("step_number") or index,
                    template_id=raw["template_id"],
                    delay_days=raw.get("delay_days", 3),
                    delay_hours=raw.get("delay_hours", 0),
                    condition={"if": key} if key else None,
                    send_window_start=raw.get("send_window_start"),
                    send_window_end=raw.get("send_window_end"),
                    skip_weekends=raw.get("skip_weekends", True),
                )
            )
        await self.session.flush()

    # ------------------------------------------------------------ calendario

    async def preview(
        self,
        sequence: Sequence,
        lead_ids: list[uuid.UUID],
        *,
        start_at: datetime | None = None,
    ) -> list[SchedulePreview]:
        """Calendario previsto, sin escribir nada.

        Es lo que se enseña antes de confirmar. Incluye los avisos por
        prospecto: sin email, ya inscrito, dado de baja.
        """
        settings = await self._settings()
        previews: list[SchedulePreview] = []

        for lead_id in lead_ids:
            lead = await self.leads.get_or_404(lead_id)
            warnings = await self._enrollment_warnings(lead, sequence)

            cursor = start_at or datetime.now(UTC)
            steps: list[PlannedStep] = []
            for step in sequence.steps:
                cursor = cursor + timedelta(days=step.delay_days, hours=step.delay_hours)
                cursor = next_valid_slot(
                    cursor,
                    settings=settings,
                    window_start=step.send_window_start,
                    window_end=step.send_window_end,
                    skip_weekends=step.skip_weekends,
                )
                template = await self.session.get(EmailTemplate, step.template_id)
                steps.append(
                    PlannedStep(
                        step_number=step.step_number,
                        template_id=step.template_id,
                        template_name=template.name if template else "(plantilla borrada)",
                        scheduled_at=cursor,
                        condition=step.condition_key,
                    )
                )

            previews.append(
                SchedulePreview(
                    lead_id=lead.id,
                    company_name=lead.company.name,
                    steps=steps,
                    warnings=warnings,
                )
            )

        return previews

    async def enroll(
        self,
        sequence: Sequence,
        lead_ids: list[uuid.UUID],
        *,
        start_at: datetime | None = None,
    ) -> tuple[list[FollowUp], list[SchedulePreview]]:
        """Inscribe prospectos y programa **solo el primer paso**.

        Los siguientes los programa el worker al ejecutar cada uno. Si se
        programaran los tres de golpe, una respuesta al primero dejaría dos
        correos ya escritos en el calendario que habría que cancelar; así la
        regla de parada actúa antes de que exista la fila.
        """
        if not sequence.is_active:
            raise ValidationError("La secuencia está desactivada.", code="SEQUENCE_INACTIVE")
        if not sequence.steps:
            raise ValidationError("La secuencia no tiene pasos.", code="SEQUENCE_WITHOUT_STEPS")

        settings = await self._settings()
        first = sequence.steps[0]
        created: list[FollowUp] = []
        previews = await self.preview(sequence, lead_ids, start_at=start_at)

        for preview in previews:
            if preview.warnings and any(w.startswith("BLOQUEA") for w in preview.warnings):
                continue

            lead = await self.leads.get_or_404(preview.lead_id)
            when = next_valid_slot(
                (start_at or datetime.now(UTC))
                + timedelta(days=first.delay_days, hours=first.delay_hours),
                settings=settings,
                window_start=first.send_window_start,
                window_end=first.send_window_end,
                skip_weekends=first.skip_weekends,
            )

            follow_up = FollowUp(
                lead_id=lead.id,
                sequence_id=sequence.id,
                sequence_step=first.step_number,
                template_id=first.template_id,
                scheduled_at=when,
                owner_id=lead.owner_id,
            )
            self.session.add(follow_up)

            lead.sequence_id = sequence.id
            lead.sequence_step = 0
            lead.sequence_paused = False
            lead.next_follow_up_at = when

            await self.activities.record(
                ActivityType.FOLLOWUP_SCHEDULED,
                title=f"Inscrito en la secuencia '{sequence.name}'",
                description=f"Primer seguimiento el {when:%d/%m/%Y a las %H:%M} UTC.",
                lead_id=lead.id,
                company_id=lead.company_id,
                actor=ActorType.USER,
                owner_id=lead.owner_id,
            )
            created.append(follow_up)

        await self.session.flush()
        logger.info(
            "sequence_enrolled", sequence=str(sequence.id), leads=len(created), asked=len(lead_ids)
        )
        return created, previews

    async def schedule_next_step(
        self, follow_up: FollowUp, *, after: datetime | None = None
    ) -> FollowUp | None:
        """Programa el paso siguiente al que se acaba de ejecutar."""
        if follow_up.sequence_id is None or follow_up.sequence_step is None:
            return None

        sequence = await self.get_or_404(follow_up.sequence_id)
        following = [s for s in sequence.steps if s.step_number > follow_up.sequence_step]
        if not following or follow_up.sequence_step >= sequence.max_steps:
            return None

        step = following[0]
        settings = await self._settings()
        when = next_valid_slot(
            (after or datetime.now(UTC)) + timedelta(days=step.delay_days, hours=step.delay_hours),
            settings=settings,
            window_start=step.send_window_start,
            window_end=step.send_window_end,
            skip_weekends=step.skip_weekends,
        )

        nxt = FollowUp(
            lead_id=follow_up.lead_id,
            sequence_id=sequence.id,
            sequence_step=step.step_number,
            template_id=step.template_id,
            scheduled_at=when,
            owner_id=follow_up.owner_id,
        )
        self.session.add(nxt)
        await self.session.flush()
        return nxt

    async def _enrollment_warnings(self, lead: Lead, sequence: Sequence) -> list[str]:
        """Avisos por prospecto. Los que empiezan por BLOQUEA impiden inscribir."""
        warnings: list[str] = []

        contact_email = (lead.contact.email if lead.contact else None) or lead.company.email
        if not contact_email:
            warnings.append("BLOQUEA: el prospecto no tiene email")
        if lead.status.value != "OPEN":
            warnings.append(f"BLOQUEA: el prospecto está {lead.status.value}")
        if lead.replied_at is not None and sequence.stop_on_reply:
            warnings.append("BLOQUEA: ya respondió y la secuencia se detiene al responder")

        existing = await self.session.execute(
            select(FollowUp.id).where(
                FollowUp.lead_id == lead.id,
                FollowUp.sequence_id == sequence.id,
                FollowUp.status == FOLLOWUP_PENDING,
            )
        )
        if existing.scalar_one_or_none() is not None:
            warnings.append("BLOQUEA: ya está inscrito en esta secuencia")

        if lead.contact is not None and lead.contact.do_not_contact:
            warnings.append("BLOQUEA: el contacto está marcado como 'no contactar'")

        return warnings

    async def _settings(self) -> AppSettings:
        result = await self.session.execute(select(AppSettings).limit(1))
        settings = result.scalar_one_or_none()
        if settings is None:
            raise ValidationError(
                "Falta la configuración de la aplicación.", code="SETTINGS_MISSING"
            )
        return settings


class FollowUpService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FollowUpRepository(session)
        self.leads = LeadService(session)

    async def get_or_404(self, follow_up_id: uuid.UUID) -> FollowUp:
        follow_up = await self.repo.get(follow_up_id)
        if follow_up is None:
            raise NotFoundError.for_entity("follow_up", follow_up_id)
        return follow_up

    def build_list_query(
        self,
        *,
        status: str | None = None,
        lead_id: uuid.UUID | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
    ) -> Select[tuple[FollowUp]]:
        stmt = select(FollowUp).order_by(FollowUp.scheduled_at)
        if status:
            stmt = stmt.where(FollowUp.status == status.upper())
        if lead_id is not None:
            stmt = stmt.where(FollowUp.lead_id == lead_id)
        if due_before is not None:
            stmt = stmt.where(FollowUp.scheduled_at <= due_before)
        if due_after is not None:
            stmt = stmt.where(FollowUp.scheduled_at >= due_after)
        return stmt

    async def create_manual(self, data: dict) -> FollowUp:
        """Seguimiento suelto, sin secuencia. Es el "recuérdame escribirle el
        martes" de toda la vida."""
        lead = await self.leads.get_or_404(data["lead_id"])
        follow_up = FollowUp(
            lead_id=lead.id,
            template_id=data.get("template_id"),
            scheduled_at=data["scheduled_at"],
            note=data.get("note"),
            is_manual=True,
            owner_id=lead.owner_id,
        )
        await self.repo.add(follow_up)

        if lead.next_follow_up_at is None or follow_up.scheduled_at < lead.next_follow_up_at:
            lead.next_follow_up_at = follow_up.scheduled_at
        await self.session.flush()
        return follow_up

    async def update(self, follow_up_id: uuid.UUID, data: dict) -> FollowUp:
        follow_up = await self.get_or_404(follow_up_id)
        if not follow_up.is_pending:
            raise ValidationError(
                "Solo se puede editar un seguimiento pendiente.",
                code="FOLLOWUP_NOT_PENDING",
                details={"status": follow_up.status},
            )
        for key, value in data.items():
            setattr(follow_up, key, value)
        await self.session.flush()
        return follow_up

    async def skip(self, follow_up_id: uuid.UUID, *, reason: str = "manual") -> FollowUp:
        follow_up = await self.get_or_404(follow_up_id)
        follow_up.status = FOLLOWUP_SKIPPED
        follow_up.skip_reason = reason
        follow_up.executed_at = datetime.now(UTC)
        await self.session.flush()
        return follow_up

    async def cancel(self, follow_up_id: uuid.UUID) -> FollowUp:
        follow_up = await self.get_or_404(follow_up_id)
        follow_up.status = FOLLOWUP_CANCELLED
        follow_up.executed_at = datetime.now(UTC)
        await self.session.flush()
        return follow_up

    async def cancel_pending_for_lead(self, lead_id: uuid.UUID, *, reason: str) -> int:
        """Cancela todo lo pendiente de un prospecto.

        Se llama cuando el prospecto responde, se da de baja o se cierra: a
        partir de ahí, cualquier correo automático es un error.
        """
        result = await self.session.execute(
            select(FollowUp).where(FollowUp.lead_id == lead_id, FollowUp.status == FOLLOWUP_PENDING)
        )
        pending = list(result.scalars().all())
        for follow_up in pending:
            follow_up.status = FOLLOWUP_CANCELLED
            follow_up.skip_reason = reason
            follow_up.executed_at = datetime.now(UTC)

        if pending:
            lead = await self.leads.repo.get(lead_id)
            if lead is not None:
                lead.next_follow_up_at = None
            await self.session.flush()
            logger.info("followups_cancelled", lead=str(lead_id), count=len(pending), reason=reason)

        return len(pending)
