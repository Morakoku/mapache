"""Marca los contacts de leads de prueba (example.com) como do_not_contact.

Los leads 'System Verify' y 'Empresa Real' son artefactos de test del intake
que Resend rechaza (422) — ensucian los contadores del warm-up y no deben
reintentarse jamás.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

_env = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env)


async def main() -> None:
    import httpx

    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{url}/rest/v1/contacts?select=id,email,do_not_contact&email=like.*@example.com",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        rows = r.json()
        print(f"contacts de prueba (example.com): {len(rows)}")
        ids = [row["id"] for row in rows if not row.get("do_not_contact")]
        if not ids:
            print("nada que marcar (ya estan excluidos)")
            return
        r2 = await c.patch(
            f"{url}/rest/v1/contacts?id=in.({','.join(ids)})",
            headers=headers,
            json={"do_not_contact": True},
        )
        print(f"patch do_not_contact=true: HTTP {r2.status_code} -> {len(r2.json())} filas")


asyncio.run(main())
