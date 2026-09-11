"""Tests de la sección Veyra de productos/soluciones en la Torre de Control.

Verifica:
- La galería sirve y lista las fichas.
- El PDF válido se sirve; uno fuera de la lista responde 404.
- El envío por correo exige token, valida correo y archivo.
- Con envío mockeado responde OK.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.routers import torre_productos


def _client(monkeypatch: Any) -> TestClient:
    from app.main import app

    monkeypatch.setenv("VEYRA_ADMIN_TOKEN", "115115")
    return TestClient(app)


AUTH = {"X-Veyra-Token": "115115"}


def test_galeria_lista_fichas(monkeypatch: Any) -> None:
    c = _client(monkeypatch)
    r = c.get("/torre-control/productos")
    assert r.status_code == 200
    assert r.text.count('class="item"') == len(torre_productos.FICHAS)


def test_pdf_valido_e_invalido(monkeypatch: Any) -> None:
    c = _client(monkeypatch)
    ok = c.get("/torre-control/productos/14_Agenda_IA.pdf")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "application/pdf"
    malo = c.get("/torre-control/productos/otro.pdf")
    assert malo.status_code == 404


def test_enviar_sin_token_responde_401(monkeypatch: Any) -> None:
    c = _client(monkeypatch)
    r = c.post("/torre-control/productos/14_Agenda_IA.pdf/enviar-email", json={"to": "a@b.com"})
    assert r.status_code == 401


def test_enviar_correo_invalido_responde_422(monkeypatch: Any) -> None:
    c = _client(monkeypatch)
    r = c.post(
        "/torre-control/productos/14_Agenda_IA.pdf/enviar-email",
        headers=AUTH, json={"to": "no-es-correo"},
    )
    assert r.status_code == 422


def test_enviar_archivo_invalido_responde_404(monkeypatch: Any) -> None:
    c = _client(monkeypatch)
    r = c.post(
        "/torre-control/productos/no.pdf/enviar-email",
        headers=AUTH, json={"to": "a@b.com"},
    )
    assert r.status_code == 404


def test_enviar_ok_con_resend_mockeado(monkeypatch: Any) -> None:
    enviados: list[tuple] = []

    async def fake_send(to, ficha, ruta, mensaje):
        enviados.append((to, ficha["archivo"], mensaje))
        return True

    monkeypatch.setattr(torre_productos, "_resend_enviar", fake_send)
    c = _client(monkeypatch)
    r = c.post(
        "/torre-control/productos/15_Veyra_Ops.pdf/enviar-email",
        headers=AUTH, json={"to": "cliente@empresa.com"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "OK"
    assert enviados and enviados[0][0] == "cliente@empresa.com"


def test_enviar_falla_si_resend_falla(monkeypatch: Any) -> None:
    async def fake_send(*args, **kwargs):
        return False

    monkeypatch.setattr(torre_productos, "_resend_enviar", fake_send)
    c = _client(monkeypatch)
    r = c.post(
        "/torre-control/productos/15_Veyra_Ops.pdf/enviar-email",
        headers=AUTH, json={"to": "cliente@empresa.com"},
    )
    assert r.status_code == 502
