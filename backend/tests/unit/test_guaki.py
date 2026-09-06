"""Guaki Filter (LOOP-22): clasificación de oportunidad de presencia digital.

Tests puros (sin base de datos). Casos mínimos requeridos: relevante, no
relevante, sin web, con web, con redes, información incompleta/suficiente, datos
faltantes, combinación de señales, determinismo y razones explicables.
"""

from __future__ import annotations

from app.scoring.dimensions import ScoreInput
from app.scoring.guaki import GuakiOpportunity, guaki_opportunity


def _panaderia(**overrides: object) -> ScoreInput:
    """Base: panadería local en Soacha con actividad y contacto."""
    base = {
        "company_category": "Panadería",
        "company_city": "Soacha",
        "company_phone": "+573001112233",
        "company_email": "info@panaderia.co",
        "rating": 4.4,
        "reviews_count": 45,
        "data_quality_score": 60,
        "signals": frozenset({"no_website"}),
    }
    base.update(overrides)
    return ScoreInput(**base)


def test_relevant_high() -> None:
    g = guaki_opportunity(_panaderia())
    assert g.opportunity == "HIGH"
    assert g.is_relevant


def test_not_relevant_without_local_identity() -> None:
    g = guaki_opportunity(ScoreInput(company_category="", company_city="", rating=4.0))
    assert g.opportunity == "NOT_RELEVANT"


def test_not_relevant_without_activity() -> None:
    g = guaki_opportunity(
        ScoreInput(company_category="Panadería", company_city="Soacha")
    )
    assert g.opportunity == "NOT_RELEVANT"


def test_with_web_and_complete_info_is_low() -> None:
    g = guaki_opportunity(
        _panaderia(
            company_website="https://panaderia.co",
            data_quality_score=80,
            signals=frozenset(),
        )
    )
    assert g.opportunity == "LOW"
    assert any("presencia digital suficiente" in r for r in g.reasons)


def test_no_web_observed_but_no_contact_is_medium() -> None:
    g = guaki_opportunity(
        _panaderia(company_phone=None, company_email=None, contact_email=None)
    )
    assert g.opportunity == "MEDIUM"
    assert any("falta contacto" in r for r in g.reasons)


def test_social_presence_detected_adds_reason() -> None:
    g = guaki_opportunity(_panaderia(signals=frozenset({"no_website", "instagram"})))
    assert any("redes detectada" in r for r in g.reasons)


def test_social_missing_is_not_claimed() -> None:
    g = guaki_opportunity(_panaderia(signals=frozenset({"no_website"})))
    # No se afirma "no tiene redes": solo se dice que no se verificó.
    assert not any("no tiene redes" in r for r in g.reasons)
    assert any("redes no verificada" in r for r in g.reasons)


def test_incomplete_info_flagged() -> None:
    g = guaki_opportunity(_panaderia(data_quality_score=30))
    assert any("incompleta o parcial" in r for r in g.reasons)


def test_sufficient_info_flagged() -> None:
    g = guaki_opportunity(
        _panaderia(data_quality_score=85, signals=frozenset())
    )
    assert any("información pública suficiente" in r for r in g.reasons)


def test_missing_website_evidence_not_claimed_as_missing() -> None:
    # Sin web ni señal no_website: no se afirma que no tenga web.
    g = guaki_opportunity(_panaderia(signals=frozenset()))
    assert not any("no se encontró sitio web" in r for r in g.reasons)
    assert any("no se pudo verificar si tiene sitio web" in r for r in g.reasons)


def test_combined_signals_high() -> None:
    g = guaki_opportunity(
        _panaderia(
            signals=frozenset({"no_website", "instagram", "facebook"}),
            data_quality_score=40,
        )
    )
    assert g.opportunity == "HIGH"


def test_deterministic() -> None:
    a = guaki_opportunity(_panaderia())
    b = guaki_opportunity(_panaderia())
    assert a.opportunity == b.opportunity
    assert a.reasons == b.reasons


def test_reasons_explainable() -> None:
    g = guaki_opportunity(_panaderia())
    assert isinstance(g, GuakiOpportunity)
    assert g.reasons
    for r in g.reasons:
        assert isinstance(r, str) and r.strip()
