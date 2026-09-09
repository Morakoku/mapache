"""
Servidor local Scrapling para Mapache CRM.

Expone endpoints REST en localhost:8080 para ejecutar búsquedas de Scrapling
desde otros componentes del CRM (scheduler, control tower, etc.).

Corre de forma standalone:
    python -m uvicorn app.scrapers.scrapling_server:app --host localhost --port 8080

O como módulo de la app principal:
    uvicorn app.scrapers.scrapling_server:app --host localhost --port 8080
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.scrapers.scrapling_scraper import ScraplingMapsScraper, get_scraper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Término de búsqueda")
    limit: int = Field(default=20, ge=1, le=100, description="Número máximo de resultados")
    language: str = Field(default="es", pattern=r"^[a-z]{2}(-[A-Z]{2})?$", description="Idioma (ej es, en-US)")
    location: str | None = Field(default=None, max_length=200, description="Ubicación para centrar búsqueda")
    filters: dict[str, Any] | None = Field(default=None, description="Filtros adicionales (country, city, business_type)")


class SearchResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]
    count: int
    language: str
    location: str | None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["scrapling"])


@router.get("/health")
async def health() -> dict[str, Any]:
    """Health check del servidor Scrapling."""
    scraper = get_scraper()
    return {
        "status": "ok",
        "scraper_type": "scrapling",
        "scraper_class": "ScraplingMapsScraper",
        "base_url": scraper.base_url,
    }


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    """
    Ejecuta una búsqueda de Scrapling en Google Maps.

    Args:
        req: Request body con query, limit, language, location, filters

    Returns:
        SearchResponse con los resultados extraídos
    """
    logger.info(f"Scrapling server: search {req.query} limit={req.limit}")
    scraper = get_scraper()
    results = await scraper.search(
        query=req.query,
        limit=req.limit,
        language=req.language,
        location=req.location,
        filters=req.filters,
    )
    return SearchResponse(
        query=req.query,
        results=results,
        count=len(results),
        language=req.language,
        location=req.location,
    )


@router.get("/search", response_model=SearchResponse)
async def search_get(
    query: str,
    limit: int = 20,
    language: str = "es",
    location: str | None = None,
) -> SearchResponse:
    """
    Versión GET de search para testing rápido y compatibilidad.

    Ejemplo: GET /search?query=restaurantes+en+bogota&limit=10
    """
    logger.info(f"Scrapling server: search GET {query} limit={limit}")
    scraper = get_scraper()
    results = await scraper.search(
        query=query,
        limit=limit,
        language=language,
        location=location,
    )
    return SearchResponse(
        query=query,
        results=results,
        count=len(results),
        language=language,
        location=location,
    )


# ---------------------------------------------------------------------------
# App (standalone mode)
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Mapache Scrapling Server",
    description="Servidor local de Scrapling para Mapache CRM. Ejecuta búsquedas en Google Maps.",
    version="1.0.0",
)

app.include_router(router)


@app.get("/")
async def root() -> dict[str, Any]:
    """Root endpoint - información del servidor."""
    return {
        "name": "Mapache Scrapling Server",
        "version": "1.0.0",
        "endpoints": {
            "GET /health": "Health check",
            "POST /search": "Ejecutar búsqueda (body JSON)",
            "GET /search": "Ejecutar búsqueda (query params)",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=8081)
