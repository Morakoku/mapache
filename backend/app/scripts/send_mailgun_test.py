"""Script de prueba para enviar email via Mailgun.

Uso:
    python -m app.scripts.send_mailgun_test --to mbmbrochero510@gmail.com --api-key <tu-api-key> --domain <tu-dominio>

Ejemplo:
    python -m app.scripts.send_mailgun_test --to mbmbrochero510@gmail.com --api-key key-abc123 --domain mg.tudominio.com
"""

import asyncio
import sys

import httpx


async def send_mailgun_test(to_email: str, api_key: str, domain: str):
    url = f"https://api.mailgun.net/v3/{domain}/messages"
    auth = ("api", api_key)

    data = {
        "from": f"Mapache CRM <mailgun@{domain}>",
        "to": to_email,
        "subject": "Prueba de envío - Mapache CRM",
        "text": """Hola,

Este es un correo de prueba enviado desde Mapache CRM usando Mailgun.

Si lo recibiste correctamente, el envío está funcionando.

Saludos,
Mapache CRM
""",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, auth=auth, data=data)
            if resp.status_code == 200:
                result = resp.json()
                print(f"✅ Email enviado correctamente a {to_email}")
                print(f"   ID: {result.get('id', 'N/A')}")
                print(f"   Status: {result.get('message', 'N/A')}")
                return True
            else:
                print(f"❌ Error {resp.status_code}: {resp.text}")
                return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Prueba de envío Mailgun")
    parser.add_argument("--to", default="mbmbrochero510@gmail.com", help="Email destino")
    parser.add_argument("--api-key", required=True, help="API key de Mailgun")
    parser.add_argument("--domain", required=True, help="Dominio configurado en Mailgun")

    args = parser.parse_args()
    asyncio.run(send_mailgun_test(args.to, args.api_key, args.domain))
