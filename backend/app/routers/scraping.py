"""Servicio de scraping con Scrapling.

Reemplaza a google-maps-scraper.exe (Go). Usa StealthyFetcher con
Playwright/Camoufox para búsquedas en Google Maps y expone la API
para Mapache CRM. El scraping pesado corre en el servidor local de
Scrapling (localhost:8080) — ver app/scrapers/scrapling_server.py.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["scraping"])

# Servidor local de Scrapling (corría aparte con uvicorn)
# Puerto 8080 suele estar tomado por Steam webhelper; default 8081.
SCRAPER_PORT = int(os.environ.get("SCRAPER_PORT", "8081"))
SCRAPER_URL = os.environ.get("SCRAPER_URL", f"http://localhost:{SCRAPER_PORT}")


class SearchBody(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)
    language: str = Field(default="es", max_length=8)
    location: str | None = Field(default=None, max_length=200)


def _scraper_up() -> bool:
    """True si el servidor local de Scrapling responde /health."""
    try:
        resp = httpx.get(f"{SCRAPER_URL}/health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


@router.get("/scraping/health")
async def scraping_health() -> dict[str, Any]:
    """Verifica el servicio de scraping (servidor local Scrapling)."""
    if not _scraper_up():
        return {
            "status": "degraded",
            "service": "scrapling",
            "detail": f"Servidor local no responde en {SCRAPER_URL}. "
            f"Iniciar con: python -m uvicorn app.scrapers.scrapling_server:app --port {SCRAPER_PORT}",
        }
    return {"status": "ok", "service": "scrapling", "url": SCRAPER_URL}


@router.post("/scraping/search")
async def scraping_search(body: SearchBody) -> dict[str, Any]:
    """Ejecuta una búsqueda en Google Maps vía Scrapling.

    Si el servidor local está arriba, lo usa; si no, ejecuta el
    scraper en-proceso (más lento pero funcional).
    """
    try:
        if _scraper_up():
            resp = httpx.post(
                f"{SCRAPER_URL}/search",
                json=body.model_dump(),
                timeout=180,
            )
            if resp.status_code == 200:
                return resp.json()
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        # Fallback en-proceso (sin servidor local)
        from app.scrapers.scrapling_scraper import get_scraper

        scraper = get_scraper()
        results = await scraper.search(
            query=body.query,
            limit=body.limit,
            language=body.language,
            location=body.location,
        )
        return {
            "query": body.query,
            "count": len(results),
            "results": results,
            "engine": "scrapling-inprocess",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("scraping_search_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scraping/batch")
async def scraping_batch(
    queries: list[str],
    limit_per_query: int = 20,
) -> dict[str, Any]:
    """Ejecuta múltiples búsquedas en lote vía Scrapling."""
    from app.scrapers.scrapling_scraper import get_scraper

    scraper = get_scraper()
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for query in queries:
        try:
            items = await scraper.search(query=query, limit=limit_per_query, language="es")
            results.append({"query": query, "data": items, "count": len(items)})
        except Exception as e:
            errors.append({"query": query, "error": str(e)})

    return {
        "total_queries": len(queries),
        "successful": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
        "engine": "scrapling",
    }


@router.post("/scraping/enrich")
async def scraping_enrich(place_url: str) -> dict[str, Any]:
    """Enriquece una ficha: teléfono, website, reviews desde la página de detalle."""
    from app.scrapers.scrapling_scraper import get_scraper

    scraper = get_scraper()
    details = scraper.enrich_details(place_url)
    return {"url": place_url, "details": details, "engine": "scrapling"}
