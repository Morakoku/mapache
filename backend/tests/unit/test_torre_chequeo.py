"""Tests del kanban del Chequeo Express (F4) en la Torre de Control.

Verifica:
- GET /torre-control/chequeo sirve la página del kanban.
- GET /torre-control/chequeo/data devuelve las solicitudes con su diagnóstico.
- POST /torre-control/chequeo/{id}/stage valida y persiste la etapa.
- Una etapa inventada se rechaza (422) sin tocar la base.
- Una solicitud inexistente responde 404.

PostgREST se mockea: los tests no tocan producción.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import torre_control


class _FakeSettings:
    supabase_url = "https://fake.supabase.co"
    supabase_service_role_key_value = "fake-key"


def _client(monkeypatch: Any, filas: list[dict[str, Any]] | None = None) -> TestClient:
    rows = filas if filas is not None else []
    updates: list[dict[str, Any]] = []

    async def fake_pg_select(table: str, **kwargs: Any) -> list[dict[str, Any]]:
        if table == "veyra_intakes":
            return rows
        return []

    async def fake_pg_update(table: str, filters: dict[str, str], data: dict[str, Any]) -> list[dict[str, Any]]:
        updates.append({"table": table, "filters": filters, "data": data})
        return [{"intake_id": filters.get("intake_id")}]

    monkeypatch.setattr(torre_control, "get_settings", lambda: _FakeSettings())
    monkeypatch.setenv("VEYRA_ADMIN_TOKEN", "115115")
    from app.core import supabase_http as sbh

    monkeypatch.setattr(sbh, "select", fake_pg_select)
    monkeypatch.setattr(sbh, "update", fake_pg_update)

    app = FastAPI()
    app.include_router(torre_control.router, prefix="/torre-control")
    client = TestClient(app)
    client._updates = updates  # type: ignore[attr-defined]
    return client


AUTH = {"X-Veyra-Token": "115115"}


def _fila(intake_id: str = "CHK-001", stage: str = "nuevo") -> dict[str, Any]:
    return {
        "intake_id": intake_id,
        "name": "Cliente Prueba",
        "phone": "+57 300 111 2222",
        "token": "VY-token-1",
        "status": "chequeo",
        "created_at": "2026-09-10T12:00:00+00:00",
        "whatsapp_sent_at": None,
        "payload": {
            "chequeo": {
                "stage": stage,
                "diagnosis": {
                    "overall": 35,
                    "levelLabel": "FRÁGIL",
                    "profileName": "EL CUELLO DE BOTELLA ERES TÚ",
                    "anualizado": "más de $84M COP al año",
                },
            }
        },
    }


def test_pagina_kanban_se_sirve(monkeypatch: Any) -> None:
    client = _client(monkeypatch)
    r = client.get("/torre-control/chequeo")
    assert r.status_code == 200
    assert "Solicitudes del Chequeo Express" in r.text
    assert "/torre-control/chequeo/data" in r.text


def test_data_sin_token_responde_401(monkeypatch: Any) -> None:
    client = _client(monkeypatch, filas=[_fila()])
    r = client.get("/torre-control/chequeo/data")
    assert r.status_code == 401


def test_token_incorrecto_responde_401(monkeypatch: Any) -> None:
    client = _client(monkeypatch, filas=[_fila()])
    r = client.get("/torre-control/chequeo/data", headers={"X-Veyra-Token": "000000"})
    assert r.status_code == 401


def test_data_devuelve_diagnostico_y_telefono_wa(monkeypatch: Any) -> None:
    client = _client(monkeypatch, filas=[_fila()])
    r = client.get("/torre-control/chequeo/data", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["perfil"] == "EL CUELLO DE BOTELLA ERES TÚ"
    assert item["score"] == 35
    assert item["stage"] == "nuevo"
    # Teléfono listo para wa.me: solo dígitos con indicativo.
    assert item["phone"] == "573001112222"


def test_token_por_query_param_tambien_autoriza(monkeypatch: Any) -> None:
    client = _client(monkeypatch, filas=[_fila()])
    r = client.get("/torre-control/chequeo/data?token=115115")
    assert r.status_code == 200


def test_mover_etapa_persiste_en_payload(monkeypatch: Any) -> None:
    client = _client(monkeypatch, filas=[_fila(stage="nuevo")])
    r = client.post("/torre-control/chequeo/CHK-001/stage", json={"stage": "agendado"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["stage"] == "agendado"
    updates = client._updates  # type: ignore[attr-defined]
    assert len(updates) == 1
    assert updates[0]["filters"] == {"intake_id": "CHK-001"}
    assert updates[0]["data"]["payload"]["chequeo"]["stage"] == "agendado"


def test_mover_etapa_sin_token_responde_401(monkeypatch: Any) -> None:
    client = _client(monkeypatch, filas=[_fila()])
    r = client.post("/torre-control/chequeo/CHK-001/stage", json={"stage": "agendado"})
    assert r.status_code == 401
    assert client._updates == []  # type: ignore[attr-defined]


def test_etapa_invalida_se_rechaza_sin_tocar_la_base(monkeypatch: Any) -> None:
    client = _client(monkeypatch, filas=[_fila()])
    r = client.post("/torre-control/chequeo/CHK-001/stage", json={"stage": "hackeado"}, headers=AUTH)
    assert r.status_code == 422
    assert client._updates == []  # type: ignore[attr-defined]


def test_solicitud_inexistente_responde_404(monkeypatch: Any) -> None:
    client = _client(monkeypatch, filas=[])
    r = client.post("/torre-control/chequeo/CHK-999/stage", json={"stage": "contactado"}, headers=AUTH)
    assert r.status_code == 404
    assert client._updates == []  # type: ignore[attr-defined]


def test_sin_token_configurado_falla_cerrado(monkeypatch: Any) -> None:
    client = _client(monkeypatch, filas=[_fila()])
    monkeypatch.delenv("VEYRA_ADMIN_TOKEN", raising=False)
    r = client.get("/torre-control/chequeo/data", headers=AUTH)
    assert r.status_code == 401
