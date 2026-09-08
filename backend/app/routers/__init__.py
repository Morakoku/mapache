"""Agregador de routers.

`api_router` lleva el prefijo /api/v1 y agrupa los recursos del CRM.
`public_router` va sin prefijo ni autenticación: healthchecks, el pixel de
tracking, la redirección de clicks, el unsubscribe y los callbacks de OAuth.
Esos endpoints los llaman clientes de correo, navegadores de prospectos y
Google/Microsoft — no el frontend.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.routers import (
    auth,
    calls,
    companies,
    contact_queue,
    contacts,
    conversations,
    csv_import,
    email_simple,
    emails,
    followups,
    guaki,
    health,
    hermes,
    jobs,
    leads,
    metrics,
    pipeline,
    scraping,
    searches,
    sequences,
    services,
    settings,
    suppression,
    templates,
    torre_control,
    tracking,
)
from app.routers.security_schemes import SERVICE_BEARER

# Declaración OpenAPI de la autenticación servicio-a-servicio (PATCH-02).
#
# La capa real la aplica `app.middleware.ServiceAuthMiddleware`. Aquí solo se
# describe en el esquema; `HTTPBearer(auto_error=False)` no rechaza nada en
# runtime: devuelve `None` si no hay header. Cuando `SERVICE_AUTH_ENABLED` es
# false (default local) el middleware es pasivo y esto queda documental.
#
# Límite de OpenAPI: el token lleva <client_id>.<timestamp>.<nonce>.<signature>
# firmado con HMAC-SHA256 (TTL 300s, anti-replay por nonce). OpenAPI no puede
# expresar este formato como esquema nativo, así que se declara como HTTP Bearer
# con `bearerFormat` documentando la estructura real.
api_router = APIRouter(dependencies=[Depends(SERVICE_BEARER)])
public_router = APIRouter()

public_router.include_router(health.router)

# Prospección (Fase 2)
api_router.include_router(services.router, prefix="/services", tags=["servicios"])
api_router.include_router(searches.router, prefix="/searches", tags=["búsquedas"])
api_router.include_router(scraping.router, prefix="/scraping", tags=["scraping"])
api_router.include_router(companies.router, prefix="/companies", tags=["empresas"])
api_router.include_router(csv_import.router)
api_router.include_router(contact_queue.router)
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])

# Contrato Hermes (LOOP-16): dispatch + estado de jobs. Nada más.
api_router.include_router(hermes.router, prefix="/hermes", tags=["hermes"])

# Puente comercial Guaki (LOOP-23): prospectos para el Command Center de Guaki.
api_router.include_router(guaki.router, prefix="/guaki", tags=["guaki"])

# CRM (Fase 3)
api_router.include_router(contacts.contacts_router, prefix="/contacts", tags=["contactos"])
api_router.include_router(leads.router, prefix="/leads", tags=["prospectos"])
api_router.include_router(pipeline.router, prefix="/pipeline", tags=["pipeline"])
api_router.include_router(contacts.activities_router, prefix="/activities", tags=["actividades"])
api_router.include_router(contacts.tasks_router, prefix="/tasks", tags=["tareas"])
api_router.include_router(calls.scripts_router, prefix="/call-scripts", tags=["guiones"])
api_router.include_router(calls.router, prefix="/calls", tags=["llamadas"])

# Correo (Fase 4)
api_router.include_router(email_simple.router, prefix="/email-simple", tags=["email-simple"])
api_router.include_router(settings.router, prefix="/settings", tags=["configuración"])
api_router.include_router(templates.router, prefix="/templates", tags=["plantillas"])
api_router.include_router(emails.router, prefix="/emails", tags=["correos"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversaciones"])
api_router.include_router(suppression.router, prefix="/suppression", tags=["supresión"])

# Analítica (Fase 9)
api_router.include_router(metrics.router, prefix="/metrics", tags=["métricas"])

# Automatización (Fase 7)
api_router.include_router(sequences.router, prefix="/sequences", tags=["secuencias"])
api_router.include_router(followups.router, prefix="/follow-ups", tags=["seguimientos"])

# Público: los llaman terceros, no la SPA.
# El tracking va sin prefijo de versión porque sus URLs viven dentro de correos
# ya enviados: cambiarlas rompería los enlaces de todo lo mandado.
public_router.include_router(tracking.router, prefix="/tracking", tags=["tracking"])
public_router.include_router(auth.router, prefix="/auth", tags=["oauth"])
public_router.include_router(email_simple.router, prefix="/email-simple-public", tags=["email-simple-public"])
public_router.include_router(torre_control.router, prefix="/torre-control", tags=["torre-control"])

__all__ = ["api_router", "public_router"]
