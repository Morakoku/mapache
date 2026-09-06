"""Logging estructurado con structlog.

Consola legible en local, JSON en producción. Los emails y teléfonos se
ofuscan antes de salir por el log: un log de prospección lleno de datos de
contacto en claro es un problema de privacidad esperando a pasar.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from app.core.config import get_settings

_EMAIL_RE = re.compile(r"\b([\w.+-])[\w.+-]*@([\w-]+\.[\w.-]+)\b")
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s().-]{7,}\d)(?!\w)")

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "smtp_password",
        "imap_password",
        "token",
        "access_token",
        "refresh_token",
        "oauth_access_token",
        "oauth_refresh_token",
        "api_key",
        "secret",
        "encryption_key",
        "authorization",
        "client_secret",
    }
)


def mask_email(value: str) -> str:
    """`juan.perez@empresa.com` -> `j***@empresa.com`."""
    return _EMAIL_RE.sub(r"\1***@\2", value)


def mask_phone(value: str) -> str:
    """Deja los últimos 2 dígitos: `+57 300 123 4567` -> `+57*******67`."""

    def _replace(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) < 6:
            return m.group(1)
        return f"{digits[:2]}{'*' * (len(digits) - 4)}{digits[-2:]}"

    return _PHONE_RE.sub(_replace, value)


def _redact(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Procesador structlog: ofusca PII y elimina secretos."""
    for key, value in list(event_dict.items()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***"
        elif isinstance(value, str):
            event_dict[key] = mask_phone(mask_email(value))
    return event_dict


def setup_logging() -> None:
    settings = get_settings()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level),
    )
    # Uvicorn duplica el acceso si lo dejamos suelto.
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = True

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact,
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings.log_level)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
