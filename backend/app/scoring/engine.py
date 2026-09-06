"""Motor del prospect score (§8).

Combina las seis dimensiones con los pesos de `app_settings.score_weights` y
guarda el desglose. El desglose es la mitad del módulo: un score sin explicación
no se puede corregir ni discutir, y acaba ignorado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.settings import DEFAULT_SCORE_WEIGHTS
from app.scoring.dimensions import (
    DimensionScore,
    ScoreInput,
    contactability,
    data_quality,
    fit,
    intent,
    opportunity,
    timing,
)

# Nombre legible de cada dimensión, para la UI y para el propio desglose.
DIMENSION_LABEL: dict[str, str] = {
    "fit": "Encaje",
    "opportunity": "Oportunidad",
    "contactability": "Contactabilidad",
    "data_quality": "Calidad del dato",
    "intent": "Interés mostrado",
    "timing": "Frescura",
}


@dataclass(frozen=True, slots=True)
class ScoreResult:
    total: int
    breakdown: dict[str, Any]
    computed_at: datetime

    @property
    def summary(self) -> str:
        """La razón de más peso, para enseñarla sin abrir el desglose."""
        dims: dict[str, Any] = self.breakdown["dimensions"]
        best = max(dims.items(), key=lambda item: item[1]["contribution"])
        reasons: list[str] = best[1]["reasons"]
        return reasons[0] if reasons else DIMENSION_LABEL.get(best[0], best[0])


def compute_score(
    data: ScoreInput, weights: dict[str, float] | None = None, now: datetime | None = None
) -> ScoreResult:
    """Calcula el score y el desglose de un prospecto.

    Los pesos llegan de la configuración para poder ajustarlos sin desplegar.
    Si vienen incompletos o no suman 1, se normalizan: es preferible un score
    coherente a rechazar la operación por una configuración a medio editar.
    """
    now = now or datetime.now(UTC)

    scores: dict[str, DimensionScore] = {
        "fit": fit(data),
        "opportunity": opportunity(data),
        "contactability": contactability(data),
        "data_quality": data_quality(data),
        "intent": intent(data),
        "timing": timing(data, now),
    }

    effective = _normalized_weights(weights)

    dimensions: dict[str, Any] = {}
    total = 0.0
    for key, dimension in scores.items():
        weight = effective[key]
        contribution = dimension.value * weight
        total += contribution
        dimensions[key] = {
            "label": DIMENSION_LABEL[key],
            "value": dimension.value,
            "weight": round(weight, 4),
            "contribution": round(contribution, 2),
            "reasons": dimension.reasons,
        }

    return ScoreResult(
        total=round(total),
        breakdown={"dimensions": dimensions, "computed_at": now.isoformat()},
        computed_at=now,
    )


def _normalized_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """Pesos utilizables a partir de lo que haya en la configuración."""
    raw = {**DEFAULT_SCORE_WEIGHTS, **(weights or {})}
    usable = {
        key: float(raw.get(key, 0.0))
        for key in DEFAULT_SCORE_WEIGHTS
        if isinstance(raw.get(key), (int, float)) and float(raw.get(key, 0.0)) >= 0
    }
    for key in DEFAULT_SCORE_WEIGHTS:
        usable.setdefault(key, DEFAULT_SCORE_WEIGHTS[key])

    total = sum(usable.values())
    if total <= 0:
        return dict(DEFAULT_SCORE_WEIGHTS)
    return {key: value / total for key, value in usable.items()}
