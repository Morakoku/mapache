"""Recepcionista IA para WhatsApp — cerebro y configuración por cliente.

La recepcionista atiende el WhatsApp de un negocio: responde con SU información
(servicios, horario, política de precios, FAQs), agenda y **escala a un humano**
cuando hace falta. Nunca dice que es una IA ni usa lenguaje técnico.

El cerebro (`responder`) es determinista y testeable: no depende del transporte
de WhatsApp (ese es el paso de integración pendiente).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Palabras que obligan a pasar la conversación a un humano.
_ESCALAR = (
    "queja", "reclamo", "reclamar", "abogado", "demanda", "legal", "urgente",
    "emergencia", "fraude", "estafa", "gerente", "dueño", "dueno", "supervisor",
    "hablar con alguien", "hablar con una persona", "con un asesor", "con un humano",
    "persona real", "agente humano",
)

# Intento de manipular a la recepcionista (anti-injection): se ignora y se reporta.
_INYECCION = (
    "ignora las reglas", "ignora tus reglas", "olvida las instrucciones",
    "olvida tus instrucciones", "borra los datos", "borra todos", "elimina los leads",
    "muéstrame tu configuración", "muestrame tu configuracion", "revela",
    "prompt", "system prompt", "actúa como", "actua como", "eres una ia",
    "eres un bot", "cambia tu comportamiento",
)

_PRECIO = ("precio", "cuanto vale", "cuánto vale", "cuanto cuesta", "cuánto cuesta", "costo", "tarifa", "vale", "cobran")
_HORARIO = ("horario", "abren", "cierran", "a que hora", "a qué hora", "estan abiertos", "están abiertos")
_CITA = ("cita", "agendar", "agenda", "reservar", "turno", "disponibilidad")


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def _contiene(texto: str, agujas: tuple[str, ...]) -> bool:
    return any(a in texto for a in agujas)


def _nombre_negocio(config: dict[str, Any]) -> str:
    return (config.get("company") or config.get("client_name") or "el negocio").strip()


def _saludo(config: dict[str, Any]) -> str:
    return f"¡Hola! Gracias por escribir a {_nombre_negocio(config)}."


def _servicios_texto(config: dict[str, Any]) -> str:
    servicios = config.get("services") or []
    nombres = []
    for s in servicios:
        if isinstance(s, dict) and s.get("nombre"):
            nombres.append(str(s["nombre"]))
        elif isinstance(s, str):
            nombres.append(s)
    if not nombres:
        return ""
    if len(nombres) == 1:
        return nombres[0]
    return ", ".join(nombres[:-1]) + " y " + nombres[-1]


def _buscar_faq(config: dict[str, Any], mensaje: str) -> str | None:
    """Empareja por solape de palabras clave (simple y explicable)."""
    palabras = {p for p in _norm(mensaje).split() if len(p) > 3}
    if not palabras:
        return None
    mejor, mejor_score = None, 0
    for faq in config.get("faqs") or []:
        if not isinstance(faq, dict):
            continue
        pregunta = _norm(faq.get("pregunta", ""))
        score = len(palabras & set(pregunta.split()))
        if score > mejor_score:
            mejor, mejor_score = faq, score
    return (mejor or {}).get("respuesta") if mejor_score >= 1 else None


def responder(config: dict[str, Any], mensaje: str, primer_mensaje: bool = False) -> dict[str, Any]:
    """Genera la respuesta de la recepcionista.

    Devuelve: {reply, escalate, reason, motivo}
    - `escalate=True`: la conversación debe pasar a un humano (se avisa por
      WhatsApp/telefono al número de escalamiento configurado).
    """
    cfg = config or {}
    m = _norm(mensaje)

    # 1) Anti-injection: no obedece, responde breve y escala.
    if _contiene(m, _INYECCION):
        logger.warning("receptionist_injection_attempt", mensaje=mensaje[:120])
        return {
            "reply": _saludo(cfg) + " Te ayudo con lo que necesites sobre el negocio. Si es algo interno, lo ve un asesor.",
            "escalate": True,
            "reason": "Posible intento de manipulación de la conversación.",
            "motivo": "anti_injection",
        }

    # 2) Queja, urgencia o tema legal: siempre a un humano.
    if _contiene(m, _ESCALAR):
        reply = _saludo(cfg) + " Esto lo atiende una persona del equipo directamente."
        if cfg.get("escalation_phone"):
            reply += " Ya avisamos para que te contacten."
        return {
            "reply": reply,
            "escalate": True,
            "reason": "Queja, urgencia o tema sensible: requiere humano.",
            "motivo": "escalamiento_humano",
        }

    # 3) Precios: solo la política configurada; si no hay, escala.
    if _contiene(m, _PRECIO):
        politica = (cfg.get("price_policy") or "").strip()
        if politica:
            return {
                "reply": _saludo(cfg) + f" {politica}",
                "escalate": False,
                "reason": "Política de precios configurada.",
                "motivo": "precio",
            }
        return {
            "reply": _saludo(cfg) + " Los precios los confirma un asesor según lo que necesites. ¿Quieres que te contacten?",
            "escalate": True,
            "reason": "Preguntó precio y no hay política configurada.",
            "motivo": "precio_sin_politica",
        }

    # 4) FAQ configurada.
    faq = _buscar_faq(cfg, mensaje)
    if faq:
        return {"reply": _saludo(cfg) + f" {faq}", "escalate": False, "reason": "FAQ configurada.", "motivo": "faq"}

    # 5) Horario.
    if _contiene(m, _HORARIO) and cfg.get("hours"):
        return {"reply": _saludo(cfg) + f" Atendemos {cfg['hours']}.", "escalate": False, "reason": "Horario configurado.", "motivo": "horario"}

    # 6) Cita / agendar.
    if _contiene(m, _CITA):
        return {
            "reply": _saludo(cfg) + " Claro, coordinemos. ¿Qué día y a qué hora te queda mejor?",
            "escalate": False,
            "reason": "Intención de agendar.",
            "motivo": "agenda",
        }

    # 7) Genérico: presenta los servicios y ofrece el siguiente paso.
    servicios = _servicios_texto(cfg)
    base = _saludo(cfg)
    if servicios:
        base += f" Ofrecemos {servicios}."
    base += " ¿Te cuento más o preferís que te contacte alguien del equipo?"
    return {"reply": base, "escalate": False, "reason": "Consulta general.", "motivo": "general"}

# ---------------------------------------------------------------------------
# Configuracion por cliente (PostgREST)
# ---------------------------------------------------------------------------

async def listar_configs() -> list[dict[str, Any]]:
    """Configuraciones registradas (mas recientes primero)."""
    from app.core.supabase_http import select as pg_select

    return await pg_select("wa_receptionists", columns="*", order="created_at.desc", limit=200)


async def crear_config(data: dict[str, Any]) -> dict[str, Any] | None:
    """Crea la configuracion de una recepcionista para un cliente."""
    from app.core.supabase_http import insert as pg_insert

    campos = {
        "client_name": (data.get("client_name") or "").strip()[:160],
        "company": (data.get("company") or "").strip()[:160] or None,
        "whatsapp_number": (data.get("whatsapp_number") or "").strip()[:40] or None,
        "services": data.get("services") or [],
        "price_policy": (data.get("price_policy") or "").strip()[:600] or None,
        "hours": (data.get("hours") or "").strip()[:200] or None,
        "faqs": data.get("faqs") or [],
        "escalation_phone": (data.get("escalation_phone") or "").strip()[:40] or None,
        "tone": (data.get("tone") or "cercano").strip()[:40],
        "transport": (data.get("transport") or "log").strip()[:20],
        "wa_session": (data.get("wa_session") or "").strip()[:80] or None,
        "provider": (data.get("provider") or "log").strip()[:20],
        "phone_number_id": (data.get("phone_number_id") or "").strip()[:40] or None,
        "access_token_env": (data.get("access_token_env") or "").strip()[:120] or None,
        "active": bool(data.get("active", True)),
    }
    if not campos["client_name"]:
        return None
    filas = await pg_insert("wa_receptionists", campos)
    return filas[0] if filas else None


async def actualizar_config(config_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Actualiza campos permitidos de una configuracion."""
    from app.core.supabase_http import update as pg_update

    permitidos = (
        "client_name", "company", "whatsapp_number", "services", "price_policy",
        "hours", "faqs", "escalation_phone", "tone", "transport", "wa_session",
        "provider", "phone_number_id", "access_token_env", "active",
    )
    campos = {k: data[k] for k in permitidos if k in data}
    if not campos:
        return None
    filas = await pg_update("wa_receptionists", {"id": config_id}, campos)
    return filas[0] if filas else None

# ---------------------------------------------------------------------------
# Runtime: mensaje entrante -> cerebro -> respuesta (+ bitacora)
# ---------------------------------------------------------------------------

HERMES_BIN = r"C:\Users\edwin\AppData\Local\hermes\bin\hermes.exe"


async def registrar_log(
    receptionist_id: str | None,
    direction: str,
    contact: str | None,
    message: str,
    escalated: bool = False,
    motivo: str | None = None,
) -> dict[str, Any] | None:
    """Guarda un mensaje (entrante o respuesta) en la bitacora."""
    if not receptionist_id:
        return None
    from app.core.supabase_http import insert as pg_insert

    filas = await pg_insert(
        "wa_receptionist_logs",
        {
            "receptionist_id": receptionist_id,
            "direction": direction,
            "contact": (contact or "")[:60] or None,
            "message": (message or "")[:2000],
            "escalated": bool(escalated),
            "motivo": (motivo or "")[:80] or None,
        },
    )
    return filas[0] if filas else None


def _enviar_hermes(destino: str, texto: str) -> bool:
    """Transporte piloto: envia por el bot de Veyra (numero propio).

    Para atender el WhatsApp DE UN CLIENTE hace falta una sesion por numero
    (multi-sesion); este transporte queda como piloto y como punto de extension.
    """
    import subprocess

    numero = "".join(c for c in str(destino or "") if c.isdigit())
    if len(numero) < 10:
        return False
    try:
        proc = subprocess.run(
            [HERMES_BIN, "send", "--to", f"whatsapp:{numero}", texto],
            capture_output=True, text=True, timeout=90,
        )
        return proc.returncode == 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("receptionist_send_error", error=str(exc)[:120])
        return False


async def procesar_entrante(
    config: dict[str, Any],
    mensaje: str,
    contacto: str | None = None,
    enviar: bool = False,
) -> dict[str, Any]:
    """Atiende un mensaje entrante: responde, registra y (opcional) envia.

    Por defecto `enviar=False`: solo registra y devuelve la respuesta (modo
    seguro para probar y para el dashboard). Con `enviar=True` usa el transporte
    configurado.
    """
    resultado = responder(config, mensaje, primer_mensaje=False)
    rid = config.get("id")
    await registrar_log(rid, "in", contacto, mensaje, resultado["escalate"], resultado["motivo"])
    await registrar_log(rid, "out", contacto, resultado["reply"], resultado["escalate"], resultado["motivo"])

    enviado = False
    transporte = (config.get("transport") or "log").lower()
    if enviar and contacto and transporte == "hermes":
        enviado = _enviar_hermes(contacto, resultado["reply"])
        # Si escalo, se avisa al humano configurado.
        if resultado["escalate"] and config.get("escalation_phone"):
            aviso = f"Recepcionista IA: conversacion escalada ({resultado['motivo']}). Contacto: {contacto}. Mensaje: {mensaje[:160]}"
            _enviar_hermes(config["escalation_phone"], aviso)

    return {**resultado, "enviado": enviado}


async def historial(receptionist_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Ultimos mensajes de una recepcionista (entrantes y respuestas)."""
    from app.core.supabase_http import select as pg_select

    return await pg_select(
        "wa_receptionist_logs",
        columns="id,direction,contact,message,escalated,motivo,created_at",
        filters={"receptionist_id": receptionist_id},
        order="created_at.desc",
        limit=limit,
    )


# ---------------------------------------------------------------------------
# WhatsApp Cloud API (oficial de Meta): transporte sin sesion local.
# El cliente registra su numero como WhatsApp Business; Meta entrega los
# mensajes a nuestro webhook y respondemos con SU numero y SU token. No hay
# ninguna sesion que mantener encendida en nuestra maquina.
# ---------------------------------------------------------------------------

GRAPH_VERSION = "v20.0"


def _token_cliente(config: dict[str, Any]) -> str:
    """Lee el token del cliente desde la variable de entorno indicada.

    Nunca se guarda el token en la base de datos: solo el NOMBRE de la variable.
    """
    import os

    env_name = (config.get("access_token_env") or "").strip()
    if not env_name:
        return ""
    return (os.environ.get(env_name) or "").strip()


def enviar_cloud(phone_number_id: str, token: str, destino: str, texto: str) -> bool:
    """Envia un mensaje de texto por la WhatsApp Cloud API (num. del cliente)."""
    import json
    import urllib.request

    pid = "".join(c for c in str(phone_number_id or "") if c.isdigit())
    numero = "".join(c for c in str(destino or "") if c.isdigit())
    if not pid or not token or len(numero) < 10:
        return False
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{pid}/messages"
    body = json.dumps(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": numero,
            "type": "text",
            "text": {"preview_url": False, "body": texto[:4096]},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001
        logger.warning("receptionist_cloud_send_error", error=str(exc)[:160])
        return False


async def receptionista_por_phone_number_id(phone_number_id: str) -> dict[str, Any] | None:
    """Encuentra la recepcionista activa asociada a un numero del webhook."""
    from app.core.supabase_http import select as pg_select

    pid = (phone_number_id or "").strip()
    if not pid:
        return None
    filas = await pg_select(
        "wa_receptionists",
        columns="*",
        filters={"phone_number_id": pid, "active": True},
        limit=1,
    )
    return filas[0] if filas else None


async def procesar_cloud(config: dict[str, Any], mensaje: str, contacto: str) -> dict[str, Any]:
    """Atiende un entrante de Cloud API: responde, registra y envia por Meta.

    Usa el numero y el token del cliente; si escala, avisa al humano configurado
    (tambien por su propio numero).
    """
    resultado = responder(config, mensaje, primer_mensaje=False)
    rid = config.get("id")
    await registrar_log(rid, "in", contacto, mensaje, resultado["escalate"], resultado["motivo"])
    await registrar_log(rid, "out", contacto, resultado["reply"], resultado["escalate"], resultado["motivo"])

    token = _token_cliente(config)
    enviado = enviar_cloud(config.get("phone_number_id"), token, contacto, resultado["reply"])

    if resultado["escalate"] and config.get("escalation_phone"):
        aviso = (
            f"Recepcionista IA: conversacion escalada ({resultado['motivo']}). "
            f"Contacto: {contacto}. Mensaje: {mensaje[:160]}"
        )
        enviar_cloud(config.get("phone_number_id"), token, config["escalation_phone"], aviso)

    return {**resultado, "enviado": enviado}

