"""Orquestador de descubrimiento continuo (loop infinito hasta marcador de stop).

Rota las búsquedas maestras de Colombia + Venezuela. Lanza la siguiente cuando
la anterior termina. Reporta incrementos de empresas/email/leads en la salida
estándar y a un archivo de log. Para detenerlo, se crea el archivo STOP_FILE;
el orquestador termina tras el búsqueda en curso y lo limpia.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error

API = os.getenv("MAPACHE_DISCOVERY_API", "http://127.0.0.1:8000/api/v1").rstrip("/")
SEARCH_NAMES = [
    "Odonto-CO-Medellin", "Odonto-VE-Caracas",
    "Salud-Clinicas-CO-Bogota", "Salud-Clinicas-VE-Caracas",
    "Inmobiliaria-CO-Medellin", "Inmobiliaria-VE-Valencia",
    "Logistica-CO-Barranquilla", "Logistica-VE-Maracaibo",
    "Retail-CO-Cali", "Finanzas-CO-Bogota",
    "Barberia-CO-Medellin", "Barberia-VE-Caracas",
    "Autos-CO-Bogota", "Gastronomia-CO-Cali",
]
CYCLE_INTERVAL_S = 90       # espera entre una búsqueda terminada y la siguiente
JOB_POLL_S = 12            # cadencia de sondeo del estado del job en cola
REPORT_EVERY_TICKS = 4     # imprime un snapshot global cada N polls estando en carrera
STOP_FILE = os.getenv(
    "DISCOVERY_STOP_FILE",
    r"C:\Users\ADMINI~1\AppData\Local\Temp\opencode\guaki_stop.flag",
)
LOG_FILE = os.getenv(
    "DISCOVERY_LOG_FILE",
    r"C:\Users\ADMINI~1\AppData\Local\Temp\opencode\guaki_discovery.log",
)


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _http(method: str, path: str) -> dict | None:
    url = f"{API}/{path.lstrip('/')}"
    req = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def _post(path: str) -> dict | None:
    req = urllib.request.Request(
        f"{API}/{path.lstrip('/')}", method="POST",
        headers={"Content-Type": "application/json"}, data=b"{}",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if e.code in (400, 409, 422):
            return None  # ya corriendo o triple duplicado: se relée más tarde
        raise


def _search_ids() -> dict:
    try:
        arr = _http("GET", "searches") or []
    except Exception:
        return {}
    return {s["name"]: str(s["id"]) for s in arr}


def _launch(sid: str) -> str | None:
    job = _post(f"searches/{sid}/run")
    return str((job or {}).get("job_id")) if job else None


def _job(job_id: str) -> tuple[str, dict]:
    try:
        data = _http("GET", "jobs?limit=60") or {}
    except Exception:
        return "UNKNOWN", {}
    for item in data.get("items", []):
        if str(item["id"]) == job_id:
            return item["status"], (item.get("result") or {})
    return "GONE", {}


def _overview() -> dict:
    try:
        return _http("GET", "metrics/overview") or {}
    except Exception:
        return {}


def main() -> None:
    _log(f"GUAKI/AI-STUDIO DISCOVERY LOOP arrancado — stop cuando exista: {STOP_FILE}")
    snap = _overview()
    _log(f"base -> companies={snap.get('companies_found')} email={snap.get('companies_with_email')} leads={snap.get('leads')}")

    total_new = 0
    tick = 0
    while True:
        if os.path.exists(STOP_FILE):
            _log("Marcador de stop detectado. Fin.")
            try:
                os.remove(STOP_FILE)
            except OSError:
                pass
            break

        ids = _search_ids()
        names = [n for n in SEARCH_NAMES if n in ids]
        if not names:
            _log("Sin búsquedas disponibles; reconsulto en 30s.")
            time.sleep(30)
            continue

        for name in names:
            if os.path.exists(STOP_FILE):
                break
            sid = ids[name]
            job_id = _launch(sid)
            if not job_id:
                time.sleep(10)
                continue

            ticks_in = 0
            while True:
                time.sleep(JOB_POLL_S)
                st, res = _job(job_id)
                ticks_in += 1
                if st in ("COMPLETED", "FAILED", "CANCELLED", "GONE"):
                    break
                if ticks_in % REPORT_EVERY_TICKS == 0:
                    snap = _overview()
                    _log(f"[en curso {name}] {st} | tot={snap.get('companies_found')} email={snap.get('companies_with_email')} leads={snap.get('leads')}")

            if res and st == "COMPLETED":
                total_new += int(res.get("new") or 0)
            snap = _overview()
            _log(f"[FIN {name}] {st} found={res.get('found')} new={res.get('new')} dup={res.get('duplicate')} | acum_new={total_new} | tot={snap.get('companies_found')} email={snap.get('companies_with_email')} leads={snap.get('leads')}")

            time.sleep(CYCLE_INTERVAL_S)

        # Una vuelta completa de las 14 buscadas; se re-emite el resumen y gira.
        snap = _overview()
        _log(f"Ronda completada. Acumulado nuevas={total_new} | tot={snap.get('companies_found')} email={snap.get('companies_with_email')} leads={snap.get('leads')}")
        time.sleep(CYCLE_INTERVAL_S)


if __name__ == "__main__":
    main()
