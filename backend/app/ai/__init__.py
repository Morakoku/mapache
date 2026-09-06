"""Capa de IA (Fase 8).

Todo lo de aquí es opcional: sin `ANTHROPIC_API_KEY` el CRM funciona igual,
renderizando plantillas y clasificando respuestas por reglas.
"""

from __future__ import annotations

from app.ai.classifier import Classification, classify, classify_with_rules
from app.ai.client import AIResult, AIUnavailableError, is_configured
from app.ai.personalizer import AIDraft, personalize

__all__ = [
    "AIDraft",
    "AIResult",
    "AIUnavailableError",
    "Classification",
    "classify",
    "classify_with_rules",
    "is_configured",
    "personalize",
]
