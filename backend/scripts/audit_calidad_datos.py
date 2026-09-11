#!/usr/bin/env python3
"""Auditoría de calidad de datos del CRM Mapache (solo lectura, sin cambios).

Reporta 4 métricas:
  1. companies sin phone, sin website y sin email (no contactables).
  2. companies con city NULL (o vacía).
  3. contacts con email invalido (regex básica) — emails no nulos que no
     matchean el patron; se imprimen enmascarados.
  4. leads con company_id que apunta a una company inexistente (FK rota),
     y de paso leads sin company_id.

Standalone: solo httpx + credenciales de backend/.env.
Uso: python audit_calidad_datos.py [--env ruta/.env]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import httpx

RUTA_BACKEND = Path(__file__).resolve().parent.parent
RUTA_ENV_PREDETERMINADA = RUTA_BACKEND / ".env"

TIMEOUT_HTTP = 60.0
PAGINA = 1000

REGEX_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def leer_env(ruta: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def es_vacio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def enmascarar_email(email: str) -> str:
    email = email.strip()
    if "@" not in email:
        return (email[:2] + "***") if email else "(vacio)"
    local, dominio = email.rsplit("@", 1)
    prefijo = local[:2] if len(local) >= 3 else local[:1]
    return f"{prefijo}***@{dominio}"


def select_todos(cliente: httpx.Client, base: str, headers: dict[str, str], tabla: str, columns: str) -> list[dict[str, Any]]:
    filas: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {"select": columns, "order": "id.asc", "limit": str(PAGINA), "offset": str(offset)}
        r = cliente.get(base + tabla, headers=headers, params=params, timeout=TIMEOUT_HTTP)
        if r.status_code != 200:
            raise RuntimeError(f"GET {tabla} HTTP {r.status_code}: {r.text[:200]}")
        lote = r.json()
        filas.extend(lote)
        if len(lote) < PAGINA:
            return filas
        offset += PAGINA


def main() -> int:
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="Auditoria de calidad de datos del CRM Mapache (solo lectura).")
    parser.add_argument("--env", default=str(RUTA_ENV_PREDETERMINADA))
    args = parser.parse_args()

    ruta_env = Path(args.env)
    if not ruta_env.exists():
        print(f"ERROR: no existe {ruta_env}")
        return 1
    env = leer_env(ruta_env)
    base = env["SUPABASE_URL"].rstrip("/") + "/rest/v1/"
    headers = {
        "apikey": env["SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": "Bearer " + env["SUPABASE_SERVICE_ROLE_KEY"],
    }

    print("=" * 70)
    print("AUDITORIA DE CALIDAD DE DATOS — CRM Mapache (solo lectura)")
    print("=" * 70)

    with httpx.Client(timeout=TIMEOUT_HTTP) as cliente:
        companies = select_todos(cliente, base, headers, "companies", "id,name,phone,website,email,city")
        contacts = select_todos(cliente, base, headers, "contacts", "id,company_id,email")
        leads = select_todos(cliente, base, headers, "leads", "id,company_id,status")

    # ---- 1. companies no contactables -------------------------------------
    no_contactables = [
        c for c in companies
        if es_vacio(c.get("phone")) and es_vacio(c.get("website")) and es_vacio(c.get("email"))
    ]
    # ---- 2. companies con city NULL/vacia
    sin_ciudad = [c for c in companies if es_vacio(c.get("city"))]
    # ---- 3. contacts con email invalido
    invalidos = [
        c for c in contacts
        if not es_vacio(c.get("email")) and not REGEX_EMAIL.match(str(c["email"]).strip())
    ]
    sin_email_contact = [c for c in contacts if es_vacio(c.get("email"))]
    # ---- 4. leads con company inexistente
    ids_companies = {c["id"] for c in companies}
    leads_fk_rota = [l for l in leads if l.get("company_id") and l["company_id"] not in ids_companies]
    leads_sin_company = [l for l in leads if es_vacio(l.get("company_id"))]

    print()
    print(f"1. companies NO contactables (sin phone+website+email) : {len(no_contactables)} de {len(companies)}")
    print(f"2. companies con city NULL/vacia                       : {len(sin_ciudad)} de {len(companies)}")
    print(f"3. contacts con email INVALIDO (regex basica)          : {len(invalidos)} de {len(contacts)}")
    print(f"   (ademas: contacts sin email: {len(sin_email_contact)})")
    for c in invalidos[:20]:
        print(f"     contact={c['id'][:8]} email={enmascarar_email(str(c['email']))}")
    if len(invalidos) > 20:
        print(f"     ... y {len(invalidos) - 20} mas")
    print(f"4. leads con company_id INEXISTENTE (FK rota)          : {len(leads_fk_rota)} de {len(leads)}")
    for l in leads_fk_rota[:20]:
        print(f"     lead={l['id'][:8]} company_id={str(l.get('company_id'))[:8]}...")
    print(f"   (ademas: leads sin company_id: {len(leads_sin_company)})")
    print()
    print("Auditoria terminada (no se modifico nada).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
