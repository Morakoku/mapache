"""Módulo 15 — clasificación de respuestas.

Dos motores, mismo contrato: la IA cuando hay clave, y un motor de reglas por
palabras clave cuando no la hay o cuando falla. El de reglas no es un adorno:
es el que corre por defecto en una instalación sin `ANTHROPIC_API_KEY`, así
que tiene que ser honesto — clasifica lo evidente y devuelve `UNKNOWN` con
confianza baja en cuanto duda.

La IA sugiere, el usuario decide (§9 del diseño). Lo único que ocurre solo es
la supresión ante una baja explícita, porque equivocarse hacia el otro lado
—seguir escribiendo a quien pidió que pararas— es mucho peor.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.ai import client, prompts
from app.core.enums import ReplyIntent, StageType
from app.core.logging import get_logger

logger = get_logger(__name__)

# Por debajo de esto la sugerencia se muestra atenuada y no dispara nada.
CONFIDENCE_THRESHOLD = 0.7

# Etapa que se sugiere para cada intención. Sugerir, no mover: el avance más
# allá de "Conversación" es una decisión comercial del usuario.
SUGGESTED_STAGE: dict[ReplyIntent, StageType | None] = {
    ReplyIntent.POSITIVE: StageType.INTERESTED,
    ReplyIntent.QUESTION: StageType.CONVERSATION,
    ReplyIntent.PRICING: StageType.OPPORTUNITY,
    ReplyIntent.MEETING_REQUEST: StageType.MEETING,
    ReplyIntent.NEUTRAL: StageType.REPLIED,
    ReplyIntent.NEGATIVE: StageType.LOST,
    ReplyIntent.UNSUBSCRIBE: StageType.LOST,
    ReplyIntent.WRONG_PERSON: None,
    ReplyIntent.OUT_OF_OFFICE: None,
    ReplyIntent.UNKNOWN: None,
}

# Puntos de engagement adicionales por intención (§8 del diseño).
INTENT_ENGAGEMENT: dict[ReplyIntent, str | None] = {
    ReplyIntent.POSITIVE: "INFO_REQUESTED",
    ReplyIntent.PRICING: "INFO_REQUESTED",
    ReplyIntent.MEETING_REQUEST: "MEETING_REQUESTED",
}


@dataclass(frozen=True, slots=True)
class Classification:
    intent: ReplyIntent
    confidence: float
    summary: str
    suggested_stage: StageType | None = None
    suggested_reply_points: list[str] = field(default_factory=list)
    # "ai" o "rules". Se guarda para poder medir después si la IA acierta más.
    source: str = "rules"

    @property
    def is_confident(self) -> bool:
        return self.confidence >= CONFIDENCE_THRESHOLD


# ------------------------------------------------------------------ reglas

# Orden intencionado: lo que se comprueba antes gana. La baja explícita va
# primero porque "no me escriban más, no nos interesa" es una baja, no un no.
_RULES: tuple[tuple[ReplyIntent, tuple[str, ...], float], ...] = (
    (
        ReplyIntent.UNSUBSCRIBE,
        (
            "no me escriban",
            "no me escribas",
            "no vuelvan a escribir",
            "dejen de escribir",
            "deja de escribirme",
            "borrenme",
            "borrame",
            "eliminen mis datos",
            "elimina mis datos",
            "darme de baja",
            "dar de baja",
            "remove me",
            "unsubscribe",
            "no quiero recibir",
            "no deseo recibir",
            "sacame de",
            "saquenme de",
        ),
        0.92,
    ),
    (
        ReplyIntent.OUT_OF_OFFICE,
        (
            "fuera de la oficina",
            "out of office",
            "estare ausente",
            "estoy de vacaciones",
            "respuesta automatica",
            "automatic reply",
            "regreso el",
            "volvere el",
        ),
        0.9,
    ),
    (
        ReplyIntent.WRONG_PERSON,
        (
            "no soy la persona",
            "no soy quien",
            "no me corresponde",
            "no soy el encargado",
            "no soy la encargada",
            "escribele a",
            "escribale a",
            "contacta a",
            "contacte a",
            "mi colega",
            "quien ve eso es",
            "ya no trabajo",
        ),
        0.85,
    ),
    (
        ReplyIntent.MEETING_REQUEST,
        (
            "agendemos",
            "agendar",
            "reunion",
            "reunirnos",
            "llamada",
            "podemos hablar",
            "cuando podemos",
            "que dia",
            "que hora",
            "disponibilidad",
            "calendario",
            "videollamada",
            "zoom",
            "meet",
        ),
        0.85,
    ),
    (
        ReplyIntent.PRICING,
        (
            "cuanto cuesta",
            # "cuanto cost" cubre costaría, costaba y costó: en un correo real
            # el precio se pregunta de muchas formas y todas son PRICING.
            "cuanto cost",
            "cuanto vale",
            "que valor",
            "que precio",
            "precios",
            "cotizacion",
            "cotizar",
            "presupuesto",
            "tarifas",
            "cuanto seria",
            "cuanto me sale",
            "valor del servicio",
        ),
        0.88,
    ),
    (
        ReplyIntent.NEGATIVE,
        (
            "no estamos interesados",
            "no nos interesa",
            "no me interesa",
            "no gracias",
            "por ahora no",
            "no es el momento",
            "ya tenemos",
            "no lo necesitamos",
            "no requerimos",
            "declinamos",
        ),
        0.85,
    ),
    (
        ReplyIntent.POSITIVE,
        (
            "me interesa",
            "nos interesa",
            "cuentame mas",
            "cuenteme mas",
            "quiero saber mas",
            "suena bien",
            "me gustaria",
            "nos gustaria",
            "adelante",
            "claro que si",
            "enviame mas informacion",
            "mas informacion",
            "envieme informacion",
        ),
        0.82,
    ),
    (
        ReplyIntent.NEUTRAL,
        (
            "gracias por escribir",
            "gracias por tu correo",
            "recibido",
            "lo reviso",
            "lo revisamos",
            "te cuento",
            "le cuento",
            "quedo atento",
            "quedamos atentos",
        ),
        0.75,
    ),
)

_SUMMARIES: dict[ReplyIntent, str] = {
    ReplyIntent.POSITIVE: "Muestra interés y quiere saber más.",
    ReplyIntent.QUESTION: "Hace una pregunta concreta sobre el servicio.",
    ReplyIntent.PRICING: "Pregunta por precio o cotización.",
    ReplyIntent.MEETING_REQUEST: "Pide o propone una reunión.",
    ReplyIntent.NEUTRAL: "Acusa recibo sin comprometerse.",
    ReplyIntent.NEGATIVE: "Dice que no le interesa.",
    ReplyIntent.UNSUBSCRIBE: "Pide no recibir más correos.",
    ReplyIntent.WRONG_PERSON: "No es la persona que decide.",
    ReplyIntent.OUT_OF_OFFICE: "Respuesta automática de ausencia.",
    ReplyIntent.UNKNOWN: "No se pudo determinar la intención.",
}


def _normalize(text: str) -> str:
    """Minúsculas y sin tildes: 'cotización' y 'cotizacion' son lo mismo."""
    lowered = text.lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def classify_with_rules(subject: str, body: str) -> Classification:
    """Clasificación por palabras clave. Es el camino sin IA."""
    haystack = _normalize(f"{subject}\n{body}")

    for intent, markers, confidence in _RULES:
        if any(marker in haystack for marker in markers):
            return Classification(
                intent=intent,
                confidence=confidence,
                summary=_SUMMARIES[intent],
                suggested_stage=SUGGESTED_STAGE[intent],
            )

    # Una pregunta sin más señales: hay interrogación y el mensaje es corto.
    if "?" in body and len(body) < 600:
        return Classification(
            intent=ReplyIntent.QUESTION,
            confidence=0.6,
            summary=_SUMMARIES[ReplyIntent.QUESTION],
            suggested_stage=SUGGESTED_STAGE[ReplyIntent.QUESTION],
        )

    return Classification(
        intent=ReplyIntent.UNKNOWN,
        confidence=0.3,
        summary=_SUMMARIES[ReplyIntent.UNKNOWN],
    )


# ------------------------------------------------------------------ IA


async def classify(
    *,
    subject: str,
    body: str,
    company_name: str,
    service_name: str | None = None,
    our_last_message: str | None = None,
    ai_enabled: bool = True,
    credentials: client.AICredentials | None = None,
) -> Classification:
    """Clasifica una respuesta. Con IA si se puede, con reglas si no.

    Nunca lanza: una respuesta sin clasificar es peor que una clasificada por
    palabras clave.
    """
    fallback = classify_with_rules(subject, body)

    if not ai_enabled or credentials is None:
        return fallback

    try:
        result = await client.complete_json(
            system=prompts.CLASSIFIER_SYSTEM,
            user=prompts.build_classifier_input(
                subject=subject,
                body=body,
                company_name=company_name,
                service_name=service_name,
                our_last_message=our_last_message,
            ),
            schema=prompts.CLASSIFIER_SCHEMA,
            credentials=credentials,
            # Clasificar es una tarea acotada: no necesita razonar hondo y así
            # cuesta ~$0,005 por respuesta.
            effort="low",
            max_tokens=2000,
        )
    except client.AIUnavailableError as exc:
        logger.info("classification_fell_back_to_rules", reason=exc.code)
        return fallback

    try:
        intent = ReplyIntent(result.data["intent"])
    except (KeyError, ValueError):
        return fallback

    confidence = float(result.data.get("confidence", 0.0))
    return Classification(
        intent=intent,
        confidence=max(0.0, min(1.0, confidence)),
        summary=str(result.data.get("summary") or _SUMMARIES[intent]),
        suggested_stage=SUGGESTED_STAGE[intent],
        suggested_reply_points=[str(p) for p in result.data.get("suggested_reply_points", [])],
        source="ai",
    )


def looks_like_unsubscribe(text: str) -> bool:
    """Baja explícita detectada por reglas.

    Se usa como red de seguridad independiente de la IA: la supresión es la
    única acción automática y no puede depender de que el modelo responda.
    """
    normalized = _normalize(text)
    markers = next(m for i, m, _ in _RULES if i is ReplyIntent.UNSUBSCRIBE)
    return any(marker in normalized for marker in markers)


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))
