"""Torre de control Mapache CRM.

Dashboard en tiempo real con:
- Estado de todos los servicios
- Controles de encendido/apagado (vía HTTP, no subprocess)
- Métricas de DB, emails, scraping
- Logs en vivo
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["torre-control"])

# ---------------------------------------------------------------- HTML dashboard

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mapache CRM - Torre de Control</title>
<style>
:root {
  --bg: #0a0a0a;
  --card: #111111;
  --border: #1a1a1a;
  --fg: #e0e0e0;
  --muted: #666;
  --accent: #ff6b35;
  --green: #00c853;
  --red: #ff1744;
  --yellow: #ffd600;
  --blue: #2979ff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace;
  background: var(--bg);
  color: var(--fg);
  min-height: 100vh;
  padding: 20px;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 30px;
}
.header h1 { font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }
.header .logo { color: var(--accent); }
.header .status { display: flex; gap: 15px; align-items: center; }
.pill {
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
}
.pill.on { background: rgba(0,200,83,0.15); color: var(--green); border: 1px solid var(--green); }
.pill.off { background: rgba(255,23,68,0.15); color: var(--red); border: 1px solid var(--red); }
.pill.warn { background: rgba(255,214,0,0.15); color: var(--yellow); border: 1px solid var(--yellow); }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 20px;
  margin-bottom: 30px;
}
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  transition: border-color 0.2s;
}
.card:hover { border-color: var(--accent); }
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.card-title { font-size: 14px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
.card-value { font-size: 36px; font-weight: 700; margin-bottom: 8px; }
.card-sub { font-size: 13px; color: var(--muted); }

.btn {
  padding: 10px 20px;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}
.btn-on { background: var(--green); color: #000; }
.btn-on:hover { background: #00e676; }
.btn-off { background: var(--red); color: #fff; }
.btn-off:hover { background: #ff5252; }
.btn-blue { background: var(--blue); color: #fff; }
.btn-blue:hover { background: #448aff; }

.logs {
  background: #000;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  max-height: 400px;
  overflow-y: auto;
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12px;
  line-height: 1.6;
}
.log-entry { padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
.log-time { color: var(--muted); margin-right: 8px; }
.log-ok { color: var(--green); }
.log-err { color: var(--red); }
.log-warn { color: var(--yellow); }
.log-info { color: var(--blue); }

.refresh-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.refresh-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--fg);
  padding: 8px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
}
.refresh-btn:hover { border-color: var(--accent); }

@media (max-width: 768px) {
  .grid { grid-template-columns: 1fr; }
  .header { flex-direction: column; gap: 15px; }
}
</style>
</head>
<body>

<div class="header">
  <h1><span class="logo">🦝</span> Mapache CRM</h1>
  <div class="status">
    <span id="overall-pill" class="pill off">Verificando...</span>
    <span style="color: var(--muted); font-size: 13px;" id="last-update">--:--:--</span>
  </div>
</div>

<div class="refresh-bar">
  <div>
    <button class="refresh-btn" onclick="loadAll()">🔄 Refrescar</button>
    <button class="refresh-btn" onclick="toggleAuto()">⏱ Auto: <span id="auto-status">ON</span></button>
  </div>
  <div style="color: var(--muted); font-size: 12px;">Actualización cada 10s</div>
</div>

<div class="grid" id="services-grid"></div>

<div class="card" style="margin-bottom: 30px;">
  <div class="card-header">
    <span class="card-title">📊 Métricas de Base de Datos</span>
  </div>
  <div id="metrics-grid" class="grid" style="margin-top: 16px;">
    <div class="card"><div class="card-sub">Empresas</div><div class="card-value" id="metric-companies">--</div></div>
    <div class="card"><div class="card-sub">Contactos</div><div class="card-value" id="metric-contacts">--</div></div>
    <div class="card"><div class="card-sub">Emails enviados</div><div class="card-value" id="metric-emails">--</div></div>
    <div class="card"><div class="card-sub">Jobs activos</div><div class="card-value" id="metric-jobs">--</div></div>
  </div>
</div>

<div class="card">
  <div class="card-header">
    <span class="card-title">📋 Logs del Sistema</span>
    <button class="btn btn-blue" onclick="clearLogs()">Limpiar</button>
  </div>
  <div class="logs" id="logs-container">
    <div class="log-entry"><span class="log-time">--:--:--</span><span class="log-info">Esperando datos...</span></div>
  </div>
</div>

<script>
const API = '/torre-control';
let autoInterval = null;
let logs = [];

async function loadAll() {
  await Promise.all([loadServices(), loadMetrics()]);
  document.getElementById('last-update').textContent = new Date().toLocaleTimeString('es-CO');
}

async function loadServices() {
  try {
    const res = await fetch(`${API}/status`);
    const data = await res.json();
    renderServices(data);
    updateOverall(data);
    addLog('Servicios verificados', 'ok');
  } catch (e) {
    addLog('Error cargando servicios: ' + e.message, 'err');
  }
}

async function loadMetrics() {
  try {
    const res = await fetch(`${API}/metrics`);
    const data = await res.json();
    document.getElementById('metric-companies').textContent = data.companies ?? '--';
    document.getElementById('metric-contacts').textContent = data.contacts ?? '--';
    document.getElementById('metric-emails').textContent = data.emails_sent ?? '--';
    document.getElementById('metric-jobs').textContent = data.active_jobs ?? '--';
  } catch (e) {
    addLog('Error cargando métricas: ' + e.message, 'err');
  }
}

function renderServices(data) {
  const grid = document.getElementById('services-grid');
  const services = [
    { key: 'database', icon: '🗄️', title: 'Base de Datos', desc: 'PostgREST + Supabase' },
    { key: 'email', icon: '📧', title: 'Email (Resend)', desc: 'Envío de correos' },
    { key: 'scraper', icon: '🕷️', title: 'Google Maps Scraper', desc: 'Descubrimiento' },
    { key: 'api', icon: '⚡', title: 'API Mapache', desc: 'FastAPI + Vercel' },
  ];
  
  grid.innerHTML = services.map(s => {
    const st = data.services?.[s.key] || {};
    const status = st.status || 'unknown';
    const pillClass = status === 'ok' ? 'on' : status === 'error' ? 'off' : 'warn';
    const pillText = status === 'ok' ? 'Operativo' : status === 'error' ? 'Error' : 'Verificando';
    const canToggle = s.key === 'scraper';
    return `
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">${s.title}</div>
            <div style="font-size: 20px; margin-top: 4px;">${s.icon} ${st.message || ''}</div>
          </div>
          <span class="pill ${pillClass}">${pillText}</span>
        </div>
        <div class="card-sub">${s.desc}</div>
        ${st.latency ? `<div class="card-sub" style="margin-top:8px;">Latencia: <strong>${st.latency}ms</strong></div>` : ''}
        ${st.detail ? `<div class="card-sub" style="color:var(--yellow);margin-top:4px;">${st.detail}</div>` : ''}
        ${canToggle ? `
          <div style="margin-top:16px; display:flex; gap:8px;">
            <button class="btn btn-on" onclick="toggleService('scraper', 'start')">▶ Encender</button>
            <button class="btn btn-off" onclick="toggleService('scraper', 'stop')">⏹ Apagar</button>
          </div>
        ` : ''}
      </div>
    `;
  }).join('');
}

function updateOverall(data) {
  const services = data.services || {};
  const allOk = Object.values(services).every(s => s.status === 'ok');
  const pill = document.getElementById('overall-pill');
  pill.textContent = allOk ? 'Todos operativos' : 'Atención requerida';
  pill.className = 'pill ' + (allOk ? 'on' : 'warn');
}

async function toggleService(service, action) {
  try {
    addLog(`${action === 'start' ? 'Encendiendo' : 'Apagando'} ${service}...`, 'info');
    const res = await fetch(`${API}/services/${service}/${action}`, { method: 'POST' });
    const data = await res.json();
    addLog(data.message || 'Completado', data.success ? 'ok' : 'err');
    loadAll();
  } catch (e) {
    addLog('Error: ' + e.message, 'err');
  }
}

function addLog(msg, level = 'info') {
  const time = new Date().toLocaleTimeString('es-CO');
  logs.unshift({ time, msg, level });
  if (logs.length > 100) logs.pop();
  renderLogs();
}

function renderLogs() {
  const container = document.getElementById('logs-container');
  container.innerHTML = logs.map(l => `
    <div class="log-entry">
      <span class="log-time">${l.time}</span>
      <span class="log-${l.level}">${l.msg}</span>
    </div>
  `).join('');
}

function clearLogs() { logs = []; renderLogs(); }

function toggleAuto() {
  if (autoInterval) {
    clearInterval(autoInterval);
    autoInterval = null;
    document.getElementById('auto-status').textContent = 'OFF';
  } else {
    autoInterval = setInterval(loadAll, 10000);
    document.getElementById('auto-status').textContent = 'ON';
  }
}

// Carga inicial
loadAll();
autoInterval = setInterval(loadAll, 10000);
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    """Dashboard principal de la torre de control."""
    return DASHBOARD_HTML


@router.get("/status")
async def torre_status() -> dict[str, Any]:
    """Estado de todos los servicios (público para el dashboard)."""
    results: dict[str, Any] = {"services": {}, "timestamp": datetime.now(UTC).isoformat()}
    
    # 1) Base de datos (PostgREST)
    t0 = time.time()
    try:
        settings = get_settings()
        if settings.supabase_url and settings.supabase_service_role_key_value:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    settings.supabase_url.rstrip("/") + "/rest/v1/alembic_version?select=version_num&limit=1",
                    headers={
                        "apikey": settings.supabase_service_role_key_value,
                        "Authorization": f"Bearer {settings.supabase_service_role_key_value}",
                    },
                )
                latency = int((time.time() - t0) * 1000)
                if resp.status_code == 200:
                    results["services"]["database"] = {
                        "status": "ok",
                        "latency": latency,
                        "message": "Conectado",
                    }
                else:
                    results["services"]["database"] = {
                        "status": "error",
                        "latency": latency,
                        "message": f"HTTP {resp.status_code}",
                    }
        else:
            results["services"]["database"] = {
                "status": "error",
                "message": "Sin configuración",
                "detail": "SUPABASE_URL o SERVICE_ROLE_KEY faltantes",
            }
    except Exception as e:
        results["services"]["database"] = {
            "status": "error",
            "message": "Sin conexión",
            "detail": str(e)[:100],
        }
    
    # 2) Email (Resend)
    t0 = time.time()
    try:
        settings = get_settings()
        if settings.resend_api_key_value:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.resend.com/api-keys",
                    headers={"Authorization": f"Bearer {settings.resend_api_key_value}"},
                )
                latency = int((time.time() - t0) * 1000)
                results["services"]["email"] = {
                    "status": "ok" if resp.status_code == 200 else "error",
                    "latency": latency,
                    "message": "Resend OK" if resp.status_code == 200 else f"HTTP {resp.status_code}",
                }
        else:
            results["services"]["email"] = {
                "status": "error",
                "message": "Sin API key",
                "detail": "RESEND_API_KEY no configurada",
            }
    except Exception as e:
        results["services"]["email"] = {
            "status": "error",
            "message": "Sin conexión",
            "detail": str(e)[:100],
        }
    
    # 3) Scraper (verifica vía HTTP si está corriendo)
    t0 = time.time()
    try:
        scraper_url = os.environ.get("SCRAPER_URL", "http://localhost:8080")
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{scraper_url}/health")
            latency = int((time.time() - t0) * 1000)
            results["services"]["scraper"] = {
                "status": "ok" if resp.status_code == 200 else "error",
                "latency": latency,
                "message": "Corriendo" if resp.status_code == 200 else f"HTTP {resp.status_code}",
            }
    except Exception:
        results["services"]["scraper"] = {
            "status": "warn",
            "message": "Detenido",
            "detail": "No responde en " + os.environ.get("SCRAPER_URL", "http://localhost:8080"),
        }
    
    # 4) API Mapache
    t0 = time.time()
    try:
        settings = get_settings()
        base_url = settings.public_base_url or "http://localhost:8000"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/health")
            latency = int((time.time() - t0) * 1000)
            results["services"]["api"] = {
                "status": "ok" if resp.status_code == 200 else "error",
                "latency": latency,
                "message": "API respondiendo" if resp.status_code == 200 else f"HTTP {resp.status_code}",
            }
    except Exception as e:
        results["services"]["api"] = {
            "status": "error",
            "message": "Sin conexión",
            "detail": str(e)[:100],
        }
    
    return results


@router.get("/metrics")
async def torre_metrics() -> dict[str, Any]:
    """Métricas de la base de datos (público para el dashboard)."""
    metrics = {"companies": 0, "contacts": 0, "emails_sent": 0, "active_jobs": 0}
    
    try:
        from app.core.postgrest_client import pg_count
        
        metrics["companies"] = await pg_count("companies")
        metrics["contacts"] = await pg_count("contacts") 
        metrics["emails_sent"] = await pg_count("email_messages", {"direction": "eq.OUTBOUND"})
        metrics["active_jobs"] = await pg_count("jobs", {"status": "eq.QUEUED"})
    except Exception as e:
        logger.error("metrics_error", error=str(e))
    
    return metrics


@router.post("/services/scraper/{action}")
async def control_scraper(action: str) -> dict[str, Any]:
    """Controla el servicio de scraping vía HTTP.
    
    En Vercel serverless no se pueden usar subprocess. En su lugar,
    enviamos un request HTTP al scraper para iniciarlo o detenerlo.
    El scraper debe estar corriendo en una máquina local o VPS.
    """
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="Acción inválida: use 'start' o 'stop'")
    
    scraper_url = os.environ.get("SCRAPER_URL", "http://localhost:8080")
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if action == "start":
                resp = await client.post(f"{scraper_url}/start")
                return {
                    "success": resp.status_code == 200,
                    "message": f"Señal de inicio enviada al scraper" if resp.status_code == 200 else f"Error: HTTP {resp.status_code}",
                }
            else:
                resp = await client.post(f"{scraper_url}/stop")
                return {
                    "success": resp.status_code == 200,
                    "message": f"Señal de detención enviada al scraper" if resp.status_code == 200 else f"Error: HTTP {resp.status_code}",
                }
    except Exception as e:
        return {
            "success": False,
            "message": f"No se conectar al scraper en {scraper_url}: {e}",
        }


@router.post("/services/database/test")
async def test_database() -> dict[str, Any]:
    """Prueba la conexión a la base de datos."""
    try:
        from app.core.postgrest_client import pg_select
        result = await pg_select("alembic_version", columns="version_num", limit=1)
        return {"success": True, "message": f"DB OK. Versión: {result[0].get('version_num', 'N/A') if result else 'N/A'}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/services/email/test")
async def test_email() -> dict[str, Any]:
    """Prueba el envío de email."""
    settings = get_settings()
    if not settings.resend_api_key_value:
        return {"success": False, "message": "Resend API key no configurada"}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key_value}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": "Mapache CRM <hola@veyrasoluciones.com>",
                    "to": "edwin@veyrasoluciones.com",
                    "subject": "Test desde Torre de Control",
                    "text": f"Email de prueba enviado a las {datetime.now(UTC).isoformat()}",
                },
            )
            return {"success": resp.status_code == 200, "message": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)}
