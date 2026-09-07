"""Llamadas: qué decir, a quién, y qué pasó después.

El CRM no marca el teléfono. Lo que hace es preparar la llamada —el guion de
la situación correcta, con los datos del prospecto ya sustituidos— y guardar
el resultado para que la siguiente persona que abra la ficha sepa qué se habló.

La decisión de fondo: **el guion se elige por la situación, no por la etapa**.
Un prospecto en "Contacto encontrado" puede necesitar una llamada en frío o
una de seguimiento según si ya se le escribió; mirar solo la columna del
Kanban se equivoca la mitad de las veces.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActivityType, ActorType, CallOutcome, CallScriptType, StageType
from app.core.exceptions import (
    ConfigurationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.mail import renderer
from app.mail.guardrails import is_within_window, local_now
from app.models.call import CallLog, CallScript
from app.models.company import CompanySignal
from app.models.lead import Lead
from app.models.settings import AppSettings
from app.repositories.base import BaseRepository
from app.services.activity_svc import ActivityService
from app.services.lead_svc import LeadService
from app.services.pipeline_svc import PipelineService

# Qué se sugiere hacer con el prospecto según cómo acabó la llamada. Es una
# sugerencia: mover de etapa siempre lo confirma la persona (§9).
SUGGESTED_STAGE: dict[CallOutcome, StageType] = {
    CallOutcome.INTERESTED: StageType.INTERESTED,
    CallOutcome.MEETING_SCHEDULED: StageType.MEETING,
    # No hay etapa "no le interesa": el prospecto muerto va a Perdido, igual
    # que cuando la respuesta por correo es negativa.
    CallOutcome.NOT_INTERESTED: StageType.LOST,
}

# Resultados que significan "hablé con la persona". Separarlos importa para la
# métrica que de verdad se usa: cuántos intentos por conversación.
CONNECTED: frozenset[CallOutcome] = frozenset(
    {
        CallOutcome.CALLBACK,
        CallOutcome.NOT_INTERESTED,
        CallOutcome.INTERESTED,
        CallOutcome.MEETING_SCHEDULED,
        CallOutcome.DO_NOT_CALL,
    }
)

OUTCOME_LABELS: dict[CallOutcome, str] = {
    CallOutcome.NO_ANSWER: "No contestó",
    CallOutcome.VOICEMAIL: "Buzón de voz",
    CallOutcome.GATEKEEPER: "No pasó de recepción",
    CallOutcome.WRONG_NUMBER: "Número equivocado",
    CallOutcome.CALLBACK: "Pidió que le llame después",
    CallOutcome.NOT_INTERESTED: "No le interesa",
    CallOutcome.INTERESTED: "Interesado",
    CallOutcome.MEETING_SCHEDULED: "Reunión agendada",
    CallOutcome.DO_NOT_CALL: "Pidió que no le vuelvan a llamar",
}


# Corta por el final de la frase, nunca por el principio: «¿» y «¡» abren, no
# cierran. Incluirlos aquí se comía el signo de apertura y dejaba el guion con
# preguntas escritas a la inglesa.
_SENTENCE_RE = re.compile(r"[^.!?]*[.!?]+\s*|[^.!?]+$")


def _render_dropping_empty(text: str, context: dict[str, object]) -> tuple[str, list[str]]:
    """Sustituye variables y tira las frases que se quedaron sin dato.

    En un correo, una variable vacía deja un hueco que casi no se nota. Leída
    en voz alta, "me llamó la atención que ." delata que hay una plantilla
    detrás y hunde la llamada en el primer segundo.

    Así que una frase que dependía de un dato que no tenemos no se dice. Se
    devuelve además qué faltaba, para avisar de que el guion salió más corto.
    """
    faltan: list[str] = []
    frases: list[str] = []

    for match in _SENTENCE_RE.finditer(text):
        frase = match.group(0)
        if not frase.strip():
            continue
        resultado = renderer.render(subject="", body_text=frase, context=context)
        for name in resultado.missing:
            if name not in faltan:
                faltan.append(name)
        # La frase entera se cae solo si le faltaba algo; el resto sigue.
        if not resultado.missing:
            frases.append(resultado.body_text)

    unido = "".join(frases).strip()
    # Sin frases útiles se devuelve vacío: es más honesto que media frase.
    return re.sub(r"[ \t]{2,}", " ", unido), faltan


@dataclass(slots=True)
class RenderedScript:
    """Un guion con los datos del prospecto ya puestos."""

    script_id: uuid.UUID
    name: str
    script_type: CallScriptType
    opening: str
    context: str | None
    questions: list[str]
    value_pitch: str | None
    close: str | None
    objections: list[dict[str, str]]
    # Variables que se quedaron sin valor. Se avisan porque una frase con un
    # hueco se nota al leerla en voz alta.
    missing: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CallBrief:
    """Todo lo necesario para levantar el teléfono."""

    lead_id: uuid.UUID
    company_name: str
    contact_name: str | None
    contact_id: uuid.UUID | None
    phone: str | None
    phone_source: str | None
    suggested_type: CallScriptType
    suggested_reason: str
    script: RenderedScript | None
    available_types: list[CallScriptType]
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    previous_calls: int = 0
    last_call_at: datetime | None = None
    last_call_outcome: CallOutcome | None = None

    @property
    def can_call(self) -> bool:
        return self.blocked_reason is None


class CallScriptRepository(BaseRepository[CallScript]):
    model = CallScript


class CallLogRepository(BaseRepository[CallLog]):
    model = CallLog


class CallService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.scripts = CallScriptRepository(session)
        self.logs = CallLogRepository(session)

    # ------------------------------------------------------------ guiones

    def build_script_query(
        self,
        *,
        script_type: CallScriptType | None = None,
        service_id: uuid.UUID | None = None,
        only_active: bool = True,
    ) -> Select[tuple[CallScript]]:
        stmt = select(CallScript).order_by(CallScript.script_type, CallScript.name)
        if script_type is not None:
            stmt = stmt.where(CallScript.script_type == script_type)
        if service_id is not None:
            stmt = stmt.where(
                (CallScript.service_id == service_id) | (CallScript.service_id.is_(None))
            )
        if only_active:
            stmt = stmt.where(CallScript.is_active.is_(True))
        return stmt

    async def get_script_or_404(self, script_id: uuid.UUID) -> CallScript:
        script = await self.scripts.get(script_id)
        if script is None:
            raise NotFoundError("No existe ese guion de llamada.", code="CALL_SCRIPT_NOT_FOUND")
        return script

    async def create_script(self, data: dict) -> CallScript:
        self._validate_script_text(data)
        existing = await self.session.execute(
            select(CallScript).where(CallScript.name == data["name"])
        )
        if existing.scalars().first() is not None:
            raise ConflictError(
                f"Ya existe un guion llamado '{data['name']}'.", code="CALL_SCRIPT_NAME_TAKEN"
            )
        script = CallScript(**data)
        self.session.add(script)
        await self.session.flush()
        return script

    async def update_script(self, script: CallScript, data: dict) -> CallScript:
        self._validate_script_text({**self._as_dict(script), **data})
        for key, value in data.items():
            setattr(script, key, value)
        await self.session.flush()
        return script

    @staticmethod
    def _as_dict(script: CallScript) -> dict:
        return {
            "opening": script.opening,
            "context": script.context,
            "questions": list(script.questions or []),
            "value_pitch": script.value_pitch,
            "close": script.close,
            "objections": list(script.objections or []),
        }

    @staticmethod
    def _validate_script_text(data: dict) -> None:
        """Las variables del guion son las mismas que las del correo.

        Se valida al guardar y no al llamar: una variable mal escrita se leería
        en voz alta delante del prospecto.
        """
        textos: list[str | None] = [
            data.get("opening"),
            data.get("context"),
            data.get("value_pitch"),
            data.get("close"),
            *(data.get("questions") or []),
        ]
        for objection in data.get("objections") or []:
            textos.extend([objection.get("objection"), objection.get("response")])

        usadas = renderer.extract_variables(*textos)
        # `unsubscribe_url` es de correo; en una llamada no significa nada.
        disponibles = set(renderer.AVAILABLE_VARIABLES) - {renderer.REQUIRED_VARIABLE}
        desconocidas = [v for v in usadas if v not in disponibles]
        if desconocidas:
            listado = ", ".join("{{" + v + "}}" for v in desconocidas)
            raise ValidationError(
                f"Variables desconocidas en el guion: {listado}.",
                code="UNKNOWN_SCRIPT_VARIABLE",
                details={"unknown": desconocidas, "available": sorted(disponibles)},
            )

    # ------------------------------------------------------------ preparar

    async def _settings(self) -> AppSettings:
        result = await self.session.execute(select(AppSettings).limit(1))
        settings = result.scalar_one_or_none()
        if settings is None:
            raise ConfigurationError(
                "Falta la configuración de la aplicación.", code="SETTINGS_MISSING"
            )
        return settings

    async def suggest_type(self, lead: Lead) -> tuple[CallScriptType, str]:
        """Qué guion toca, y por qué.

        El motivo se enseña en la interfaz: una sugerencia sin explicación es
        indistinguible de una decisión arbitraria, y la persona necesita poder
        contradecirla con criterio.
        """
        if lead.stage.stage_type == StageType.MEETING:
            return CallScriptType.MEETING, "Ya hay una reunión en juego: toca cuadrarla."

        if lead.replied_at is not None:
            return (
                CallScriptType.INBOUND,
                "Este prospecto ya respondió: no es una llamada en frío.",
            )

        calls = await self.session.execute(
            select(func.count())
            .select_from(CallLog)
            .where(CallLog.lead_id == lead.id, CallLog.outcome == CallOutcome.GATEKEEPER)
        )
        if calls.scalar_one() > 0:
            return (
                CallScriptType.GATEKEEPER,
                "La última vez no pasaste de recepción.",
            )

        if lead.first_contact_at is not None:
            return (
                CallScriptType.FOLLOW_UP,
                "Ya se le escribió antes y no ha contestado.",
            )

        return CallScriptType.COLD_FIRST, "Primer contacto: no te conoce de nada."

    async def build_brief(
        self,
        lead: Lead,
        *,
        script_type: CallScriptType | None = None,
        script_id: uuid.UUID | None = None,
    ) -> CallBrief:
        settings = await self._settings()
        suggested, reason = await self.suggest_type(lead)
        chosen_type = script_type or suggested

        script = await self._pick_script(chosen_type, lead, script_id=script_id)
        contact = lead.contact
        phone, phone_source = self._pick_phone(lead)

        brief = CallBrief(
            lead_id=lead.id,
            company_name=lead.company.name,
            contact_name=contact.display_name if contact else None,
            contact_id=contact.id if contact else None,
            phone=phone,
            phone_source=phone_source,
            suggested_type=suggested,
            suggested_reason=reason,
            script=await self._render(script, lead, settings) if script else None,
            available_types=list(CallScriptType),
        )

        await self._add_history(brief, lead)
        self._add_warnings(brief, lead, settings)
        return brief

    async def _pick_script(
        self,
        script_type: CallScriptType,
        lead: Lead,
        *,
        script_id: uuid.UUID | None,
    ) -> CallScript | None:
        if script_id is not None:
            return await self.get_script_or_404(script_id)

        stmt = self.build_script_query(script_type=script_type, service_id=lead.service_id)
        result = await self.session.execute(stmt)
        candidatos = list(result.scalars())
        if not candidatos:
            return None
        # El guion específico del servicio gana al genérico.
        candidatos.sort(key=lambda s: (s.service_id is None, s.name))
        return candidatos[0]

    @staticmethod
    def _pick_phone(lead: Lead) -> tuple[str | None, str | None]:
        """Teléfono a marcar, y de dónde salió.

        El del contacto manda sobre el de la empresa: llamar a la centralita
        cuando ya se conoce el directo es perder la llamada en recepción.
        """
        contact = lead.contact
        if contact is not None and contact.phone:
            return contact.phone, "contacto"
        if lead.company.phone:
            return lead.company.phone, "empresa"
        return None, None

    async def _render(
        self, script: CallScript, lead: Lead, settings: AppSettings
    ) -> RenderedScript:
        signals = await self._signal_keys(lead.company_id)
        context = renderer.build_context(
            company_name=lead.company.name,
            sender_name=settings.sender_name or "",
            service_name=lead.service.name if lead.service else None,
            contact_name=lead.contact.display_name if lead.contact else None,
            city=lead.company.city,
            category=lead.company.category,
            website=lead.company.website_domain,
            signals=signals,
        )

        missing: list[str] = []

        def sub(text: str | None) -> str | None:
            if not text:
                return text
            kept, faltan = _render_dropping_empty(text, context)
            for name in faltan:
                if name not in missing:
                    missing.append(name)
            return kept

        objections = [
            {
                "objection": sub(item.get("objection")) or "",
                "response": sub(item.get("response")) or "",
            }
            for item in (script.objections or [])
        ]

        return RenderedScript(
            script_id=script.id,
            name=script.name,
            script_type=script.script_type,
            opening=sub(script.opening) or "",
            context=sub(script.context),
            questions=[sub(q) or "" for q in (script.questions or [])],
            value_pitch=sub(script.value_pitch),
            close=sub(script.close),
            objections=objections,
            missing=missing,
        )

    async def _signal_keys(self, company_id: uuid.UUID) -> list[str]:
        result = await self.session.execute(
            select(CompanySignal.signal_key).where(CompanySignal.company_id == company_id)
        )
        return [row[0] for row in result]

    async def _add_history(self, brief: CallBrief, lead: Lead) -> None:
        result = await self.session.execute(
            select(CallLog)
            .where(CallLog.lead_id == lead.id)
            .order_by(CallLog.occurred_at.desc())
            .limit(1)
        )
        last = result.scalars().first()

        total = await self.session.execute(
            select(func.count()).select_from(CallLog).where(CallLog.lead_id == lead.id)
        )
        brief.previous_calls = total.scalar_one()
        if last is not None:
            brief.last_call_at = last.occurred_at
            brief.last_call_outcome = last.outcome

    @staticmethod
    def _add_warnings(brief: CallBrief, lead: Lead, settings: AppSettings) -> None:
        """Avisos y bloqueos antes de marcar.

        `blocked_reason` es un no rotundo —alguien pidió que no le llamen— y
        los `warnings` son cosas que conviene saber pero no impiden llamar.
        """
        contact = lead.contact

        if contact is not None and contact.do_not_contact:
            brief.blocked_reason = "Este contacto está marcado como 'no contactar'. No le llames."
        elif brief.last_call_outcome == CallOutcome.DO_NOT_CALL:
            brief.blocked_reason = "En la última llamada pidió que no le volvieran a llamar."

        if not brief.phone:
            brief.warnings.append(
                "No hay teléfono ni del contacto ni de la empresa. Búscalo antes de llamar."
            )

        if not is_within_window(settings, datetime.now(UTC)):
            local = local_now(settings)
            brief.warnings.append(
                f"Son las {local:%H:%M} en {settings.timezone}: estás fuera de tu horario "
                f"de contacto ({settings.send_window_start:%H:%M} a "
                f"{settings.send_window_end:%H:%M}). Llamar fuera de hora molesta y, "
                "según a quién llames, puede incumplir la normativa local."
            )

        if brief.script is None:
            brief.warnings.append(
                "No hay ningún guion para esta situación. Créalo en Guiones de llamada."
            )
        elif brief.script.missing:
            faltan = ", ".join(brief.script.missing)
            brief.warnings.append(
                f"El guion menciona datos que no tenemos ({faltan}): esas frases quedan "
                "cojas. Léelas antes de marcar."
            )

        if brief.previous_calls >= 5 and brief.last_call_outcome not in (
            CallOutcome.CALLBACK,
            CallOutcome.INTERESTED,
        ):
            brief.warnings.append(
                f"Van {brief.previous_calls} llamadas sin llegar a nada. Insistir más "
                "cansa al prospecto y no suele cambiar el resultado."
            )

    # ------------------------------------------------------------ registro

    async def log_call(
        self,
        lead: Lead,
        *,
        outcome: CallOutcome,
        script_id: uuid.UUID | None = None,
        phone: str | None = None,
        duration_seconds: int | None = None,
        notes: str | None = None,
        occurred_at: datetime | None = None,
    ) -> CallLog:
        """Registra la llamada y aplica lo que se deriva de ella.

        No mueve al prospecto de etapa: eso lo confirma la persona desde la
        ficha. Lo que sí hace sin preguntar es lo irrenunciable —marcar 'no
        contactar' cuando alguien lo pide expresamente—.
        """
        occurred = occurred_at or datetime.now(UTC)
        attempts = await self.session.execute(
            select(func.count()).select_from(CallLog).where(CallLog.lead_id == lead.id)
        )

        log = CallLog(
            lead_id=lead.id,
            contact_id=lead.contact_id,
            script_id=script_id,
            phone=phone or self._pick_phone(lead)[0],
            outcome=outcome,
            duration_seconds=duration_seconds,
            notes=notes,
            attempt=attempts.scalar_one() + 1,
            occurred_at=occurred,
        )
        self.session.add(log)

        if script_id is not None:
            script = await self.scripts.get(script_id)
            if script is not None:
                script.times_used += 1

        # Hablar cuenta como contacto; que suene y no contesten, no.
        if outcome in CONNECTED:
            lead.last_contact_at = occurred
            if lead.first_contact_at is None:
                lead.first_contact_at = occurred
        lead.last_activity_at = occurred

        if outcome == CallOutcome.DO_NOT_CALL and lead.contact is not None:
            # Petición explícita del prospecto: se aplica ya, sin confirmar.
            lead.contact.do_not_contact = True

        await ActivityService(self.session).record(
            ActivityType.CALL_LOGGED,
            title=f"Llamada: {OUTCOME_LABELS[outcome]}",
            description=notes,
            lead_id=lead.id,
            company_id=lead.company_id,
            contact_id=lead.contact_id,
            actor=ActorType.USER,
            metadata={
                "outcome": outcome.value,
                "attempt": log.attempt,
                "duration_seconds": duration_seconds,
            },
        )

        await self.session.flush()
        return log

    async def apply_suggested_stage(self, lead: Lead, outcome: CallOutcome) -> bool:
        """Mueve al prospecto a la etapa que sugiere el resultado.

        Se llama solo cuando la persona lo pide explícitamente.
        """
        stage_type = SUGGESTED_STAGE.get(outcome)
        if stage_type is None:
            return False

        stage = await PipelineService(self.session).get_by_type(stage_type)
        if stage is None:
            return False

        await LeadService(self.session).move_stage(
            lead,
            stage.id,
            actor=ActorType.USER,
            reason=f"Llamada: {OUTCOME_LABELS[outcome].lower()}",
        )
        return True

    def build_log_query(
        self,
        *,
        lead_id: uuid.UUID | None = None,
        outcome: CallOutcome | None = None,
    ) -> Select[tuple[CallLog]]:
        stmt = select(CallLog).order_by(CallLog.occurred_at.desc())
        if lead_id is not None:
            stmt = stmt.where(CallLog.lead_id == lead_id)
        if outcome is not None:
            stmt = stmt.where(CallLog.outcome == outcome)
        return stmt
