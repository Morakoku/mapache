"""Tests de la autorización por scope (L2, LOOP-14).

Política del contrato Hermes: la identidad `hermes` SOLO puede
`hermes.dispatch` (POST /api/v1/hermes/dispatch) y `hermes.jobs.read`
(GET /api/v1/hermes/jobs/{id}). Cualquier otro scope (crm.read, scrape.run,
jobs.read, mail.send, admin…) no es grantable a una identidad de servicio y el
resto de los 141 endpoints queda DENY.

Cubre:
- La política pura (`app.core.scopes`).
- El enforcement en `ServiceAuthMiddleware` con `service_auth_enabled=True`.
"""

from __future__ import annotations

import re

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.scopes import (
    DEFAULT_HERMES_SCOPES,
    FORBIDDEN_SCOPES,
    PERMITTED_SCOPES,
    SCOPE_DISPATCH_HERMES,
    SCOPE_HERMES_JOBS_READ,
    UI_SCOPES,
    declared_endpoints,
    declares,
    scope_for,
    valid_scope_name,
    validate_scope_set,
)
from app.core.security import NonceStore, issue_service_token
from app.middleware import ServiceAuthMiddleware

TEST_KEY = "clave-de-prueba-de-32-bytes-abcdefghijklmn"
TTL = 300

DEFAULT_HERMES = "hermes.dispatch,hermes.jobs.read"


def make_token(
    *,
    client_id: str = "hermes",
    key: str = TEST_KEY,
    now: int | None = None,
    nonce: str | None = None,
) -> str:
    return issue_service_token(client_id, key, timestamp=now, nonce=nonce)


def _settings(
    *,
    enabled: bool = True,
    hermes_scopes: str = DEFAULT_HERMES,
    trusted_ids: str = "hermes",
    key: str | None = TEST_KEY,
) -> Settings:
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
        service_trusted_client_ids=trusted_ids,
        hermes_scopes=hermes_scopes,
    )


def _build_app(settings: Settings) -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/hermes/dispatch")
    async def dispatch(request: Request) -> dict:
        return {
            "client": getattr(request.state, "service_client", None),
            "scopes": sorted(getattr(request.state, "service_scopes", frozenset())),
            "required": getattr(request.state, "required_scope", None),
        }

    @app.get("/api/v1/hermes/jobs/{job_id}")
    async def job_status(request: Request) -> dict:
        return {"required": getattr(request.state, "required_scope", None)}

    @app.get("/api/v1/ping")  # NO declarada en el scope map → DENY en L2
    async def ping() -> dict:
        return {"pong": True}

    @app.get("/api/v1/leads")  # CRM: fuera del contrato → DENY para hermes
    async def leads() -> dict:
        return {"leads": []}

    app.add_middleware(
        ServiceAuthMiddleware,
        settings=settings,
        nonce_store=NonceStore(ttl_seconds=TTL),
    )
    return app


def _client(settings: Settings) -> AsyncClient:
    app = _build_app(settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ------------------------------------------------------------------ política pura


class TestScopeNaming:
    def test_valid_names_are_domain_action(self) -> None:
        assert valid_scope_name("hermes.dispatch")
        assert valid_scope_name("hermes.jobs.read")
        assert valid_scope_name("crm.read")
        assert valid_scope_name("my_scope.deep.action")

    def test_rejects_wildcards_and_admin_words(self) -> None:
        for bad in ("*", "admin", "all", "full_access", "superuser", "*.*"):
            assert not valid_scope_name(bad), bad

    def test_rejects_malformed_shapes(self) -> None:
        for bad in (
            "crm",
            "crm.",
            ".read",
            "crm..read",
            "Crm.Read",
            "crm read",
            "1crm.read",
            "crm.read!",
        ):
            assert not valid_scope_name(bad), bad


class TestValidateScopeSet:
    def test_empty_set_rejected(self) -> None:
        assert validate_scope_set(frozenset()) == "EMPTY"

    def test_unknown_scope_rejected(self) -> None:
        assert validate_scope_set({"hermes.dispatch", "invented.scope"}) == "NOT_ALLOWED"

    def test_ui_scope_not_grantable(self) -> None:
        # crm.read es un nombre VÁLIDO del catálogo UI pero NO es grantable a
        # una identidad de servicio.
        assert validate_scope_set({"hermes.dispatch", "crm.read"}) == "NOT_ALLOWED"
        assert validate_scope_set({"hermes.dispatch", "scrape.run"}) == "NOT_ALLOWED"
        assert validate_scope_set({"hermes.dispatch", "jobs.read"}) == "NOT_ALLOWED"

    def test_forbidden_scope_rejected(self) -> None:
        assert validate_scope_set({"hermes.dispatch", "crm.write"}) == "FORBIDDEN"

    def test_wildcard_rejected(self) -> None:
        assert validate_scope_set({"hermes.dispatch", "*"}) == "WILDCARD"

    def test_contract_set_returns_none(self) -> None:
        assert validate_scope_set(DEFAULT_HERMES_SCOPES) is None
        assert validate_scope_set({SCOPE_DISPATCH_HERMES}) is None
        assert validate_scope_set({SCOPE_HERMES_JOBS_READ}) is None

    def test_sets_are_coherent(self) -> None:
        assert PERMITTED_SCOPES.isdisjoint(FORBIDDEN_SCOPES)
        assert DEFAULT_HERMES_SCOPES <= PERMITTED_SCOPES
        assert PERMITTED_SCOPES.isdisjoint(UI_SCOPES)
        assert UI_SCOPES.isdisjoint(FORBIDDEN_SCOPES)


class TestEndpointScopeMap:
    def test_contract_routes_return_scope(self) -> None:
        assert scope_for("POST", "/api/v1/hermes/dispatch") == SCOPE_DISPATCH_HERMES
        assert scope_for("GET", "/api/v1/hermes/jobs/123") == SCOPE_HERMES_JOBS_READ

    def test_contract_templates_are_matched(self) -> None:
        assert scope_for("GET", "/api/v1/hermes/jobs/lead-42") == SCOPE_HERMES_JOBS_READ
        assert scope_for("GET", "/api/v1/hermes/jobs/0000-1111") == SCOPE_HERMES_JOBS_READ

    def test_resto_de_los_141_endpoints_devuelve_none(self) -> None:
        # Fuera del contrato → DENY por defecto.
        assert scope_for("GET", "/api/v1/leads") is None
        assert scope_for("POST", "/api/v1/searches/abc/run") is None
        assert scope_for("GET", "/api/v1/jobs/123") is None
        assert scope_for("GET", "/api/v1/ping") is None
        assert scope_for("GET", "/api/v1/metrics/funnel") is None
        assert scope_for("DELETE", "/api/v1/leads/1") is None

    def test_no_inference_by_prefix(self) -> None:
        assert scope_for("GET", "/api/v1/hermes/jobs/123/timeline") is None
        assert scope_for("POST", "/api/v1/hermes/dispatch/extra") is None
        assert scope_for("GET", "/api/v1/hermes") is None

    def test_method_matters(self) -> None:
        assert scope_for("GET", "/api/v1/hermes/dispatch") is None
        assert scope_for("POST", "/api/v1/hermes/jobs/123") is None

    def test_declares_boolean(self) -> None:
        assert declares("POST", "/api/v1/hermes/dispatch")
        assert declares("GET", "/api/v1/hermes/jobs/1")
        assert not declares("GET", "/api/v1/leads")
        assert not declares("GET", "/api/v1/ping")

    def test_all_declared_scopes_are_permitted(self) -> None:
        for _, _, scope in declared_endpoints():
            assert scope in PERMITTED_SCOPES, scope

    def test_every_declared_template_is_reachable(self) -> None:
        def concretize(template: str) -> str:
            return re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "x", template)

        for method, template, scope in declared_endpoints():
            assert scope_for(method, concretize(template)) == scope, (method, template)


# ---------------------------------------------------------- enforcement (L2)


class TestL2Middleware:
    async def test_dispatch_con_scope_correcto_pasa(self) -> None:
        async with _client(_settings()) as ac:
            r = await ac.post(
                "/api/v1/hermes/dispatch", headers={"Authorization": f"Bearer {make_token()}"}
            )
        assert r.status_code == 200
        body = r.json()
        assert body["client"] == "hermes"
        assert body["scopes"] == sorted(DEFAULT_HERMES_SCOPES)
        assert body["required"] == SCOPE_DISPATCH_HERMES

    async def test_jobs_read_con_scope_correcto_pasa(self) -> None:
        async with _client(_settings()) as ac:
            r = await ac.get(
                "/api/v1/hermes/jobs/abc", headers={"Authorization": f"Bearer {make_token()}"}
            )
        assert r.status_code == 200
        assert r.json()["required"] == SCOPE_HERMES_JOBS_READ

    async def test_scope_missing_returns_403(self) -> None:
        # hermes solo con hermes.jobs.read: no puede hacer dispatch.
        settings = _settings(hermes_scopes="hermes.jobs.read")
        async with _client(settings) as ac:
            r = await ac.post(
                "/api/v1/hermes/dispatch", headers={"Authorization": f"Bearer {make_token()}"}
            )
        assert r.status_code == 403
        err = r.json()["error"]
        assert err["code"] == "SCOPE_MISSING"
        assert err["details"]["scope"] == SCOPE_DISPATCH_HERMES

    async def test_undeclared_operation_returns_403(self) -> None:
        # Token válido con los scopes del contrato, pero la operación (CRM)
        # no está declarada → DENY.
        async with _client(_settings()) as ac:
            r = await ac.get("/api/v1/leads", headers={"Authorization": f"Bearer {make_token()}"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SCOPE_NOT_ASSIGNED"

    async def test_ping_no_declarado_returns_403(self) -> None:
        async with _client(_settings()) as ac:
            r = await ac.get("/api/v1/ping", headers={"Authorization": f"Bearer {make_token()}"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SCOPE_NOT_ASSIGNED"

    async def test_forbidden_scope_in_identity_returns_403(self) -> None:
        settings = _settings(hermes_scopes="hermes.dispatch,crm.write")
        async with _client(settings) as ac:
            r = await ac.post(
                "/api/v1/hermes/dispatch", headers={"Authorization": f"Bearer {make_token()}"}
            )
        assert r.status_code == 403
        err = r.json()["error"]
        assert err["code"] == "SCOPE_INVALID_SET"
        assert err["details"]["reason"] == "FORBIDDEN"

    async def test_ui_scope_in_identity_returns_403(self) -> None:
        settings = _settings(hermes_scopes="hermes.dispatch,crm.read")
        async with _client(settings) as ac:
            r = await ac.post(
                "/api/v1/hermes/dispatch", headers={"Authorization": f"Bearer {make_token()}"}
            )
        assert r.status_code == 403
        err = r.json()["error"]
        assert err["code"] == "SCOPE_INVALID_SET"
        assert err["details"]["reason"] == "NOT_ALLOWED"

    async def test_unknown_scope_in_identity_returns_403(self) -> None:
        settings = _settings(hermes_scopes="hermes.dispatch,cosas.misteriosas")
        async with _client(settings) as ac:
            r = await ac.post(
                "/api/v1/hermes/dispatch", headers={"Authorization": f"Bearer {make_token()}"}
            )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "SCOPE_INVALID_SET"

    async def test_identity_without_scopes_returns_403(self) -> None:
        settings = _settings(trusted_ids="hermes,auxiliar")
        token = make_token(client_id="auxiliar")
        async with _client(settings) as ac:
            r = await ac.post(
                "/api/v1/hermes/dispatch", headers={"Authorization": f"Bearer {token}"}
            )
        assert r.status_code == 403
        err = r.json()["error"]
        assert err["code"] == "SCOPE_INVALID_SET"
        assert err["details"]["reason"] == "EMPTY"

    async def test_bypasses_when_disabled(self) -> None:
        # service_auth_enabled=False → ni autentica ni autoriza.
        async with _client(_settings(enabled=False)) as ac:
            no_token = await ac.get("/api/v1/leads")
            no_token_ping = await ac.get("/api/v1/ping")
        assert no_token.status_code == 200
        assert no_token_ping.status_code == 200
        assert no_token.json() == {"leads": []}
