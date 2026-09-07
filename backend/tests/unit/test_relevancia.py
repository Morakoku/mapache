"""Que la búsqueda traiga exactamente lo que se pidió.

Google devuelve de todo alrededor del término: buscando "panadería" entran
cafeterías, supermercados y ferreterías mal etiquetadas, y el radio se salta
los límites municipales. Sin filtrar, la base se llena de empresas que hay que
descartar a mano una por una — que es justo lo que el CRM debía evitar.
"""

from __future__ import annotations

import pytest

from app.core.enums import SourceType
from app.scrapers.base import RawPlace, SearchQuery


def _place(name: str, **kw: object) -> RawPlace:
    return RawPlace(
        name=name,
        source=SourceType.GOOGLE_MAPS,
        # Con municipio, como las direcciones reales de Google en Colombia.
        address=kw.pop("address", "Calle 10 #43-20, Medellín"),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


def _query(**kw: object) -> SearchQuery:
    base: dict[str, object] = {"business_type": "panadería", "city": "Medellín"}
    return SearchQuery(**{**base, **kw})  # type: ignore[arg-type]


# ------------------------------------------------------------------ tipo


def test_la_categoria_correcta_pasa() -> None:
    assert _query().matches_filters(_place("Pan de Ayer", category="Panadería"))


def test_las_tildes_no_importan() -> None:
    """El usuario escribe "panaderia" y Google devuelve "Panadería"."""
    query = _query(business_type="panaderia")
    assert query.matches_filters(_place("Pan de Ayer", category="Panadería"))


def test_una_cafeteria_no_es_una_panaderia() -> None:
    """El caso concreto: se pidió panadería y llegó un café."""
    reason = _query().rejection_reason(_place("Café del Parque", category="Cafetería"))
    assert reason == "tipo_de_negocio"


def test_un_supermercado_tampoco() -> None:
    assert not _query().matches_filters(_place("Éxito Wow", category="Supermercado"))


def test_vale_con_que_lo_diga_el_nombre() -> None:
    """Muchas fichas tienen la categoría mal puesta pero el nombre lo dice."""
    assert _query().matches_filters(_place("Panadería La Espiga", category="Tienda"))


def test_una_de_las_categorias_secundarias_basta() -> None:
    assert _query().matches_filters(
        _place("La Espiga", category="Restaurante", categories=["Restaurante", "Panadería"])
    )


def test_con_varias_palabras_basta_una() -> None:
    """ "restaurante italiano" acepta algo categorizado solo "Restaurante"."""
    query = _query(business_type="restaurante italiano")
    assert query.matches_filters(_place("Da Vinci", category="Restaurante"))


def test_las_palabras_clave_tambien_cuentan() -> None:
    query = _query(business_type="tienda", keywords=["ferretería"])
    assert query.matches_filters(_place("El Tornillo", category="Ferretería"))


def test_las_palabras_cortas_no_filtran() -> None:
    """'bar' como subcadena aparece en 'barbería', 'barrio'…

    Aceptarlas devolvería el mismo ruido que no filtrar, así que un término de
    menos de cuatro letras no restringe nada.
    """
    query = _query(business_type="bar")
    # Sin palabras significativas, no se descarta por tipo.
    assert query.matches_filters(_place("Barbería El Corte", category="Barbería"))


# ------------------------------------------------------------------ ciudad


def test_otra_ciudad_se_descarta() -> None:
    """El radio de Google se salta los límites municipales."""
    reason = _query().rejection_reason(
        _place(
            "Pan Envigado",
            category="Panadería",
            city="Envigado",
            address="Cra 43 #30, Envigado",
        )
    )
    assert reason == "ciudad"


def test_la_misma_ciudad_pasa() -> None:
    assert _query().matches_filters(
        _place("Pan Medellín", category="Panadería", city="Medellín", address="Calle 10, Medellín")
    )


def test_sin_ciudad_no_se_descarta() -> None:
    """Falta el dato, no lo contradice: descartar sería tirar un buen prospecto."""
    assert _query().matches_filters(_place("Pan Sin Dirección", category="Panadería", address=None))


def test_un_barrio_de_la_ciudad_pasa() -> None:
    """La dirección de Google en Colombia nombra el municipio tras el barrio."""
    assert _query().matches_filters(
        _place(
            "Pan del Poblado",
            category="Panadería",
            city="El Poblado",
            address="Cra 43 #10-20, El Poblado, Medellín",
        )
    )


def test_una_direccion_que_no_nombra_el_municipio_se_descarta() -> None:
    """Es el precio de filtrar: sin el municipio escrito no se puede confirmar.

    Se prefiere quedarse corto a llenar la base de otras ciudades; quien
    prefiera lo contrario apaga el modo estricto.
    """
    reason = _query().rejection_reason(
        _place("Pan Anónimo", category="Panadería", city="El Poblado", address="Cra 43 #10-20")
    )
    assert reason == "ciudad"


# ------------------------------------------------------------------ modo laxo


def test_sin_modo_estricto_entra_todo() -> None:
    """Quien quiera el comportamiento de antes puede pedirlo."""
    query = _query(strict_match=False)
    assert query.matches_filters(
        _place("Éxito Wow", category="Supermercado", city="Envigado", address="Cra 1, Envigado")
    )


# ------------------------------------------------------------------ combinados


def test_la_calificacion_sigue_filtrando() -> None:
    query = _query(min_rating=4.0)
    assert query.rejection_reason(_place("Pan Malo", category="Panadería", rating=3.1)) == "rating"


def test_el_motivo_dice_que_falló() -> None:
    """El motivo se enseña al usuario: "descartó 45 porque no eran panaderías"."""
    query = _query(min_rating=4.0)
    place = _place("Café", category="Cafetería", rating=4.8, city="Envigado")
    # El tipo se comprueba antes que la ciudad: es el motivo más informativo.
    assert query.rejection_reason(place) == "tipo_de_negocio"


@pytest.mark.parametrize(
    ("categoria", "esperado"),
    [
        ("Panadería", True),
        ("Pastelería y panadería", True),
        ("Restaurante", False),
        ("Peluquería", False),
    ],
)
def test_tabla_de_categorias(categoria: str, esperado: bool) -> None:
    assert _query().matches_filters(_place("Negocio", category=categoria)) is esperado
