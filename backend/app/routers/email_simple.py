"""Endpoint de envío de emails con Resend (serverless-compatible).

Este módulo expone un endpoint simple para enviar emails usando Resend
directamente, sin depender de SQLAlchemy ni del EmailService completo.
Funciona en Vercel serverless.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["email-simple"])


@router.post("/email-simple/send")
async def send_email_simple(
    to: str,
    subject: str,
    text: str | None = None,
    html: str | None = None,
    from_name: str = "Veyra Soluciones",
    from_email: str = "hola@veyrasoluciones.com",
) -> dict[str, Any]:
    """Envía un email usando Resend API directamente.
    
    Args:
        to: Email del destinatario
        subject: Asunto del email
        text: Cuerpo en texto plano
        html: Cuerpo en HTML (opcional)
        from_name: Nombre del remitente
        from_email: Email del remitente (debe estar verificado en Resend)
    """
    settings = get_settings()
    
    if not settings.resend_api_key_value:
        raise HTTPException(
            status_code=503,
            detail="Resend API key no configurada"
        )
    
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key_value}",
        "Content-Type": "application/json",
    }
    
    data: dict[str, Any] = {
        "from": f"{from_name} <{from_email}>",
        "to": to,
        "subject": subject,
    }
    
    if html:
        data["html"] = html
    elif text:
        data["text"] = text
    else:
        raise HTTPException(
            status_code=400,
            detail="Debe proporcionar text o html"
        )
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=data)
            
            if resp.status_code == 200:
                result = resp.json()
                logger.info("email_sent", to=to, message_id=result.get("id"))
                return {
                    "success": True,
                    "message_id": result.get("id"),
                    "to": to,
                }
            else:
                logger.error("email_send_failed", status=resp.status_code, detail=resp.text)
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Error de Resend: {resp.text}"
                )
    except httpx.HTTPError as exc:
        logger.error("email_http_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/email-simple/health")
async def email_simple_health() -> dict[str, str]:
    """Verifica que Resend esté configurado."""
    settings = get_settings()
    if settings.resend_api_key_value:
        return {"status": "ok", "provider": "resend"}
    return {"status": "error", "detail": "Resend API key no configurada"}
