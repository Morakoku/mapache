"""Guiones de llamada y registro de llamadas.

El CRM no marca el teléfono: eso lo hace la persona. Lo que aporta aquí es
tener delante el guion correcto para la situación —con los datos reales del
prospecto ya sustituidos— y dejar constancia de lo que pasó.

Un guion es una ayuda, no un texto para leer palabra por palabra. Por eso se
guarda troceado en secciones: se lee de un vistazo mientras se habla, en vez
de perderse dentro de un párrafo largo.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import CallOutcome, CallScriptType
from app.models.base import OwnedModel, pg_enum

if TYPE_CHECKING:  # pragma: no cover - solo para el tipo de la relación
    from app.models.lead import Lead


class CallScript(OwnedModel):
    """Guion de llamada para una situación concreta."""

    __tablename__ = "call_scripts"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    script_type: Mapped[CallScriptType] = mapped_column(
        pg_enum(CallScriptType, "call_script_type"), nullable=False
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("services.id", ondelete="CASCADE"), nullable=True
    )

    # Los primeros diez segundos. Es donde se decide la llamada.
    opening: Mapped[str] = mapped_column(Text, nullable=False)
    # Por qué llamas a esta empresa y no a otra.
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Preguntas de descubrimiento. En lista porque se van tachando al hablar.
    questions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    value_pitch: Mapped[str | None] = mapped_column(Text, nullable=True)
    close: Mapped[str | None] = mapped_column(Text, nullable=True)

    # [{"objection": "...", "response": "..."}]. La respuesta a una objeción
    # hay que tenerla escrita antes de la llamada: improvisándola sale mal.
    objections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    times_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "name",
            name="uq_call_scripts_owner_name",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_call_scripts_type", "owner_id", "script_type", "is_active"),
    )


class CallLog(OwnedModel):
    """Una llamada que ya ocurrió.

    Se registra incluso cuando no contestan. Sin los intentos fallidos no se
    puede responder a "¿cuántas llamadas hacen falta para una reunión?", que
    es justo lo que hay que saber para decidir si vale la pena llamar.
    """

    __tablename__ = "call_logs"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    script_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("call_scripts.id", ondelete="SET NULL"), nullable=True
    )

    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    outcome: Mapped[CallOutcome] = mapped_column(
        pg_enum(CallOutcome, "call_outcome"), nullable=False
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Número de intento con este prospecto. Se calcula al registrar.
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lead: Mapped[Lead] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_call_logs_lead", "lead_id", "occurred_at"),
        Index("ix_call_logs_outcome", "owner_id", "outcome", "occurred_at"),
    )


# ------------------------------------------------------------------ semillas


def _objection(objection: str, response: str) -> dict[str, str]:
    return {"objection": objection, "response": response}


# Guiones que trae el CRM de fábrica, en español de Colombia.
#
# Están escritos para decir la verdad desde el primer segundo: quién llama, de
# dónde salió el número y que se puede cortar la llamada. No hay pretextos ni
# falsas referencias — además de ser lo correcto, funciona mejor: quien se
# siente engañado no compra.
DEFAULT_SCRIPTS: tuple[dict[str, Any], ...] = (
    {
        "name": "Primer contacto en frío",
        "script_type": CallScriptType.COLD_FIRST,
        "opening": (
            "Hola, ¿hablo con {{first_name}}? Soy {{sender_name}}. "
            "Te llamo en frío, sin que me esperes, así que si te agarré en mal "
            "momento dímelo y colgamos. ¿Tienes treinta segundos?"
        ),
        "context": (
            "Vi {{company_name}} en {{city}} y me llamó la atención "
            "que {{signal_summary}}."
        ),
        "questions": [
            "¿Hoy quién les ve ese tema?",
            "¿Es algo que les esté doliendo o ya lo tienen resuelto?",
            "¿Qué han intentado hasta ahora?",
        ],
        "value_pitch": (
            "Lo que hago es {{service_name}}. No te lo voy a vender por "
            "teléfono: si te sirve, te muestro un caso parecido al tuyo y decides."
        ),
        "close": (
            "¿Te parece si agendamos quince minutos esta semana? "
            "¿Te sirve mejor mañana o el jueves?"
        ),
        "objections": [
            _objection(
                "Estoy ocupado",
                "Te entiendo, llamé sin avisar. ¿Te llamo mañana a esta misma hora "
                "o prefieres que te escriba?",
            ),
            _objection(
                "Mándame información al correo",
                "Claro. Para no mandarte un genérico: ¿qué es lo que más te "
                "interesaría ver? Te lo mando hoy mismo.",
            ),
            _objection(
                "No me interesa",
                "Sin problema. ¿Es que ya lo tienen resuelto o que no es prioridad "
                "ahora? Lo pregunto para no volver a molestarte si no aplica.",
            ),
            _objection(
                "¿De dónde sacaste mi número?",
                "De la ficha pública de {{company_name}} en internet. Si prefieres "
                "que no te vuelva a llamar, lo registro ahora mismo y no vuelves a "
                "saber de mí.",
            ),
            _objection(
                "Ya trabajamos con alguien",
                "Perfecto, no vengo a que cambien. ¿Qué les falta de lo que tienen "
                "hoy? Si no falta nada, te dejo tranquilo.",
            ),
        ],
    },
    {
        "name": "Pasar de recepción",
        "script_type": CallScriptType.GATEKEEPER,
        "opening": (
            "Buenos días, ¿me ayudas con una consulta? Busco a quien ve el tema "
            "de {{service_name}} en {{company_name}}."
        ),
        "context": (
            "Soy {{sender_name}}. No es para venderle nada por teléfono, es para "
            "saber si les aplica o no y dejar de insistir si no."
        ),
        "questions": [
            "¿Con quién debería hablar?",
            "¿A qué hora suele estar disponible?",
            "¿Prefieres que le escriba primero?",
        ],
        "close": "¿Me lo pasas o mejor llamo a otra hora?",
        "objections": [
            _objection(
                "No pasamos llamadas comerciales",
                "Lo entiendo, es su trabajo. ¿Hay un correo al que sí pueda "
                "escribir? Mando una sola vez y si no interesa, no insisto.",
            ),
            _objection(
                "Déjame tus datos y te llamamos",
                "Claro: {{sender_name}}. Igual te dejo mi correo por si prefieren "
                "escribir. ¿Te lo dicto?",
            ),
        ],
    },
    {
        "name": "Respondió el correo",
        "script_type": CallScriptType.INBOUND,
        "opening": (
            "Hola {{first_name}}, soy {{sender_name}}. Te llamo por el correo que "
            "me respondiste sobre {{service_name}}. ¿Tienes un minuto?"
        ),
        "context": (
            "Me quedé con que te interesaba. Antes de proponerte nada quiero "
            "entender bien qué necesitan."
        ),
        "questions": [
            "¿Qué fue lo que te hizo responder?",
            "¿Qué tienen hoy y qué les falta?",
            "¿Para cuándo lo necesitarían?",
            "¿Quién más decide esto contigo?",
        ],
        "value_pitch": "Por lo que me cuentas, lo que encaja es {{service_name}}.",
        "close": (
            "Te mando una propuesta concreta hoy. ¿La revisamos juntos en una "
            "llamada de quince minutos?"
        ),
        "objections": [
            _objection(
                "¿Cuánto cuesta?",
                "Depende del alcance, y prefiero no inventarme un número. Con lo "
                "que me acabas de contar te paso hoy un rango cerrado, sin letra "
                "pequeña.",
            ),
            _objection(
                "Tengo que consultarlo",
                "Lógico. ¿Qué necesitarías tener en la mano para esa conversación? "
                "Te lo preparo.",
            ),
        ],
    },
    {
        "name": "Seguimiento de un correo sin respuesta",
        "script_type": CallScriptType.FOLLOW_UP,
        "opening": (
            "Hola {{first_name}}, soy {{sender_name}}. Te escribí hace unos días "
            "sobre {{service_name}} y te llamo por si el correo se te perdió."
        ),
        "context": (
            "No te llamo a insistir: solo quiero saber si te sirve o lo descarto "
            "y no te molesto más."
        ),
        "questions": [
            "¿Alcanzaste a verlo?",
            "¿Tiene sentido para ustedes ahora?",
            "Si no es ahora, ¿cuándo tendría sentido?",
        ],
        "close": "¿Lo dejo hasta aquí o te busco en un par de meses?",
        "objections": [
            _objection(
                "Ahora no es el momento",
                "Perfecto. ¿Te busco en dos o tres meses? Lo anoto y hasta entonces "
                "no te molesto.",
            ),
            _objection(
                "No lo vi",
                "Sin problema, pasa siempre. En una frase: {{signal_summary}}. "
                "¿Te lo reenvío o te lo cuento ahora?",
            ),
        ],
    },
    {
        "name": "Cerrar la reunión",
        "script_type": CallScriptType.MEETING,
        "opening": (
            "Hola {{first_name}}, soy {{sender_name}}. Te llamo para cuadrar la "
            "reunión que quedamos."
        ),
        "questions": [
            "¿Te sirve esta semana o la próxima?",
            "¿Quién más debería estar?",
            "¿Prefieres virtual o presencial?",
        ],
        "close": (
            "Perfecto. Te mando la invitación ahora mismo y te la confirmo por "
            "correo para que quede por escrito."
        ),
        "objections": [
            _objection(
                "Mejor te confirmo después",
                "Vale. Dejo una tentativa y si no te sirve la movemos. Así no se "
                "nos pasa la semana.",
            ),
        ],
    },
)
