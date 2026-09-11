"""
Scheduler local de Mapache CRM para Windows.

Corre el scraper Scrapling cada X minutos, guarda resultados en Supabase
(vía PostgREST HTTP) — tabla companies con dedupe_key — y registra cada
run en search_runs/searches. Cada query va etiquetada con su vertical
(veyra = leads B2B, guaki = marketplace) y, tras guardar los resultados
de cada query, se enriquecen best-effort las fichas individuales.

Nota: companies NO tiene columna `source`, así que la vertical no se
persiste en la fila — viaja en el resumen del ciclo y en los logs hasta
que el schema la añada.

Ejecución:
    python app/scheduler_local.py            # loop con intervalo de .env (todas las verticales)
    python app/scheduler_local.py --once     # un solo ciclo (para Task Scheduler)
    python app/scheduler_local.py --interval 30
    python app/scheduler_local.py --once --vertical guaki
    python app/scheduler_local.py --queries "dentistas Bogota" --vertical veyra

Instalación como tarea de Windows (Task Scheduler):
    backend/scripts/install_scheduler.bat
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
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

# Queries semilla por vertical. Veyra: B2B (agencias, odontólogos, abogados)
# en Colombia y Venezuela. Guaki: marketplace (restaurantes y servicios locales).
VERTICAL_QUERIES: dict[str, list[str]] = {
    "veyra": [
        "agencias de marketing digital Bogotá",
        "agencias de marketing digital Medellín",
        "odontólogos Bogotá",
        "odontólogos Medellín",
        "abogados Caracas",
    ],
    "guaki": [
        "restaurantes Bogotá",
        "restaurantes Medellín",
    ],
}

# Vertical por defecto para --queries custom (compat con CLI anterior).
DEFAULT_VERTICAL = "veyra"

DEFAULT_INTERVAL_MIN = int(os.environ.get("MAPACHE_SCRAPE_INTERVAL", "30"))

# Cuántas fichas individuales enriquecer tras cada query (best-effort).
ENRICH_TOP_N = int(os.environ.get("MAPACHE_ENRICH_TOP_N", "3"))

# Cuántas queries correr por ciclo (default: 2). Con 7 queries semilla,
# correr todas en cada ciclo haría: (a) siempre el mismo orden —
# Google Maps sirve resultados casi idénticos cada vez, (b) ciclos de
# ~5 min. Randomizar orden + muestrear subset evita el footprint
# repetido y reparte el scraping entre verticales.
QUERIES_PER_CYCLE = int(os.environ.get("MAPACHE_QUERIES_PER_CYCLE", "2"))


def _build_random_plan(queries_per_cycle: int) -> list[tuple[str, str]]:
    """Plan de queries randomizado: mezcla verticales y muestrea subset.

    - Baraja el orden de todas las queries semilla (vertical + query).
    - Toma las primeras `queries_per_cycle` del mazo barajado.
    - Si una vertical queda sin representación tras el muestreo, la
      reinyecta para no dejar semanas enteras sin datos de Guaki.
    """
    plan_all = [(v, q) for v, qs in VERTICAL_QUERIES.items() for q in qs]
    random.shuffle(plan_all)
    plan = plan_all[:queries_per_cycle]
    verticals_in_plan = {v for v, _ in plan}
    for v in VERTICAL_QUERIES:
        if v not in verticals_in_plan and queries_per_cycle >= len(VERTICAL_QUERIES):
            # Solo si el ciclo tiene espacio para todas las verticales
            candidates = [(vv, q) for vv, q in plan_all if vv == v]
            if candidates:
                plan.append(candidates[0])
    return plan


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


def _phone_e164(raw: str | None) -> str | None:
    """Teléfono a E.164 (región CO por defecto) o None si no es válido.

    companies.phone es VARCHAR(20) en E.164; el formato local de Maps
    (`(601) 7443466`) no cabe garantizado y rompe el dedupe por teléfono.
    """
    from app.utils.phone import to_e164

    return to_e164(raw)


async def _upsert_company(row: dict[str, Any]) -> dict[str, Any]:
    """Inserta/actualiza un negocio en companies con dedupe (upsert)."""
    from app.core.supabase_http import insert as pg_insert

    return await pg_insert("companies", row, upsert=True)


async def _enrich_top(
    scraper: Any,
    results: list[dict[str, Any]],
    top_n: int,
    *,
    company_ids: dict[str, str] | None = None,
) -> int:
    """Enriquece best-effort las `top_n` primeras fichas de una query.

    Visita la ficha individual de Google Maps (scrapling_scraper.enrich_details)
    para sacar teléfono/web que la lista de resultados no trae, y actualiza la
    fila en companies vía PostgREST. Nunca lanza: un fallo aquí no debe tumbar
    el ciclo de scraping — se loguea y se sigue.

    Args:
        scraper: instancia de ScraplingMapsScraper.
        results: resultados de la query (dicts con name, url, ...).
        top_n: cuántas fichas enriquecer.
        company_ids: opcional, name -> id de companies para filtrar el update.
            Si viene, solo se actualizan filas guardadas en este ciclo.

    Returns:
        Número de fichas que aportaron al menos un dato nuevo (phone/website).
    """
    from app.core.supabase_http import update as pg_update
    from app.utils.phone import to_e164
    from app.utils.url import normalize_url

    if top_n <= 0:
        return 0

    loop = asyncio.get_running_loop()
    enriched = 0
    for r in results[:top_n]:
        name = r.get("name", "?")
        place_url = r.get("url") or r.get("google_maps_url") or ""
        if not place_url:
            continue
        try:
            # enrich_details es síncrono (StealthyFetcher.fetch) — executor,
            # igual que hace scraper.search() por dentro.
            details = await loop.run_in_executor(
                None,
                lambda url=place_url: scraper.enrich_details(url),
            )
            phone_e164 = to_e164(details.get("phone")) or None
            website = normalize_url(details.get("website")) or None
            if not phone_e164 and not website:
                continue

            # Solo actualizar filas que este ciclo acaba de guardar (o que
            # existan con ese id); si el insert falló, no hay dónde escribir.
            if company_ids is not None and name not in company_ids:
                continue

            update_data: dict[str, Any] = {"last_enriched_at": _now()}
            if phone_e164:
                update_data["phone"] = phone_e164
                update_data["phone_raw"] = details.get("phone") or phone_e164
            if website:
                update_data["website"] = website
            row_id = company_ids.get(name) if company_ids else r.get("id")
            if not row_id:
                continue
            res = await pg_update("companies", {"id": str(row_id)}, update_data)
            if res:
                enriched += 1
                logger.info(
                    "Scheduler: enriquecida %r (tel=%s, web=%s)",
                    name,
                    "sí" if phone_e164 else "no",
                    "sí" if website else "no",
                )
            else:
                logger.warning("Scheduler: update de enrich falló para %r", name)
        except Exception as exc:
            # Best-effort: nunca romper el ciclo por el enriquecimiento.
            logger.warning("Scheduler: enrich falló para %r: %s", name, exc)
    return enriched


async def run_cycle(
    queries: list[str] | None = None,
    limit: int = 10,
    vertical: str = DEFAULT_VERTICAL,
    enrich_top: int = ENRICH_TOP_N,
    randomize: bool = True,
) -> dict[str, Any]:
    """Un ciclo de scraping: busca, guarda en Supabase y enriquece fichas.

    Args:
        queries: queries custom; si es None, plan randomizado de las verticales.
        limit: máximo de resultados por query.
        vertical: vertical de las queries custom ("veyra" | "guaki"). Con
            queries=None se ignoran queries y vertical — corre el plan random.
        enrich_top: fichas a enriquecer por query tras guardar (0 desactiva).
        randomize: si True (default), muestrea y baraja las queries semilla.
            False = todas las queries en orden fijo (comportamiento legacy).

    Returns:
        Resumen con started_at/finished_at, contadores inserted/failed/enriched
        y el detalle per_query.
    """
    from app.scrapers.scrapling_scraper import get_scraper

    # queries=None -> plan randomizado: subset barajado de las verticales.
    # queries=[...] -> las custom tal cual (compat con --queries del CLI).
    if queries is None:
        if randomize:
            plan = _build_random_plan(QUERIES_PER_CYCLE)
        else:
            plan = [(v, q) for v, qs in VERTICAL_QUERIES.items() for q in qs]
    else:
        plan = [(vertical, q) for q in queries]

    scraper = get_scraper()
    summary: dict[str, Any] = {
        "started_at": _now(),
        "queries": len(plan),
        "verticals": sorted({v for v, _ in plan}),
        "inserted": 0,
        "failed": 0,
        "enriched": 0,
        "per_query": [],
    }

    for vertical_tag, query in plan:
        try:
            # 1) buscar o crear la fila en searches (plantilla de búsqueda)
            # 2) crear search_run en progreso
            # 3) scrapear y upsert companies
            # 4) cerrar search_run con contadores
            # 5) enriquecer best-effort las primeras fichas de la query
            results = await scraper.search(query=query, limit=limit, language="es")
            ok = 0
            fail = 0
            # name -> id de las filas guardadas en ESTA query (para enrich)
            saved_ids: dict[str, str] = {}
            for r in results:
                if not r.get("name"):
                    continue
                company = {
                    # Sin `id`: en el upsert por (owner_id, dedupe_key) un id nuevo
                    # reescribiría la PK y rompería la FK de leads. La BD genera el
                    # id en inserts nuevos y conserva el existente en los merges.
                    "name": r.get("name", ""),
                    "category": r.get("category", "") or None,
                    "categories": [r["category"]] if r.get("category") else [],
                    "address": r.get("address", "") or None,
                    "phone": _phone_e164(r.get("phone")),
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
                        saved_ids[r.get("name", "")] = res[0].get("id")
                    else:
                        fail += 1
                        logger.warning("Supabase insert falló: %s", r.get("name", "?"))
                except Exception as exc:
                    fail += 1
                    logger.warning("Supabase insert falló (%s): %s", r.get("name", "?"), exc)

            # Enriquecimiento best-effort tras guardar la query.
            enriched = await _enrich_top(
                scraper, results, enrich_top, company_ids=saved_ids
            )

            summary["inserted"] += ok
            summary["failed"] += fail
            summary["enriched"] += enriched
            summary["per_query"].append(
                {
                    "query": query,
                    "vertical": vertical_tag,
                    "found": len(results),
                    "saved": ok,
                    "failed": fail,
                    "enriched": enriched,
                }
            )
            logger.info(
                "Scheduler: [%s] %s -> %s encontrados, %s guardados, %s fallos, %s enriquecidos",
                vertical_tag,
                query,
                len(results),
                ok,
                fail,
                enriched,
            )
        except Exception as exc:
            summary["failed"] += 1
            summary["per_query"].append(
                {"query": query, "vertical": vertical_tag, "error": str(exc)}
            )
            logger.error("Scheduler: fallo en query %r: %s", query, exc)

    summary["finished_at"] = _now()
    return summary


async def main_loop(
    interval_min: int,
    once: bool,
    queries: list[str] | None,
    vertical: str = DEFAULT_VERTICAL,
    enrich_top: int = ENRICH_TOP_N,
    all_queries: bool = False,
) -> None:
    """Loop principal: ciclo + sleep, o ciclo único con --once."""
    logger.info(
        "Scheduler local Mapache: intervalo=%smin once=%s vertical=%s enrich_top=%s all=%s",
        interval_min,
        once,
        vertical,
        enrich_top,
        all_queries,
    )
    while True:
        try:
            summary = await run_cycle(
                queries,
                vertical=vertical,
                enrich_top=enrich_top,
                randomize=not all_queries,
            )
            logger.info(
                "Scheduler: ciclo completo. guardados=%s fallos=%s enriquecidos=%s queries=%s",
                summary["inserted"],
                summary["failed"],
                summary["enriched"],
                summary["queries"],
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
    parser.add_argument(
        "--vertical",
        choices=sorted(VERTICAL_QUERIES.keys()),
        default=DEFAULT_VERTICAL,
        help="Vertical de las --queries custom (default: veyra)",
    )
    parser.add_argument(
        "--enrich-top",
        type=int,
        default=ENRICH_TOP_N,
        help=f"Fichas a enriquecer por query tras guardar (default: {ENRICH_TOP_N}, 0 desactiva)",
    )
    parser.add_argument(
        "--queries", nargs="*", help="Queries custom (default: plan randomizado de verticales)"
    )
    parser.add_argument(
        "--all-queries",
        action="store_true",
        help="Corre TODAS las queries semilla en orden fijo (legacy, sin randomizar)",
    )
    args = parser.parse_args()
    queries = None if not args.queries else args.queries
    asyncio.run(
        main_loop(
            args.interval,
            args.once,
            queries,
            args.vertical,
            args.enrich_top,
            all_queries=args.all_queries,
        )
    )


if __name__ == "__main__":
    _cli()
