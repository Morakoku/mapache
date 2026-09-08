"""Endpoints públicos para el dashboard (sin auth)."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["public-dashboard"])

_DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "static" / "torre-control.html"


def _read_dashboard_html() -> str:
    """Lee el HTML del dashboard desde static/torre-control.html."""
    if _DASHBOARD_PATH.exists():
        return _DASHBOARD_PATH.read_text(encoding="utf-8")
    return "<html><body><h1>Dashboard no disponible</h1><p>torre-control.html no encontrado en static/.</p></body></html>"


@router.get("/")
async def serve_dashboard() -> str:
    """Serve the control tower dashboard."""
    return _read_dashboard_html()


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


@router.get("/alert")
async def tc_alert() -> dict[str, Any]:
    """Chequea la salud de BD, email, scraper y envía email si alguno está down.
    Endpoint público para que el scheduler pueda invocarlo sin auth.
    """
    settings = get_settings()
    timestamp = datetime.now(UTC).isoformat()
    down_services: list[dict[str, Any]] = []

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
                status = "ok" if r.status_code == 200 else "error"
                message = "Conectado" if r.status_code == 200 else f"HTTP {r.status_code}"
                if status == "error":
                    down_services.append({"servicio": "database", "status": status, "latency": lat, "message": message})
        else:
            down_services.append({"servicio": "database", "status": "error", "message": "Sin config"})
    except Exception as e:
        down_services.append({"servicio": "database", "status": "error", "message": str(e)[:80]})

    # 2) Resend / Email
    t0 = time.time()
    try:
        if settings.resend_api_key_value:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get("https://api.resend.com/api-keys", headers={"Authorization": f"Bearer {settings.resend_api_key_value}"})
                lat = int((time.time() - t0) * 1000)
                status = "ok" if r.status_code == 200 else "error"
                message = "Resend OK" if r.status_code == 200 else f"HTTP {r.status_code}"
                if status == "error":
                    down_services.append({"servicio": "email", "status": status, "latency": lat, "message": message})
        else:
            down_services.append({"servicio": "email", "status": "error", "message": "Sin API key"})
    except Exception as e:
        down_services.append({"servicio": "email", "status": "error", "message": str(e)[:80]})

    # 3) Scraper
    t0 = time.time()
    try:
        scraper_url = os.environ.get("SCRAPER_URL", "")
        if scraper_url:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{scraper_url}/health")
                lat = int((time.time() - t0) * 1000)
                status = "ok" if r.status_code == 200 else "error"
                message = "Corriendo" if r.status_code == 200 else f"HTTP {r.status_code}"
                if status == "error":
                    down_services.append({"servicio": "scraper", "status": status, "latency": lat, "message": message})
        else:
            down_services.append({"servicio": "scraper", "status": "warn", "message": "No configurado", "detail": "SCRAPER_URL no definida"})
    except Exception:
        down_services.append({"servicio": "scraper", "status": "warn", "message": "Detenido", "detail": "No responde"})

    if not down_services:
        return {"status": "ok", "timestamp": timestamp, "mensaje": "Todos los servicios están operativos"}

    # Construir cuerpo del email
    lines = [f"<h3>Alerta de salud — {timestamp}</h3>", "<ul>"]
    for svc in down_services:
        lines.append(f"<li><strong>{svc['servicio']}</strong> — {svc.get('message', 'Desconocido')}"
                     f"{' (latencia: ' + str(svc.get('latency', 0)) + 'ms)' if 'latency' in svc else ''}"
                     f"{'<br><small>' + svc.get('detail', '') + '</small>' if svc.get('detail') else ''}"
                     f"</li>")
    lines.append("</ul>")
    html_body = "<br>".join(lines)

    # Enviar email con Resend
    api_key = settings.resend_api_key_value
    if not api_key:
        return {"status": "error", "timestamp": timestamp, "mensaje": "Resend API key no configurada", "fallos": down_services}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": "Veyra Soluciones <hola@veyrasoluciones.com>",
                    "to": "mbmbrochero510@gmail.com",
                    "subject": f"Mapache Alerta: {[s['servicio'] for s in down_services]} falló",
                    "html": html_body,
                },
            )
            if resp.status_code == 200:
                result = resp.json()
                logger.info("tc_alert_email_sent", to="mbmbrochero510@gmail.com", message_id=result.get("id"), fallos=[s["servicio"] for s in down_services])
                return {"status": "alert_sent", "timestamp": timestamp, "message_id": result.get("id"), "fallos": down_services}
            else:
                logger.error("tc_alert_email_failed", status=resp.status_code, detail=resp.text)
                return {"status": "error", "timestamp": timestamp, "mensaje": f"Error al enviar email: {resp.text}", "fallos": down_services}
    except httpx.HTTPError as exc:
        logger.error("tc_alert_http_error", error=str(exc))
        return {"status": "error", "timestamp": timestamp, "mensaje": str(exc), "fallos": down_services}


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
