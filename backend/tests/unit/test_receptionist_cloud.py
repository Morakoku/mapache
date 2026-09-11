"""Tests del transporte WhatsApp Cloud API y del soporte multi-pais (57/58)."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from app.routers import whatsapp_webhook
from app.services import receptionist_svc

# Carga el puente (script) para probar la normalizacion de telefonos.
_BRIDGE = Path(__file__).resolve().parents[2] / "scripts" / "veyra_chequeo_bridge.py"
_spec = importlib.util.spec_from_file_location("veyra_chequeo_bridge", _BRIDGE)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

CONFIG = {
    "id": "11111111-1111-1111-1111-111111111111",
    "client_name": "Clinica Sonrisas",
    "services": [{"nombre": "Ortodoncia"}],
    "price_policy": "La valoracion inicial es gratuita.",
    "hours": "lunes a viernes de 8 a.m. a 6 p.m.",
    "faqs": [{"pregunta": "Aceptan tarjetas?", "respuesta": "Si, aceptamos tarjetas."}],
    "escalation_phone": "+573028460200",
    "phone_number_id": "1234567890",
    "access_token_env": "WA_TOKEN_CLIENTE_TEST",
}


# --- Multi-pais: 57 Colombia / 58 Venezuela -------------------------------

def test_telefono_colombiano_agrega_57():
    assert bridge.valid_phone("3028460200") == "573028460200"
    assert bridge.valid_phone("+57 302 846 0200") == "573028460200"


def test_telefono_venezolano_agrega_58():
    assert bridge.valid_phone("04141234567") == "584141234567"
    assert bridge.valid_phone("+58 414 123 4567") == "584141234567"


def test_telefono_con_indicativo_se_respeta():
    assert bridge.valid_phone("573028460200") == "573028460200"
    assert bridge.valid_phone("584141234567") == "584141234567"


def test_telefono_invalido_se_rechaza():
    assert bridge.valid_phone("123") == ""
    assert bridge.valid_phone("") == ""


def test_telefono_placeholder_de_formulario_se_rechaza():
    assert bridge.valid_phone("+57 300 000 0000") == ""
    assert bridge.valid_phone("+57 000 000 0000") == ""


# --- Mensajes del puente (chequeo, diagnostico, MRI, scorecard) -----------

def test_mensaje_business_mri():
    row = {"name": "Ana Restrepo", "status": "pending_review", "company_name": "Clinica Sonrisas", "payload": {}}
    msg = bridge.build_message(row)
    assert "Business MRI" in msg
    assert "Clinica Sonrisas" in msg


def test_mensaje_scorecard():
    row = {"name": "Ana", "status": "scorecard", "payload": {"scorecard": {"overall": 62, "tier": "sweet"}}}
    msg = bridge.build_message(row)
    assert "62/100" in msg


def test_mensaje_diagnostico():
    row = {"name": "Edwin", "status": "diagnostico",
           "payload": {"diagnostico": {"analysis": {"madurez": 53, "nivel": "EN CONSTRUCCIÓN",
                      "oportunidades": [{"title": "Seguimiento comercial"}]}}}}
    msg = bridge.build_message(row)
    assert "53/100" in msg and "Seguimiento comercial" in msg


# --- Firma del webhook (Meta) --------------------------------------------

def test_firma_sin_secreto_no_valida(monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    assert whatsapp_webhook._firma_valida(b"{}", None) is None


def test_firma_valida_e_invalida(monkeypatch):
    import hashlib
    import hmac
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secreto-meta")
    raw = b'{"entry":[]}'
    good = "sha256=" + hmac.new(b"secreto-meta", raw, hashlib.sha256).hexdigest()
    assert whatsapp_webhook._firma_valida(raw, good) is True
    assert whatsapp_webhook._firma_valida(raw, "sha256=deadbeef") is False
    assert whatsapp_webhook._firma_valida(raw, None) is False


# --- Payload de Meta ------------------------------------------------------
def test_extraer_mensajes_de_payload_meta():
    body = {
        "entry": [{
            "changes": [{
                "value": {
                    "metadata": {"phone_number_id": "1234567890"},
                    "messages": [{"from": "584141234567", "type": "text", "text": {"body": "hola"}}],
                }
            }]
        }]
    }
    msgs = whatsapp_webhook._extraer_mensajes(body)
    assert msgs == [{"phone_number_id": "1234567890", "from": "584141234567", "text": "hola"}]


def test_extraer_mensajes_ignora_no_texto():
    body = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "1"},
        "messages": [{"from": "57", "type": "image", "image": {"id": "x"}}],
    }}]}]}
    assert whatsapp_webhook._extraer_mensajes(body) == []


# --- Envio por Cloud API --------------------------------------------------

def test_enviar_cloud_sin_token_no_hace_llamada():
    assert receptionist_svc.enviar_cloud("123", "", "573028460200", "hola") is False


def test_token_cliente_lee_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("WA_TOKEN_CLIENTE_TEST", "  token-secreto  ")
    assert receptionist_svc._token_cliente(CONFIG) == "token-secreto"


def test_procesar_cloud_responde_loguea_y_avisa_al_escalar(monkeypatch):
    registros = []
    enviados = []

    async def fake_log(rid, direction, contact, message, escalated=False, motivo=None):
        registros.append(direction)
        return {}

    def fake_cloud(pid, token, destino, texto):
        enviados.append((destino, texto[:30]))
        return True

    monkeypatch.setattr(receptionist_svc, "registrar_log", fake_log)
    monkeypatch.setattr(receptionist_svc, "enviar_cloud", fake_cloud)

    r = asyncio.run(receptionist_svc.procesar_cloud(CONFIG, "tengo una queja", "584141234567"))

    assert r["escalate"] is True
    assert r["enviado"] is True
    assert registros == ["in", "out"]
    destinos = [d for d, _ in enviados]
    assert "584141234567" in destinos          # respuesta al cliente
    assert CONFIG["escalation_phone"] in destinos  # aviso al humano
