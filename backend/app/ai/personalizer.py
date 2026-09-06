"""Módulo 11 — personalización de correos con IA.

Genera un borrador; **no envía nada**. El envío sigue siendo un segundo paso
explícito con preview editable (§12.2), y `was_edited_by_user` registra si el
usuario tocó el texto — así se puede medir con el tiempo si la IA vale.

Sin clave de Anthropic esto no rompe nada: el llamante recibe `None` y usa la
plantilla renderizada de siempre.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import client, prompts
from app.ai.classifier import word_count
from app.core.logging import get_logger
from app.enrichment.signal_detector import SIGNAL_LABELS
from app.models.company import CompanySignal
from app.models.email import EmailTemplate
from app.models.lead import Lead
from app.models.settings import AppSettings

logger = get_logger(__name__)

# Tope del diseño. Si el modelo se pasa, se avisa en la UI en vez de recortar:
# cortar un correo a mitad de frase es peor que uno un poco largo.
MAX_WORDS = 120


@dataclass(frozen=True, slots=True)
class AIDraft:
    subject: str
    body_text: str
    reasoning: str
    model: str
    observation: str
    cta: str
    estimated_cost_usd: float
    warnings: list[str]


async def personalize(
    session: AsyncSession,
    lead: Lead,
    *,
    settings: AppSettings,
    template: EmailTemplate | None = None,
    tone: str | None = None,
    goal: str | None = None,
) -> AIDraft | None:
    """Redacta un borrador para este prospecto.

    Devuelve `None` cuando la IA está apagada o no disponible: es la señal de
    "sigue con la plantilla", no un error.
    """
    credentials = client.credentials_from(settings)
    if not settings.ai_enabled or credentials is None:
        return None

    signals = await _signal_labels(session, lead.company_id)
    contact = lead.contact
    company = lead.company

    user_message = prompts.build_personalizer_input(
        company_name=company.name,
        category=company.category,
        city=company.city,
        website=company.website,
        rating=str(company.rating) if company.rating is not None else None,
        reviews=company.reviews_count,
        signals=signals,
        service_name=lead.service.name if lead.service else "el servicio",
        value_proposition=lead.service.value_proposition if lead.service else None,
        problems_solved=list(lead.service.problems_solved) if lead.service else [],
        contact_name=contact.display_name if contact else None,
        job_title=contact.job_title if contact else None,
        sender_name=settings.sender_name or "",
        tone=tone or settings.ai_tone,
        template_subject=template.subject if template else None,
        template_body=template.body_text if template else None,
    )
    if goal:
        user_message += f"\n\nOBJETIVO CONCRETO DE ESTE CORREO:\n{goal}"

    try:
        result = await client.complete_json(
            system=prompts.PERSONALIZER_SYSTEM,
            user=user_message,
            schema=prompts.PERSONALIZER_SCHEMA,
            credentials=credentials,
            effort="medium",
        )
    except client.AIUnavailableError as exc:
        logger.info("personalization_unavailable", lead=str(lead.id), reason=exc.code)
        return None

    body = str(result.data.get("full_body", "")).strip()
    subject = str(result.data.get("subject", "")).strip()
    if not body or not subject:
        return None

    warnings = _review(body, signals)

    return AIDraft(
        subject=subject,
        body_text=body + "\n",
        reasoning=str(result.data.get("reasoning", "")),
        model=result.model,
        observation=str(result.data.get("observation", "")),
        cta=str(result.data.get("cta", "")),
        estimated_cost_usd=result.estimated_cost_usd,
        warnings=warnings,
    )


def _review(body: str, signals: list[str]) -> list[str]:
    """Avisos sobre el borrador, para que el usuario revise antes de enviar.

    No se corrige el texto automáticamente: quien firma el correo es el
    usuario, y editar a sus espaldas lo dejaría enviando algo que no leyó.
    """
    warnings: list[str] = []

    words = word_count(body)
    if words > MAX_WORDS:
        warnings.append(f"El borrador tiene {words} palabras; el objetivo son {MAX_WORDS}.")

    lowered = body.lower()
    for cliche in ("espero que te encuentres bien", "espero que estés bien", "líderes en"):
        if cliche in lowered:
            warnings.append(f"Contiene una fórmula vacía: «{cliche}».")

    if not signals:
        warnings.append(
            "No había señales detectadas de esta empresa: revisa que la observación "
            "no afirme nada que no puedas sostener."
        )

    return warnings


async def _signal_labels(session: AsyncSession, company_id: uuid.UUID) -> list[str]:
    """Señales en texto legible, con su evidencia.

    Se le pasan al modelo así para que pueda citarlas tal cual; darle la clave
    interna (`no_website`) produciría observaciones robóticas.
    """
    result = await session.execute(
        select(CompanySignal.signal_key, CompanySignal.value).where(
            CompanySignal.company_id == company_id
        )
    )
    labels = []
    for key, value in result:
        label = SIGNAL_LABELS.get(key, key)
        detail = ", ".join(f"{k}: {v}" for k, v in (value or {}).items())
        labels.append(f"{label} ({detail})" if detail else label)
    return labels
