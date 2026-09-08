"""Punto de entrada de la aplicación."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.audit import AuditMiddleware
from app.core.config import Settings, get_settings
from app.core.container import get_job_queue, get_job_registry
from app.core.database import dispose_engine, session_scope
from app.core.destructive import DestructiveGuardMiddleware
from app.core.enums import JobType
from app.core.exceptions import DomainError
from app.core.headers import SecurityHeadersMiddleware
from app.core.logging import get_logger, setup_logging
from app.core.rate_limit import RateLimitMiddleware
from app.core.scheduler import PeriodicTask, Scheduler
from app.middleware import ServiceAuthMiddleware
from app.routers import api_router, public_router
from app.routers.health import VERSION
from app.services.job_svc import JobService
from app.workers import register_workers

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()
    logger.info(
        "app_starting",
        environment=settings.environment,
        version=VERSION,
        job_queue=settings.job_queue_backend,
    )

    register_workers(get_job_registry())

    scheduler = Scheduler(get_job_queue())
    if settings.scheduler_enabled:
        scheduler.start(
            [
                PeriodicTask(
                    JobType.FOLLOWUP_TICK,
                    every_seconds=settings.followup_tick_minutes * 60,
                    payload={},
                ),
                PeriodicTask(
                    JobType.INBOX_SYNC,
                    every_seconds=settings.inbox_sync_minutes * 60,
                    payload={},
                    initial_delay=60,
                ),
            ]
        )
    else:
        logger.info("scheduler_disabled")

    # Un reinicio deja huérfano lo que estuviera corriendo en la cola en
    # memoria. Se reencola para que la UI no muestre progreso congelado.
    try:
        async with session_scope() as session:
            if session is not None:
                recovered = await JobService(session).recover_stale()
                if recovered:
                    logger.warning("stale_jobs_requeued", count=recovered)
            else:
                logger.info("stale_job_recovery_skipped_no_db")
    except Exception as exc:  # noqa: BLE001 - la app debe arrancar igualmente
        logger.error("stale_job_recovery_failed", error=str(exc))

    yield

    logger.info("app_stopping")
    await scheduler.shutdown()
    await get_job_queue().shutdown()
    await dispose_engine()


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mapache CRM - Torre de Control</title>
<style>
:root{--bg:#0a0a0a;--card:#111;--border:#1a1a1a;--fg:#e0e0e0;--muted:#666;--accent:#ff6b35;--green:#00c853;--red:#ff1744;--yellow:#ffd600;--blue:#2979ff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',monospace;background:var(--bg);color:var(--fg);min-height:100vh;padding:20px}
.header{display:flex;align-items:center;justify-content:space-between;padding:20px 0;border-bottom:1px solid var(--border);margin-bottom:30px;flex-wrap:wrap;gap:15px}
.header h1{font-size:24px;font-weight:700}
.pill{padding:6px 14px;border-radius:20px;font-size:12px;font-weight:600;text-transform:uppercase}
.pill.on{background:rgba(0,200,83,.15);color:var(--green);border:1px solid var(--green)}
.pill.off{background:rgba(255,23,68,.15);color:var(--red);border:1px solid var(--red)}
.pill.warn{background:rgba(255,214,0,.15);color:var(--yellow);border:1px solid var(--yellow)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;margin-bottom:30px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.card-title{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.card-value{font-size:32px;font-weight:700;margin-bottom:4px}
.card-sub{font-size:12px;color:var(--muted)}
.btn{padding:10px 18px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}
.btn-on{background:var(--green);color:#000}
.btn-off{background:var(--red);color:#fff}
.btn-blue{background:var(--blue);color:#fff}
.logs{background:#000;border:1px solid var(--border);border-radius:12px;padding:16px;max-height:300px;overflow-y:auto;font-family:monospace;font-size:12px;line-height:1.6}
.log-e{padding:2px 0}
.log-t{color:var(--muted);margin-right:8px}
.log-ok{color:var(--green)}.log-err{color:var(--red)}.log-warn{color:var(--yellow)}.log-info{color:var(--blue)}
.refresh-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:10px}
.refresh-btn{background:0 0;border:1px solid var(--border);color:var(--fg);padding:8px 14px;border-radius:8px;cursor:pointer;font-size:13px}
.refresh-btn:hover{border-color:var(--accent)}
</style>
</head>
<body>
<div class="header">
<h1>🦝 Mapache CRM</h1>
<div style="display:flex;gap:15px;align-items:center">
<span id="overall" class="pill off">Verificando...</span>
<span style="color:var(--muted);font-size:12px" id="updated">--:--:--</span>
</div>
</div>
<div class="refresh-bar">
<div style="display:flex;gap:10px">
<button class="refresh-btn" onclick="loadAll()">🔄 Refrescar</button>
<button class="refresh-btn" onclick="toggleAuto()">⏱ <span id="auto-txt">ON</span></button>
</div>
<span style="color:var(--muted);font-size:12px">Cada 10s</span>
</div>
<div class="grid" id="services"></div>
<div class="card" style="margin-bottom:30px">
<div class="card-header"><span class="card-title">📊 Métricas</span></div>
<div class="grid" style="margin-top:12px">
<div class="card"><div class="card-sub">Empresas</div><div class="card-value" id="m-companies">--</div></div>
<div class="card"><div class="card-sub">Contactos</div><div class="card-value" id="m-contacts">--</div></div>
<div class="card"><div class="card-sub">Emails enviados</div><div class="card-value" id="m-emails">--</div></div>
<div class="card"><div class="card-sub">Jobs activos</div><div class="card-value" id="m-jobs">--</div></div>
</div>
</div>
<div class="card">
<div class="card-header"><span class="card-title">📋 Logs</span><button class="btn btn-blue" onclick="clearLogs()">Limpiar</button></div>
<div class="logs" id="logs"><div class="log-e"><span class="log-t">--:--:--</span><span class="log-info">Cargando...</span></div></div>
</div>
<script>
const API='/tc-api';
let autoT=null;
let logs=[];
function t(){return new Date().toLocaleTimeString('es-CO')}
function log(msg,l='info'){logs.unshift({t:t(),msg,l});if(logs.length>100)logs.pop();renderLogs()}
function renderLogs(){document.getElementById('logs').innerHTML=logs.map(l=>`<div class="log-e"><span class="log-t">${l.t}</span><span class="log-${l.l}">${l.msg}</span></div>`).join('')}
function clearLogs(){logs=[];renderLogs()}
async function loadAll(){
try{
const[s,m]=await Promise.all([fetch(API+'/status').then(r=>r.json()),fetch(API+'/metrics').then(r=>r.json())]);
renderServices(s);
document.getElementById('m-companies').textContent=m.companies??'--';
document.getElementById('m-contacts').textContent=m.contacts??'--';
document.getElementById('m-emails').textContent=m.emails_sent??'--';
document.getElementById('m-jobs').textContent=m.active_jobs??'--';
document.getElementById('updated').textContent=t();
log('Datos actualizados','ok');
}catch(e){log('Error: '+e.message,'err')}
}
function renderServices(s){
const g=document.getElementById('services');
const sv=[
{k:'database',icon:'🗄️',title:'Base de Datos',desc:'PostgREST + Supabase'},
{k:'email',icon:'📧',title:'Email (Resend)',desc:'Envío de correos'},
{k:'scraper',icon:'🕷️',title:'Google Maps Scraper',desc:'Descubrimiento'},
{k:'api',icon:'⚡',title:'API Mapache',desc:'FastAPI + Vercel'}
];
g.innerHTML=sv.map(x=>{
const d=s.services?.[x.k]||{};
const st=d.status||'warn';
const pc=st==='ok'?'on':st==='error'?'off':'warn';
const pt=st==='ok'?'Operativo':st==='error'?'Error':'Sin datos';
return`<div class="card"><div class="card-header"><div><div class="card-title">${x.title}</div><div style="font-size:18px;margin-top:4px">${x.icon} ${d.message||''}</div></div><span class="pill ${pc}">${pt}</span></div><div class="card-sub">${x.desc}</div>${d.latency?`<div class="card-sub" style="margin-top:6px">Latencia: <strong>${d.latency}ms</strong></div>`:''}${d.detail?`<div class="card-sub" style="color:var(--yellow);margin-top:4px">${d.detail}</div>`:''}</div>`;
}).join('');
const allOk=Object.values(s.services||{}).every(v=>v.status==='ok');
const p=document.getElementById('overall');
p.textContent=allOk?'Todos operativos':'Atención requerida';
p.className='pill '+(allOk?'on':'warn');
}
async function toggleAuto(){
if(autoT){clearInterval(autoT);autoT=null;document.getElementById('auto-txt').textContent='OFF'}
else{autoT=setInterval(loadAll,10000);document.getElementById('auto-txt').textContent='ON'}
}
loadAll();
autoT=setInterval(loadAll,10000);
</script>
</body>
</html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=VERSION,
        description="CRM de prospección, outbound sales y conversión de clientes.",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(ServiceAuthMiddleware, settings=settings)

    # LOOP-13: capas de seguridad sin tocar la funcionalidad. Orden de registro
    # = inverso al de ejecución: SecurityHeaders queda como capa más externa.
    if settings.destructive_confirm_required:
        app.add_middleware(DestructiveGuardMiddleware)
    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            enabled=True,
            window_seconds=settings.rate_limit_window_seconds,
        )
    if settings.audit_enabled:
        app.add_middleware(AuditMiddleware)
    if settings.security_headers_enabled:
        app.add_middleware(SecurityHeadersMiddleware, hsts=settings.hsts_enabled)

    _register_exception_handlers(app)

    # Dashboard de torre de control - ANTES del middleware de auth
    @app.get("/torre-control/", include_in_schema=False)
    async def serve_dashboard():
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=DASHBOARD_HTML)

    @app.get("/tc-api/status", include_in_schema=False)
    async def tc_status():
        return await _get_torre_status()

    @app.get("/tc-api/metrics", include_in_schema=False)
    async def tc_metrics():
        return await _get_torre_metrics()

    app.include_router(public_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


async def _get_torre_status() -> dict:
    """Obtener estado de todos los servicios."""
    import httpx
    import time
    from datetime import UTC, datetime
    
    results: dict = {"services": {}, "timestamp": datetime.now(UTC).isoformat()}
    settings = get_settings()

    # 1) Database
    t0 = time.time()
    try:
        if settings.supabase_url and settings.supabase_service_role_key_value:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(
                    f"{settings.supabase_url.rstrip('/')}/rest/v1/alembic_version?select=version_num&limit=1",
                    headers={"apikey": settings.supabase_service_role_key_value, "Authorization": f"Bearer {settings.supabase_service_role_key_value}"},
                )
                lat = int((time.time() - t0) * 1000)
                results["services"]["database"] = {"status": "ok" if r.status_code == 200 else "error", "latency": lat, "message": "Conectado" if r.status_code == 200 else f"HTTP {r.status_code}"}
        else:
            results["services"]["database"] = {"status": "error", "message": "Sin config"}
    except Exception as e:
        results["services"]["database"] = {"status": "error", "message": str(e)[:80]}

    # 2) Resend
    t0 = time.time()
    try:
        if settings.resend_api_key_value:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get("https://api.resend.com/api-keys", headers={"Authorization": f"Bearer {settings.resend_api_key_value}"})
                lat = int((time.time() - t0) * 1000)
                results["services"]["email"] = {"status": "ok" if r.status_code == 200 else "error", "latency": lat, "message": "Resend OK" if r.status_code == 200 else f"HTTP {r.status_code}"}
        else:
            results["services"]["email"] = {"status": "error", "message": "Sin API key"}
    except Exception as e:
        results["services"]["email"] = {"status": "error", "message": str(e)[:80]}

    # 3) Scraper
    try:
        scraper_url = os.environ.get("SCRAPER_URL", "")
        if scraper_url:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{scraper_url}/health")
                lat = int((time.time() - t0) * 1000)
                results["services"]["scraper"] = {"status": "ok" if r.status_code == 200 else "error", "latency": lat, "message": "Corriendo" if r.status_code == 200 else f"HTTP {r.status_code}"}
        else:
            results["services"]["scraper"] = {"status": "warn", "message": "No configurado", "detail": "SCRAPER_URL no definida"}
    except Exception:
        results["services"]["scraper"] = {"status": "warn", "message": "Detenido", "detail": "No responde"}

    # 4) API self-check
    t0 = time.time()
    try:
        base = settings.public_base_url or "http://localhost:8000"
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{base}/health")
            lat = int((time.time() - t0) * 1000)
            results["services"]["api"] = {"status": "ok" if r.status_code == 200 else "error", "latency": lat, "message": "OK" if r.status_code == 200 else f"HTTP {r.status_code}"}
    except Exception as e:
        results["services"]["api"] = {"status": "error", "message": str(e)[:80]}

    return results


async def _get_torre_metrics() -> dict:
    """Obtener métricas de la base de datos."""
    from app.core.postgrest_client import pg_count
    
    return {
        "companies": await pg_count("companies"),
        "contacts": await pg_count("contacts"),
        "emails_sent": await pg_count("email_messages", {"direction": "OUTBOUND"}),
        "active_jobs": await pg_count("jobs", {"status": "QUEUED"}),
    }


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        """Único punto de traducción dominio -> HTTP.

        Los Services levantan DomainError; nadie por debajo del router conoce
        códigos de estado.
        """
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "SCHEMA_VALIDATION_ERROR",
                    "message": "Datos de entrada inválidos.",
                    "details": {"fields": exc.errors()},
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", path=request.url.path, method=request.method)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Error interno del servidor.",
                    "details": {},
                }
            },
        )


app = create_app()
