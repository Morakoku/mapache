"""Torre de control Mapache CRM - 100% funcional.

Además del dashboard y los checks de salud, expone el estado del pipeline
de scraping (empresas, enriquecimiento, leads, runs del scheduler) y el
contacto manual de leads por WhatsApp: lista de contactables (empresa con
teléfono), generación de copys de primer contacto en dos variantes, y
registro de intentos con el mismo insert en activities que usa
/api/v1/contact-queue.

Se monta en public_router bajo el prefijo /torre-control (dashboard
público, sin auth de servicio): el ServiceAuthMiddleware ya trata
/torre-control como prefijo público y el SecurityHeadersMiddleware lo
exime del CSP restrictivo.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote as url_quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["torre-control"])


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
.header .logo{color:var(--accent)}
.pill{padding:6px 14px;border-radius:20px;font-size:12px;font-weight:600;text-transform:uppercase}
.pill.on{background:rgba(0,200,83,.15);color:var(--green);border:1px solid var(--green)}
.pill.off{background:rgba(255,23,68,.15);color:var(--red);border:1px solid var(--red)}
.pill.warn{background:rgba(255,214,0,.15);color:var(--yellow);border:1px solid var(--yellow)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;margin-bottom:30px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;transition:border-color .2s}
.card:hover{border-color:var(--accent)}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.card-title{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.card-value{font-size:32px;font-weight:700;margin-bottom:4px}
.card-sub{font-size:12px;color:var(--muted)}
.btn{padding:10px 18px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s}
.btn-on{background:var(--green);color:#000}
.btn-off{background:var(--red);color:#fff}
.btn-blue{background:var(--blue);color:#fff}
.btn:disabled{opacity:.5;cursor:not-allowed}
.logs{background:#000;border:1px solid var(--border);border-radius:12px;padding:16px;max-height:350px;overflow-y:auto;font-family:'SF Mono','Fira Code',monospace;font-size:12px;line-height:1.6}
.log-e{padding:2px 0;border-bottom:1px solid rgba(255,255,255,.03)}
.log-t{color:var(--muted);margin-right:8px}
.log-ok{color:var(--green)}.log-err{color:var(--red)}.log-warn{color:var(--yellow)}.log-info{color:var(--blue)}
.refresh-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:10px}
.refresh-btn{background:0 0;border:1px solid var(--border);color:var(--fg);padding:8px 14px;border-radius:8px;cursor:pointer;font-size:13px}
.refresh-btn:hover{border-color:var(--accent)}
.btn-sm{padding:6px 12px;font-size:12px}
.btn-accent{background:var(--accent);color:#000}
.btn-outline{background:0 0;border:1px solid var(--border);color:var(--fg)}
.btn-outline:hover{border-color:var(--accent)}
.wa-list{display:flex;flex-direction:column;gap:10px;margin-top:12px}
.wa-item{border:1px solid var(--border);border-radius:10px;padding:16px}
.wa-item.done{border-color:var(--green)}
.wa-row{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.wa-name{font-size:15px;font-weight:700}
.wa-meta{font-size:12px;color:var(--muted);margin-top:3px}
.wa-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.wa-panel{display:none;margin-top:14px;border-top:1px solid var(--border);padding-top:14px}
.wa-panel.open{display:block}
.wa-tabs{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.wa-tab{background:0 0;border:1px solid var(--border);color:var(--muted);padding:6px 12px;border-radius:20px;cursor:pointer;font-size:12px;font-weight:600}
.wa-tab.active{border-color:var(--accent);color:var(--accent)}
.wa-text{width:100%;min-height:110px;background:#000;border:1px solid var(--border);color:var(--fg);border-radius:8px;padding:10px;font-family:inherit;font-size:13px;line-height:1.5;resize:vertical}
.wa-text:focus{outline:none;border-color:var(--accent)}
.wa-count{font-size:11px;color:var(--muted);text-align:right;margin-top:4px}
.wa-count.over{color:var(--red)}
</style>
</head>
<body>
<div class="header">
<h1><span class="logo">🦝</span> Mapache CRM</h1>
<div style="display:flex;gap:15px;align-items:center">
<span id="overall" class="pill off">Verificando...</span>
<span style="color:var(--muted);font-size:12px" id="updated">--:--:--</span>
</div>
</div>
<div class="refresh-bar">
<div style="display:flex;gap:10px">
<button class="refresh-btn" onclick="loadAll();loadPipeline();loadWhatsApp()">🔄 Refrescar</button>
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
<div class="card" style="margin-bottom:30px">
<div class="card-header"><span class="card-title">Pipeline de scraping</span><span style="font-size:12px;color:var(--muted)" id="pl-updated">--</span></div>
<div class="grid" style="margin-top:12px">
<div class="card"><div class="card-sub">Empresas totales</div><div class="card-value" id="pl-companies">--</div></div>
<div class="card"><div class="card-sub">Enriquecidas</div><div class="card-value" id="pl-enriched">--</div></div>
<div class="card"><div class="card-sub">Pendientes de enriquecer</div><div class="card-value" id="pl-pending">--</div></div>
<div class="card"><div class="card-sub">Leads</div><div class="card-value" id="pl-leads">--</div></div>
</div>
<div class="card-sub" style="margin-top:10px">Ultimos runs del scheduler: <span id="pl-runs">--</span></div>
</div>
<div class="card" style="margin-bottom:30px">
<div class="card-header">
<span class="card-title">📱 Contacto WhatsApp Manual</span>
<span style="font-size:12px;color:var(--muted)"><span id="wa-total">--</span> contactables / <span id="wa-sin-tel">--</span> sin teléfono</span>
</div>
<div class="wa-list" id="wa-list"><div class="card-sub">Cargando leads...</div></div>
<div class="card-sub" style="margin-top:10px">Genera copys, edítalos si quieres, abre WhatsApp con el texto listo y marca el intento. Los copys usan los datos reales de la ficha del negocio.</div>
</div>
<div class="card">
<div class="card-header"><span class="card-title">📋 Logs</span><button class="btn btn-blue" onclick="clearLogs()">Limpiar</button></div>
<div class="logs" id="logs"><div class="log-e"><span class="log-t">--:--:--</span><span class="log-info">Cargando...</span></div></div>
</div>
<script>
const API='/torre-control';
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
// --- Pipeline de scraping ---
// No va en el auto-refresh de 10s: son counts de miles de filas contra
// PostgREST; se carga al abrir y con el boton de refrescar.
async function loadPipeline(){
try{
const r=await fetch(API+'/pipeline',{cache:'no-store'});
if(!r.ok)throw new Error('HTTP '+r.status);
const d=await r.json();
document.getElementById('pl-companies').textContent=d.companies_total??'--';
document.getElementById('pl-enriched').textContent=d.companies_enriched??'--';
document.getElementById('pl-pending').textContent=d.companies_pending_enrich??'--';
document.getElementById('pl-leads').textContent=d.leads_total??'--';
document.getElementById('pl-updated').textContent='actualizado '+t();
const runs=(d.recent_runs||[]).slice(0,3).map(r=>`${r.status||'?'} (${r.results_new??0} nuevas)`);
document.getElementById('pl-runs').textContent=runs.length?runs.join(' | '):'sin runs registrados';
}catch(e){document.getElementById('pl-runs').textContent='Error: '+e.message}
}
// --- Contacto WhatsApp Manual ---
let waData=[];
let waState={}; // por lead_id: {copies, active, contacted}
async function loadWhatsApp(){
try{
const r=await fetch(API+'/contact-whatsapp',{cache:'no-store'});
const d=await r.json();
if(d.error){log('WA: '+d.error,'err');return}
waData=d.contactables||[];
document.getElementById('wa-total').textContent=d.total??'--';
document.getElementById('wa-sin-tel').textContent=d.sin_telefono??'--';
renderWhatsApp();
log('WhatsApp: '+d.total+' contactables','ok');
}catch(e){log('WhatsApp: '+e.message,'err')}
}
function esc(s){const d=document.createElement('div');d.textContent=s??'';return d.innerHTML}
function renderWhatsApp(){
const el=document.getElementById('wa-list');
if(!waData.length){el.innerHTML='<div class="card-sub">No hay leads con teléfono por ahora.</div>';return}
el.innerHTML=waData.map(l=>{
const st=waState[l.lead_id]||{};
const done=st.contacted;
return`<div class="wa-item${done?' done':''}" id="wa-${l.lead_id}">
<div class="wa-row">
<div>
<div class="wa-name">${esc(l.company)}</div>
<div class="wa-meta">${esc(l.category)||'Sin categoría'} · ${esc(l.phone_display)}${l.rating!=null?' · ⭐ '+l.rating:''}${l.city?' · '+esc(l.city):''}</div>
</div>
<div class="wa-actions">
<button class="btn btn-sm btn-accent" onclick="waCopys('${l.lead_id}')">Generar copys</button>
<button class="btn btn-sm btn-outline" id="wa-mark-${l.lead_id}" onclick="waMark('${l.lead_id}')" ${done?'disabled':''}>${done?'Contactado ✓':'Marcar contactado'}</button>
</div>
</div>
<div class="wa-panel" id="wa-panel-${l.lead_id}"${st.open?' data-open="1"':''}>
<div class="wa-tabs">
<button class="wa-tab" id="wa-tab-A-${l.lead_id}" onclick="waPick('${l.lead_id}',0)">variante A</button>
<button class="wa-tab" id="wa-tab-B-${l.lead_id}" onclick="waPick('${l.lead_id}',1)">variante B</button>
</div>
<textarea class="wa-text" id="wa-text-${l.lead_id}" oninput="waCount('${l.lead_id}')"></textarea>
<div class="wa-count" id="wa-count-${l.lead_id}">0/300</div>
<div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
<button class="btn btn-sm btn-on" onclick="waOpen('${l.lead_id}')">💬 Abrir WhatsApp</button>
</div>
</div>
</div>`;
}).join('');
// Restaurar estado tras el re-render (el refresh cada 10s no debe pisar lo
// que el usuario editó ni cerrar paneles abiertos)
for(const l of waData){
const st=waState[l.lead_id];if(!st||!st.copies)continue;
if(st.open||document.getElementById(`wa-panel-${l.lead_id}`)?.dataset.open==='1'){
document.getElementById(`wa-panel-${l.lead_id}`).classList.add('open');
st.open=true;
}
if(st.open){
const ta=document.getElementById(`wa-text-${l.lead_id}`);
if(st.edited!=null){ta.value=st.edited}else{ta.value=st.copies[st.active||0].text}
document.getElementById(`wa-tab-A-${l.lead_id}`).classList.toggle('active',(st.active||0)===0);
document.getElementById(`wa-tab-B-${l.lead_id}`).classList.toggle('active',(st.active||0)===1);
waCount(l.lead_id);
}
}
}
function waFind(id){return waData.find(l=>l.lead_id===id)}
async function waCopys(id){
try{
const r=await fetch(API+`/contact-whatsapp/${id}/copy`,{method:'POST'});
if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||('HTTP '+r.status))}
const d=await r.json();
waState[id]={...waState[id],copies:d.copies,active:0,phone:d.phone,open:true,edited:null};
document.getElementById(`wa-panel-${id}`).classList.add('open');
waPick(id,0);
log('Copys de '+d.company+' listos','ok');
}catch(e){log('Copys: '+e.message,'err')}
}
function waPick(id,idx){
const st=waState[id];if(!st)return;
st.active=idx;st.edited=null;
document.getElementById(`wa-text-${id}`).value=st.copies[idx].text;
document.getElementById(`wa-tab-A-${id}`).classList.toggle('active',idx===0);
document.getElementById(`wa-tab-B-${id}`).classList.toggle('active',idx===1);
waCount(id);
}
function waCount(id){
const ta=document.getElementById(`wa-text-${id}`);if(!ta)return;
const st=waState[id];
if(st)st.edited=ta.value;
const c=document.getElementById(`wa-count-${id}`);
c.textContent=ta.value.length+'/300';
c.className='wa-count'+(ta.value.length>300?' over':'');
}
function waOpen(id){
const st=waState[id];
if(!st||!st.copies){log('Primero genera los copys','warn');return}
const texto=document.getElementById(`wa-text-${id}`).value.trim();
if(!texto){log('El copy está vacío','warn');return}
const link=`https://wa.me/${st.phone}?text=${encodeURIComponent(texto)}`;
log('Abriendo WhatsApp para '+(waFind(id)?.company||'lead'),'ok');
window.open(link,'_blank');
}
async function waMark(id){
try{
const r=await fetch(API+`/contact-whatsapp/${id}/attempt`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({result:'sent'})});
if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||('HTTP '+r.status))}
const l=waFind(id);
waState[id]={...waState[id],contacted:true};
document.getElementById(`wa-mark-${id}`).textContent='Contactado ✓';
document.getElementById(`wa-mark-${id}`).disabled=true;
document.getElementById(`wa-${id}`).classList.add('done');
log('Intento registrado: '+(l?.company||id),'ok');
}catch(e){log('Marcar: '+e.message,'err')}
}
async function toggleAuto(){
if(autoT){clearInterval(autoT);autoT=null;document.getElementById('auto-txt').textContent='OFF'}
else{autoT=setInterval(loadAll,10000);document.getElementById('auto-txt').textContent='ON'}
}
loadAll();
loadPipeline();
loadWhatsApp();
autoT=setInterval(loadAll,10000);
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return DASHBOARD_HTML


# /torre-control sin slash: sin este alias Starlette responderia 307 hacia
# /torre-control/ (redirect_slashes). Se registra explicitamente para que la
# URL canonical sirva el HTML con 200 directo.
@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_sin_slash() -> str:
    return DASHBOARD_HTML


@router.get("/status")
async def status() -> dict[str, Any]:
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

    # 3) Scraper — dos modos (mismo criterio que /api/v1/control/status):
    #    a) SCRAPER_URL definida (servidor local): health check HTTP.
    #    b) Sin SCRAPER_URL (Vercel): verificar actividad reciente en companies
    #       vía PostgREST — el scheduler local inserta ahí; empresas recientes
    #       = scraper vivo.
    t0 = time.time()
    try:
        scraper_url = os.environ.get("SCRAPER_URL", "")
        if scraper_url:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{scraper_url}/health")
                lat = int((time.time() - t0) * 1000)
                results["services"]["scraper"] = {"status": "ok" if r.status_code == 200 else "error", "latency": lat, "message": "Corriendo" if r.status_code == 200 else f"HTTP {r.status_code}"}
        else:
            from app.core.supabase_http import select as pg_select

            rows = await pg_select(
                "companies", columns="created_at", order="created_at.desc", limit=1
            )
            lat = int((time.time() - t0) * 1000)
            if rows:
                last = rows[0].get("created_at", "")
                results["services"]["scraper"] = {
                    "status": "ok",
                    "latency": lat,
                    "message": f"Scheduler activo (última empresa: {str(last)[:19]})",
                }
            else:
                results["services"]["scraper"] = {
                    "status": "warn",
                    "message": "Sin datos aún",
                    "detail": "El scheduler local aún no ha guardado empresas",
                }
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
async def metrics() -> dict[str, Any]:
    """Métricas de volumen con conteo exacto (Prefer: count=exact).

    pg_count satura en 1000: PostgREST de Supabase tiene max-rows=1000 por
    defecto y un select con limit=9999 devuelve solo 1000 filas. Con 9688
    empresas el dashboard mostraria 1000; el conteo exacto via Content-Range
    devuelve el total real.
    """
    from app.core.supabase_http import pg_count_exact

    return {
        "companies": await pg_count_exact("companies"),
        "contacts": await pg_count_exact("contacts"),
        "emails_sent": await pg_count_exact("email_messages", filters={"direction": "eq.OUTBOUND"}),
        "active_jobs": await pg_count_exact("jobs", filters={"status": "eq.QUEUED"}),
    }


@router.get("/pipeline")
async def pipeline() -> dict[str, Any]:
    """Estado del pipeline de scraping: verticales, actividad reciente y salud.

    Refleja lo que el scheduler local (randomizado) está haciendo:
    - companies totales y enriquecidas (last_enriched_at no nulo), con
      conteo exacto vía Prefer: count=exact (Content-Range), no limit=1000
    - leads activos (el paso company -> lead del embudo)
    - últimos runs del scheduler (search_runs) con su query y resultado
    """
    from app.core.supabase_http import pg_count_exact, select as pg_select

    out: dict[str, Any] = {"timestamp": datetime.now(UTC).isoformat()}

    # Empresas totales y enriquecidas: conteo exacto (el scheduler ya pasa
    # de 9000; un select con limit nunca reflejaria el total real).
    total = await pg_count_exact("companies")
    enriched = await pg_count_exact("companies", filters={"last_enriched_at": "not.is.null"})
    out["companies_total"] = total
    out["companies_enriched"] = enriched
    out["companies_pending_enrich"] = total - enriched

    # Leads activos (paso 2 del embudo)
    leads = await pg_select(
        "leads", columns="id,status", limit=1000
    )
    out["leads_total"] = len(leads)
    out["leads_by_status"] = {}
    for l in leads:
        st = l.get("status") or "?"
        out["leads_by_status"][st] = out["leads_by_status"].get(st, 0) + 1

    # Últimos runs del scheduler (search_runs más recientes)
    runs = await pg_select(
        "search_runs",
        columns="id,status,provider,results_found,results_new,results_duplicate,started_at,finished_at",
        order="started_at.desc",
        limit=5,
    )
    out["recent_runs"] = runs

    return out


# ---------------------------------------------------------------------------
# Contacto manual por WhatsApp
#
# Veyra Soluciones (agencia B2B de Edwin) escribe a negocios locales para
# ofrecer automatización con IA y presencia digital. El copy es de primer
# contacto, corto (WhatsApp no es email) y suena a persona: sin lenguaje de
# vendedor, sin "estamos emocionados de", sin triadas ni frases hechas de IA.
# ---------------------------------------------------------------------------

# Fragmentos que delatan copy generado por plantilla de IA. Si el generador
# los produce, el endpoint los reporta en el log para poder ajustar los
# templates.
_FRASES_PROHIBIDAS = [
    "estamos emocionados",
    "nos encantaría",
    "no solo",
    "no solamente",
    "el corazón de",
    "en el corazón de",
    "sumergirse",
    "adentrarnos",
    "permítame",
    "permítenos",
    "revolucionar",
    "elevar",
    "impulsar",
    "soluciones integrales",
    "de vanguardia",
    "a la medida",
    "comprometidos con",
    "en constante evolución",
]


def _limpiar_texto(texto: str) -> str:
    """Normaliza espacios y recorta a un máximo de caracteres.

    WhatsApp no es email: el copy tiene que caber en una pantalla de
    teléfono sin scroll. 300 caracteres ya es largo para un primer mensaje.
    """
    texto = " ".join(texto.split())
    if len(texto) > 300:
        texto = texto[:297].rstrip() + "..."
    return texto


def _generar_copys_whatsapp(
    company_name: str,
    category: str | None,
    city: str | None,
) -> list[dict[str, str]]:
    """Genera dos variantes de copy de primer contacto por WhatsApp.

    Variante A (directa / pregunta): presentación mínima y una pregunta
    concreta sobre la operación del negocio. El interlocutor entiende en
    dos líneas quién escribe y qué se le propone.

    Variante B (contexto / observación): abre con un dato verificable del
    negocio (categoría y ciudad de su ficha de Google Maps), explica en una
    línea qué hace Veyra, y cierra con una pregunta de sí/no para bajar la
    fricción de responder.

    Los templates usan únicamente datos reales de la ficha: nombre,
    categoría y ciudad. No se inventan detalles del negocio.
    """
    negocio = company_name.strip() or "su negocio"
    categoria = (category or "").strip()
    ciudad = (city or "").strip()

    # Trato de igual a igual: "tú" estándar colombiano, sin corporate speak.
    texto_a = (
        f"Hola, ¿{negocio}? Soy Edwin, de Veyra Soluciones. "
        "Trabajamos con negocios locales automatizando lo que hoy les quita "
        "tiempo: responder mensajes, agendar y mantener su info al día. "
        "¿Les pasa que las consultas de clientes llegan en horas en las que "
        "nadie puede contestar?"
    )

    if categoria and ciudad:
        texto_b = (
            f"Hola. Encontré la ficha de {negocio} ({categoria}, en {ciudad}) "
            "y de ahí este mensaje. Con Veyra Soluciones ayudamos a negocios "
            "como el suyo a responder consultas fuera de horario y aparecer "
            "mejor en las búsquedas. ¿Le interesa que le cuente en dos "
            "mensajes cómo funciona?"
        )
    elif categoria:
        texto_b = (
            f"Hola. Vi la ficha de {negocio} ({categoria}) en Google y de ahí "
            "este mensaje. Con Veyra Soluciones ayudamos a negocios locales a "
            "responder consultas fuera de horario y aparecer mejor en las "
            "búsquedas. ¿Le cuento en dos mensajes cómo funciona?"
        )
    else:
        texto_b = (
            f"Hola. Vi la ficha de {negocio} en Google y de ahí este mensaje. "
            "Con Veyra Soluciones ayudamos a negocios locales a responder "
            "consultas fuera de horario y aparecer mejor en las búsquedas. "
            "¿Le cuento en dos mensajes cómo funciona?"
        )

    copys = [
        {"label": "variante A", "style": "directo / pregunta", "text": _limpiar_texto(texto_a)},
        {"label": "variante B", "style": "contexto / observación", "text": _limpiar_texto(texto_b)},
    ]

    # Autochequeo anti-patrones: si un template degenerara en lenguaje de
    # vendedor, que quede registrado y sea visible en el log, no silencioso.
    for c in copys:
        texto_bajo = c["text"].lower()
        for frase in _FRASES_PROHIBIDAS:
            if frase in texto_bajo:
                logger.warning(
                    "whatsapp_copy_patron_prohibido",
                    company=company_name,
                    frase=frase,
                )

    return copys


def _telefono_e164(
    phone: str | None, phone_raw: str | None, whatsapp: str | None = None
) -> str | None:
    """Devuelve el número apto para wa.me: solo dígitos, con indicativo país.

    wa.me/<numero> exige el formato internacional sin "+" ni espacios. Se
    prefiere `phone` (debería ser E.164) y de último `phone_raw` tal como lo
    trae la ficha de Google Maps.

    Reglas:
    - Si el valor trae "+" y tiene 8 a 15 dígitos, se usa tal cual: E.164
      es válido por definición.
    - Heurística Colombia para números sin indicativo: 10 dígitos
      empezando en 3 (celular) o 60 (fijo con indicativo de ciudad).
    - Antes de extraer dígitos se corta la extensión (ext/x/anexo), que de
      lo contrario contamina el número.
    Lo que no calza no se devuelve: un link wa.me con número malformado
    abre un chat que no existe y el intento se pierde sin rastro.
    """
    for crudo in (phone, whatsapp, phone_raw):
        if not crudo:
            continue
        # Cortar extensiones telefonicas: "ext", "x" o "anexo" seguido de digitos
        base = re.split(r"\b(?:ext?\.?|x\.?|anexo)\b", crudo, flags=re.IGNORECASE)[0]
        digitos = re.sub(r"\D", "", base)
        if not digitos:
            continue
        # E.164 explicito: confiar tal cual
        if base.strip().startswith("+") and 8 <= len(digitos) <= 15:
            return digitos
        # Heuristicas Colombia (el scraper opera ahi)
        if len(digitos) == 12 and digitos.startswith("57"):
            return digitos
        if len(digitos) == 10 and (digitos.startswith("3") or digitos.startswith("60")):
            return "57" + digitos
    return None


def _wa_link(numero: str, texto: str) -> str:
    """Link wa.me con el texto precargado en el campo del chat."""
    return f"https://wa.me/{numero}?text={url_quote(texto)}"


@router.get("/contact-whatsapp")
async def contact_whatsapp(limit: int = 50) -> dict[str, Any]:
    """Leads contactables por WhatsApp: empresa con teléfono y datos de ficha.

    El JOIN leads x companies se hace vía PostgREST con resource embedding
    (select=*,companies(...)); la relación se llama "companies" porque así
    se llama la tabla referenciada por leads.company_id. OJO: el esquema
    desplegado en Supabase no tiene la columna companies.whatsapp, así que
    no se referencia (phone y phone_raw son las fuentes de número). Se
    devuelve cada lead una sola vez con los datos de su empresa; los que no
    tienen teléfono se cuentan aparte.
    """
    from app.core.supabase_http import select as pg_select

    try:
        filas = await pg_select(
            "leads",
            columns="id,status,company_id,companies(name,phone,phone_raw,category,rating,address,city)",
            order="created_at.desc",
            limit=limit,
        )
    except Exception as exc:
        logger.error("contact_whatsapp_select_error", error=str(exc))
        return {"contactables": [], "total": 0, "sin_telefono": 0, "error": str(exc)}

    contactables: list[dict[str, Any]] = []
    sin_telefono = 0

    for fila in filas:
        comp = fila.get("companies") or {}
        numero = _telefono_e164(comp.get("phone"), comp.get("phone_raw"))
        if not numero:
            sin_telefono += 1
            continue
        contactables.append(
            {
                "lead_id": fila.get("id"),
                "status": fila.get("status"),
                "company_id": fila.get("company_id"),
                "company": comp.get("name"),
                "phone": numero,
                "phone_display": comp.get("phone") or comp.get("phone_raw"),
                "category": comp.get("category"),
                "rating": float(comp["rating"]) if comp.get("rating") is not None else None,
                "address": comp.get("address"),
                "city": comp.get("city"),
            }
        )

    return {
        "contactables": contactables,
        "total": len(contactables),
        "sin_telefono": sin_telefono,
    }


@router.post("/contact-whatsapp/{lead_id}/copy")
async def contact_whatsapp_copy(lead_id: str) -> dict[str, Any]:
    """Genera dos variantes de copy de primer contacto para un lead.

    Lee el lead con su empresa de Supabase, arma los copys con datos reales
    del negocio y devuelve el link wa.me con la variante A precargada.
    """
    from app.core.supabase_http import select as pg_select

    try:
        filas = await pg_select(
            "leads",
            columns="id,status,companies(name,phone,phone_raw,category,address,city)",
            filters={"id": lead_id},
            limit=1,
        )
    except Exception as exc:
        logger.error("whatsapp_copy_select_error", lead_id=lead_id, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Error leyendo lead: {exc}") from exc

    if not filas:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} no encontrado")

    fila = filas[0]
    comp = fila.get("companies") or {}
    nombre = comp.get("name") or ""
    numero = _telefono_e164(comp.get("phone"), comp.get("phone_raw"))
    if not numero:
        raise HTTPException(
            status_code=422,
            detail=f"La empresa de {nombre or 'este lead'} no tiene teléfono usable",
        )

    copys = _generar_copys_whatsapp(
        company_name=nombre,
        category=comp.get("category"),
        city=comp.get("city"),
    )

    return {
        "lead_id": lead_id,
        "company": nombre,
        "phone": numero,
        "copies": copys,
        "wa_link": _wa_link(numero, copys[0]["text"]),
    }


class WhatsAppAttemptIn(BaseModel):
    """Payload del intento de contacto manual por WhatsApp."""

    result: str = "sent"
    notes: str | None = None


@router.post("/contact-whatsapp/{lead_id}/attempt")
async def contact_whatsapp_attempt(
    lead_id: str, payload: WhatsAppAttemptIn | None = None
) -> dict[str, Any]:
    """Registra un intento de contacto manual por WhatsApp.

    Replica lo que hace /api/v1/contact-queue/{lead_id}/attempt pero vía
    PostgREST (este router no tiene sesión de BD): insert en activities con
    activity_type=STAGE_CHANGED, actor=USER, y first_contact_at en leads si
    es el primer contacto del lead.
    """
    from app.core.postgrest_client import pg_insert, pg_select, pg_update

    datos = payload or WhatsAppAttemptIn()

    try:
        # Validar que el lead existe antes de escribir, con el nombre de su
        # empresa en la misma lectura (resource embedding).
        filas = await pg_select(
            "leads",
            columns="id,first_contact_at,companies(name)",
            filters={"id": lead_id},
            limit=1,
        )
        if not filas:
            raise HTTPException(status_code=404, detail=f"Lead {lead_id} no encontrado")

        nombre_empresa = (filas[0].get("companies") or {}).get("name") or ""

        now = datetime.now(UTC).isoformat()
        descripcion = (
            datos.notes
            or f"Contacto manual por WhatsApp a {nombre_empresa or 'este lead'}: {datos.result}"
        )

        # Insert en activities. El esquema desplegado en Supabase difiere del
        # modelo SQLAlchemy: no tiene title/description/actor/occurred_at;
        # usa subject/body/actor_type. La semantica es la misma que
        # /api/v1/contact-queue/{lead_id}/attempt: activity_type STAGE_CHANGED
        # (el enum desplegado no tiene WHATSAPP_SENT), actor USER y el id es
        # BIGSERIAL (no se envia; pg_insert no muta el payload).
        actividad = {
            "lead_id": lead_id,
            "activity_type": "STAGE_CHANGED",
            "actor_type": "USER",
            "subject": "Contacto vía WhatsApp",
            "body": descripcion,
            "metadata": {
                "channel": "whatsapp",
                "result": datos.result,
                "origen": "torre-control",
            },
        }
        insertados = await pg_insert("activities", actividad)
        if not insertados:
            logger.error("whatsapp_attempt_insert_failed", lead_id=lead_id)
            raise HTTPException(
                status_code=502, detail="No se pudo registrar la actividad en Supabase"
            )

        # first_contact_at solo si es el primer contacto (mismo criterio que
        # contact_queue: first_contact_at = first_contact_at or now)
        if not filas[0].get("first_contact_at"):
            await pg_update("leads", {"id": lead_id}, {"first_contact_at": now})

        logger.info(
            "whatsapp_attempt_registrado",
            lead_id=lead_id,
            company=nombre_empresa,
            result=datos.result,
        )
        return {
            "status": "recorded",
            "lead_id": lead_id,
            "company": nombre_empresa,
            "channel": "whatsapp",
            "result": datos.result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("whatsapp_attempt_error", lead_id=lead_id, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Error registrando intento: {exc}") from exc


# ──────────────────────────────────────────────────────────────────────────
# Intake público desde el sitio web (veyrasoluciones.com)
# ──────────────────────────────────────────────────────────────────────────
# El formulario de contacto del sitio necesita un destino durable para sus
# leads. Este endpoint vive bajo /torre-control (prefijo público exento de la
# autenticación de servicio) y escribe en el CRM vía PostgREST:
# companies + contacts (source=WEBSITE) + leads (stage "new") + task de
# revisión + activity LEAD_CREATED.
#
# Diferencias con /api/v1/intake/business-mri: aquel exige token de servicio
# firmado (scope hermes.business_mri.intake) y una sesión de BD asyncpg, que
# no existe en Vercel serverless. Este usa el mismo mapeo pero por PostgREST
# con la service role key, que es como el resto de /torre-control accede a la
# base. No expone PII en las respuestas: solo el lead_id generado.

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Rate limit propio del intake (el RateLimitMiddleware solo cubre /api/v1 y
# /tracking; /torre-control pasa sin contador). Ventana deslizante en memoria:
# suficiente para el volumen de un formulario, mismo espíritu que NonceStore.
_INTAKE_LIMIT = 10  # envíos por IP
_INTAKE_WINDOW = 3600.0  # ventana en segundos
_intake_hits: dict[str, list[float]] = {}
_intake_lock = threading.Lock()


def _intake_allow(ip: str, *, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else now
    cutoff = current - _INTAKE_WINDOW
    with _intake_lock:
        times = [t for t in _intake_hits.get(ip, []) if t > cutoff]
        if len(times) >= _INTAKE_LIMIT:
            _intake_hits[ip] = times
            return False
        times.append(current)
        _intake_hits[ip] = times
        return True


class WebsiteIntakeIn(BaseModel):
    """Payload del formulario de contacto de veyrasoluciones.com."""

    name: str
    email: str
    company_name: str | None = None
    company_process: str
    phone: str | None = None
    city: str | None = None
    sector: str | None = None
    goal: str | None = None
    bottleneck: str | None = None
    priority: str | None = None
    budget: str | None = None


def _clean(value: str | None, max_len: int) -> str:
    """Recorta y sanea un campo de texto; None se vuelve cadena vacía."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


@router.post("/intake", status_code=201)
async def website_intake(payload: WebsiteIntakeIn, request: Request) -> dict[str, Any]:
    """Crea un lead en el CRM desde el formulario del sitio web.

    Rate limit propio por IP (10/hora): un formulario real no supera eso y
    un bot lo hace de inmediato. El middleware global no cubre /torre-control.

    Flujo (todo vía PostgREST, sin sesión de BD):
      1. companies: upsert por dedupe_key (sha256 del email) → la empresa.
      2. contacts: contacto principal con source=WEBSITE.
      3. leads: lead en stage "new", status NEW, source WEBSITE.
      4. tasks: tarea de revisión para el operador.
      5. activities: LEAD_CREATED con metadata del origen.

    El dedupe_key evita duplicar la empresa si el mismo prospecto envía el
    formulario dos veces: el upsert fusiona la ficha en lugar de crear otra.
    """
    from app.core.postgrest_client import pg_insert, pg_insert_upsert
    from app.core.supabase_http import select as pg_select

    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )
    if not _intake_allow(ip):
        raise HTTPException(
            status_code=429,
            detail="Demasiadas solicitudes desde esta conexión. Intenta más tarde.",
        )

    nombre = _clean(payload.name, 120)
    email = _clean(payload.email, 254).lower()
    empresa = _clean(payload.company_name, 255) or nombre
    proceso = _clean(payload.company_process, 2000)

    if not nombre or len(nombre) < 2:
        raise HTTPException(status_code=422, detail="El nombre es obligatorio.")
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="El correo no es válido.")
    if not proceso or len(proceso) < 10:
        raise HTTPException(
            status_code=422,
            detail="Describe brevemente tu empresa y el reto (mínimo 10 caracteres).",
        )

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key_value:
        logger.error("intake_not_configured")
        raise HTTPException(status_code=503, detail="Captura no configurada.")

    now_iso = datetime.now(UTC).isoformat()
    dedupe_key = hashlib.sha256(f"veyra-website:{email}".encode()).hexdigest()[:40]

    # 0. Etapa inicial del pipeline (se valida ANTES de escribir: si el
    #    pipeline no existe, no queda media empresa huérfana en la base).
    # Nota: el helper `select` de supabase_http prefija cada filtro con `eq.`,
    #    así que `owner_id is null` no se puede expresar ahí. Se consulta la
    #    etapa "new" y se prefiere la del sistema (owner_id nulo) en Python.
    stages = await pg_select(
        "pipeline_stages",
        columns="id,stage_key,owner_id",
        filters={"stage_key": "new"},
        order="position.asc",
        limit=10,
    )
    system_stages = [s for s in stages if s.get("owner_id") is None]
    target_stage = (system_stages or stages or [None])[0]
    if not target_stage:
        logger.error("intake_stage_missing")
        raise HTTPException(status_code=503, detail="Pipeline no inicializado.")
    stage_id = target_stage["id"]

    # 1. Empresa (upsert merge por dedupe_key del namespace del sitio web).
    # La constraint unica de companies es (owner_id, dedupe_key): el mismo
    # email desde el sitio siempre cae en la misma ficha.
    company_payload = {
        "name": empresa,
        "email": email,
        "dedupe_key": dedupe_key,
        "description": proceso[:500],
        "owner_id": "00000000-0000-4000-8000-000000000002",
        "first_extracted_at": now_iso,
        "last_extracted_at": now_iso,
    }
    if _clean(payload.city, 80):
        company_payload["city"] = _clean(payload.city, 80)

    company = await pg_insert_upsert("companies", company_payload, on_conflict="owner_id,dedupe_key")
    if not company:
        logger.error("intake_company_failed", email_hash=hashlib.sha256(email.encode()).hexdigest()[:12])
        raise HTTPException(
            status_code=502, detail="No se pudo registrar la empresa. Intenta de nuevo."
        )
    company_id = company[0].get("id")

    # 2. Contacto principal (source=WEBSITE según el enum del CRM).
    contacto = await pg_insert(
        "contacts",
        {
            "company_id": company_id,
            "full_name": nombre,
            "email": email,
            "phone": _clean(payload.phone, 40) or None,
            "source": "WEBSITE",
            "is_primary": True,
        },
    )
    if not contacto:
        logger.error("intake_contact_failed", company=str(company_id))
        raise HTTPException(
            status_code=502, detail="No se pudo registrar el contacto. Intenta de nuevo."
        )
    contact_id = contacto[0].get("id") if isinstance(contacto, list) else contacto.get("id")

    # 3. Lead en la etapa inicial (stage_id ya validado en el paso 0).
    lead = await pg_insert(
        "leads",
        {
            "company_id": company_id,
            "contact_id": contact_id,
            "stage_id": stage_id,
            "status": "NEW",
            "source": "WEBSITE",
        },
    )
    if not lead:
        logger.error("intake_lead_failed", company=str(company_id))
        raise HTTPException(status_code=502, detail="No se pudo registrar la solicitud.")
    lead_id = lead[0].get("id") if isinstance(lead, list) else lead.get("id")

    # 4. Tarea de revisión (mismo título que usa el intake autenticado).
    await pg_insert(
        "tasks",
        {
            "lead_id": lead_id,
            "contact_id": contact_id,
            "title": "Intake Review",
            "description": "Solicitud Business MRI desde veyrasoluciones.com. Revisar y clasificar.",
            "priority": 1,
        },
    )

    # 5. Activity con el contexto completo del formulario para el operador.
    extras: dict[str, Any] = {}
    if _clean(payload.sector, 120):
        extras["sector"] = _clean(payload.sector, 120)
    if _clean(payload.goal, 300):
        extras["objetivo"] = _clean(payload.goal, 300)
    if _clean(payload.bottleneck, 2000):
        extras["cuello_de_botella"] = _clean(payload.bottleneck, 2000)
    if _clean(payload.priority, 80):
        extras["prioridad"] = _clean(payload.priority, 80)
    if _clean(payload.budget, 60):
        extras["presupuesto"] = _clean(payload.budget, 60)

    await pg_insert(
        "activities",
        {
            "lead_id": lead_id,
            "contact_id": contact_id,
            "company_id": company_id,
            "activity_type": "LEAD_CREATED",
            "actor_type": "SYSTEM",
            "subject": "Lead creado desde el sitio web",
            "body": proceso,
            "is_system_generated": True,
            "metadata": {"origen": "veyrasoluciones.com", "canal": "formulario-contacto", **extras},
        },
    )

    logger.info(
        "intake_website_ok",
        lead_id=str(lead_id),
        company=str(company_id),
        email_hash=hashlib.sha256(email.encode()).hexdigest()[:12],
    )
    return {
        "status": "CREATED",
        "lead_id": str(lead_id),
        "message": "Solicitud registrada en el CRM.",
    }
