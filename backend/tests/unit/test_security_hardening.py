"""Tests de la infraestructura de seguridad del LOOP-13.

Cubre lógica pura (rate limiter, clasificación de categorías/acciones, guardia
destructiva) y los middlewares en mini-apps: cabeceras de seguridad, rate
limit 429 y confirmación de operaciones destructivas.
"""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.audit import action_for, should_audit
from app.core.destructive import DestructiveGuardMiddleware, is_destructive
from app.core.headers import SecurityHeadersMiddleware
from app.core.rate_limit import RateLimiter, RateLimitMiddleware, category_for


def _app(*middlewares: tuple[object, ...]) -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/ping")
    async def ping() -> dict:
        return {"ok": True}

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel() -> dict:
        return {"cancelled": True}

    @app.delete("/api/v1/templates/{template_id}")
    async def delete_template() -> None:
        return None

    for mw, kwargs in middlewares:
        app.add_middleware(mw, **kwargs)
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ------------------------------------------------------------------ rate limit


class TestRateLimiter:
    def test_allows_until_limit_then_denies(self) -> None:
        limiter = RateLimiter()
        key = ("email", "127.0.0.1")
        assert limiter.allow(key, 2, 60, now=1000.0)
        assert limiter.allow(key, 2, 60, now=1001.0)
        assert not limiter.allow(key, 2, 60, now=1002.0)

    def test_window_expires(self) -> None:
        limiter = RateLimiter()
        key = ("email", "127.0.0.1")
        limiter.allow(key, 2, 60, now=1000.0)
        limiter.allow(key, 2, 60, now=1001.0)
        # Tras la ventana se libera.
        assert limiter.allow(key, 2, 60, now=1062.0)

    def test_reset(self) -> None:
        limiter = RateLimiter()
        key = ("email", "127.0.0.1")
        limiter.allow(key, 1, 60, now=1000.0)
        assert not limiter.allow(key, 1, 60, now=1001.0)
        limiter.reset()
        assert limiter.allow(key, 1, 60, now=1002.0)


class TestCategoryFor:
    def test_email(self) -> None:
        assert category_for("POST", "/api/v1/emails/send") == "email"
        assert category_for("POST", "/api/v1/conversations/1/reply") == "email"

    def test_scraping(self) -> None:
        assert category_for("POST", "/api/v1/searches/abc/run") == "scraping"
        assert category_for("POST", "/api/v1/companies/1/enrich") == "scraping"
        assert category_for("POST", "/api/v1/services/1/prospect-plan/run") == "scraping"

    def test_settings_jobs_tracking(self) -> None:
        assert category_for("DELETE", "/api/v1/settings/ai") == "settings"
        assert category_for("POST", "/api/v1/jobs/abc/cancel") == "jobs"
        assert category_for("POST", "/tracking/unsubscribe/abc") == "tracking"

    def test_lecturas_no_se_limitan(self) -> None:
        assert category_for("GET", "/api/v1/leads") == "none"
        assert category_for("GET", "/health") == "none"


class TestRateLimitMiddleware:
    async def test_429_tras_superar_limite(self) -> None:
        app = _app(
            (RateLimitMiddleware, {"enabled": True, "limits": {"jobs": 2}, "window_seconds": 60})
        )
        async with _client(app) as ac:
            first = await ac.post("/api/v1/jobs/1/cancel?confirm=true")
            second = await ac.post("/api/v1/jobs/2/cancel?confirm=true")
            third = await ac.post("/api/v1/jobs/3/cancel?confirm=true")
        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "RATE_LIMITED"

    async def test_disabled_bypasses(self) -> None:
        app = _app((RateLimitMiddleware, {"enabled": False, "limits": {"default": 0}}))
        async with _client(app) as ac:
            r = await ac.post("/api/v1/jobs/1/cancel?confirm=true")
        assert r.status_code == 200


# ------------------------------------------------------- confirmación destructiva


class TestIsDestructive:
    def test_delete_y_cancel(self) -> None:
        assert is_destructive("DELETE", "/api/v1/leads/1")
        assert is_destructive("POST", "/api/v1/jobs/abc/cancel")
        assert not is_destructive("GET", "/api/v1/leads/1")
        assert not is_destructive("POST", "/api/v1/leads")


class TestDestructiveGuard:
    async def test_delete_sin_confirm_409(self) -> None:
        app = _app((DestructiveGuardMiddleware, {"enabled": True}))
        async with _client(app) as ac:
            r = await ac.delete("/api/v1/templates/abc")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "REQUIRES_CONFIRMATION"

    async def test_delete_con_confirm_pasa(self) -> None:
        app = _app((DestructiveGuardMiddleware, {"enabled": True}))
        async with _client(app) as ac:
            r = await ac.delete("/api/v1/templates/abc?confirm=true")
        assert r.status_code in (200, 204)

    async def test_job_cancel_sin_confirm_409(self) -> None:
        app = _app((DestructiveGuardMiddleware, {"enabled": True}))
        async with _client(app) as ac:
            r = await ac.post("/api/v1/jobs/abc/cancel")
        assert r.status_code == 409

    async def test_deshabilitado_no_bloquea(self) -> None:
        app = _app((DestructiveGuardMiddleware, {"enabled": False}))
        async with _client(app) as ac:
            r = await ac.delete("/api/v1/templates/abc")
        assert r.status_code in (200, 204)


# ------------------------------------------------------------- security headers


class TestSecurityHeaders:
    async def test_cabeceras_en_todas_las_respuestas(self) -> None:
        app = _app((SecurityHeadersMiddleware, {"hsts": False}))
        async with _client(app) as ac:
            r = await ac.get("/api/v1/ping")
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert r.headers.get("referrer-policy") == "no-referrer"
        assert r.headers.get("content-security-policy") is not None

    async def test_hsts_solo_con_flag(self) -> None:
        app = _app((SecurityHeadersMiddleware, {"hsts": True}))
        async with _client(app) as ac:
            r = await ac.get("/api/v1/ping")
        assert r.headers.get("strict-transport-security") is not None

        app2 = _app((SecurityHeadersMiddleware, {"hsts": False}))
        async with _client(app2) as ac:
            r2 = await ac.get("/api/v1/ping")
        assert r2.headers.get("strict-transport-security") is None


# --------------------------------------------------------------------- auditoría


class TestAuditClassification:
    def test_action_for(self) -> None:
        assert action_for("DELETE", "/api/v1/leads/1") == "delete"
        assert action_for("POST", "/api/v1/emails/send") == "email"
        assert action_for("POST", "/api/v1/searches/1/run") == "scraping"
        assert action_for("PATCH", "/api/v1/settings") == "admin"
        assert action_for("GET", "/api/v1/settings") == "admin"
        assert action_for("POST", "/tracking/unsubscribe/1") == "tracking"

    def test_should_audit(self) -> None:
        assert should_audit("POST", "/api/v1/leads")
        assert should_audit("DELETE", "/api/v1/leads/1")
        assert should_audit("PATCH", "/api/v1/settings")
        assert should_audit("GET", "/api/v1/settings/ai")
        assert should_audit("POST", "/tracking/open/1.gif")
        assert not should_audit("GET", "/api/v1/leads")
        assert not should_audit("GET", "/health")
        # El contrato Hermes se audita en su propio handler (LOOP-16).
        assert not should_audit("POST", "/api/v1/hermes/dispatch")
        assert not should_audit("GET", "/api/v1/hermes/jobs/1")
