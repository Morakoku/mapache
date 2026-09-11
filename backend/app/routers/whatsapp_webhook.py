"""Webhook de WhatsApp Cloud API (oficial de Meta).

Con este transporte la Recepcionista IA atiende el numero del CLIENTE sin
depender de ninguna sesion local: Meta entrega los mensajes aqui y respondemos
con la API oficial usando su `phone_number_id` y su token.

Configuracion (variables de entorno del backend):
  WHATSAPP_VERIFY_TOKEN  -> cadena que se configura en Meta para verificar el webhook.
  <ACCESS_TOKEN_ENV>     -> el token del cliente, guardado por NOMBRE en la
                            columna `access_token_env` de `wa_receptionists`.

En Meta: Webhooks -> Callback URL = https://<host>/webhook/whatsapp,
Verify token = WHATSAPP_VERIFY_TOKEN, y suscribir el campo `messages`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.core.logging import get_logger
from app.services import receptionist_svc

logger = get_logger(__name__)
router = APIRouter()


def _firma_valida(raw: bytes, header: str | None) -> bool | None:
    """Valida la firma de Meta (X-Hub-Signature-256).

    Devuelve True/False cuando `WHATSAPP_APP_SECRET` esta configurado; None si
    no lo esta (no se puede validar: se deja pasar, avisando en el log).
    """
    secret = (os.environ.get("WHATSAPP_APP_SECRET") or "").strip()
    if not secret:
        return None
    if not header or not header.startswith("sha256="):
        return False
    esperado = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, header.split("=", 1)[1])


@router.get("/whatsapp")
async def verificar_webhook(request: Request) -> Response:
    """Verificacion del webhook que hace Meta una sola vez al configurarlo."""
    params = request.query_params
    modo = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")

    esperado = (os.environ.get("WHATSAPP_VERIFY_TOKEN") or "").strip()
    if modo == "subscribe" and esperado and token == esperado:
        return PlainTextResponse(challenge)
    logger.warning("whatsapp_webhook_verify_failed", mode=modo)
    return PlainTextResponse("forbidden", status_code=403)


def _extraer_mensajes(body: dict) -> list[dict]:
    """Aplana el payload de Meta a una lista de {phone_number_id, from, text}."""
    salida: list[dict] = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            pid = metadata.get("phone_number_id")
            for msg in value.get("messages") or []:
                texto = ""
                if msg.get("type") == "text":
                    texto = (msg.get("text") or {}).get("body") or ""
                elif msg.get("type") == "button":
                    texto = (msg.get("button") or {}).get("text") or ""
                elif msg.get("type") == "interactive":
                    interactive = msg.get("interactive") or {}
                    texto = (
                        (interactive.get("button_reply") or {}).get("title")
                        or (interactive.get("list_reply") or {}).get("title")
                        or ""
                    )
                if pid and msg.get("from") and texto:
                    salida.append({"phone_number_id": pid, "from": msg["from"], "text": texto})
    return salida


@router.post("/whatsapp")
async def recibir_webhook(request: Request) -> JSONResponse:
    """Recibe mensajes entrantes y los atiende con la Recepcionista del cliente."""
    raw = await request.body()
    firma = _firma_valida(raw, request.headers.get("x-hub-signature-256"))
    if firma is False:
        logger.warning("whatsapp_webhook_firma_invalida")
        return JSONResponse({"status": "forbidden"}, status_code=403)
    if firma is None:
        logger.warning("whatsapp_webhook_sin_app_secret",
                       nota="configura WHATSAPP_APP_SECRET para validar la firma de Meta")
    try:
        body = json.loads(raw or b"{}")
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "ignored"}, status_code=200)

    mensajes = _extraer_mensajes(body if isinstance(body, dict) else {})
    for msg in mensajes:
        try:
            config = await receptionist_svc.receptionista_por_phone_number_id(msg["phone_number_id"])
            if not config:
                logger.warning("whatsapp_webhook_sin_recepcionista", pid=msg["phone_number_id"])
                continue
            await receptionist_svc.procesar_cloud(config, msg["text"], msg["from"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp_webhook_error", error=str(exc)[:200])

    # Meta exige 200 rapido; cualquier error se registra y se responde OK.
    return JSONResponse({"status": "ok"}, status_code=200)
