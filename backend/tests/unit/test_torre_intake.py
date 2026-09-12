"""Tests del intake público del sitio web (POST /torre-control/intake).

El endpoint escribe en el CRM vía PostgREST cuando Supabase está configurado.
Para los tests se mockea `pg_insert`/`pg_insert_upsert`/`pg_select` para no
tocar producción. Lo que se verifica:

- Validación: nombre, email y descripción del reto son obligatorios.
- Rate limit por IP: al 11º envío en la ventana responde 429.
- El flujo llama a las tablas en el orden correcto y con los valores
  esperados (companies con dedupe_key, contacts con source=WEBSITE, lead OPEN
  con el servicio base canónico — `crm.leads` no tiene columna `source` —, task
  de revisión y activity LEAD_CREATED).
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi.testclient import TestClient

from app.routers import torre_control


class _FakeSettings:
    supabase_url = "https://fake.supabase.co"
    supabase_service_role_key_value = "fake-key"


def _client(monkeypatch: Any) -> TestClient:
    """App mínima solo con el router de torre-control y PostgREST mockeado."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_pg_insert(table: str, data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(("insert", {"table": table, "data": data}))
        return [{"id": f"uuid-{table}"}]

    async def fake_pg_upsert(
        table: str, data: dict[str, Any], on_conflict: str | None = None
    ) -> list[dict[str, Any]]:
        calls.append(("upsert", {"table": table, "data": data, "on_conflict": on_conflict}))
        return [{"id": "uuid-companies"}]

    async def fake_pg_select(
        table: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        calls.append(("select", {"table": table, **kwargs}))
        if table == "pipeline_stages":
            return [{"id": "uuid-stage-new", "stage_key": "new", "owner_id": None}]
        if table == "services":
            return [{"id": "uuid-service-base"}]
        # Sin lead previo: la empresa nueva debe generar un lead nuevo.
        return []

    monkeypatch.setattr(torre_control, "get_settings", lambda: _FakeSettings())
    from app.core import postgrest_client as prc
    from app.core import supabase_http as sbh

    monkeypatch.setattr(prc, "pg_insert", fake_pg_insert)
    monkeypatch.setattr(prc, "pg_insert_upsert", fake_pg_upsert)
    monkeypatch.setattr(sbh, "select", fake_pg_select)

    # El rate limit es estado global en memoria: se limpia entre tests.
    torre_control._intake_hits.clear()

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(torre_control.router, prefix="/torre-control")
    client = TestClient(app)
    client._intake_calls = calls  # type: ignore[attr-defined]
    return client


PAYLOAD = {
    "name": "Ana Pérez",
    "email": "ana@example.com",
    "company_name": "Empresa Ana",
    "company_process": "Necesitamos ordenar el proceso comercial.",
    "phone": "+57 300 111 2222",
    "city": "Medellín",
    "sector": "salud",
}


class TestValidacion:
    def test_rechaza_email_invalido(self, monkeypatch: Any) -> None:
        client = _client(monkeypatch)
        payload = dict(PAYLOAD, email="no-es-email")
        r = client.post("/torre-control/intake", json=payload)
        assert r.status_code == 422
        assert "correo" in r.json()["detail"].lower()

    def test_rechaza_descripcion_corta(self, monkeypatch: Any) -> None:
        client = _client(monkeypatch)
        payload = dict(PAYLOAD, company_process="corta")
        r = client.post("/torre-control/intake", json=payload)
        assert r.status_code == 422

    def test_rechaza_nombre_vacio(self, monkeypatch: Any) -> None:
        client = _client(monkeypatch)
        payload = dict(PAYLOAD, name="   ")
        r = client.post("/torre-control/intake", json=payload)
        assert r.status_code == 422


class TestFlujo:
    def test_crea_lead_open_con_servicio_base(self, monkeypatch: Any) -> None:
        client = _client(monkeypatch)
        r = client.post("/torre-control/intake", json=PAYLOAD)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "CREATED"
        assert "lead_id" in body
        # No expone PII en la respuesta
        assert "email" not in body
        assert "name" not in body

        calls = client._intake_calls  # type: ignore[attr-defined]
        inserts = [c for op, c in calls if op == "insert"]
        tablas_insert = [c["table"] for c in inserts]
        assert tablas_insert == ["contacts", "leads", "tasks", "activities"]

        # contacts con source=WEBSITE
        contact = inserts[0]["data"]
        assert contact["source"] == "WEBSITE"
        assert contact["is_primary"] is True

        # lead OPEN con el servicio base canónico; `crm` no tiene columna `source`
        lead = inserts[1]["data"]
        assert lead["stage_id"] == "uuid-stage-new"
        assert lead["service_id"] == "uuid-service-base"
        assert lead["status"] == "OPEN"
        assert "source" not in lead

        # activity LEAD_CREATED con metadata del origen
        activity = inserts[3]["data"]
        assert activity["activity_type"] == "LEAD_CREATED"
        assert activity["actor_type"] == "SYSTEM"
        assert activity["metadata"]["origen"] == "veyrasoluciones.com"

    def test_empresa_upsert_con_dedupe_key_por_email(self, monkeypatch: Any) -> None:
        client = _client(monkeypatch)
        r = client.post("/torre-control/intake", json=PAYLOAD)
        assert r.status_code == 201
        calls = client._intake_calls  # type: ignore[attr-defined]
        upsert = [c for op, c in calls if op == "upsert"][0]
        assert upsert["table"] == "companies"
        assert upsert["on_conflict"] == "owner_id,dedupe_key"
        esperado = hashlib.sha256(b"veyra-website:ana@example.com").hexdigest()[:40]
        assert upsert["data"]["dedupe_key"] == esperado


class TestRateLimit:
    def test_limite_por_ip(self, monkeypatch: Any) -> None:
        client = _client(monkeypatch)
        codigo_final = None
        for i in range(11):
            payload = dict(PAYLOAD, email=f"rl-{i}@example.com")
            r = client.post("/torre-control/intake", json=payload)
            codigo_final = r.status_code
            if r.status_code == 429:
                break
        assert codigo_final == 429
