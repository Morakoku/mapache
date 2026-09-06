"""Tests de la vista comercial BANT de VEYRA (LOOP-20).

Verifica que el score BANT (4x25) se deriva correctamente del ScoreInput de
Mapache: Budget (tamaño+sector), Authority (contacto decisor), Need (señales de
fricción), Timing (frescura), y que los umbrales SQL>70 / cierre>85 funcionan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.enums import VerificationStatus
from app.scoring.dimensions import ScoreInput
from app.scoring.vevra import BantScore, bant_score

NOW = datetime(2026, 8, 9, tzinfo=UTC)


def _input(**overrides) -> ScoreInput:
    base = {
        "rating": Decimal("4.4"),
        "reviews_count": 45,
        "extracted_at": NOW,
    }
    base.update(overrides)
    return ScoreInput(**base)


class TestBudget:
    def test_pyme_en_rango_icp_y_sector_alto_valor(self) -> None:
        data = _input(
            employee_range="10-150 empleados",
            company_category="Inmobiliaria",
        )
        score = bant_score(data, now=NOW)
        assert score.budget == 100  # 60 tamaño ICP + 40 sector alto valor

    def test_micro_empresa_no_prioritaria(self) -> None:
        data = _input(employee_range="1-9", company_category="Panadería")
        score = bant_score(data, now=NOW)
        assert score.budget <= 35

    def test_tamaño_desconocido_no_infla(self) -> None:
        data = _input(employee_range=None)
        score = bant_score(data, now=NOW)
        assert score.budget <= 50


class TestAuthority:
    def test_contacto_decisor_completo(self) -> None:
        data = _input(
            contact_email="juan@inmobiliaria.co",
            contact_email_status=VerificationStatus.MX_OK,
            contact_linkedin="https://linkedin.com/in/juan",
            contact_phone="+573001234567",
        )
        score = bant_score(data, now=NOW)
        assert score.authority == 100

    def test_sin_contacto_cero(self) -> None:
        score = bant_score(_input(), now=NOW)
        assert score.authority == 0

    def test_correo_de_rol_autoridad_parcial(self) -> None:
        data = _input(
            contact_email="info@inmobiliaria.co",
            contact_email_status=VerificationStatus.MX_OK,
        )
        score = bant_score(data, now=NOW)
        assert score.authority == 50
        assert any("rol" in r for r in score.reasons["authority"])


class TestNeed:
    def test_no_website_y_señal_del_servicio(self) -> None:
        data = _input(
            signals=frozenset({"no_website", "slow_response"}),
            opportunity_signals=("no_website",),
        )
        score = bant_score(data, now=NOW)
        assert score.need >= 70

    def test_sin_dolor_cero(self) -> None:
        score = bant_score(_input(signals=frozenset()), now=NOW)
        assert score.need == 0


class TestTiming:
    def test_fresco_cien(self) -> None:
        score = bant_score(_input(extracted_at=NOW), now=NOW)
        assert score.timing == 100

    def test_30_dias_mitad_vida(self) -> None:
        data = _input(extracted_at=NOW - timedelta(days=30))
        score = bant_score(data, now=NOW)
        assert score.timing == 50

    def test_actividad_reciente_bonifica(self) -> None:
        data = _input(extracted_at=NOW - timedelta(days=30), last_activity_at=NOW)
        score = bant_score(data, now=NOW)
        assert score.timing == 70  # 50 + 20 de actividad


class TestUmbrales:
    def test_ideal_pyme_es_sql_y_cierre(self) -> None:
        data = _input(
            employee_range="10-150",
            company_category="Clínica",
            contact_email="gerente@clinica.co",
            contact_email_status=VerificationStatus.MX_OK,
            contact_linkedin="https://linkedin.com/in/gerente",
            contact_phone="+5712345678",
            signals=frozenset({"no_website"}),
            opportunity_signals=("no_website",),
            extracted_at=NOW,
        )
        score = bant_score(data, now=NOW)
        assert score.is_sql is True
        assert score.is_close is True
        assert score.total > 70

    def test_micro_sin_contacto_ni_dolor_no_es_sql(self) -> None:
        data = _input(
            employee_range="1-9",
            company_category="Panadería",
            extracted_at=NOW - timedelta(days=90),
        )
        score = bant_score(data, now=NOW)
        assert score.is_sql is False
        assert score.total < 30

    def test_datos_desconocidos_no_inflan(self) -> None:
        score = bant_score(ScoreInput(), now=NOW)
        assert score.is_sql is False
        assert score.total <= 30

    def test_umbral_estricto(self) -> None:
        sql = BantScore(70, 70, 70, 70, 70, reasons={})
        assert sql.is_sql is False  # >70, no >=70
        close = BantScore(86, 86, 86, 86, 86, reasons={})
        assert close.is_close is True

    def test_total_es_media_de_componentes(self) -> None:
        data = _input(
            employee_range="10-150",
            company_category="Logística",
            contact_email="a@b.co",
            contact_email_status=VerificationStatus.MX_OK,
            signals=frozenset({"no_website"}),
        )
        score = bant_score(data, now=NOW)
        mean = round((score.budget + score.authority + score.need + score.timing) / 4.0)
        assert score.total == mean
