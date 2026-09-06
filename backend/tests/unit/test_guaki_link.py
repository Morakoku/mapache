"""Vínculo Prospecto↔Negocio (LOOP-24): motor de matching puro.

No se relaciona solo por el nombre: usa dominio, email, teléfono, identificador
externo y nombre+ubicación. Determinista y con razones.
"""

from __future__ import annotations

from app.services.guaki_link_svc import match_business


def _panaderia(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Panadería La Esquina",
        "domain": "laesquina.co",
        "email": "info@laesquina.co",
        "phone": "+573001112233",
        "city": "Soacha",
    }
    base.update(overrides)
    return base


def test_domain_match_is_matched() -> None:
    b = _panaderia(domain="https://www.laesquina.co/", email="otro@x.co")
    m = match_business(_panaderia(), b)
    assert m.state == "MATCHED"
    assert "dominio" in m.signals
    assert m.confidence >= 90


def test_email_match_is_matched() -> None:
    b = _panaderia(domain=None, email="INFO@LAESQUINA.CO")
    m = match_business(_panaderia(), b)
    assert m.state == "MATCHED"
    assert "email" in m.signals


def test_phone_match_is_matched() -> None:
    b = _panaderia(domain=None, email=None, phone="573001112233")
    m = match_business(_panaderia(), b)
    assert m.state == "MATCHED"
    assert "telefono" in m.signals


def test_external_id_match_is_matched() -> None:
    p = _panaderia(external_id="g-biz-123")
    b = _panaderia(domain=None, email=None, phone=None, external_id="g-biz-123")
    m = match_business(p, b)
    assert m.state == "MATCHED"
    assert "identificador_externo" in m.signals


def test_name_and_city_is_possible_match() -> None:
    p = _panaderia(domain=None, email=None, phone=None)
    b = _panaderia(domain=None, email=None, phone=None)
    m = match_business(p, b)
    assert m.state == "POSSIBLE_MATCH"
    assert "nombre+ubicacion" in m.signals
    assert m.confidence == 70


def test_same_name_different_city_is_rejected() -> None:
    p = _panaderia(domain=None, email=None, phone=None, city="Soacha")
    b = _panaderia(domain=None, email=None, phone=None, city="Bogotá")
    m = match_business(p, b)
    assert m.state == "REJECTED"


def test_not_only_by_name() -> None:
    # Mismo nombre, distinto email/teléfono/dominio/ciudad → no se asume match.
    p = _panaderia(email="a@x.co", phone="+571111", domain=None)
    b = _panaderia(email="b@y.co", phone="+572222", domain=None, city="Medellín")
    m = match_business(p, b)
    assert m.state == "REJECTED"


def test_no_signals_is_unmatched() -> None:
    p = _panaderia(name="Panadería A", domain=None, email=None, phone=None, city="Cali")
    b = _panaderia(name="Barbería B", domain=None, email=None, phone=None, city="Cali")
    m = match_business(p, b)
    assert m.state == "UNMATCHED"
    assert m.confidence == 0


def test_phone_normalization() -> None:
    p = _panaderia(phone="+57 300 111 2233")
    b = _panaderia(phone="573001112233")
    m = match_business(p, b)
    assert m.state == "MATCHED"
    assert "telefono" in m.signals


def test_domain_normalization() -> None:
    p = _panaderia(domain="www.laesquina.co/pagina")
    b = _panaderia(domain="https://laesquina.co")
    m = match_business(p, b)
    assert m.state == "MATCHED"
    assert "dominio" in m.signals


def test_deterministic() -> None:
    a = match_business(_panaderia(), _panaderia())
    b = match_business(_panaderia(), _panaderia())
    assert a.state == b.state
    assert a.signals == b.signals
