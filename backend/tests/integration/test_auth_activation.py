"""LOOP-15 — Activación controlada de L1/L2 SOLO en entorno de TEST.

Construye la app REAL (`create_app`) con `SERVICE_AUTH_ENABLED=true` y una clave
de TEST, y demuestra que la barrera funciona de verdad:
- 401: token ausente / inválido / expirado / client_id no permitido / nonce reusado.
- 403: operación no declarada (SCOPE_NOT_ASSIGNED), scope declarado pero no
  concedido (SCOPE_MISSING) y set de scopes inválido (SCOPE_INVALID_SET).
- 200/404: el contrato (dispatch + jobs de Hermes) pasa la barrera con el scope
  correcto; el resto de /api/v1 queda DENY.
- /health y /health/ready siguen públicos.
- Ningún secreto en logs, respuestas ni OpenAPI.

PRODUCCIÓN NO CAMBIA: los settings por defecto del entorno de test (y el .env
real del servidor) siguen con `service_auth_enabled=False`; aquí la auth solo se
activa en la app construida en memoria de este archivo.
"""

from __future__ import annotations

import logging
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.security import issue_service_token
from app.main import create_app

TEST_KEY = "clave-de-prueba-de-32-bytes-abcdefghijklmn"
TTL = 300

# Operaciones del CRM que Hermes NO debe poder tocar (fuera del contrato).
DENIED_OPERATIONS: list[tuple[str, str]] = [
    ("GET", "/api/v1/leads"),
    ("GET", "/api/v1/companies"),
    ("GET", "/api/v1/jobs"),
    ("GET", "/api/v1/settings"),
    ("GET", "/api/v1/metrics/overview"),
    ("POST", "/api/v1/searches/00000000-0000-0000-0000-000000000000/run"),
    ("POST", "/api/v1/companies/00000000-0000-0000-0000-000000000000/enrich"),
    ("POST", "/api/v1/emails/send"),
    ("POST", "/api/v1/follow-ups/run"),
    ("DELETE", "/api/v1/leads/00000000-0000-0000-0000-000000000000?confirm=true"),
]


def _auth_settings() -> Settings:
    return Settings(
        app_name="test",
        environment="test",
        debug=False,
        database_url="postgresql://crm:crm@localhost:5434/crm_test",
        encryption_key="x" * 44,
        secret_key="test-secret-key",
        service_auth_enabled=True,
        service_token_key=TEST_KEY,
        service_token_ttl_seconds=TTL,
        service_trusted_client_ids="hermes",
        hermes_scopes="hermes.dispatch,hermes.jobs.read",
        # Aislar la demostración de L1/L2: audit y rate-limit se prueban por
        # separado (LOOP-13); aquí solo interesa la barrera de auth.
        audit_enabled=False,
        rate_limit_enabled=False,
    )


def _token(*, client_id: str = "hermes", nonce: str | None = None, now: int | None = None) -> str:
    return issue_service_token(client_id, TEST_KEY, timestamp=now, nonce=nonce)


@pytest.fixture
def auth_app() -> Settings:
    return _auth_settings()


@pytest.fixture
def auth_client() -> AsyncClient:
    app = create_app(settings=_auth_settings())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestPublicRoutes:
    async def test_health_y_ready_siguen_publicos(self, auth_client: AsyncClient) -> None:
        health = await auth_client.get("/health")
        ready = await auth_client.get("/health/ready")
        assert health.status_code == 200
        assert ready.status_code == 200


class TestAuthenticationL1:
    async def test_token_ausente_401(self, auth_client: AsyncClient) -> None:
        r = await auth_client.get("/api/v1/leads")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "SERVICE_TOKEN_MISSING"

    async def test_token_invalido_401(self, auth_client: AsyncClient) -> None:
        r = await auth_client.get("/api/v1/leads", headers={"Authorization": "Bearer no-valido"})
        assert r.status_code == 401

    async def test_token_expirado_401(self, auth_client: AsyncClient) -> None:
        old = _token(now=int(time.time()) - 10_000)
        r = await auth_client.get("/api/v1/leads", headers={"Authorization": f"Bearer {old}"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "SERVICE_TOKEN_EXPIRED"

    async def test_client_id_no_permitido_401(self, auth_client: AsyncClient) -> None:
        token = _token(client_id="attacker")
        r = await auth_client.get("/api/v1/leads", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "SERVICE_TOKEN_UNKNOWN_CLIENT"

    async def test_nonce_reutilizado_401(self, auth_client: AsyncClient) -> None:
        """El nonce se consume una sola vez, aunque L2 deniegue después."""
        token = _token(nonce=f"replay-{uuid.uuid4().hex}")
        headers = {"Authorization": f"Bearer {token}"}
        first = await auth_client.get("/api/v1/leads", headers=headers)
        second = await auth_client.get("/api/v1/leads", headers=headers)
        assert first.status_code == 403  # L2: fuera del contrato
        assert second.status_code == 401  # replay de nonce
        assert second.json()["error"]["code"] == "SERVICE_TOKEN_REPLAY"


class TestAuthorizationL2:
    async def test_token_valido_no_accede_al_crm(self, auth_client: AsyncClient) -> None:
        for method, path in DENIED_OPERATIONS:
            r = await auth_client.request(
                method, path, headers={"Authorization": f"Bearer {_token()}"}
            )
            assert r.status_code == 403, f"{method} {path} debía ser DENY"
            # SCOPE_NOT_ASSIGNED: la operación no está declarada en el contrato
            # de servicio. SCOPE_MISSING: está declarada (p. ej. companies es
            # del contrato Guaki) pero la identidad no tiene el scope.
            assert r.json()["error"]["code"] in {
                "SCOPE_NOT_ASSIGNED",
                "SCOPE_MISSING",
                "SCOPE_INVALID_SET",
            }

    async def test_dispatch_con_scope_correcto_atraviesa_la_barrera(
        self, auth_client: AsyncClient
    ) -> None:
        # El contrato POST /api/v1/hermes/dispatch pasa L1+L2 con hermes.dispatch
        # (ya no es 404: el router existe desde LOOP-16) y llega al handler, que
        # exige Idempotency-Key → 400. Nunca 401/403.
        r = await auth_client.post(
            "/api/v1/hermes/dispatch",
            json={"job_type": "FOLLOWUP_TICK", "payload": {}},
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    async def test_jobs_hermes_con_scope_correcto_atraviesa_la_barrera(
        self, auth_client: AsyncClient
    ) -> None:
        # GET /api/v1/hermes/jobs/{id} pasa L1+L2 con hermes.jobs.read; el 404
        # es del handler (job inexistente), no de la barrera.
        r = await auth_client.get(
            "/api/v1/hermes/jobs/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert r.status_code == 404  # atraviesa la barrera; job inexistente
        assert r.status_code not in (401, 403)

    async def test_jobs_hermes_sin_token_401(self, auth_client: AsyncClient) -> None:
        r = await auth_client.get("/api/v1/hermes/jobs/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 401


class TestProduccionSinCambios:
    def test_settings_por_defecto_siguen_con_auth_desactivada(self) -> None:
        # El entorno de test general (y por tanto el .env de la suite) sigue con
        # SERVICE_AUTH_ENABLED=false: la activación es SOLO de este archivo.
        assert get_settings().service_auth_enabled is False


class TestNoSecrets:
    async def test_openapi_no_expone_la_clave(self, auth_app: Settings) -> None:
        text = str(create_app(settings=auth_app).openapi())
        assert TEST_KEY not in text

    async def test_respuestas_de_error_no_expone_la_clave(self, auth_client: AsyncClient) -> None:
        responses = [
            await auth_client.get("/api/v1/leads"),  # 401
            await auth_client.get("/api/v1/leads", headers={"Authorization": "Bearer x"}),
            await auth_client.get("/api/v1/leads", headers={"Authorization": f"Bearer {_token()}"}),
        ]
        for r in responses:
            assert TEST_KEY not in r.text

    async def test_logs_no_expone_la_clave(self, auth_client: AsyncClient, caplog) -> None:
        with caplog.at_level(logging.DEBUG):
            await auth_client.get("/api/v1/leads")  # 401
            await auth_client.get(
                "/api/v1/leads", headers={"Authorization": f"Bearer {_token()}"}
            )  # 403
            await auth_client.get("/health")
        assert TEST_KEY not in caplog.text
