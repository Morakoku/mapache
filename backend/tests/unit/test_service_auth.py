"""Tests de la capa de autenticación servicio-a-servicio (L1, PATCH-01).

Cubre los 6 casos exigidos por LOOP-04, a dos niveles:
- verify_service_token / issue_service_token: unidades puras.
- ServiceAuthMiddleware: sobre una mini-app que exige Bearer en /api/v1/*.

Desde PATCH-04 (L2) el middleware también autoriza por scope: las rutas del
mini-app usan paths declarados en `app.core.scopes` (`/api/v1/hermes/jobs/lead-1`); los
endpoints de gestión `/auth/*` pasan a DENY 403 (SCOPE_NOT_ASSIGNED) para
clientes de servicio.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.security import (
    NonceStore,
    ServiceTokenError,
    issue_service_token,
    verify_service_token,
)
from app.middleware import ServiceAuthMiddleware, is_public_service_path

TEST_KEY = "clave-de-prueba-de-32-bytes-abcdefghijklmn"
TTL = 300


def make_token(
    *,
    client_id: str = "hermes",
    key: str = TEST_KEY,
    now: int | None = None,
    nonce: str | None = None,
) -> str:
    return issue_service_token(client_id, key, timestamp=now, nonce=nonce)


def _settings(enabled: bool = True, key: str | None = TEST_KEY) -> Settings:
    return Settings(
        app_name="test",
        environment="test",
        debug=False,
        database_url="postgresql://crm:crm@localhost:5435/crm_test",
        encryption_key="x" * 44,  # 44 chars => 32 bytes Fernet
        secret_key="test-secret-key",
        service_auth_enabled=enabled,
        service_token_key=key,
        service_token_ttl_seconds=TTL,
    )


def _build_app(settings: Settings, *, with_auth: bool = True) -> FastAPI:
    app = FastAPI()

    # Ruta declarada en el scope map del contrato (hermes.jobs.read): la usan
    # los flujos que necesitan llegar al handler. Rutas inventadas (p.ej.
    # /api/v1/ping) o del CRM (/api/v1/leads) ahora DENY en L2.
    @app.get("/api/v1/hermes/jobs/lead-1")
    async def leads(request: Request) -> dict:
        return {"ok": True, "client": getattr(request.state, "service_client", None)}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict:
        return {"status": "ok", "database": "ok"}

    @app.get("/auth/{provider}/connect")
    async def connect(provider: str) -> dict:
        return {"redirect": "https://provider.example"}

    @app.get("/auth/{provider}/callback")
    async def callback(provider: str) -> dict:
        return {"ok": True, "provider": provider}

    @app.get("/auth/accounts")
    async def accounts() -> dict:
        return {"accounts": []}

    @app.post("/auth/{provider}/disconnect/{account_id}")
    async def disconnect(provider: str, account_id: str) -> dict:
        return {"disconnected": True}

    if with_auth:
        app.add_middleware(
            ServiceAuthMiddleware,
            settings=settings,
            nonce_store=NonceStore(ttl_seconds=settings.service_token_ttl_seconds),
        )
    return app


def _client(settings: Settings, *, with_auth: bool = True) -> AsyncClient:
    app = _build_app(settings, with_auth=with_auth)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ------------------------------------------------------------------ token puro


class TestVerifyServiceToken:
    def test_valid_token_returns_client_id(self) -> None:
        token = make_token()
        assert verify_service_token(token, key=TEST_KEY, ttl_seconds=TTL) == "hermes"

    def test_missing_token_raises(self) -> None:
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token(None, key=TEST_KEY, ttl_seconds=TTL)
        assert ei.value.code == "SERVICE_TOKEN_MISSING"

    def test_malformed_token_raises(self) -> None:
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token("solo.tres", key=TEST_KEY, ttl_seconds=TTL)
        assert ei.value.code == "SERVICE_TOKEN_INVALID"

    def test_expired_timestamp_raises(self) -> None:
        old = make_token(now=int(time.time()) - 10_000)
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token(old, key=TEST_KEY, ttl_seconds=TTL)
        assert ei.value.code == "SERVICE_TOKEN_EXPIRED"

    def test_future_timestamp_raises(self) -> None:
        token = make_token(now=int(time.time()) + 10_000)
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token(token, key=TEST_KEY, ttl_seconds=TTL)
        assert ei.value.code == "SERVICE_TOKEN_EXPIRED"

    def test_invalid_signature_raises(self) -> None:
        parts = make_token().split(".")
        parts[-1] = "0" * 64  # firma inventada
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token(".".join(parts), key=TEST_KEY, ttl_seconds=TTL)
        assert ei.value.code == "SERVICE_TOKEN_BAD_SIGNATURE"

    def test_wrong_key_rejected(self) -> None:
        token = make_token(key=TEST_KEY)
        with pytest.raises(ServiceTokenError):
            verify_service_token(token, key="otra-clave", ttl_seconds=TTL)


class TestNonceStoreReplay:
    def test_duplicate_nonce_rejected(self) -> None:
        store = NonceStore(ttl_seconds=TTL)
        fixed = make_token(nonce="nonce-fijo")

        verify_service_token(fixed, key=TEST_KEY, ttl_seconds=TTL, nonce_store=store)
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token(fixed, key=TEST_KEY, ttl_seconds=TTL, nonce_store=store)
        assert ei.value.code == "SERVICE_TOKEN_REPLAY"


# ------------------------------------------------------------------ middleware


class TestMiddlewareDisabled:
    async def test_bypasses_when_disabled(self) -> None:
        app = _build_app(_settings(enabled=False))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/hermes/jobs/lead-1")
        assert r.status_code == 200


class TestMiddlewareEnabled:
    async def test_missing_token_returns_401(self) -> None:
        async with _client(_settings()) as ac:
            r = await ac.get("/api/v1/hermes/jobs/lead-1")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "SERVICE_TOKEN_MISSING"

    async def test_invalid_bearer_returns_401(self) -> None:
        async with _client(_settings()) as ac:
            r = await ac.get(
                "/api/v1/hermes/jobs/lead-1", headers={"Authorization": "Bearer not-valid"}
            )
        assert r.status_code == 401

    async def test_expired_token_returns_401(self) -> None:
        expired = make_token(now=int(time.time()) - 10_000)
        async with _client(_settings()) as ac:
            r = await ac.get(
                "/api/v1/hermes/jobs/lead-1", headers={"Authorization": f"Bearer {expired}"}
            )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "SERVICE_TOKEN_EXPIRED"

    async def test_forged_signature_returns_401(self) -> None:
        parts = make_token().split(".")
        parts[-1] = "1" * 64
        forged = ".".join(parts)
        async with _client(_settings()) as ac:
            r = await ac.get(
                "/api/v1/hermes/jobs/lead-1", headers={"Authorization": f"Bearer {forged}"}
            )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "SERVICE_TOKEN_BAD_SIGNATURE"

    async def test_valid_token_passes_and_exposes_client(self) -> None:
        token = make_token()
        async with _client(_settings()) as ac:
            r = await ac.get(
                "/api/v1/hermes/jobs/lead-1", headers={"Authorization": f"Bearer {token}"}
            )
        assert r.status_code == 200
        assert r.json()["client"] == "hermes"

    async def test_public_route_stays_open(self) -> None:
        async with _client(_settings()) as ac:
            r_health = await ac.get("/health")
            r_ready = await ac.get("/health/ready")
        assert r_health.status_code == 200
        assert r_ready.status_code == 200


# ------------------------------------------------------------------ clasificación pública (LOOP-06)


class TestPublicPathClassification:
    def test_static_public_routes(self) -> None:
        assert is_public_service_path("/health")
        assert is_public_service_path("/health/ready")
        assert is_public_service_path("/tracking/open/abc.gif")
        assert is_public_service_path("/docs")
        assert is_public_service_path("/redoc")
        assert is_public_service_path("/openapi.json")

    def test_oauth_connect_is_public(self) -> None:
        assert is_public_service_path("/auth/google/connect")
        assert is_public_service_path("/auth/microsoft/connect")
        assert is_public_service_path("/auth/microsoft/callback")
        assert is_public_service_path("/auth/google/callback")

    def test_oauth_management_is_protected(self) -> None:
        assert not is_public_service_path("/auth/accounts")
        assert not is_public_service_path("/auth/google/disconnect/123")
        assert not is_public_service_path("/auth/{provider}/disconnect/{account_id}")
        assert not is_public_service_path("/auth")
        assert not is_public_service_path("/auth/otro")


class TestOAuthRoutesPublicProtected:
    async def test_connect_and_callback_stay_public(self) -> None:
        app = _build_app(_settings())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            connect = await ac.get("/auth/google/connect")
            callback = await ac.get("/auth/google/callback")
        assert connect.status_code == 200
        assert callback.status_code == 200

    async def test_accounts_denied_for_service_clients(self) -> None:
        # LOOP-06: /auth/accounts NO es pública. Con L2, además de exigir token,
        # el middleware deniega la operación porque no está declarada en el
        # scope map (gestión de buzones = dominio de la UI, no del servicio).
        app = _build_app(_settings())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            no_token = await ac.get("/auth/accounts")
            with_token = await ac.get(
                "/auth/accounts", headers={"Authorization": f"Bearer {make_token()}"}
            )
        assert no_token.status_code == 401
        assert with_token.status_code == 403
        assert with_token.json()["error"]["code"] == "SCOPE_NOT_ASSIGNED"

    async def test_disconnect_denied_for_service_clients(self) -> None:
        app = _build_app(_settings())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            no_token = await ac.post("/auth/google/disconnect/123")
            with_token = await ac.post(
                "/auth/google/disconnect/123",
                headers={"Authorization": f"Bearer {make_token()}"},
            )
        assert no_token.status_code == 401
        assert with_token.status_code == 403
        assert with_token.json()["error"]["code"] == "SCOPE_NOT_ASSIGNED"


async def test_replay_rejected_through_middleware() -> None:
    app = _build_app(_settings())
    token = make_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.get("/api/v1/hermes/jobs/lead-1", headers=headers)
        second = await ac.get("/api/v1/hermes/jobs/lead-1", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "SERVICE_TOKEN_REPLAY"
