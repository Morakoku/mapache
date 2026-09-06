"""Gates de arranque: produccion no puede iniciar con seguridad incompleta."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _base_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "database_url": "postgresql://crm:crm@localhost:5434/crm_test",
        "encryption_key": "x" * 44,
        "secret_key": "test-secret-key",
        "public_base_url": "https://mapache.example.test",
    }
    values.update(overrides)
    return values


def test_local_allows_service_auth_disabled() -> None:
    settings = Settings(**_base_settings(environment="local", service_auth_enabled=False))
    assert settings.service_auth_enabled is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"service_auth_enabled": False}, "SERVICE_AUTH_ENABLED"),
        (
            {"service_auth_enabled": True, "service_token_key": "<placeholder>"},
            "SERVICE_TOKEN_KEY",
        ),
        (
            {
                "service_auth_enabled": True,
                "service_token_key": "managed-test-key",
                "public_base_url": "http://localhost:8000",
            },
            "HTTPS PUBLIC_BASE_URL",
        ),
    ],
)
def test_production_rejects_incomplete_security(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(**_base_settings(environment="production", **overrides))


def test_production_accepts_minimum_service_security() -> None:
    settings = Settings(
        **_base_settings(
            environment="production",
            public_base_url="https://mapache.example.test",
            service_auth_enabled=True,
            service_token_key="managed-test-key",
            tenant_isolation_enforced=True,
        )
    )
    assert settings.is_production is True


def test_production_rejects_tenant_isolation_disabled() -> None:
    with pytest.raises(ValidationError, match="TENANT_ISOLATION_ENFORCED"):
        Settings(
            **_base_settings(
                environment="production",
                public_base_url="https://mapache.example.test",
                service_auth_enabled=True,
                service_token_key="managed-test-key",
                tenant_isolation_enforced=False,
            )
        )
