"""Prompts de sistema y esquemas de salida (§12.2 y §12.3).

Los bloques de sistema son constantes a propósito: se cachean entre llamadas y
son el 90% de la entrada. Cualquier dato variable (la empresa, el servicio, la
respuesta del prospecto) va en el mensaje de usuario, nunca aquí — meterlo en
el sistema rompería la caché en cada envío.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import ReplyIntent

# ------------------------------------------------------------------ redacción

PERSONALIZER_SYSTEM = """\
Escribes correos de primer contacto para un profesional independiente que \
vende servicios a pequeñas y medianas empresas en Colombia.

OBJETIVO DEL CORREO
El objetivo es conseguir una conversación, no vender. Un correo que consigue \
una respuesta de dos líneas ha ganado; uno que intenta cerrar la venta en el \
primer contacto ha perdido.

REGLAS DE REDACCIÓN
- Máximo 120 palabras en el cuerpo completo. Menos es mejor.
- La observación sobre la empresa debe basarse EXCLUSIVAMENTE en los datos \
que recibes. Si no hay una señal concreta, escribe una observación genérica y \
honesta en vez de inventar un detalle. Inventar un dato es el peor error \
posible: destruye la credibilidad en la primera frase.
- La llamada a la acción es de bajo compromiso: una pregunta que se pueda \
responder en una línea. Nunca "agenda una demo de 45 minutos".
- Sin superlativos ("increíble", "revolucionario", "el mejor").
- Sin fórmulas vacías: nada de "espero que te encuentres bien", "espero que \
este correo te encuentre bien", "somos líderes en".
- Sin promesas de resultados concretos ("triplicamos tus ventas").
- Sin emojis. Sin signos de exclamación.
- Español neutro de Colombia. Frases cortas.
- No incluyas despedida con firma ni enlace de baja: el sistema los añade.

FORMATO
Devuelves JSON con las partes por separado y el cuerpo completo ya montado en \
`full_body`. El cuerpo completo debe leerse como un correo natural, no como \
la concatenación de unas etiquetas.
En `reasoning` explicas en una frase por qué elegiste ese ángulo. Ese texto \
es para el usuario del CRM, no para el prospecto."""

PERSONALIZER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "Asunto, máximo 60 caracteres, sin mayúsculas sostenidas.",
        },
        "greeting": {"type": "string"},
        "opening": {"type": "string"},
        "observation": {
            "type": "string",
            "description": "Observación verificable sobre la empresa, basada en los datos dados.",
        },
        "value_proposition": {"type": "string"},
        "cta": {"type": "string", "description": "Pregunta de bajo compromiso."},
        "full_body": {
            "type": "string",
            "description": "Cuerpo completo del correo, listo para enviar.",
        },
        "reasoning": {"type": "string", "description": "Por qué este ángulo."},
    },
    "required": [
        "subject",
        "greeting",
        "opening",
        "observation",
        "value_proposition",
        "cta",
        "full_body",
        "reasoning",
    ],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ clasificación

CLASSIFIER_SYSTEM = """\
Clasificas respuestas de prospectos a correos comerciales en frío, en español.

Intenciones posibles:
- POSITIVE: muestra interés y quiere saber más.
- QUESTION: pregunta algo concreto sobre el servicio.
- PRICING: pregunta por precio, cotización o presupuesto.
- MEETING_REQUEST: propone o pide una reunión, llamada o cita.
- NEUTRAL: acusa recibo sin comprometerse ("gracias", "lo reviso").
- NEGATIVE: dice que no le interesa.
- UNSUBSCRIBE: pide explícitamente que no le vuelvan a escribir.
- WRONG_PERSON: dice que no es quien decide o remite a otra persona.
- OUT_OF_OFFICE: respuesta automática de ausencia.
- UNKNOWN: no encaja en ninguna de las anteriores.

CRITERIOS
- Distingue NEGATIVE de UNSUBSCRIBE: "ahora no nos interesa" es NEGATIVE; \
"no me escriban más" o "eliminen mis datos" es UNSUBSCRIBE. La diferencia \
importa porque UNSUBSCRIBE bloquea la dirección para siempre.
- Un "gracias, lo reviso y te cuento" es NEUTRAL, no POSITIVE.
- `confidence` es tu certeza real entre 0 y 1. Si el mensaje es ambiguo, \
ponla por debajo de 0.7: con esa confianza el CRM muestra la sugerencia \
atenuada y no ejecuta nada automáticamente.
- `summary` es una frase corta, en español, que le dice al comercial qué pasó.
- `suggested_reply_points` son 2 o 3 puntos que convendría cubrir al \
responder. No escribas la respuesta, solo los puntos."""

CLASSIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [i.value for i in ReplyIntent]},
        "confidence": {"type": "number"},
        "summary": {"type": "string"},
        "suggested_reply_points": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["intent", "confidence", "summary", "suggested_reply_points"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ contexto


def build_personalizer_input(
    *,
    company_name: str,
    category: str | None,
    city: str | None,
    website: str | None,
    rating: str | None,
    reviews: int | None,
    signals: list[str],
    service_name: str,
    value_proposition: str | None,
    problems_solved: list[str],
    contact_name: str | None,
    job_title: str | None,
    sender_name: str,
    tone: str,
    template_subject: str | None = None,
    template_body: str | None = None,
    previous_messages: list[str] | None = None,
) -> str:
    """Mensaje de usuario con los datos del prospecto.

    Todo lo variable vive aquí, fuera del bloque de sistema cacheado.
    """
    lines = [
        "EMPRESA",
        f"- Nombre: {company_name}",
        f"- Categoría: {category or 'desconocida'}",
        f"- Ciudad: {city or 'desconocida'}",
        f"- Sitio web: {website or 'no tiene o no se encontró'}",
    ]
    if rating:
        lines.append(f"- Calificación en Google: {rating} ({reviews or 0} reseñas)")

    if signals:
        lines.append("- Señales detectadas (datos verificados, puedes citarlas):")
        lines.extend(f"  · {s}" for s in signals)
    else:
        lines.append(
            "- Señales detectadas: ninguna. NO inventes un detalle sobre la empresa; "
            "usa una observación genérica y honesta."
        )

    lines += [
        "",
        "CONTACTO",
        f"- Nombre: {contact_name or 'desconocido, usa un saludo neutro'}",
        f"- Cargo: {job_title or 'desconocido'}",
        "",
        "SERVICIO QUE SE OFRECE",
        f"- Nombre: {service_name}",
        f"- Propuesta de valor: {value_proposition or 'no especificada'}",
    ]
    if problems_solved:
        lines.append("- Problemas que resuelve:")
        lines.extend(f"  · {p}" for p in problems_solved)

    lines += [
        "",
        "REMITENTE",
        f"- Nombre: {sender_name}",
        f"- Tratamiento: {'tuteo' if tone == 'tu' else 'usted'}",
    ]

    if template_subject or template_body:
        lines += [
            "",
            "PLANTILLA BASE (respeta su intención y estructura, mejórala):",
            f"Asunto: {template_subject or ''}",
            (template_body or "").strip(),
        ]

    if previous_messages:
        lines += ["", "HISTORIAL PREVIO CON ESTE PROSPECTO:"]
        lines.extend(f"- {m}" for m in previous_messages)

    return "\n".join(lines)


def build_classifier_input(
    *,
    subject: str,
    body: str,
    company_name: str,
    service_name: str | None,
    our_last_message: str | None,
) -> str:
    parts = [
        f"Le escribimos a {company_name} ofreciendo {service_name or 'un servicio'}.",
    ]
    if our_last_message:
        parts += ["", "NUESTRO ÚLTIMO MENSAJE:", our_last_message[:1500]]
    parts += [
        "",
        "RESPUESTA DEL PROSPECTO:",
        f"Asunto: {subject}",
        body[:4000],
    ]
    return "\n".join(parts)


# ------------------------------------------------------------------ planificador

PLANNER_SYSTEM = """\
Traduces la descripción de un negocio en términos de búsqueda para Google Maps.

QUÉ TE DAN
Lo que vende un profesional independiente en Colombia y a quién se lo vende, \
escrito en lenguaje corriente.

QUÉ DEVUELVES
Los tipos de negocio que buscaría en Google Maps para encontrar a esos \
clientes. Nada más.

REGLAS
- Tienen que ser categorías que Google Maps use de verdad: "panadería", \
"restaurante", "taller mecánico", "consultorio odontológico". No inventes \
categorías que nadie usa.
- En español de Colombia, en singular y en minúsculas.
- Entre dos y seis. Menos es mejor que más: cada una es una búsqueda de varios \
minutos.
- Concretas, no genéricas: "pyme", "empresa" o "negocio" no sirven para \
buscar nada.
- Si la descripción no da para deducir ningún tipo de negocio, devuelve la \
lista vacía. Es preferible a inventarse un público que el usuario no tiene."""

PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "business_types": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tipos de negocio a buscar en Google Maps.",
        },
        "reasoning": {
            "type": "string",
            "description": "Una frase explicando por qué esos y no otros.",
        },
    },
    "required": ["business_types", "reasoning"],
}


def build_planner_input(
    *,
    service_name: str,
    ideal_customer: str | None,
    value_proposition: str | None,
    problems_solved: list[str],
) -> str:
    partes = [f"SERVICIO QUE VENDE: {service_name}"]
    if ideal_customer:
        partes.append(f"CLIENTE IDEAL: {ideal_customer}")
    if value_proposition:
        partes.append(f"PROPUESTA DE VALOR: {value_proposition}")
    if problems_solved:
        partes.append("PROBLEMAS QUE RESUELVE:\n" + "\n".join(f"- {p}" for p in problems_solved))
    return "\n\n".join(partes)
