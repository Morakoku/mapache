"""Motor del prospect score (Módulo 8).

Se prueba dimensión por dimensión con casos concretos, y luego la combinación.
El motor es puro, así que aquí no hace falta base de datos: si un día un test
necesita una, es señal de que la lógica se ha escapado al servicio.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.enums import VerificationStatus
from app.models.settings import DEFAULT_SCORE_WEIGHTS
from app.scoring import ScoreInput, compute_score
from app.scoring.dimensions import (
    contactability,
    data_quality,
    fit,
    intent,
    opportunity,
    timing,
)

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def make_input(**overrides: object) -> ScoreInput:
    base: dict[str, object] = {
        "company_category": "Panadería",
        "company_city": "Medellín",
        "target_industries": ("panaderia",),
        "target_cities": frozenset({"medellin"}),
        "extracted_at": NOW - timedelta(days=1),
    }
    base.update(overrides)
    return ScoreInput(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------- fit


def test_fit_compara_categoria_sin_tildes() -> None:
    """`Panadería` y `panaderia` son la misma categoría.

    Los datos vienen de Maps con tildes y las industrias objetivo las escribe
    el usuario a mano: comparar en crudo haría que nunca coincidieran.
    """
    result = fit(make_input())
    assert result.value >= 40
    assert any("categoría coincide" in reason for reason in result.reasons)


def test_fit_sin_ciudades_objetivo_no_penaliza() -> None:
    """Un servicio sin búsquedas todavía no puede penalizar por ciudad.

    Antes sí lo hacía: los 20 puntos de ciudad eran inalcanzables mientras el
    servicio no tuviera búsquedas, así que todos sus prospectos nacían con el
    encaje deflactado por un dato que nadie había podido rellenar.
    """
    sin_ciudad = fit(make_input(target_cities=frozenset()))
    con_ciudad_mala = fit(make_input(company_city="Cali"))
    assert sin_ciudad.value > con_ciudad_mala.value


def test_fit_ignora_la_calificacion_cuando_no_existe() -> None:
    """Sin calificación en Maps no se juzga la reputación: no cuenta como fallo.

    La consecuencia buscada es que una empresa sin calificación empate con una
    de 4,5 estrellas si el resto encaja igual. Es deliberado: el criterio se
    reparte solo entre lo evaluable, y castigar la ausencia de reseñas sería
    puntuar la antigüedad del negocio, no su encaje.
    """
    sin_rating = fit(make_input(reviews_count=120))
    mala = fit(make_input(reviews_count=120, rating=Decimal("2.9")))
    buena = fit(make_input(reviews_count=120, rating=Decimal("4.5")))
    assert mala.value < sin_rating.value
    assert sin_rating.value == buena.value


def test_fit_reconoce_tamano_por_resenas() -> None:
    grande = fit(make_input(reviews_count=120))
    recien_abierto = fit(make_input(reviews_count=2))
    assert grande.value > recien_abierto.value


def test_fit_perfecto_solo_con_todos_los_criterios() -> None:
    todo = fit(
        make_input(
            reviews_count=120,
            rating=Decimal("4.7"),
        )
    )
    assert todo.value == 100


def test_un_solo_criterio_no_da_encaje_perfecto() -> None:
    """El suelo del denominador evita la confianza barata.

    Sin industrias objetivo, sin ciudades y sin calificación, lo único
    evaluable es el tamaño: acertarlo no puede valer un 100.
    """
    result = fit(
        make_input(
            target_industries=(),
            target_cities=frozenset(),
            reviews_count=120,
        )
    )
    assert result.value == 33


# ------------------------------------------------------------- opportunity


def test_opportunity_sin_senales_declaradas_lo_dice() -> None:
    """Si el servicio no declara señales, el 0 tiene que explicarse.

    Sin la razón, un 0 aquí se lee como "esta empresa no sirve" cuando lo que
    falta es configurar el servicio.
    """
    result = opportunity(make_input(signals=frozenset({"no_website"})))
    assert result.value == 0
    assert "no declara" in result.reasons[0]


def test_opportunity_suma_por_senal() -> None:
    result = opportunity(
        make_input(
            opportunity_signals=("no_website", "no_whatsapp", "not_responsive"),
            signals=frozenset({"no_whatsapp", "not_responsive"}),
        )
    )
    assert result.value == 50


def test_no_website_cuenta_doble_para_servicios_web() -> None:
    """La señal más directa para quien vende webs no puede valer lo mismo."""
    comun = opportunity(
        make_input(
            service_name="Consultoría contable",
            opportunity_signals=("no_website",),
            signals=frozenset({"no_website"}),
        )
    )
    web = opportunity(
        make_input(
            service_name="Diseño de páginas web",
            opportunity_signals=("no_website",),
            signals=frozenset({"no_website"}),
        )
    )
    assert comun.value == 25
    assert web.value == 50


# ----------------------------------------------------------- contactability


def test_email_rebotado_anula_la_contactabilidad() -> None:
    """Da igual lo bueno que sea el prospecto: escribirle hace daño."""
    result = contactability(
        make_input(
            contact_email="hola@negocio.co",
            contact_email_status=VerificationStatus.MX_OK,
            contact_linkedin="https://linkedin.com/in/x",
            has_bounced=True,
        )
    )
    assert result.value == 0
    assert "rebotó" in result.reasons[0]


def test_email_invalido_anula_la_contactabilidad() -> None:
    result = contactability(
        make_input(
            contact_email="roto@",
            contact_email_status=VerificationStatus.INVALID,
        )
    )
    assert result.value == 0


def test_buzon_generico_puntua_menos_que_persona() -> None:
    persona = contactability(
        make_input(
            contact_email="ana@negocio.co",
            contact_email_status=VerificationStatus.MX_OK,
        )
    )
    generico = contactability(
        make_input(
            contact_email="info@negocio.co",
            contact_email_status=VerificationStatus.MX_OK,
            contact_is_role_email=True,
        )
    )
    assert persona.value == 70
    assert generico.value == 50


def test_sin_email_no_hay_contactabilidad() -> None:
    result = contactability(make_input(contact_phone="+573001112233"))
    assert result.value == 0


# ------------------------------------------------------------------ resto


def test_intent_deriva_del_engagement() -> None:
    assert intent(make_input(engagement_score=0)).value == 0
    assert intent(make_input(engagement_score=40)).value == 48
    assert intent(make_input(engagement_score=200)).value == 100


def test_timing_decae_con_los_dias() -> None:
    reciente = timing(make_input(extracted_at=NOW - timedelta(days=1)), NOW)
    viejo = timing(make_input(extracted_at=NOW - timedelta(days=90)), NOW)
    assert reciente.value > 90
    assert viejo.value < 10


def test_timing_premia_actividad_de_las_ultimas_48h() -> None:
    con_actividad = timing(
        make_input(
            extracted_at=NOW - timedelta(days=10),
            last_activity_at=NOW - timedelta(hours=3),
        ),
        NOW,
    )
    sin_actividad = timing(make_input(extracted_at=NOW - timedelta(days=10)), NOW)
    assert con_actividad.value == sin_actividad.value + 20


def test_data_quality_usa_lo_que_calculo_el_enriquecimiento() -> None:
    assert data_quality(make_input(data_quality_score=77)).value == 77


# ----------------------------------------------------------------- motor


def test_score_total_pondera_las_seis_dimensiones() -> None:
    result = compute_score(
        make_input(
            rating=Decimal("4.6"),
            reviews_count=150,
            data_quality_score=100,
            engagement_score=50,
            contact_email="ana@negocio.co",
            contact_email_status=VerificationStatus.MX_OK,
            contact_phone="+573001112233",
            contact_linkedin="https://linkedin.com/in/ana",
            service_name="Diseño de páginas web",
            opportunity_signals=("no_website",),
            signals=frozenset({"no_website"}),
        ),
        None,
        NOW,
    )

    assert 80 <= result.total <= 100
    assert set(result.breakdown["dimensions"]) == set(DEFAULT_SCORE_WEIGHTS)


def test_el_desglose_explica_cada_dimension() -> None:
    """El desglose es la mitad del módulo: un número sin porqué no se corrige."""
    result = compute_score(make_input(), None, NOW)
    for key, detail in result.breakdown["dimensions"].items():
        assert detail["label"], key
        assert detail["reasons"], f"{key} no explica su valor"
        assert 0 <= detail["value"] <= 100


def test_un_prospecto_sin_datos_tambien_explica_sus_ceros() -> None:
    """El caso peor es el que más necesita explicación.

    Un prospecto recién extraído, con el servicio a medio configurar, saca
    ceros en casi todo. Si esos ceros salen mudos, el usuario no sabe si el
    prospecto es malo o si le falta configurar el servicio —que es lo que pasa
    de verdad—.
    """
    result = compute_score(ScoreInput(), None, NOW)
    for key, detail in result.breakdown["dimensions"].items():
        assert detail["reasons"], f"{key} devuelve un valor mudo"


def test_pesos_a_medio_configurar_no_rompen_el_calculo() -> None:
    """Editar un peso desde la configuración no puede dejar el CRM sin scores."""
    parcial = compute_score(make_input(), {"fit": 1.0}, NOW)
    vacio = compute_score(make_input(), {}, NOW)
    cero = compute_score(make_input(), dict.fromkeys(DEFAULT_SCORE_WEIGHTS, 0.0), NOW)

    assert 0 <= parcial.total <= 100
    assert vacio.total == compute_score(make_input(), None, NOW).total
    assert cero.total == vacio.total  # cae a los pesos por defecto


def test_pesos_se_normalizan_aunque_no_sumen_uno() -> None:
    doble = compute_score(make_input(), {k: v * 2 for k, v in DEFAULT_SCORE_WEIGHTS.items()}, NOW)
    normal = compute_score(make_input(), None, NOW)
    assert doble.total == normal.total


def test_summary_devuelve_la_razon_de_la_dimension_que_mas_aporta() -> None:
    """Con el encaje a cero, la oportunidad pasa a ser lo que sostiene el score."""
    result = compute_score(
        make_input(
            target_industries=(),
            target_cities=frozenset(),
            service_name="Diseño de páginas web",
            opportunity_signals=("no_website",),
            signals=frozenset({"no_website"}),
        ),
        None,
        NOW,
    )
    assert "no_website" in result.summary


@pytest.mark.parametrize("engagement", [0, 10, 45, 100])
def test_el_score_siempre_cae_en_el_rango(engagement: int) -> None:
    result = compute_score(make_input(engagement_score=engagement), None, NOW)
    assert 0 <= result.total <= 100
