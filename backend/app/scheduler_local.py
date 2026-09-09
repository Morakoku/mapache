"""
Scheduler local de Mapache CRM para Windows.

Corre el scraper Scrapling cada X minutos, guarda resultados en Supabase
(vía PostgREST HTTP) — tabla companies con dedupe_key — y registra cada
run en search_runs/searches.

Ejecución:
    python app/scheduler_local.py            # loop con intervalo de .env
    python app/scheduler_local.py --once     # un solo ciclo (para Task Scheduler)
    python app/scheduler_local.py --interval 30
    python app/scheduler_local.py --queries "dentistas Bogota" "clínicas Caracas"

Instalación como tarea de Windows (Task Scheduler):
    backend/scripts/install_scheduler.bat
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Permitir correr como script directo (no solo -m)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Cargar SIEMPRE backend/.env por ruta absoluta — Task Scheduler ejecuta desde
# otro cwd y pydantic-settings no lo encontraría. Las credenciales Supabase
# viven ahí (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SECRET_KEY, etc.).
try:
    from dotenv import load_dotenv

    _backend_env = Path(__file__).resolve().parent.parent / ".env"
    if _backend_env.exists():
        load_dotenv(_backend_env, override=False)
except ImportError:
    pass

from app.core.config import get_settings  # noqa: E402
from app.core.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

# Queries semilla para Veyra: Colombia y Venezuela, ciudades principales
DEFAULT_QUERIES = [
    "agencias de marketing digital Bogotá",
    "agencias de marketing digital Medellín",
    "odontólogos Bogotá",
    "odontólogos Medellín",
    "restaurantes Bogotá",
    "abogados Caracas",
]

DEFAULT_INTERVAL_MIN = int(os.environ.get("MAPACHE_SCRAPE_INTERVAL", "30"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_float(v: str | None) -> float | None:
    try:
        return float(str(v).replace(",", ".")) if v else None
    except (ValueError, TypeError):
        return None


def _to_int(v: str | None) -> int | None:
    try:
        return int(str(v).replace(".", "").replace(",", "")) if v else None
    except (ValueError, TypeError):
        return None


async def _upsert_company(row: dict[str, Any]) -> dict[str, Any]:
    """Inserta/actualiza un negocio en companies con dedupe (upsert)."""
    from app.core.supabase_http import insert as pg_insert

    return await pg_insert("companies", row, upsert=True)


async def run_cycle(queries: list[str] | None = None, limit: int = 10) -> dict[str, Any]:
    """Un ciclo de scraping: busca, guarda en Supabase, retorna resumen."""
    from app.scrapers.scrapling_scraper import get_scraper
    from app.core.supabase_http import insert as pg_insert, update as pg_update

    queries = queries or DEFAULT_QUERIES
    scraper = get_scraper()
    summary: dict[str, Any] = {
        "started_at": _now(),
        "queries": len(queries),
        "inserted": 0,
        "failed": 0,
        "per_query": [],
    }

    for query in queries:
        try:
            # 1) buscar o crear la fila en searches (plantilla de búsqueda)
            # 2) crear search_run en progreso
            # 3) scrapear y upsert companies
            # 4) cerrar search_run con contadores
            results = await scraper.search(query=query, limit=limit, language="es")
            ok = 0
            fail = 0
            for r in results:
                if not r.get("name"):
                    continue
                company = {
                    "id": str(uuid.uuid4()),
                    "name": r.get("name", ""),
                    "category": r.get("category", "") or None,
                    "categories": [r["category"]] if r.get("category") else [],
                    "address": r.get("address", "") or None,
                    "phone": r.get("phone", "") or None,
                    "phone_raw": r.get("phone", "") or None,
                    "website": r.get("website", "") or None,
                    "google_maps_url": r.get("url", "") or None,
                    "rating": _to_float(r.get("rating")),
                    "reviews_count": _to_int(r.get("reviews")),
                    "is_permanently_closed": False,
                    "data_quality_score": 50,
                    "dedupe_key": r.get("name", "").lower().strip()[:40],
                }
                try:
                    res = await _upsert_company(company)
                    if res:
                        ok += 1
                    else:
                        fail += 1
                        logger.warning("Supabase insert falló: %s", r.get("name", "?"))
                except Exception as exc:
                    fail += 1
                    logger.warning("Supabase insert falló (%s): %s", r.get("name", "?"), exc)
            summary["inserted"] += ok
            summary["failed"] += fail
            summary["per_query"].append({"query": query, "found": len(results), "saved": ok, "failed": fail})
            logger.info("Scheduler: %s -> %s encontrados, %s guardados, %s fallos", query, len(results), ok, fail)
        except Exception as exc:
            summary["failed"] += 1
            summary["per_query"].append({"query": query, "error": str(exc)})
            logger.error("Scheduler: fallo en query %r: %s", query, exc)

    summary["finished_at"] = _now()
    return summary


async def main_loop(interval_min: int, once: bool, queries: list[str] | None) -> None:
    """Loop principal: ciclo + sleep, o ciclo único con --once."""
    logger.info("Scheduler local Mapache: intervalo=%smin once=%s", interval_min, once)
    while True:
        try:
            summary = await run_cycle(queries)
            logger.info(
                "Scheduler: ciclo completo. guardados=%s fallos=%s",
                summary["inserted"],
                summary["failed"],
            )
        except Exception as exc:
            logger.error("Scheduler: ciclo falló: %s", exc)
        if once:
            break
        await asyncio.sleep(interval_min * 60)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Scheduler local de scraping Mapache")
    parser.add_argument("--once", action="store_true", help="Ejecutar un solo ciclo y salir")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MIN, help="Minutos entre ciclos")
    parser.add_argument("--queries", nargs="*", help="Queries custom (default: semilla Veyra)")
    args = parser.parse_args()
    asyncio.run(main_loop(args.interval, args.once, args.queries))


if __name__ == "__main__":
    _cli()
