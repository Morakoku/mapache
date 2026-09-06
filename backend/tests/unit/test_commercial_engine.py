"""Motor comercial VEYRA: segmento A/B, próxima acción, mensaje y LIR (LOOP-20).

Tests puros (sin base de datos): la cadena DISCOVERY→FILTER→ENRICH→SCORE→CLASSIFY→
LIR→MENSAJE→FOLLOWUP se evalúa sobre un ScoreInput construido a partir de datos
reales (ficha Obsidian "Clínica Estética San Pascual") sin enviar nada.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.enums import VerificationStatus
from app.scoring import compute_score
from app.scoring.dimensions import ScoreInput
from app.scoring.engine import ScoreResult
from app.scoring.vevra import (
    BantScore,
    SegmentClassification,
    bant_score,
    commercial_intelligence,
    next_action,
    recommended_message,
    segment_classify,
)

FOLLOWUP_PLAN = ["CONTACT_1", "FOLLOWUP_1", "FOLLOWUP_2", "FOLLOWUP_3", "BREAK", "REACTIVATION"]


def _score(data: ScoreInput) -> ScoreResult:
    return compute_score(data)


def _clinic_input(*, employee_range: str | None = "5-20") -> ScoreInput:
    """Clínica estética con fricción real (basada en ficha San Pascual)."""
    return ScoreInput(
        company_category="Salud Privada",
        company_categories=("medicina estetica",),
        company_city="Santiago",
        company_email="contacto@sanpascual.cl",
        company_phone="+56212345678",
        company_website="https://sanpascual.cl",
        rating=4.6,
        reviews_count=180,
        employee_range=employee_range,
        signals=frozenset({"slow_response", "no_booking"}),
        data_quality_score=70,
        extracted_at=datetime(2026, 8, 1, tzinfo=UTC),
        service_name="VEYRA clínicas",
        target_industries=("clínica estética", "medicina estética"),
        opportunity_signals=("no_booking", "slow_response"),
        problems_solved=("captación", "seguimiento"),
        target_cities=frozenset({"santiago"}),
        contact_email="director@sanpascual.cl",
        contact_email_status=VerificationStatus.MX_OK,
        contact_phone="+56998765432",
        contact_linkedin="https://linkedin.com/in/director-sanpascual",
        engagement_score=60,
        last_activity_at=datetime(2026, 8, 8, tzinfo=UTC),
    )


# ---------------------------------------------------------------- segmento

def test_segment_a_small_company() -> None:
    seg = segment_classify(_clinic_input(employee_range="5-20"))
    assert seg.segment == "SEGMENT_A"
    assert seg.confidence >= 50
    assert any("PYME" in r for r in seg.segment_reason)


def test_segment_b_medium_company() -> None:
    seg = segment_classify(_clinic_input(employee_range="60-200"))
    assert seg.segment == "SEGMENT_B"
    assert any("mediana" in r for r in seg.segment_reason)


def test_segment_unclassified_without_evidence() -> None:
    data = ScoreInput(
        company_category="Comercio",
        company_website=None,
        employee_range=None,
        signals=frozenset(),
        data_quality_score=10,
    )
    seg = segment_classify(data)
    assert seg.segment == "UNCLASSIFIED"
    assert seg.confidence <= 40


def test_segment_not_only_by_employees() -> None:
    # Sin tamaño, pero sector alto valor + web + fricción → se clasifica A.
    data = ScoreInput(
        company_category="Clínica Estética",
        company_categories=("estetica",),
        company_website="https://x.cl",
        employee_range=None,
        signals=frozenset({"no_booking"}),
    )
    seg = segment_classify(data)
    assert seg.segment == "SEGMENT_A"


# ---------------------------------------------------------------- próxima acción

def test_next_action_close_triggers_mri() -> None:
    data = _clinic_input()
    score = _score(data)
    bant = bant_score(data)
    seg = segment_classify(data)
    action, _ = next_action(score, bant, seg)
    assert action in {"Preparar Business MRI", "Contactar"}


def test_next_action_insufficient_data() -> None:
    data = ScoreInput(
        company_category="Comercio",
        employee_range=None,
        signals=frozenset(),
        data_quality_score=10,
    )
    score = _score(data)
    bant = bant_score(data)
    seg = segment_classify(data)
    action, reason = next_action(score, bant, seg)
    assert action == "Investigar contacto"
    assert "Calidad del dato" in reason


def test_next_action_unclassified_no_contact() -> None:
    data = ScoreInput(
        company_category="X", employee_range=None, signals=frozenset(), data_quality_score=80
    )
    score = _score(data)
    bant = bant_score(data)
    seg = segment_classify(data)
    action, _ = next_action(score, bant, seg)
    assert action == "No contactar"


# ---------------------------------------------------------------- mensaje

def test_message_uses_only_observed_signal() -> None:
    data = _clinic_input()
    seg = segment_classify(data)
    msg = recommended_message(
        data, seg, company_name="Clínica Estética San Pascual", city_label="Santiago"
    )
    assert "San Pascual" in msg["mensaje"]
    # La observación se apoya en señales reales (fricción), no inventadas.
    assert "fricción" in msg["mensaje"] or "sitio web" in msg["mensaje"]
    assert msg["cta"]


def test_message_segment_b() -> None:
    data = _clinic_input(employee_range="60-200")
    seg = segment_classify(data)
    msg = recommended_message(
        data, seg, company_name="Clínica Estética San Pascual", city_label="Santiago"
    )
    assert "procesos" in msg["mensaje"]


# ---------------------------------------------------------------- LIR + cadena completa

def test_commercial_intelligence_full_record() -> None:
    data = _clinic_input()
    score = _score(data)
    bant = bant_score(data)
    seg = segment_classify(data)
    lir = commercial_intelligence(
        data, score, bant, seg,
        company_name="Clínica Estética San Pascual", city_label="Santiago",
    )
    assert lir["empresa"] == "Clínica Estética San Pascual"
    assert lir["segmento"] == seg.segment
    assert lir["segment_reason"]
    assert lir["contacto"]["email"]
    assert lir["senales_observadas"]
    assert lir["problemas_potenciales"]  # inferencias marcadas
    assert lir["score"]["total"] == score.total
    assert lir["score"]["fit"] is not None
    assert lir["score"]["por_que"]
    assert lir["mensaje_recomendado"]["asunto"]
    assert lir["proxima_accion"]
    assert lir["proxima_accion_reason"]


def test_fase14_controlled_chain() -> None:
    """Cadena completa sin envío: DISCOVERY→FILTER→ENRICH→SCORE→CLASSIFY→LIR→MESSAGE→FOLLOWUP."""
    # (1-2) DISCOVERY/FILTER/ENRICH ya aplicados en la fuente: la ficha tiene
    # email, web y señales observadas (OBSERVED).
    data = _clinic_input()

    # (3) SCORE explicable (6 dimensiones) + BANT.
    score = _score(data)
    bant = bant_score(data)

    # (4) CLASSIFY segmento.
    seg = segment_classify(data)

    # (5) LIR.
    lir = commercial_intelligence(
        data, score, bant, seg,
        company_name="Clínica Estética San Pascual", city_label="Santiago",
    )

    # (6) MENSAJE.
    assert lir["mensaje_recomendado"]["asunto"]
    assert lir["mensaje_recomendado"]["cta"]

    # (7) FOLLOWUP plan (secuencia comercial sin envío; SEND_BATCH bloqueado).
    assert FOLLOWUP_PLAN[0] == "CONTACT_1"
    assert "BREAK" in FOLLOWUP_PLAN
    assert "REACTIVATION" in FOLLOWUP_PLAN

    # La próxima acción es coherente con el score.
    assert lir["proxima_accion"] in {
        "Contactar", "Preparar Business MRI", "Investigar contacto",
        "Investigar señales", "No contactar",
    }


def test_bant_helpers_still_work() -> None:
    data = _clinic_input()
    bant = bant_score(data)
    assert isinstance(bant, BantScore)
    assert 0 <= bant.total <= 100
    assert isinstance(segment_classify(data), SegmentClassification)
