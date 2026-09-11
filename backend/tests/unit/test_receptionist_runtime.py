"""Tests del runtime de la Recepcionista IA y de la medicion (Fase G)."""

from __future__ import annotations

import asyncio

from app.services import receptionist_svc
from app.services.results_svc import calcular_mejora, resumen_mejora

CONFIG = {
    "id": "11111111-1111-1111-1111-111111111111",
    "client_name": "Clinica Sonrisas",
    "company": "Clinica Sonrisas",
    "services": [{"nombre": "Ortodoncia"}],
    "price_policy": "La valoracion inicial es gratuita.",
    "hours": "lunes a viernes de 8 a.m. a 6 p.m.",
    "faqs": [{"pregunta": "Aceptan tarjetas?", "respuesta": "Si, aceptamos tarjetas."}],
    "escalation_phone": "+573028460200",
}


def test_procesar_entrante_registra_entrada_y_respuesta(monkeypatch):
    registros = []

    async def fake_log(rid, direction, contact, message, escalated=False, motivo=None):
        registros.append({"direction": direction, "message": message, "motivo": motivo, "escalated": escalated})
        return {}

    monkeypatch.setattr(receptionist_svc, "registrar_log", fake_log)
    r = asyncio.run(receptionist_svc.procesar_entrante(CONFIG, "cuanto cuesta?", contacto="+573001112233"))

    assert r["motivo"] == "precio"
    assert r["enviado"] is False  # modo seguro por defecto
    assert [x["direction"] for x in registros] == ["in", "out"]
    assert "valoracion inicial es gratuita" in registros[1]["message"]


def test_procesar_entrante_escala_sin_enviar_en_modo_seguro(monkeypatch):
    async def fake_log(*args, **kwargs):
        return {}

    monkeypatch.setattr(receptionist_svc, "registrar_log", fake_log)
    r = asyncio.run(receptionist_svc.procesar_entrante(CONFIG, "tengo una queja"))
    assert r["escalate"] is True
    assert r["enviado"] is False


def test_calcular_mejora_interpreta_direccion_de_cada_metrica():
    baseline = {"tiempo_respuesta_min": 240, "leads": 40, "cierre_pct": 18}
    current = {"tiempo_respuesta_min": 15, "leads": 90, "cierre_pct": 12}
    filas = {f["metrica"]: f for f in calcular_mejora(baseline, current)}

    # bajar el tiempo de respuesta es mejorar
    assert filas["tiempo_respuesta_min"]["mejora"] is True
    assert filas["tiempo_respuesta_min"]["cambio_pct"] == -93.8
    # subir leads es mejorar
    assert filas["leads"]["mejora"] is True
    # bajar el cierre NO es mejorar
    assert filas["cierre_pct"]["mejora"] is False


def test_resumen_mejora_cuenta_y_texto():
    base = {"tiempo_respuesta_min": 100, "leads": 10}
    ahora = {"tiempo_respuesta_min": 50, "leads": 20}
    r = resumen_mejora(base, ahora)
    assert r["comparables"] == 2
    assert r["mejoraron"] == 2
    assert "2 de 2" in r["texto"]


def test_resumen_mejora_sin_datos_no_inventa():
    r = resumen_mejora({}, {})
    assert r["comparables"] == 0
    assert "Sin metricas" in r["texto"]


def test_transporte_log_no_envia_aunque_se_pida(monkeypatch):
    async def fake_log(*args, **kwargs):
        return {}

    monkeypatch.setattr(receptionist_svc, "registrar_log", fake_log)
    cfg = {**CONFIG, "transport": "log"}
    r = asyncio.run(receptionist_svc.procesar_entrante(cfg, "hola", contacto="+573001112233", enviar=True))
    assert r["enviado"] is False  # transporte log: solo registra


def test_transporte_hermes_envia_y_avisa_al_escalar(monkeypatch):
    async def fake_log(*args, **kwargs):
        return {}

    enviados = []

    def fake_send(destino, texto):
        enviados.append((destino, texto[:40]))
        return True

    monkeypatch.setattr(receptionist_svc, "registrar_log", fake_log)
    monkeypatch.setattr(receptionist_svc, "_enviar_hermes", fake_send)

    cfg = {**CONFIG, "transport": "hermes"}
    r = asyncio.run(receptionist_svc.procesar_entrante(cfg, "tengo una queja", contacto="+573001112233", enviar=True))
    assert r["escalate"] is True
    assert r["enviado"] is True
    # se envia la respuesta al cliente y el aviso al humano
    destinos = [d for d, _ in enviados]
    assert "+573001112233" in destinos
    assert cfg["escalation_phone"] in destinos
