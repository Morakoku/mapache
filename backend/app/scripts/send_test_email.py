"""Script de prueba para enviar email via Gmail SMTP.

Uso:
    python -m app.scripts.send_test_email --to mbmbrochero510@gmail.com

Requiere:
    1. Gmail App Password (https://myaccount.google.com/apppasswords)
    2. Pasar la password como variable de entorno: GMAIL_APP_PASSWORD
"""

import asyncio
import sys

import aiosmtplib
from email.message import EmailMessage


async def send_test_email(to_email: str, app_password: str):
    subject = "Prueba Mapache CRM"
    body = """Hola,

Este es un email de prueba desde Mapache CRM.

Si lo recibiste, el envio funciona correctamente.

Saludos,
Mapache CRM
"""

    msg = EmailMessage()
    msg["From"] = "Mapache CRM <mbmbrochero510@gmail.com>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        client = aiosmtplib.SMTP(
            hostname="smtp.gmail.com",
            port=587,
            start_tls=True,
            timeout=30,
        )
        await client.connect()
        await client.login("mbmbrochero510@gmail.com", app_password)
        await client.send_message(msg)
        await client.quit()
        print(f"Email enviado correctamente a {to_email}")
        return True
    except aiosmtplib.SMTPAuthenticationError:
        print("Error: Credenciales invalidas. Usa un Gmail App Password.")
        return False
    except Exception as e:
        print(f"Error al enviar: {e}")
        return False


if __name__ == "__main__":
    to = sys.argv[1] if len(sys.argv) > 1 else "mbmbrochero510@gmail.com"
    import os
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        print("Define la variable: GMAIL_APP_PASSWORD=<tu-app-password>")
        sys.exit(1)
    asyncio.run(send_test_email(to, password))
