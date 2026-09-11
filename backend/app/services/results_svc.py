"""Fase G - Medicion: resultados por cliente (linea base vs despues).

Permite registrar como estaba el negocio antes y como esta despues de la
implementacion, y calcular la mejora por metrica. No inventa cifras: solo
compara lo que se registra.

Convencion de metricas (todas numericas):
- `tiempo_respuesta_min`  -> MENOR es mejor
- cualquier otra (`leads`, `cierre_pct`, `facturacion`, `horas_ahorradas`...)
  -> MAYOR es mejor
"""

from __future__ import annotations

from typing import Any

# Metricas donde bajar es mejorar.
MENOR_ES_MEJOR = {"tiempo_respuesta_min", "tiempo_respuesta_horas", "horas_manual", "quejas"}


def calcular_mejora(baseline: dict[str, Any] | None, current: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Compara metricas comunes y devuelve el cambio porcentual por metrica."""
    base = baseline or {}
    ahora = current or {}
    filas: list[dict[str, Any]] = []
    for metrica, antes in base.items():
        if metrica not in ahora:
            continue
        despues = ahora.get(metrica)
        if not isinstance(antes, (int, float)) or not isinstance(despues, (int, float)):
            continue
        if antes == 0:
            continue
        cambio = round(((despues - antes) / abs(antes)) * 100, 1)
        mejor = (cambio < 0) if metrica in MENOR_ES_MEJOR else (cambio > 0)
        filas.append({
            "metrica": metrica,
            "antes": antes,
            "despues": despues,
            "cambio_pct": cambio,
            "mejora": mejor,
        })
    return filas


def resumen_mejora(baseline: dict[str, Any] | None, current: dict[str, Any] | None) -> dict[str, Any]:
    """Resumen legible: cuantas metricas mejoraron de cuantas comparables."""
    filas = calcular_mejora(baseline, current)
    mejoraron = [f for f in filas if f["mejora"]]
    return {
        "comparables": len(filas),
        "mejoraron": len(mejoraron),
        "detalle": filas,
        "texto": (
            f"Mejoraron {len(mejoraron)} de {len(filas)} metricas comparables."
            if filas else "Sin metricas comparables todavia."
        ),
    }


async def registrar_resultado(data: dict[str, Any]) -> dict[str, Any] | None:
    """Guarda una medicion de resultados para un cliente."""
    from app.core.supabase_http import insert as pg_insert

    client_name = (data.get("client_name") or "").strip()[:160]
    if not client_name:
        return None
    campos = {
        "client_name": client_name,
        "period": (data.get("period") or "").strip()[:40] or None,
        "baseline": data.get("baseline") or {},
        "current": data.get("current") or {},
        "notes": (data.get("notes") or "").strip()[:1000] or None,
    }
    filas = await pg_insert("client_results", campos)
    return filas[0] if filas else None


async def listar_resultados(client_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Mediciones registradas (opcionalmente filtrando por cliente)."""
    from app.core.supabase_http import select as pg_select

    filtros = {"client_name": client_name} if client_name else None
    return await pg_select(
        "client_results",
        columns="*",
        filters=filtros,
        order="measured_at.desc",
        limit=limit,
    )
