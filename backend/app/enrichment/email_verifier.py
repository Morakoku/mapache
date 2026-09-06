"""Verificación de emails: sintaxis, MX y heurísticas de calidad.

Deliberadamente **no** hace SMTP `RCPT TO` probing. Es la técnica que daría
"verificación real", pero se comporta como un escaneo, quema la reputación de
la IP de salida y muchos servidores la bloquean o la penalizan. El estado
máximo que afirmamos es `MX_OK`, y la UI lo dice tal cual en vez de fingir
certeza.
"""

from __future__ import annotations

import re

import dns.asyncresolver
import dns.exception

from app.core.enums import VerificationStatus
from app.core.logging import get_logger
from app.enrichment.extractors import is_role_email

logger = get_logger(__name__)

_SYNTAX_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Dominios de correo temporal: quien los usa no quiere que le escriban.
_DISPOSABLE_DOMAINS = frozenset(
    {
        "mailinator.com",
        "guerrillamail.com",
        "10minutemail.com",
        "tempmail.com",
        "throwawaymail.com",
        "yopmail.com",
        "trashmail.com",
        "getnada.com",
        "temp-mail.org",
        "sharklasers.com",
        "maildrop.cc",
        "fakeinbox.com",
    }
)

# Proveedores gratuitos: son válidos para una PYME, pero bajan la confianza
# frente a un email del dominio propio.
_FREE_PROVIDERS = frozenset(
    {
        "gmail.com",
        "hotmail.com",
        "outlook.com",
        "outlook.es",
        "yahoo.com",
        "yahoo.es",
        "live.com",
        "icloud.com",
        "aol.com",
        "protonmail.com",
        "hotmail.es",
        "gmail.es",
    }
)

_mx_cache: dict[str, bool] = {}


def check_syntax(email: str) -> bool:
    if not email or len(email) > 254:
        return False
    return bool(_SYNTAX_RE.fullmatch(email.strip()))


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def is_disposable(email: str) -> bool:
    return domain_of(email) in _DISPOSABLE_DOMAINS


def is_free_provider(email: str) -> bool:
    return domain_of(email) in _FREE_PROVIDERS


async def has_mx_record(domain: str, timeout_s: float = 5.0) -> bool:
    """¿El dominio puede recibir correo?

    Cachea por dominio dentro del proceso: en un lote de enriquecimiento se
    repiten muchísimo y cada consulta DNS cuesta latencia.
    """
    if not domain:
        return False
    if domain in _mx_cache:
        return _mx_cache[domain]

    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = timeout_s
    resolver.timeout = timeout_s

    result = False
    try:
        answers = await resolver.resolve(domain, "MX")
        result = len(answers) > 0
    except (TimeoutError, dns.exception.DNSException):
        # Sin MX pero con A, algunos servidores igualmente aceptan correo.
        try:
            answers = await resolver.resolve(domain, "A")
            result = len(answers) > 0
        except (TimeoutError, dns.exception.DNSException):
            result = False
    except Exception as exc:  # noqa: BLE001
        logger.debug("mx_lookup_failed", domain=domain, error=str(exc))
        result = False

    _mx_cache[domain] = result
    return result


async def verify_email(email: str) -> tuple[VerificationStatus, int]:
    """Devuelve (estado, confianza 0-100).

    La confianza alimenta la elección del email principal de la empresa
    cuando hay varios candidatos.
    """
    email = email.strip().lower()

    if not check_syntax(email):
        return VerificationStatus.INVALID, 0

    if is_disposable(email):
        return VerificationStatus.INVALID, 0

    if not await has_mx_record(domain_of(email)):
        # Sintaxis válida pero el dominio no recibe correo: casi con seguridad
        # es un error tipográfico o un dominio caducado.
        return VerificationStatus.SYNTAX_OK, 25

    confidence = 80
    if is_role_email(email):
        # Buzón genérico: llega a alguien, pero no a una persona concreta.
        confidence -= 20
    if is_free_provider(email):
        # Gmail para un negocio es normal en una PYME, pero es menos señal de
        # profesionalidad que un dominio propio.
        confidence -= 10

    return VerificationStatus.MX_OK, max(confidence, 10)


def reset_mx_cache() -> None:
    """Limpia la caché de MX. Para tests."""
    _mx_cache.clear()
