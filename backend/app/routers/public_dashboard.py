"""Endpoints públicos para el dashboard (sin auth)."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["public-dashboard"])


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
const API='/api/v1/tc';
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


@router.get("/", response_class=HTMLResponse)
async def serve_dashboard() -> str:
    """Serve the control tower dashboard."""
    return DASHBOARD_HTML


@router.get("/status")
async def tc_status() -> dict[str, Any]:
    """Get status of all services."""
    results: dict[str, Any] = {"services": {}, "timestamp": datetime.now(UTC).isoformat()}
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
    t0 = time.time()
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


@router.get("/metrics")
async def tc_metrics() -> dict[str, Any]:
    """Get database metrics."""
    from app.core.postgrest_client import pg_count

    return {
        "companies": await pg_count("companies"),
        "contacts": await pg_count("contacts"),
        "emails_sent": await pg_count("email_messages", {"direction": "OUTBOUND"}),
        "active_jobs": await pg_count("jobs", {"status": "QUEUED"}),
    }
