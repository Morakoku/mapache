"""Autorización L2 para identidades de servicio (PATCH-04 → LOOP-14).

La autenticación (L1) responde "¿quién eres?"; esta capa responde "¿qué puedes
hacer?". LOOP-14 deja a la identidad `hermes` con el MÍNIMO privilegio del
contrato diseñado en LOOP-09:

    POST /api/v1/hermes/dispatch     → scope `hermes.dispatch`
    GET  /api/v1/hermes/jobs/{id}    → scope `hermes.jobs.read`

Nada más. El resto de los 141 endpoints de Mapache queda **DENY** para
identidades de servicio (least privilege). No hay wildcards, ni scopes
administrativos, ni acceso directo de Hermes a discovery/enrichment/scoring/
CRM/email: Hermes orquesta y Mapache ejecuta.

Los scopes de catálogo (`crm.read`, `scrape.run`, `jobs.read`, …) se conservan
como NOMBRES VÁLIDOS para la futura UI humana, pero **no son grantables a
identidades de servicio**: si una identidad llega con uno de ellos, el set se
rechaza entero (403).
"""

from __future__ import annotations

import re
from collections.abc import Set
from functools import lru_cache

# --------------------------------------------------------- scopes de contrato (Hermes)

SCOPE_DISPATCH_HERMES = "hermes.dispatch"
SCOPE_HERMES_JOBS_READ = "hermes.jobs.read"

# Scopes que UNA IDENTIDAD DE SERVICIO puede tener. Whitelist estricta: solo el
# contrato. Fuera de aquí, una identidad queda DENY.
PERMITTED_SCOPES = frozenset({SCOPE_DISPATCH_HERMES, SCOPE_HERMES_JOBS_READ})

# Scopes PROHIBIDOS para identidades de servicio: si una identidad los trae en
# su set concedido, la identidad se rechaza (403) con motivo FORBIDDEN.
FORBIDDEN_SCOPES = frozenset(
    {"crm.write", "crm.admin", "mail.send", "jobs.cancel", "internal.oauth"}
)

# Catálogo de nombres de scope para la futura UI humana (NO grantables a
# identidades de servicio). Se mantienen como nombres válidos/conocidos.
SCOPE_CRM_READ = "crm.read"
SCOPE_SCRAPE_RUN = "scrape.run"
SCOPE_JOBS_READ = "jobs.read"
UI_SCOPES = frozenset({SCOPE_CRM_READ, SCOPE_SCRAPE_RUN, SCOPE_JOBS_READ})

# Nombres comodín/administrativos que jamás pueden ser un scope.
WILDCARD_NAMES = frozenset({"*", "admin", "all", "full_access", "superuser"})

# Conjunto completo de nombres conocidos (contrato + catálogo + prohibidos).
KNOWN_SCOPES = PERMITTED_SCOPES | UI_SCOPES | FORBIDDEN_SCOPES

_SCOPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


# ----------------------------------------------------------------identity

# Scopes por defecto de la identidad `hermes` (configurable vía HERMES_SCOPES).
DEFAULT_HERMES_SCOPES = PERMITTED_SCOPES


def valid_scope_name(name: str) -> bool:
    """Un scope válido es `dominio.accion`, sin wildcards ni palabras reservadas."""
    if name in WILDCARD_NAMES:
        return False
    return bool(_SCOPE_NAME_RE.match(name))


def validate_scope_set(scopes: Set[str]) -> str | None:
    """Valida el set de scopes concedido a una identidad de servicio.

    Devuelve el motivo de denegación (str) si el set NO es admisible; `None`
    si la identidad es válida. Reglas (todas → DENY):
    - vacío → "EMPTY"
    - nombre inválido/comodín → "WILDCARD"
    - scope prohibido (admin/write/mail…) → "FORBIDDEN"
    - cualquier otro scope fuera de la whitelist del contrato → "NOT_ALLOWED"
      (incluye scopes del catálogo UI como crm.read y nombres desconocidos)
    """
    if not scopes:
        return "EMPTY"
    for scope in scopes:
        if not valid_scope_name(scope):
            return "WILDCARD"
        if scope in FORBIDDEN_SCOPES:
            return "FORBIDDEN"
        if scope not in PERMITTED_SCOPES:
            return "NOT_ALLOWED"
    return None


# ---------------------------------------------------endpoint → scope (contrato, explícito)

# Mapa de autorización del CONTRATO Hermes (LOOP-09). Solo estas dos
# operaciones existen para identidades de servicio; cualquier otra ruta de los
# 141 endpoints no está declarada → scope_for devuelve None → DENY (403).
# Los templates usan la misma forma que las rutas de FastAPI (`{param}`).
_ENDPOINT_SCOPE_MAP = frozenset(
    {
        ("POST", "/api/v1/hermes/dispatch", SCOPE_DISPATCH_HERMES),
        ("GET", "/api/v1/hermes/jobs/{job_id}", SCOPE_HERMES_JOBS_READ),
    }
)


@lru_cache(maxsize=256)
def _template_pattern(template: str) -> re.Pattern[str]:
    escaped = re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", r"[^/]+", template)
    return re.compile(f"^{escaped}$")


def scope_for(method: str, path: str) -> str | None:
    """Scope requerido por `method + path` o `None` si NO está declarado.

    `None` = la operación no está en el contrato → se deniega (403). Nunca se
    infiere por prefijo ni se hereda por ruta.
    """
    upper = method.upper()
    for path_method, template, scope in _ENDPOINT_SCOPE_MAP:
        if path_method == upper and _template_pattern(template).match(path):
            return scope
    return None


def declares(method: str, path: str) -> bool:
    return scope_for(method, path) is not None


def declared_endpoints() -> frozenset[tuple[str, str, str]]:
    """Copia del mapa (para tests que inspeccionen la política)."""
    return frozenset(_ENDPOINT_SCOPE_MAP)


__all__ = [
    "DEFAULT_HERMES_SCOPES",
    "FORBIDDEN_SCOPES",
    "KNOWN_SCOPES",
    "PERMITTED_SCOPES",
    "SCOPE_CRM_READ",
    "SCOPE_DISPATCH_HERMES",
    "SCOPE_HERMES_JOBS_READ",
    "SCOPE_JOBS_READ",
    "SCOPE_SCRAPE_RUN",
    "UI_SCOPES",
    "WILDCARD_NAMES",
    "declared_endpoints",
    "declares",
    "scope_for",
    "valid_scope_name",
    "validate_scope_set",
]
