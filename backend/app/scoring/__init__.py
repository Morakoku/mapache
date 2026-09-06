"""Prospect score (Módulo 8)."""

from app.scoring.dimensions import DimensionScore, ScoreInput
from app.scoring.engine import DIMENSION_LABEL, ScoreResult, compute_score

__all__ = [
    "DIMENSION_LABEL",
    "DimensionScore",
    "ScoreInput",
    "ScoreResult",
    "compute_score",
]
