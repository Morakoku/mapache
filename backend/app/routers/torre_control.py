"""Torre de control Mapache CRM - 100% funcional.

Además del dashboard y los checks de salud, expone el estado del pipeline
de scraping (empresas, enriquecimiento, leads, runs del scheduler), el
contacto manual de leads por WhatsApp (lista de contactables, copys de
primer contacto, registro de intentos) y la gestión de plantillas de email
con la secuencia Veyra MRI Outbound: listado/preview/edición de
email_templates vía PATCH, y activación/pausa de sequences (sin worker: el
envío real lo controla el warm-up script).

Se monta en public_router bajo el prefijo /torre-control (dashboard
público, sin auth de servicio): el ServiceAuthMiddleware ya trata
/torre-control como prefijo público y el SecurityHeadersMiddleware lo
exime del CSP restrictivo.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote as url_quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger
from app.routers.torre_chequeo_html import CHEQUEO_HTML
from app.routers.torre_receptionist_html import RECEPTIONIST_HTML

logger = get_logger(__name__)

router = APIRouter(tags=["torre-control"])


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mapache CRM - Torre de Control</title>
<style>
:root{--bg:#0a0a0a;--card:#111;--border:#1a1a1a;--fg:#e0e0e0;--muted:#9aa0a6;--accent:#ff6b35;--green:#00c853;--red:#ff1744;--yellow:#ffd600;--blue:#2979ff}
    :focus-visible{outline:3px solid var(--accent);outline-offset:2px}
    ::selection{background:var(--accent);color:#0a0a0a}
    html{caret-color:var(--accent)}
    ::-webkit-scrollbar{width:10px;height:10px}
    ::-webkit-scrollbar-track{background:#0a0a0a}
    ::-webkit-scrollbar-thumb{background:#2a2a2a;border-radius:6px}
    ::-webkit-scrollbar-thumb:hover{background:#3a3a3a}
    @media (prefers-reduced-motion: reduce){*,*::before,*::after{transition-duration:.01ms !important;animation-duration:.01ms !important;animation-iteration-count:1 !important}}
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
/* --- Card Plantillas --- */
.tpl-toolbar{display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.tpl-select{background:#000;border:1px solid var(--border);color:var(--fg);padding:8px 12px;border-radius:8px;font-size:13px;font-family:inherit}
.tpl-select:focus{outline:none;border-color:var(--accent)}
.tpl-list{display:flex;flex-direction:column;gap:8px;max-height:520px;overflow-y:auto}
.tpl-item{display:flex;justify-content:space-between;align-items:center;gap:12px;border:1px solid var(--border);border-radius:10px;padding:12px 14px;flex-wrap:wrap}
.tpl-item.inactive{opacity:.55}
.tpl-name{font-size:14px;font-weight:700}
.tpl-subject{font-size:12px;color:var(--muted);margin-top:2px;max-width:560px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tpl-meta{font-size:11px;color:var(--muted);margin-top:4px}
.tpl-cat{color:var(--blue)}
.tpl-inactive{color:var(--red);font-weight:600;margin-left:6px}
.tpl-actions{display:flex;gap:8px}
/* --- Secuencia Veyra MRI --- */
.seq-steps{display:flex;flex-direction:column;gap:6px;margin-top:12px}
.seq-step{display:flex;gap:12px;align-items:flex-start;border-left:2px solid var(--accent);padding:4px 0 4px 14px}
.seq-day{font-size:12px;font-weight:700;color:var(--accent);min-width:58px;padding-top:2px}
.seq-step-name{font-size:13px;font-weight:600}
.seq-step-subject{font-size:12px;color:var(--muted);margin-top:2px}
.seq-btns{display:flex;gap:8px;margin-top:12px;align-items:center;flex-wrap:wrap}
.seq-note{font-size:12px;color:var(--yellow);margin-top:8px;display:none}
.seq-note.show{display:block}
/* --- Modales (preview y edicion de plantillas) --- */
/* Los modales viven FUERA de los contenedores que se re-renderizan: el
   refresh nunca toca su DOM, igual que el patron de la card WhatsApp. */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.78);z-index:50;align-items:center;justify-content:center;padding:24px}
.modal-overlay.open{display:flex}
.modal{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;width:100%;max-width:760px;max-height:90vh;overflow-y:auto}
.modal-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px}
.modal-title{font-size:16px;font-weight:700}
.pv-frame{width:100%;height:420px;border:1px solid var(--border);border-radius:8px;background:#fff}
.pv-text-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin:12px 0 6px}
.pv-text{background:#000;border:1px solid var(--border);border-radius:8px;padding:10px;font-size:12px;white-space:pre-wrap;max-height:140px;overflow-y:auto}
.ed-label{display:block;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin:12px 0 6px}
.ed-hint{text-transform:none;letter-spacing:0;color:var(--muted)}
.ed-input{width:100%;background:#000;border:1px solid var(--border);color:var(--fg);border-radius:8px;padding:10px;font-family:inherit;font-size:13px;line-height:1.5;resize:vertical}
.ed-code{font-family:'SF Mono','Fira Code',monospace;font-size:12px}
.ed-input:focus{outline:none;border-color:var(--accent)}
.ed-msg{font-size:12px;margin-top:10px;min-height:16px}
.ed-msg.ok{color:var(--green)}
.ed-msg.err{color:var(--red)}
.ed-msg.warn{color:var(--yellow)}
.tabbar{display:flex;gap:8px;margin:0 0 22px;flex-wrap:wrap;border-bottom:1px solid var(--border)}
.tab{padding:10px 20px;border:1px solid var(--border);border-bottom:none;border-radius:10px 10px 0 0;background:0 0;color:var(--muted);font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:1px;cursor:pointer}
.tab:hover{border-color:var(--accent);color:var(--fg)}
.tab.active{background:var(--card);color:var(--fg);border-color:var(--accent)}
.tab-panel{display:none}
.tab-panel.active{display:block}
.tab-note{color:var(--muted);font-size:12px;margin-bottom:16px}
</style>
</head>
<body>
<div class="header">
<h1><span class="logo">🦝</span> Torre de Control</h1>
<div style="display:flex;gap:15px;align-items:center">
<span id="overall" class="pill off">Verificando...</span>
<span style="color:var(--muted);font-size:12px" id="updated">--:--:--</span>
</div>
</div>
<div class="refresh-bar">
<div style="display:flex;gap:10px">
<button class="refresh-btn" onclick="loadAll();loadPipeline();loadWhatsApp();loadTemplates();loadSequenceStatus()">🔄 Refrescar</button>
<button class="refresh-btn" onclick="toggleAuto()">⏱ <span id="auto-txt">ON</span></button>
<a class="refresh-btn" href="/torre-control/chequeo" style="text-decoration:none;color:var(--accent);border-color:var(--accent)">📋 Chequeo Express</a>
</div>
<span style="color:var(--muted);font-size:12px">Cada 10s</span>
</div>
<div class="tabbar" role="tablist" aria-label="Secciones de la Torre">
<button class="tab active" role="tab" aria-selected="true" data-tab="mapache" onclick="showTab('mapache')">Mapache</button>
<button class="tab" role="tab" aria-selected="false" data-tab="veyra" onclick="showTab('veyra')">Veyra</button>
<button class="tab" role="tab" aria-selected="false" data-tab="guaki" onclick="showTab('guaki')">Guaki</button>
</div>
<div class="tab-panel active" id="tab-mapache" role="tabpanel" aria-hidden="false">
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
<div class="card">
<div class="card-header"><span class="card-title">📋 Logs</span><button class="btn btn-blue" onclick="clearLogs()">Limpiar</button></div>
<div class="logs" id="logs"><div class="log-e"><span class="log-t">--:--:--</span><span class="log-info">Cargando...</span></div></div>
</div>
</div>

<div class="tab-panel" id="tab-veyra" role="tabpanel" aria-hidden="true">
<div class="card" style="margin-bottom:30px">
<div class="card-header">
<span class="card-title">📊 Solicitudes del Chequeo Express</span>
<a class="refresh-btn" href="/torre-control/chequeo" style="text-decoration:none;color:var(--accent);border-color:var(--accent)">Abrir kanban</a>
</div>
<div class="card-sub">Diagnóstico por perfil, fuga anualizada, etapas del embudo y contacto directo por WhatsApp. Requiere token de operador.</div>
</div>
<div class="card" style="margin-bottom:30px">
<div class="card-header">
<span class="card-title">📚 Soluciones Veyra (fichas)</span>
<a class="refresh-btn" href="/torre-control/productos" style="text-decoration:none;color:var(--accent);border-color:var(--accent)">Abrir</a>
</div>
<div class="card-sub">Fichas listas para el cliente (PDF): Recepcionista IA, Agenda IA, Veyra Ops, Copiloto, Puente, Radar y Conecta. Cada una con las dos formas de implementarlo, pros y contras.</div>
</div>
<div class="card" style="margin-bottom:30px">
<div class="card-header">
<span class="card-title">🤖 Recepcionista IA para WhatsApp</span>
<a class="refresh-btn" href="/torre-control/recepcionista" style="text-decoration:none;color:var(--accent);border-color:var(--accent)">Configurar</a>
</div>
<div class="card-sub">Configura la recepcionista de cada cliente (servicios, horario, política de precios, FAQs y teléfono de escalamiento) y prueba el cerebro con un mensaje real. Producto en construcción: el transporte de WhatsApp por cliente es el último paso.</div>
</div>
<div class="card" style="margin-bottom:30px">
<div class="card-header">
<span class="card-title">📱 Contacto WhatsApp Manual</span>
<span style="font-size:12px;color:var(--muted)"><span id="wa-total">--</span> contactables / <span id="wa-sin-tel">--</span> sin teléfono</span>
</div>
<div class="wa-list" id="wa-list"><div class="card-sub">Cargando leads...</div></div>
<div class="card-sub" style="margin-top:10px">Genera copys, edítalos si quieres, abre WhatsApp con el texto listo y marca el intento. Los copys usan los datos reales de la ficha del negocio.</div>
</div>
<div class="card" style="margin-bottom:30px">
<div class="card-header">
<span class="card-title">🎨 Plantillas</span>
<span style="font-size:12px;color:var(--muted)"><span id="tpl-total">--</span> plantillas</span>
</div>
<div class="tpl-toolbar">
<select id="tpl-cat" class="tpl-select" onchange="tplSetFilter(this.value)"></select>
<span class="card-sub" id="tpl-filter-count"></span>
</div>
<div class="tpl-list" id="tpl-list"><div class="card-sub">Cargando plantillas...</div></div>
<div class="card-sub" style="margin-top:10px">Preview en iframe aislado (sin scripts) y edicion directa. Al guardar se valida que las variables [Nombre] y [sector de la empresa] no se borren: el personalizador del warm-up las reemplaza por los datos del contacto.</div>
<div style="border-top:1px solid var(--border);margin-top:20px;padding-top:16px">
<div class="card-header">
<span class="card-title">Secuencia Veyra MRI 30d</span>
<span id="seq-pill" class="pill off">--</span>
</div>
<div id="seq-body"><div class="card-sub">Cargando secuencia...</div></div>
<div class="seq-note" id="seq-note"></div>
</div>
</div>
</div>

<div class="tab-panel" id="tab-guaki" role="tabpanel" aria-hidden="true">
<div class="card" style="margin-bottom:30px">
<div class="card-header"><span class="card-title">🦝 Guaki · Marketplace</span><span id="guaki-pill" class="pill warn">Verificando...</span></div>
<p class="tab-note">Guaki es una app aparte (directorio inteligente por voz). Aquí viven sus accesos y el estado de su servicio; sus métricas se ven dentro de Guaki.</p>
<div style="display:flex;gap:10px;flex-wrap:wrap">
<a class="refresh-btn" href="https://guakiweb.vercel.app" target="_blank" rel="noopener" style="text-decoration:none">Abrir Guaki</a>
<a class="refresh-btn" href="https://guakiweb.vercel.app/directorio" target="_blank" rel="noopener" style="text-decoration:none">Directorio</a>
<a class="refresh-btn" href="https://guakiweb.vercel.app/provider/dashboard" target="_blank" rel="noopener" style="text-decoration:none">Panel de negocios</a>
<a class="refresh-btn" href="https://guakiweb.vercel.app/api/health" target="_blank" rel="noopener" style="text-decoration:none">Health</a>
</div>
</div>
<div class="card">
<div class="card-header"><span class="card-title">Estado del servicio Guaki</span></div>
<div id="guaki-body"><div class="card-sub">Cargando...</div></div>
</div>
</div>
<!-- Modal: preview de plantilla. sandbox SIN allow-scripts: las plantillas
     traen style inline, no JS, y asi nada dentro del preview puede ejecutar. -->
<div class="modal-overlay" id="modal-preview" onclick="if(event.target===this)pvClose()">
<div class="modal">
<div class="modal-head">
<div>
<div class="modal-title" id="pv-title"></div>
<div class="card-sub" id="pv-meta"></div>
</div>
<button class="btn btn-sm btn-outline" onclick="pvClose()">Cerrar</button>
</div>
<iframe id="pv-frame" class="pv-frame" sandbox="allow-same-origin" title="Preview de plantilla"></iframe>
<div class="pv-text-label">Version texto plano</div>
<div class="pv-text" id="pv-text"></div>
</div>
</div>
<!-- Modal: edicion de plantilla. Se cierra SOLO con sus botones: un click
     fuera o Escape no debe descartar ediciones a medio hacer. -->
<div class="modal-overlay" id="modal-edit">
<div class="modal">
<div class="modal-head">
<div class="modal-title" id="ed-title"></div>
<button class="btn btn-sm btn-outline" onclick="edClose()">Cerrar</button>
</div>
<label class="ed-label" for="ed-subject">Asunto</label>
<textarea id="ed-subject" class="ed-input" rows="2"></textarea>
<label class="ed-label" for="ed-html">Body HTML <span class="ed-hint">(conserva [Nombre] y [sector de la empresa] si la plantilla los usa)</span></label>
<textarea id="ed-html" class="ed-input ed-code" rows="14"></textarea>
<label class="ed-label" for="ed-text">Body texto plano</label>
<textarea id="ed-text" class="ed-input" rows="5"></textarea>
<div class="ed-msg" id="ed-msg"></div>
<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:12px">
<button class="btn btn-sm btn-outline" onclick="edClose()">Cancelar</button>
<button class="btn btn-sm btn-on" id="ed-save" onclick="tplSave()">Guardar</button>
</div>
</div>
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
// --- Plantillas de email ---
// Se cargan al abrir y con el boton Refrescar (NO en el auto-refresh de 10s:
// son 26 HTML completos y datos de configuracion estable, como el pipeline).
// Los modales de preview/edicion viven fuera del contenedor re-renderizado,
// asi que un refresh manual no cierra nada ni pierde ediciones.
let tplData=[];
let tplFilter='';
let tplEditId=null;         // plantilla abierta en el editor (o null)
let tplEditOriginal={};     // valores cargados del servidor para detectar cambios
async function loadTemplates(){
try{
const r=await fetch(API+'/templates',{cache:'no-store'});
if(!r.ok)throw new Error('HTTP '+r.status);
const d=await r.json();
if(d.error){log('Plantillas: '+d.error,'err');return}
tplData=d.templates||[];
document.getElementById('tpl-total').textContent=d.total??0;
// Select de categorias: se reconstruye en cada carga pero conservando la
// seleccion activa para que el refresh no resetee el filtro.
const sel=document.getElementById('tpl-cat');
const cats=(d.categories||[]).filter(c=>c);
const prev=sel.value;
sel.innerHTML='<option value="">Todas ('+tplData.length+')</option>'+cats.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('');
if(cats.includes(prev))sel.value=prev;
tplFilter=sel.value;
renderTemplates();
log('Plantillas: '+tplData.length+' cargadas','ok');
}catch(e){log('Plantillas: '+e.message,'err');document.getElementById('tpl-list').innerHTML='<div class="card-sub">Error cargando plantillas: '+esc(e.message)+'</div>'}
}
function tplSetFilter(v){tplFilter=v||'';renderTemplates()}
function renderTemplates(){
const el=document.getElementById('tpl-list');
const list=tplFilter?tplData.filter(t=>(t.category||'Sin categoría')===tplFilter):tplData;
document.getElementById('tpl-filter-count').textContent=list.length+' visibles';
if(!list.length){el.innerHTML='<div class="card-sub">No hay plantillas'+(tplFilter?' en esta categoría':'')+'.</div>';return}
el.innerHTML=list.map(t=>{
const inact=t.is_active?'':' inactive';
return`<div class="tpl-item${inact}">
<div style="min-width:0;flex:1">
<div class="tpl-name">${esc(t.name)}</div>
<div class="tpl-subject">${esc(t.subject)||'(sin asunto)'}</div>
      <div class="tpl-meta"><span class="tpl-cat">${esc(t.category||'Sin categoría')}</span> · usos: ${t.times_used??0}${t.is_active?'':'<span class="tpl-inactive">INACTIVA</span>'}</div>
</div>
<div class="tpl-actions">
<button class="btn btn-sm btn-outline" onclick="tplPreview('${t.id}')">Ver</button>
<button class="btn btn-sm btn-accent" onclick="tplEdit('${t.id}')">Editar</button>
</div>
</div>`}).join('');
}
// Preview: iframe con sandbox="allow-same-origin" (sin allow-scripts) y el
// HTML inyectado via srcdoc. Los style inline de las plantillas renderizan
// igual; nada puede ejecutar JS ni tocar el dashboard.
async function tplPreview(id){
const t=tplData.find(x=>x.id===id);if(!t)return;
edClose(); // un solo modal a la vez
const ov=document.getElementById('modal-preview');
document.getElementById('pv-title').textContent=t.name||id;
document.getElementById('pv-meta').textContent=(t.category||'Sin categoría')+' · '+(t.subject||'(sin asunto)');
const frame=document.getElementById('pv-frame');
frame.srcdoc='<p style="font-family:monospace;padding:12px">Cargando preview...</p>';
document.getElementById('pv-text').textContent='';
ov.classList.add('open');
try{
const r=await fetch(API+`/templates/${id}`,{cache:'no-store'});
if(!r.ok)throw new Error('HTTP '+r.status);
const d=await r.json();
if(d.error)throw new Error(d.error);
frame.srcdoc=d.template?.body_html||'<p style="font-family:monospace;padding:12px">(plantilla sin body_html)</p>';
document.getElementById('pv-text').textContent=d.template?.body_text||'(sin versión de texto)';
log('Preview: '+(t.name||id),'ok');
}catch(e){
frame.srcdoc='<p style="color:#ff1744;font-family:monospace;padding:12px">Error cargando preview: '+esc(e.message)+'</p>';
log('Preview: '+e.message,'err');
}
}
function pvClose(){document.getElementById('modal-preview').classList.remove('open')}
// Edicion: trae la plantilla completa, precarga los textareas y guarda solo
// los campos que cambiaron (PATCH parcial). El backend rechaza con 400 si
// la edicion borra [Nombre] o [sector de la empresa].
async function tplEdit(id){
const t=tplData.find(x=>x.id===id);if(!t)return;
pvClose();
try{
const r=await fetch(API+`/templates/${id}`,{cache:'no-store'});
if(!r.ok)throw new Error('HTTP '+r.status);
const d=await r.json();
if(d.error)throw new Error(d.error);
tplEditId=id;
tplEditOriginal={subject:d.template?.subject||'',body_html:d.template?.body_html||'',body_text:d.template?.body_text||''};
document.getElementById('ed-title').textContent='Editar: '+(t.name||id);
document.getElementById('ed-subject').value=tplEditOriginal.subject;
document.getElementById('ed-html').value=tplEditOriginal.body_html;
document.getElementById('ed-text').value=tplEditOriginal.body_text;
const msg=document.getElementById('ed-msg');
msg.textContent='';msg.className='ed-msg';
document.getElementById('ed-save').disabled=false;
document.getElementById('modal-edit').classList.add('open');
}catch(e){log('Editar: '+e.message,'err')}
}
function edClose(){
document.getElementById('modal-edit').classList.remove('open');
tplEditId=null;tplEditOriginal={};
}
async function tplSave(){
if(!tplEditId)return;
const subject=document.getElementById('ed-subject').value;
const body_html=document.getElementById('ed-html').value;
const body_text=document.getElementById('ed-text').value;
const msg=document.getElementById('ed-msg');
if(!subject.trim()){msg.textContent='El asunto no puede quedar vacío';msg.className='ed-msg err';return}
if(!body_html.trim()){msg.textContent='El body HTML no puede quedar vacío';msg.className='ed-msg err';return}
// PATCH minimalista: solo los campos que el usuario realmente cambió.
const payload={};
if(subject!==tplEditOriginal.subject)payload.subject=subject;
if(body_html!==tplEditOriginal.body_html)payload.body_html=body_html;
if(body_text!==tplEditOriginal.body_text)payload.body_text=body_text;
if(!Object.keys(payload).length){msg.textContent='Sin cambios que guardar';msg.className='ed-msg warn';return}
const nombre=(tplData.find(x=>x.id===tplEditId)||{}).name||tplEditId;
document.getElementById('ed-save').disabled=true;
msg.textContent='Guardando...';msg.className='ed-msg';
try{
const r=await fetch(API+`/templates/${tplEditId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
const d=await r.json().catch(()=>({}));
if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));
msg.textContent='Guardado: la plantilla se actualizó en Supabase.';
msg.className='ed-msg ok';
log('Plantilla guardada: '+nombre,'ok');
await loadTemplates();       // refetch del listado con los datos nuevos
loadSequenceStatus();        // por si la plantilla editada esta enlazada a un paso
// Cierre automatico solo si sigue abierta esta misma plantilla (evita
// cerrar un modal que el usuario abrio de nuevo durante los 900ms).
const idGuardado=tplEditId;
setTimeout(()=>{if(tplEditId===idGuardado)edClose()},900);
}catch(e){
msg.textContent='Error: '+e.message;
msg.className='ed-msg err';
log('Guardar plantilla: '+e.message,'err');
document.getElementById('ed-save').disabled=false;
}
}
// --- Secuencia Veyra MRI Outbound 30d ---
let seqData=null;
async function loadSequenceStatus(){
try{
const r=await fetch(API+'/sequence-status',{cache:'no-store'});
if(!r.ok)throw new Error('HTTP '+r.status);
const d=await r.json();
if(d.error){document.getElementById('seq-body').innerHTML='<div class="card-sub">Error: '+esc(d.error)+'</div>';log('Secuencia: '+d.error,'err');return}
seqData=d;
renderSequence();
}catch(e){document.getElementById('seq-body').innerHTML='<div class="card-sub">Error: '+esc(e.message)+'</div>';log('Secuencia: '+e.message,'err')}
}
function renderSequence(){
if(!seqData||!seqData.sequence)return;
const s=seqData.sequence;
const steps=seqData.steps||[];
const st=s.status||'?';
const pill=document.getElementById('seq-pill');
pill.textContent=st;
pill.className='pill '+(st==='ACTIVE'?'on':st==='PAUSED'?'warn':'off');
// "Dia N" acumulado: cada paso suma su propio wait_interval al contador
// (paso 1 en dia 0, luego 3, 7, 10, 14, 18, 24, 30). Si la unidad no es
// DAY se muestra el intervalo tal cual sin acumular dias.
let dia=0;
const filas=steps.map(sp=>{
const u=String(sp.wait_unit||'DAY').toUpperCase();
const it=Number(sp.wait_interval)||0;
dia+=it;
const cuando=u.startsWith('DAY')?('Día '+dia):(it+' '+u.toLowerCase());
const subj=sp.template_subject?('“'+esc(sp.template_subject)+'”'):'(sin asunto / sin plantilla enlazada)';
return`<div class="seq-step"><div class="seq-day">${cuando}</div><div><div class="seq-step-name">Paso ${esc(String(sp.position??'?'))}: ${esc(sp.name||'(sin nombre)')}</div><div class="seq-step-subject">${subj}</div></div></div>`}).join('');
document.getElementById('seq-body').innerHTML=`<div class="seq-btns">
<button class="btn btn-sm btn-on" onclick="seqToggle('activate')" ${st==='ACTIVE'?'disabled':''}>▶ Activar</button>
<button class="btn btn-sm btn-outline" onclick="seqToggle('pause')" ${st==='PAUSED'?'disabled':''}>⏸ Pausar</button>
<span class="card-sub">${steps.length} pasos · is_active: ${s.is_active?'sí':'no'}</span>
</div>
<div class="seq-steps">${filas||'<div class="card-sub">La secuencia no tiene pasos registrados.</div>'}</div>`;
}
async function seqToggle(accion){
if(!seqData||!seqData.sequence)return;
const id=seqData.sequence.id;
const nota=document.getElementById('seq-note');
try{
const r=await fetch(API+`/sequence/${id}/${accion}`,{method:'POST'});
const d=await r.json().catch(()=>({}));
if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));
if(d.warning){nota.textContent=d.warning;nota.className='seq-note show';log('Secuencia: '+d.warning,'warn')}
log('Secuencia '+(accion==='activate'?'activada':'pausada'),'ok');
await loadSequenceStatus();
}catch(e){log('Secuencia: '+e.message,'err')}
}
async function toggleAuto(){
if(autoT){clearInterval(autoT);autoT=null;document.getElementById('auto-txt').textContent='OFF'}
else{autoT=setInterval(loadAll,10000);document.getElementById('auto-txt').textContent='ON'}
}
loadAll();
loadPipeline();
loadWhatsApp();
loadTemplates();
loadSequenceStatus();
autoT=setInterval(loadAll,10000);

// ---- Pestañas por marca: Mapache · Veyra · Guaki -------------------------
// Cada marca vive en su panel. El dashboard NO mezcla datos de una con otra.
function showTab(name){
  document.querySelectorAll('.tab-panel').forEach(p=>{ const on=p.id==='tab-'+name; p.classList.toggle('active',on); p.setAttribute('aria-hidden', on?'false':'true'); });
  document.querySelectorAll('.tab').forEach(b=>{ const on=b.getAttribute('data-tab')===name; b.classList.toggle('active',on); b.setAttribute('aria-selected', on?'true':'false'); });
  try{ localStorage.setItem('torre_tab', name); }catch(_){}
  if(name==='guaki') checkGuaki();
}
function initTab(){
  let t='mapache';
  try{ t=localStorage.getItem('torre_tab')||'mapache'; }catch(_){}
  if(!document.getElementById('tab-'+t)) t='mapache';
  showTab(t);
}

// Estado real de Guaki (si el navegador bloquea por CORS, se dice con honestidad).
async function checkGuaki(){
  const pill=document.getElementById('guaki-pill');
  const body=document.getElementById('guaki-body');
  if(!pill||!body)return;
  pill.className='pill warn'; pill.textContent='Verificando...';
  body.innerHTML='<div class="card-sub">Consultando guakiweb.vercel.app/api/health...</div>';
  try{
    const r=await fetch('/torre-control/guaki/status',{cache:'no-store'});
    const j=await r.json().catch(()=>({}));
    const d=j.health||{};
    const st=(d.status||'').toUpperCase();
    const dep=d.dependencies||{};
    const ok=!!j.ok&&st==='HEALTHY';
    pill.className='pill '+(ok?'on':'warn');
    pill.textContent=ok?'Operativo':(st||'Sin estado');
    body.innerHTML='<div class="card-sub">Estado: <strong>'+(st||'—')+'</strong> · Supabase: <strong>'+(dep.supabase||'—')+'</strong></div>'
      +'<div class="card-sub" style="margin-top:8px">Mapache: '+(dep.mapache||'—')+' · Relay: '+(dep.relay||'—')+'</div>'
      +'<div class="card-sub" style="margin-top:8px">Consultado server-side (sin CORS) · HTTP '+(j.status_code||'—')+'</div>';
  }catch(e){
    pill.className='pill warn'; pill.textContent='No verificable';
    body.innerHTML='<div class="card-sub">No se pudo consultar el estado de Guaki.</div>';
    log('Guaki status: '+e.message,'warn');
  }
}
initTab();
// Escape cierra SOLO el preview: el editor se cierra por sus botones para
// no descartar ediciones a medio hacer con una tecla accidental.
document.addEventListener('keydown',e=>{if(e.key==='Escape')pvClose()});
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


# ---------------------------------------------------------------------------
# Manual de Operación del Ecosistema (ops-manual.html)
#
# Página estática autocontenida con el mapa completo del ecosistema: la meta,
# los sistemas en producción, los flujos automáticos, la guía de la Torre de
# Control, las reglas duras, los pendientes del operador y el mapa de assets.
# Vive en backend/static/ (junto a app/) y se lee del disco en cada request:
# editar el archivo actualiza la página sin tocar código ni redeploys.
# ---------------------------------------------------------------------------
_MANUAL_CANDIDATOS = (
    # Ruta canonical: backend/static/ops-manual.html (este archivo vive en
    # backend/app/routers/, tres .parent arriba queda backend/).
    Path(__file__).parent.parent.parent / "static" / "ops-manual.html",
    # Fallback para runtimes donde el CWD es la raíz del bundle (p. ej.
    # serverless): mismo archivo relativo al directorio de trabajo.
    Path.cwd() / "static" / "ops-manual.html",
)


@router.get("/manual", response_class=HTMLResponse, include_in_schema=False)
async def manual() -> str:
    """Sirve el Manual de Operación del ecosistema.

    Lee static/ops-manual.html del disco en cada request (el primer candidato
    que exista gana). Si el archivo no está (deploy sin la carpeta static/ o
    borrado accidental) responde 404 con mensaje claro, nunca un 500 opaco.
    """
    for ruta in _MANUAL_CANDIDATOS:
        if ruta.is_file():
            try:
                return ruta.read_text(encoding="utf-8")
            except OSError as exc:
                logger.error("manual_lectura_error", ruta=str(ruta), error=str(exc))
                raise HTTPException(
                    status_code=500, detail=f"Error leyendo el manual: {exc}"
                ) from exc
    logger.error("manual_no_encontrado", candidatos=[str(r) for r in _MANUAL_CANDIDATOS])
    raise HTTPException(
        status_code=404,
        detail=(
            "Manual de operación no encontrado: no existe static/ops-manual.html "
            "en el bundle (candidatos: "
            + ", ".join(str(r) for r in _MANUAL_CANDIDATOS)
            + "). Regenera el archivo y redeploya."
        ),
    )


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
async def metrics(request: Request) -> dict[str, Any]:
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
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
# Plantillas de email (email_templates) y secuencia Veyra MRI Outbound
#
# La Torre de Control permite revisar y corregir las plantillas que usan el
# warm-up diario (VeyraWarmupDaily) y la secuencia "Veyra MRI Outbound 30d"
# sin abrir Supabase. El listado NO incluye body_html/body_text (solo el
# preview y el editor los traen vía GET /templates/{id}): con 26 plantillas
# el HTML completo saturaría la respuesta.
#
# Los placeholders de personalización son sagrados: el renderizador del
# warm-up reemplaza [Nombre] y [sector de la empresa] por los datos del
# contacto. Si una edición los borra, el correo sale genérico; el PATCH
# valida que no desaparezcan y rechaza con 400 antes de escribir.
# ──────────────────────────────────────────────────────────────────────────

_PLACEHOLDERS_PERSONALIZACION = ("[Nombre]", "[sector de la empresa]")

# Columnas ligeras para el listado (sin bodies).
_COLUMNS_LISTADO = "id,name,subject,category,is_active,times_used"

# Advertencia operativa: activar la secuencia no programa nada por sí sola.
_ADVERTENCIA_ACTIVACION = (
    "Activar la secuencia no dispara envíos: el warm-up script "
    "(VeyraWarmupDaily) es quien envía"
)

# Id de la secuencia en Supabase. Si se recreara con otro id, el fallback
# la busca por nombre.
_SEQUENCE_VEYRA_ID = "47e277e5-682c-4218-ab43-d7e0a0e8d3fb"
_SEQUENCE_VEYRA_NOMBRE = "Veyra MRI Outbound 30d"


def _placeholders_faltantes(original: str, nuevo: str) -> list[str]:
    """Placeholders de personalización presentes en `original` y ausentes en
    `nuevo`.

    Comparación exacta (mayúsculas incluidas): el renderizador del warm-up
    sustituye el token literal, así que "[nombre]" tampoco valdría; si el
    editor cambia la caja, la validación lo marca igual.
    """
    return [
        token
        for token in _PLACEHOLDERS_PERSONALIZACION
        if token in original and token not in nuevo
    ]


class TemplateUpdateIn(BaseModel):
    """Campos actualizables de una plantilla (PATCH parcial, todos opcionales)."""

    subject: str | None = None
    body_html: str | None = None
    body_text: str | None = None
    is_active: bool | None = None


@router.get("/templates")
async def listar_templates(
    category: str | None = None, limit: int = 200
) -> dict[str, Any]:
    """Listado de plantillas de email SIN el body (solo el preview lo trae).

    Devuelve las columnas ligeras para la card "Plantillas": name, subject,
    category, is_active, usage_count y last_used_at. Con 26 plantillas el
    dashboard pide la lista completa de una vez y filtra por categoría en
    cliente; el parámetro ?category= queda disponible para uso de la API.
    También devuelve la lista de categorías distintas para el filtro.
    """
    from app.core.supabase_http import select as pg_select

    salida: dict[str, Any] = {"templates": [], "total": 0, "categories": []}
    try:
        filtros = {"category": category} if category else None
        filas = await pg_select(
            "email_templates",
            columns=_COLUMNS_LISTADO,
            filters=filtros,
            order="category.asc,name.asc",
            limit=limit,
        )
        # Categorías distintas (una sola lectura ligera) para el <select>.
        categorias = await pg_select("email_templates", columns="category", limit=500)
        unicas = sorted({str(c.get("category") or "Sin categoría") for c in categorias})
        salida["templates"] = filas
        salida["total"] = len(filas)
        salida["categories"] = unicas
    except Exception as exc:
        logger.error("templates_list_error", error=str(exc))
        salida["error"] = str(exc)
    return salida


@router.get("/templates/{template_id}")
async def obtener_template(template_id: str) -> dict[str, Any]:
    """Plantilla completa (body_html y body_text) para el preview/editor."""
    from app.core.supabase_http import select as pg_select

    try:
        filas = await pg_select(
            "email_templates",
            columns="id,name,subject,body_html,body_text,category,is_active,times_used",
            filters={"id": template_id},
            limit=1,
        )
    except Exception as exc:
        logger.error("templates_get_error", template_id=template_id, error=str(exc))
        raise HTTPException(
            status_code=502, detail=f"Error leyendo la plantilla: {exc}"
        ) from exc

    if not filas:
        # select() devuelve [] tanto si la fila no existe como si PostgREST
        # falló (ya quedó registrado en el log): el 404 lo deja claro.
        raise HTTPException(
            status_code=404,
            detail=f"Plantilla {template_id} no encontrada (o PostgREST no respondió; revisar logs)",
        )
    return {"template": filas[0]}


@router.patch("/templates/{template_id}")
async def actualizar_template(
    template_id: str, payload: TemplateUpdateIn
) -> dict[str, Any]:
    """Actualiza subject/body_html/body_text/is_active de una plantilla.

    Protección del personalizador del warm-up: si el body original usa
    [Nombre] y/o [sector de la empresa] y la edición los borra (aunque sea
    cambiando la caja), se rechaza con 400 y mensaje claro, sin tocar
    Supabase. Cada campo se valida por separado: una plantilla puede tener
    el token solo en body_html o solo en body_text.
    """
    from app.core.supabase_http import select as pg_select, update as pg_update

    try:
        actuales = await pg_select(
            "email_templates",
            columns="id,name,subject,body_html,body_text,category,is_active",
            filters={"id": template_id},
            limit=1,
        )
        if not actuales:
            raise HTTPException(
                status_code=404, detail=f"Plantilla {template_id} no encontrada"
            )

        # Solo los campos que el cliente envió (PATCH parcial real).
        cambios = payload.model_dump(exclude_unset=True)
        if not cambios:
            raise HTTPException(
                status_code=422,
                detail="Sin cambios: envía subject, body_html, body_text o is_active",
            )

        # Guardas de contenido: un vacío accidental rompería el envío.
        if cambios.get("subject") is not None and not cambios["subject"].strip():
            raise HTTPException(status_code=400, detail="El asunto no puede quedar vacío")
        if cambios.get("body_html") is not None and not cambios["body_html"].strip():
            raise HTTPException(
                status_code=400, detail="body_html no puede quedar vacío"
            )

        original = actuales[0]
        for campo in ("body_html", "body_text"):
            if campo not in cambios:
                continue
            faltantes = _placeholders_faltantes(
                original.get(campo) or "", cambios[campo] or ""
            )
            if faltantes:
                tokens = " y ".join(f"'{t}'" for t in faltantes)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"La plantilla original usa {tokens} en {campo} y la edición "
                        "los borró. Restáuralos tal cual: el personalizador del "
                        "warm-up reemplaza esos tokens por el nombre del contacto "
                        "y el sector de su empresa."
                    ),
                )

        actualizadas = await pg_update("email_templates", {"id": template_id}, cambios)
        if not actualizadas:
            logger.error(
                "templates_update_failed",
                template_id=template_id,
                campos=list(cambios),
            )
            raise HTTPException(
                status_code=502,
                detail="Supabase no devolvió la fila actualizada (revisar logs de PostgREST)",
            )

        logger.info(
            "templates_updated",
            template_id=template_id,
            nombre=original.get("name"),
            campos=list(cambios),
        )
        return {"template": actualizadas[0], "updated_fields": list(cambios)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("templates_update_error", template_id=template_id, error=str(exc))
        raise HTTPException(
            status_code=500, detail=f"Error actualizando plantilla: {exc}"
        ) from exc


@router.get("/sequence-status")
async def estado_secuencia() -> dict[str, Any]:
    """Estado de la secuencia "Veyra MRI Outbound 30d" con sus 8 pasos.

    Se busca primero por id fijo y, si no existe (p. ej. fue recreada), por
    nombre. Los pasos vienen de sequence_steps ordenados por position; el
    asunto de la plantilla enlazada a cada paso se resuelve con una sola
    lectura de email_templates y un mapa en memoria (más robusto que el
    resource embedding de PostgREST, que depende del nombre de la FK).
    """
    from app.core.supabase_http import select as pg_select

    salida: dict[str, Any] = {"sequence": None, "steps": [], "total_steps": 0}
    try:
        seqs = await pg_select(
            "sequences",
            columns="id,name,is_active,max_steps,stop_on_reply",
            filters={"id": _SEQUENCE_VEYRA_ID},
            limit=1,
        )
        if not seqs:
            seqs = await pg_select(
                "sequences",
                columns="id,name,is_active,max_steps,stop_on_reply",
                filters={"name": _SEQUENCE_VEYRA_NOMBRE},
                limit=1,
            )
        if not seqs:
            salida["error"] = (
                f"No se encontró la secuencia '{_SEQUENCE_VEYRA_NOMBRE}' "
                f"(id {_SEQUENCE_VEYRA_ID}) en Supabase"
            )
            return salida

        seq = seqs[0]
        seq["status"] = "ACTIVE" if seq.get("is_active") else "PAUSED"
        pasos = await pg_select(
            "sequence_steps",
            columns="id,step_number,template_id,delay_days,delay_hours",
            filters={"sequence_id": seq["id"]},
            order="step_number.asc",
            limit=50,
        )

        ids = {p.get("template_id") for p in pasos if p.get("template_id")}
        asuntos: dict[str, str] = {}
        if ids:
            plantillas = await pg_select(
                "email_templates", columns="id,subject", limit=500
            )
            asuntos = {
                p["id"]: p.get("subject") or ""
                for p in plantillas
                if p.get("id") in ids
            }

        salida["sequence"] = seq
        salida["steps"] = [
            {
                "position": p.get("step_number"),
                "name": "",
                "wait_interval": p.get("delay_days"),
                "wait_unit": "DAY",
                "email_template_id": p.get("template_id"),
                "template_subject": asuntos.get(p.get("template_id")),
            }
            for p in pasos
        ]
        salida["total_steps"] = seq.get("max_steps") or len(pasos)
    except Exception as exc:
        logger.error("sequence_status_error", error=str(exc))
        salida["error"] = str(exc)
    return salida


async def _cambiar_estado_secuencia(
    sequence_id: str, nuevo_estado: str
) -> dict[str, Any]:
    """Cambia el status de una secuencia (ACTIVE/PAUSED) vía PostgREST.

    Solo toca la columna `status`: no encola envíos ni jobs (no existe aún
    un worker de secuencia) y no muta `is_active` para no pisar el flag que
    otras piezas puedan estar leyendo. Operación idempotente: si ya está en
    el estado pedido no se escribe.
    """
    from app.core.supabase_http import select as pg_select, update as pg_update

    try:
        seqs = await pg_select(
            "sequences",
            columns="id,name,is_active,max_steps",
            filters={"id": sequence_id},
            limit=1,
        )
        if not seqs:
            raise HTTPException(
                status_code=404, detail=f"Secuencia {sequence_id} no encontrada"
            )

        activo = nuevo_estado == "ACTIVE"
        if bool(seqs[0].get("is_active")) == activo:
            seqs[0]["status"] = "ACTIVE" if activo else "PAUSED"
            respuesta: dict[str, Any] = {"sequence": seqs[0], "changed": False}
            if activo:
                respuesta["warning"] = _ADVERTENCIA_ACTIVACION
            return respuesta

        actualizadas = await pg_update(
            "sequences", {"id": sequence_id}, {"is_active": activo}
        )
        if not actualizadas:
            logger.error(
                "sequence_status_update_failed",
                sequence_id=sequence_id,
                estado=nuevo_estado,
            )
            raise HTTPException(
                status_code=502,
                detail="Supabase no confirmó el cambio de estado (revisar logs de PostgREST)",
            )

        logger.info(
            "sequence_status_changed",
            sequence_id=sequence_id,
            nombre=seqs[0].get("name"),
            estado=nuevo_estado,
        )
        actualizadas[0]["status"] = "ACTIVE" if actualizadas[0].get("is_active") else "PAUSED"
        respuesta = {"sequence": actualizadas[0], "changed": True}
        if nuevo_estado == "ACTIVE":
            respuesta["warning"] = _ADVERTENCIA_ACTIVACION
        return respuesta
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "sequence_state_error", sequence_id=sequence_id, error=str(exc)
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error cambiando estado de la secuencia: {exc}",
        ) from exc


@router.post("/sequence/{sequence_id}/activate")
async def activar_secuencia(sequence_id: str) -> dict[str, Any]:
    """Pone la secuencia en ACTIVE (no dispara envíos: ver `warning`)."""
    return await _cambiar_estado_secuencia(sequence_id, "ACTIVE")


@router.post("/sequence/{sequence_id}/pause")
async def pausar_secuencia(sequence_id: str) -> dict[str, Any]:
    """Pone la secuencia en PAUSED (sus steps dejan de considerarse activos)."""
    return await _cambiar_estado_secuencia(sequence_id, "PAUSED")


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


# ---------------------------------------------------------------------------
# CHEQUEO EXPRESS — Kanban de solicitudes (F4)
#
# Lee la tabla veyra_intakes (mismo Supabase), filtra status='chequeo' y
# expone el diagnóstico guardado en payload.chequeo.diagnosis. La etapa del
# embudo vive en payload.chequeo.stage.
# ---------------------------------------------------------------------------

_CHEQUEO_STAGES = ("nuevo", "contactado", "agendado", "llamada", "informe", "ganado", "perdido")


def _chequeo_token_ok(request: Request) -> bool:
    """Autoriza el kanban del Chequeo Express con el token de operador.

    Falla cerrado: si el token no está configurado (config o entorno), nadie
    entra. Acepta el token por cabecera X-Veyra-Token o por query (?token=).
    """
    esperado = ""
    try:
        valor = get_settings().veyra_admin_token
        esperado = valor.get_secret_value() if valor is not None else ""
    except Exception:  # noqa: BLE001
        esperado = ""
    if not esperado:
        esperado = (os.environ.get("VEYRA_ADMIN_TOKEN") or "").strip()
    if not esperado:
        return False
    dado = (
        request.headers.get("x-veyra-token")
        or request.query_params.get("token")
        or ""
    ).strip()
    return hmac.compare_digest(dado, esperado)


@router.get("/chequeo", response_class=HTMLResponse, include_in_schema=False)
async def chequeo_kanban() -> str:
    """Página (kanban) de las solicitudes del Chequeo Express.

    La página es pública pero no contiene datos: todo se consume del endpoint
    /chequeo/data, que exige el token de operador.
    """
    return CHEQUEO_HTML


@router.get("/chequeo/data")
async def chequeo_data(request: Request, limit: int = 200) -> dict[str, Any]:
    """Solicitudes del Chequeo Express con su diagnóstico y etapa."""
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")

    from app.core.supabase_http import select as pg_select

    try:
        filas = await pg_select(
            "veyra_intakes",
            columns="intake_id,name,phone,token,status,created_at,whatsapp_sent_at,payload",
            filters={"status": "chequeo"},
            order="created_at.desc",
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("chequeo_data_error", error=str(exc))
        return {"items": [], "total": 0, "error": str(exc)}

    items: list[dict[str, Any]] = []
    for fila in filas:
        payload = fila.get("payload") or {}
        chequeo = payload.get("chequeo") or {}
        diag = chequeo.get("diagnosis") or {}
        numero = _telefono_e164(fila.get("phone"), fila.get("phone"))
        items.append(
            {
                "intake_id": fila.get("intake_id"),
                "name": fila.get("name"),
                "phone": numero or "",
                "phone_display": fila.get("phone") or "",
                "token": fila.get("token") or "",
                "perfil": diag.get("profileName") or "",
                "level": diag.get("levelLabel") or "",
                "score": diag.get("overall"),
                "fuga": diag.get("anualizado") or "",
                "stage": chequeo.get("stage") or "nuevo",
                "whatsapp_sent_at": fila.get("whatsapp_sent_at"),
                "created_at": fila.get("created_at"),
            }
        )

    return {"items": items, "total": len(items)}


class _StageBody(BaseModel):
    stage: str


@router.post("/chequeo/{intake_id}/stage")
async def chequeo_set_stage(intake_id: str, body: _StageBody, request: Request) -> dict[str, Any]:
    """Mueve una solicitud de etapa en el embudo (persistido en payload)."""
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")

    from app.core.supabase_http import select as pg_select
    from app.core.supabase_http import update as pg_update

    stage = (body.stage or "").strip().lower()
    if stage not in _CHEQUEO_STAGES:
        raise HTTPException(status_code=422, detail=f"Etapa inválida: {stage}")

    try:
        filas = await pg_select(
            "veyra_intakes",
            columns="intake_id,payload",
            filters={"intake_id": intake_id},
            limit=1,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("chequeo_stage_select_error", error=str(exc))
        raise HTTPException(status_code=503, detail="No se pudo leer la solicitud.") from exc

    if not filas:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada.")

    payload = filas[0].get("payload") or {}
    chequeo = payload.get("chequeo") or {}
    chequeo["stage"] = stage
    payload["chequeo"] = chequeo

    actualizadas = await pg_update(
        "veyra_intakes",
        {"intake_id": intake_id},
        {"payload": payload},
    )
    if not actualizadas:
        raise HTTPException(status_code=503, detail="No se pudo actualizar la etapa.")

    logger.info("chequeo_stage_ok", intake_id=intake_id, stage=stage)
    return {"status": "OK", "intake_id": intake_id, "stage": stage}


@router.get("/guaki/status")
async def guaki_status() -> dict[str, Any]:
    """Estado del servicio Guaki, consultado server-side (evita CORS).

    Guaki es una app aparte; la Torre solo muestra su salud para no mezclar
    sus métricas con las de Veyra o Mapache.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get("https://guakiweb.vercel.app/api/health")
        data: dict[str, Any] = {}
        if "application/json" in (r.headers.get("content-type") or ""):
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {}
        return {"ok": r.status_code == 200, "status_code": r.status_code, "health": data}
    except Exception as exc:  # noqa: BLE001
        logger.warning("guaki_status_error", error=str(exc)[:120])
        return {"ok": False, "error": str(exc)[:120]}


# ---------------------------------------------------------------------------
# RECEPCIONISTA IA PARA WHATSAPP (producto) - configuracion por cliente
# Protegido con el token de operador (mismo que el kanban).
# ---------------------------------------------------------------------------

class _ReceptionistBody(BaseModel):
    client_name: str | None = None
    company: str | None = None
    whatsapp_number: str | None = None
    services: list[Any] | None = None
    price_policy: str | None = None
    hours: str | None = None
    faqs: list[Any] | None = None
    escalation_phone: str | None = None
    tone: str | None = None
    transport: str | None = None
    wa_session: str | None = None
    provider: str | None = None
    phone_number_id: str | None = None
    access_token_env: str | None = None
    active: bool | None = None


class _ReceptionistReplyBody(BaseModel):
    message: str
    first_message: bool = False


@router.get("/receptionists")
async def receptionists_list(request: Request) -> dict[str, Any]:
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    from app.services.receptionist_svc import listar_configs
    return {"items": await listar_configs()}


@router.post("/receptionists")
async def receptionists_create(request: Request, body: _ReceptionistBody) -> dict[str, Any]:
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    from app.services.receptionist_svc import crear_config
    fila = await crear_config(body.model_dump(exclude_none=True))
    if not fila:
        raise HTTPException(status_code=422, detail="Falta el nombre del cliente.")
    return {"status": "OK", "receptionist": fila}


@router.patch("/receptionists/{config_id}")
async def receptionists_update(config_id: str, request: Request, body: _ReceptionistBody) -> dict[str, Any]:
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    from app.services.receptionist_svc import actualizar_config
    fila = await actualizar_config(config_id, body.model_dump(exclude_none=True))
    if not fila:
        raise HTTPException(status_code=404, detail="Sin campos validos o recepcionista inexistente.")
    return {"status": "OK", "receptionist": fila}


@router.post("/receptionists/{config_id}/reply")
async def receptionists_reply(config_id: str, request: Request, body: _ReceptionistReplyBody) -> dict[str, Any]:
    """Prueba el cerebro: dado un mensaje, devuelve la respuesta y si escala."""
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    from app.core.supabase_http import select as pg_select
    from app.services.receptionist_svc import responder
    filas = await pg_select("wa_receptionists", columns="*", filters={"id": config_id}, limit=1)
    if not filas:
        raise HTTPException(status_code=404, detail="Recepcionista no encontrada.")
    resultado = responder(filas[0], body.message, primer_mensaje=body.first_message)
    return {"status": "OK", **resultado}


@router.get("/recepcionista", response_class=HTMLResponse, include_in_schema=False)
async def recepcionista_page() -> str:
    """Pagina para configurar y probar la Recepcionista IA.

    La pagina no contiene datos: todo pasa por la API, que exige el token de
    operador.
    """
    return RECEPTIONIST_HTML


# ---------------------------------------------------------------------------
# RECEPCIONISTA IA - runtime (inbound) y medicion de resultados (Fase G)
# ---------------------------------------------------------------------------

class _InboundBody(BaseModel):
    message: str
    contact: str | None = None
    send: bool = False


class _ResultBody(BaseModel):
    client_name: str | None = None
    period: str | None = None
    baseline: dict[str, Any] | None = None
    current: dict[str, Any] | None = None
    notes: str | None = None


class _CompareBody(BaseModel):
    baseline: dict[str, Any] | None = None
    current: dict[str, Any] | None = None


@router.post("/receptionists/{config_id}/inbound")
async def receptionists_inbound(config_id: str, request: Request, body: _InboundBody) -> dict[str, Any]:
    """Atiende un mensaje entrante de un cliente final (runtime del producto).

    Modo seguro por defecto: registra y devuelve la respuesta. Con `send=true`
    usa el transporte piloto (bot de Veyra) y avisa al humano si escala.
    """
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    from app.core.supabase_http import select as pg_select
    from app.services.receptionist_svc import procesar_entrante

    filas = await pg_select("wa_receptionists", columns="*", filters={"id": config_id}, limit=1)
    if not filas:
        raise HTTPException(status_code=404, detail="Recepcionista no encontrada.")
    resultado = await procesar_entrante(filas[0], body.message, contacto=body.contact, enviar=body.send)
    return {"status": "OK", **resultado}


@router.get("/receptionists/{config_id}/history")
async def receptionists_history(config_id: str, request: Request, limit: int = 100) -> dict[str, Any]:
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    from app.services.receptionist_svc import historial

    return {"items": await historial(config_id, limit=limit)}


@router.get("/results")
async def results_list(request: Request, client: str | None = None) -> dict[str, Any]:
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    from app.services.results_svc import listar_resultados

    return {"items": await listar_resultados(client)}


@router.post("/results")
async def results_create(request: Request, body: _ResultBody) -> dict[str, Any]:
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    from app.services.results_svc import registrar_resultado

    fila = await registrar_resultado(body.model_dump(exclude_none=True))
    if not fila:
        raise HTTPException(status_code=422, detail="Falta el nombre del cliente.")
    return {"status": "OK", "result": fila}


@router.post("/results/compare")
async def results_compare(request: Request, body: _CompareBody) -> dict[str, Any]:
    """Compara linea base vs medicion (util para armar el informe de resultados)."""
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    from app.services.results_svc import resumen_mejora

    return {"status": "OK", **resumen_mejora(body.baseline, body.current)}
