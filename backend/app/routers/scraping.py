"""Servicio de scraping con google-maps-scraper.

Ejecuta el scraper de Go como servidor HTTP y expone una API Python
para integrar con Mapache CRM.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["scraping"])

# Ruta al ejecutable del scraper
SCRAPER_PATH = os.environ.get(
    "SCRAPER_PATH",
    r"C:\Users\edwin\Documents\Trinidad\google-maps-scraper.exe"
)
SCRAPER_PORT = int(os.environ.get("SCRAPER_PORT", "8080"))
SCRAPER_URL = f"http://localhost:{SCRAPER_PORT}"


def _ensure_scraper_running() -> bool:
    """Verifica que el scraper esté corriendo, si no lo inicia."""
    try:
        resp = httpx.get(f"{SCRAPER_URL}/health", timeout=2)
        return resp.status_code == 200
    except Exception:
        pass
    
    # Iniciar el scraper
    try:
        subprocess.Popen(
            [SCRAPER_PATH, "-web", "-addr", f":{SCRAPER_PORT}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)  # Esperar que arranque
        return True
    except Exception as e:
        logger.error("scraper_start_failed", error=str(e))
        return False


@router.get("/scraping/health")
async def scraping_health() -> dict[str, Any]:
    """Verifica el servicio de scraping."""
    if not _ensure_scraper_running():
        raise HTTPException(status_code=503, detail="Scraper no disponible")
    return {"status": "ok", "service": "google-maps-scraper"}


@router.post("/scraping/search")
async def scraping_search(
    query: str,
    limit: int = 20,
    language: str = "es",
) -> dict[str, Any]:
    """Ejecuta una búsqueda en Google Maps.
    
    Args:
        query: Texto de búsqueda (ej: "odontología in Bogotá")
        limit: Máximo de resultados
        language: Código de idioma (es, en)
    """
    if not _ensure_scraper_running():
        raise HTTPException(status_code=503, detail="Scraper no disponible")
    
    try:
        resp = httpx.post(
            f"{SCRAPER_URL}/api/search",
            json={"query": query, "limit": limit, "language": language},
            timeout=120,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        
        return resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Timeout en búsqueda")
    except Exception as e:
        logger.error("scraping_search_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scraping/batch")
async def scraping_batch(
    queries: list[str],
    limit_per_query: int = 20,
) -> dict[str, Any]:
    """Ejecuta múltiples búsquedas en lote.
    
    Args:
        queries: Lista de textos de búsqueda
        limit_per_query: Máximo de resultados por búsqueda
    """
    if not _ensure_scraper_running():
        raise HTTPException(status_code=503, detail="Scraper no disponible")
    
    results = []
    errors = []
    
    for query in queries:
        try:
            resp = httpx.post(
                f"{SCRAPER_URL}/api/search",
                json={"query": query, "limit": limit_per_query, "language": "es"},
                timeout=120,
            )
            if resp.status_code == 200:
                results.append({"query": query, "data": resp.json()})
            else:
                errors.append({"query": query, "error": resp.text})
        except Exception as e:
            errors.append({"query": query, "error": str(e)})
    
    return {
        "total_queries": len(queries),
        "successful": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }


def parse_scraper_csv(csv_path: str) -> list[dict[str, Any]]:
    """Parsea el CSV de resultados del scraper."""
    results = []
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                results.append({
                    "name": row.get("title", ""),
                    "category": row.get("category", ""),
                    "address": row.get("address", ""),
                    "phone": row.get("phone", ""),
                    "website": row.get("website", ""),
                    "email": row.get("emails", ""),
                    "rating": row.get("review_rating", ""),
                    "reviews_count": row.get("review_count", ""),
                    "latitude": row.get("latitude", ""),
                    "longitude": row.get("longitude", ""),
                    "maps_url": row.get("link", ""),
                })
    except Exception as e:
        logger.error("csv_parse_failed", path=csv_path, error=str(e))
    
    return results
