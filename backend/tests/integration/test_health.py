"""Smoke test de la Fase 1: la app arranca y habla con Postgres."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_liveness(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"


async def test_health_readiness_checks_database(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


async def test_unknown_route_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/no-existe")

    assert response.status_code == 404
