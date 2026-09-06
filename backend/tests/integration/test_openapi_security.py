"""PATCH-02/06: la definición OpenAPI describe la autenticación Bearer.

Documental, no funcional: verifica que el esquema declara `ServiceBearerAuth`
(HTTP Bearer con formato HMAC documentado en `bearerFormat`), que el runtime
sigue con `SERVICE_AUTH_ENABLED=false` y sin credenciales, y que los endpoints
públicos — health + callbacks OAuth — quedan fuera de la declaración, mientras
que la gestión de buzones (`/auth/accounts`, `/auth/.../disconnect`) ya queda
protegida (LOOP-06).
"""

from __future__ import annotations

import re

from httpx import AsyncClient

from app.core.config import get_settings
from app.main import create_app


def _schema():
    return create_app().openapi()


def _operations(schema: dict) -> dict[str, dict]:
    operations: dict[str, dict] = {}
    for path, item in schema["paths"].items():
        for method, op in item.items():
            if method in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}:
                operations[f"{method.upper()} {path}"] = op
    return operations


def _public_in_schema(schema: dict) -> list[tuple[str, dict]]:
    return [(name, op) for name, op in _operations(schema).items() if "security" not in op]


def test_openapi_declares_security_schemes() -> None:
    schema = _schema()
    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert schemes, "OpenAPI debe declarar securitySchemes"
    assert "ServiceBearerAuth" in schemes


def test_security_scheme_is_http_bearer() -> None:
    schema = _schema()
    scheme = schema["components"]["securitySchemes"]["ServiceBearerAuth"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    # OpenAPI no puede expresar el token HMAC como OAuth2: se documenta la
    # estructura real en bearerFormat.
    assert scheme["bearerFormat"] == "<client_id>.<timestamp>.<nonce>.<signature>"


def test_all_api_v1_operations_declare_bearer() -> None:
    schema = _schema()
    operations = _operations(schema)
    api_ops = {
        name: op
        for name, op in operations.items()
        if name.split(" ", 1)[1].startswith("/api/v1")
    }
    assert api_ops, "Debe haber operaciones /api/v1"
    for name, op in api_ops.items():
        assert op["security"] == [{"ServiceBearerAuth": []}], f"Falta declaración en {name}"


def test_only_health_operations_are_public() -> None:
    schema = _schema()
    public = _public_in_schema(schema)
    assert [name for name, _ in public] == ["GET /health", "GET /health/ready"]


def test_oauth_accounts_and_disconnect_are_protected() -> None:
    schema = _schema()
    operations = _operations(schema)
    assert operations["GET /auth/accounts"]["security"] == [{"ServiceBearerAuth": []}]
    assert operations["POST /auth/{provider}/disconnect/{account_id}"]["security"] == [
        {"ServiceBearerAuth": []}
    ]


def test_reconciliation_counts() -> None:
    schema = _schema()
    operations = _operations(schema)
    secured = [op for op in operations.values() if "security" in op]
    public = [op for op in operations.values() if "security" not in op]
    # 141 del CRM + 2 del contrato Hermes (dispatch + jobs) + 4 del puente Guaki
    # (GET /guaki/prospects, GET /guaki/funnel, POST /guaki/prospects/{id}/link,
    #  POST /guaki/link/match; LOOP-23/24/26) = 147.
    assert len(operations) == 147
    assert len(secured) == 145
    assert len(public) == 2


def test_no_sensitive_endpoint_is_public() -> None:
    schema = _schema()
    operations = _operations(schema)
    sensitive = (
        "/emails/", "/conversations/", "/suppression/", "/searches/", "/services/",
        "/companies/", "/contacts/", "/leads/", "/pipeline/", "/calls/", "/follow-ups/",
        "/sequences/", "/templates/", "/settings/", "/jobs/", "/tasks/", "/activities/",
        "/call-scripts/",
    )
    for name, op in operations.items():
        method, path = name.split(" ", 1)
        if method == "DELETE" or any(s in path for s in sensitive):
            assert "security" in op, f"Endpoint sensible quedó público: {name}"


def test_authorization_declared_without_scopes() -> None:
    schema = _schema()
    operations = _operations(schema)
    for op in operations.values():
        if "security" in op:
            assert op["security"][0]["ServiceBearerAuth"] == []


def test_runtime_auth_still_disabled() -> None:
    settings = get_settings()
    # LOOP-07: credencial PREPARADA (existe en el entorno) pero la barrera sigue
    # apagada. CREDENTIAL_READY + RUNTIME_AUTH=DISABLED es exactamente el estado
    # esperado al terminar este loop.
    assert settings.service_auth_enabled is False
    assert settings.service_token_key is not None
    assert (
        settings.service_token_key.get_secret_value() != "<placeholder>"
    ), "el placeholder no es una credencial real"


def test_openapi_does_not_expose_secrets() -> None:
    schema = _schema()
    text = str(schema)
    # El nombre de la variable SERVICE_TOKEN_KEY aparece como documentación en
    # la descripción del esquema; lo que no puede aparecer es el VALOR.
    assert re.search(r"SERVICE_TOKEN_KEY\s*=\s*\S+", text) is None
    assert re.search(r"[0-9a-fA-F]{64}", text) is None
    assert "-----BEGIN" not in text
    assert "sk-" not in text


async def test_health_public_continues_working(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_continues_working(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


async def test_api_v1_does_not_block_when_auth_disabled(client: AsyncClient) -> None:
    response = await client.get("/api/v1/leads")
    assert response.status_code != 401