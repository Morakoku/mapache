"""LOOP-07: identidad de servicio Hermes — la credencial existe, nunca se expone.

Cinco demostraciones de comportamiento (valida/inválido/expirado/replay/identidad)
que ya viven en `test_service_auth.py`; LOOP-07 añade las **4 garantes de no-exposición**
y las re-etiqueta explícitas para esta fase:

    1. service_token_key existe en el entorno de prueba.
    2. nunca aparece su valor en logs.
    3. nunca aparece en excepciones.
    4. token válido verifica.
    5. token inválido falla.
    6. timestamp expirado falla.
    7. nonce repetido falla.
    8. firma incorrecta falla.
    9. client_id no autorizado falla.

Mismas reglas que el resto: solo secretos de TEST, nunca los de producción.
"""

from __future__ import annotations

import logging
import re
import time
import traceback
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.security import (
    NonceStore,
    ServiceTokenError,
    issue_service_token,
    verify_service_token,
)
from app.middleware import ServiceAuthMiddleware

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_TEST_PATH = BACKEND_DIR / ".env.test"
ENV_EXAMPLE_PATH = BACKEND_DIR / ".env.example"


def _test_key() -> str:
    """Clave de TEST cargada del entorno (conftest: .env.test → os.environ)."""
    key = get_settings().service_token_key.get_secret_value()
    assert key, "SERVICE_TOKEN_KEY debe existir en el entorno de pruebas"
    assert key != "<placeholder>", "el placeholder no vale como clave real"
    return key


def _make_token(
    *,
    client_id: str = "hermes",
    key: str | None = None,
    now: int | None = None,
    nonce: str | None = None,
) -> str:
    return issue_service_token(client_id, key or _test_key(), timestamp=now, nonce=nonce)


def _app_with_auth() -> FastAPI:
    s = get_settings()
    app = FastAPI()

    @app.get("/api/v1/ping")
    async def ping(request: Request) -> dict:
        return {"ok": True, "client": getattr(request.state, "service_client", None)}

    service_settings = s.model_copy(
        update={
            "service_auth_enabled": True,
            "service_token_key": s.service_token_key,
            "service_token_ttl_seconds": 300,
        }
    )
    app.add_middleware(
        ServiceAuthMiddleware,
        settings=service_settings,
        nonce_store=NonceStore(ttl_seconds=300),
    )
    return app


# ---------------------------------------------------------------------- 1. presente


class TestKeyPresentInTestEnv:
    def test_service_token_key_exists_and_is_not_placeholder(self) -> None:
        assert _test_key() != ""
        assert not _test_key().isspace()
        assert "<placeholder>" not in _test_key()

    def test_trusted_client_ids_contains_hermes(self) -> None:
        assert "hermes" in get_settings().trusted_client_ids

    def test_env_example_only_holds_placeholder(self) -> None:
        """El .env.example nunca puede llevar un valor real: solo <placeholder>."""
        text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        m = re.search(r"^SERVICE_TOKEN_KEY=(.*)$", text, flags=re.MULTILINE)
        assert m, "SERVICE_TOKEN_KEY debe existir en .env.example"
        assert m.group(1).strip() == "<placeholder>"
        # y el valor de TEST no puede colarse ahí tampoco
        assert _test_key() not in text


# ------------------------------------------------------------- 2. nunca en logs


class TestSecretNotInLogs:
    async def test_auth_flow_logs_never_contain_secret(self, caplog) -> None:
        key = _test_key()
        app = _app_with_auth()
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

        with caplog.at_level(logging.DEBUG):
            async with c:
                await c.get("/api/v1/ping")  # sin token -> 401
                forged = _make_token().rsplit(".", 1)[0] + "." + ("A" * 64)
                await c.get("/api/v1/ping", headers={"Authorization": f"Bearer {forged}"})
                good = _make_token()
                await c.get("/api/v1/ping", headers={"Authorization": f"Bearer {good}"})

        assert key not in caplog.text

    def test_repr_of_settings_masks_secret(self) -> None:
        assert _test_key() not in repr(get_settings())
        # model_dump(mode="json") deja "**********" para SecretStr
        dumped = get_settings().model_dump(mode="json")
        assert _test_key() not in repr(dumped)


# -------------------------------------------------------------- 3. nunca en excepciones


class TestSecretNotInExceptions:
    def test_error_string_and_traceback_never_contain_secret(self) -> None:
        key = _test_key()
        bad_tokens = [
            "solo.puntos",
            _make_token(nonce="n1"),
            None,
            _make_token(now=int(time.time()) - 6000),
        ]  # malformado, replay, ausente, expirado
        secrets_seen = []

        for tok in bad_tokens:
            try:
                verify_service_token(tok, key=key, ttl_seconds=300)
            except ServiceTokenError as exc:
                rendered = f"{exc}" + "\n" + traceback.format_exc()
                if key in rendered:
                    secrets_seen.append(rendered[:200])

        assert secrets_seen == [], f"la clave apareció en {len(secrets_seen)} excepciones"


# ------------------------------------------------------------ 4..9. comportamiento


class TestBehaviour:
    def test_valid_token_verifies(self) -> None:
        key = _test_key()
        tok = _make_token(key=key)
        assert verify_service_token(tok, key=key, ttl_seconds=300) == "hermes"

    def test_invalid_token_fails(self) -> None:
        key = _test_key()
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token("cualquier.cosa.con-tres-puntos", key=key, ttl_seconds=300)
        assert ei.value.code == "SERVICE_TOKEN_INVALID"

    def test_expired_timestamp_fails(self) -> None:
        key = _test_key()
        old = _make_token(key=key, now=int(time.time()) - 10_000)
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token(old, key=key, ttl_seconds=300)
        assert ei.value.code == "SERVICE_TOKEN_EXPIRED"

    def test_replayed_nonce_fails(self) -> None:
        key = _test_key()
        store = NonceStore(ttl_seconds=300)
        tok = _make_token(key=key, nonce="nonce-fijo-replay")
        assert verify_service_token(tok, key=key, ttl_seconds=300, nonce_store=store) == "hermes"
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token(tok, key=key, ttl_seconds=300, nonce_store=store)
        assert ei.value.code == "SERVICE_TOKEN_REPLAY"

    def test_wrong_signature_fails(self) -> None:
        key = _test_key()
        parts = _make_token(key=key).split(".")
        parts[-1] = "F" * 64
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token(".".join(parts), key=key, ttl_seconds=300)
        assert ei.value.code == "SERVICE_TOKEN_BAD_SIGNATURE"

    def test_unknown_client_id_fails(self) -> None:
        """Identidad no autorizada se rechaza aunque la firma sea válida."""
        key = _test_key()
        tok = issue_service_token("attacker", key)
        with pytest.raises(ServiceTokenError) as ei:
            verify_service_token(tok, key=key, ttl_seconds=300, allowed_client_ids=("hermes",))
        assert ei.value.code == "SERVICE_TOKEN_UNKNOWN_CLIENT"