"""Excepciones de dominio.

Los Services levantan estas; los Controllers las traducen a HTTP. Un Service
nunca importa `fastapi.HTTPException` — eso lo ata a la capa de transporte y
lo hace inusable desde un worker o un test.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base de todos los errores de negocio.

    `code` es estable y pensado para que el frontend haga `switch` sobre él;
    `message` es para humanos y puede cambiar sin romper clientes.
    """

    code: str = "DOMAIN_ERROR"
    http_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    http_status = 404

    @classmethod
    def for_entity(cls, entity: str, identifier: Any) -> NotFoundError:
        return cls(
            f"{entity} no encontrado: {identifier}",
            code=f"{entity.upper()}_NOT_FOUND",
            details={"id": str(identifier)},
        )


class ConflictError(DomainError):
    """El estado actual del recurso impide la operación (duplicado, etc.)."""

    code = "CONFLICT"
    http_status = 409


class ValidationError(DomainError):
    """Regla de negocio incumplida. Distinto de la validación de schema, que
    la hace Pydantic y devuelve 422."""

    code = "VALIDATION_ERROR"
    http_status = 400


class ForbiddenError(DomainError):
    """Sin permiso para la operación. Relevante cuando llegue el multiusuario."""

    code = "FORBIDDEN"
    http_status = 403


class UnauthorizedError(DomainError):
    """Autenticación ausente o no válida (401). Distinta de Forbidden (403):
    aquí no sabemos ni quién eres."""

    code = "UNAUTHORIZED"
    http_status = 401


class RateLimitedError(DomainError):
    """Límite propio superado (envíos/día, requests al proveedor...).

    `retry_after` (segundos) lo usa el worker para reencolar el job cuando
    vuelva a haber cupo, en vez de gastar un intento. Si es None, se aplica
    backoff exponencial normal.
    """

    code = "RATE_LIMITED"
    http_status = 429

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)
        self.retry_after = retry_after


class ExternalServiceError(DomainError):
    """Falló un tercero: proveedor de correo, scraper, API de IA."""

    code = "EXTERNAL_SERVICE_ERROR"
    http_status = 502


class ConfigurationError(DomainError):
    """Falta configuración para completar la operación.

    Ej.: enviar un correo sin cuenta conectada. Es 400, no 500: el sistema
    funciona, al usuario le falta un paso.
    """

    code = "CONFIGURATION_ERROR"
    http_status = 400
