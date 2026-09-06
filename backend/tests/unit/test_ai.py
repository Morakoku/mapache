"""Fase 8 — clasificación por reglas y contrato del cliente de IA.

El motor de reglas es lo que corre en una instalación sin `ANTHROPIC_API_KEY`,
así que se prueba como código de producción, no como plan B. El cliente se
prueba con un doble: llamar a Anthropic de verdad en los tests costaría dinero
y ataría la suite a la red.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import pytest

from app.ai import classifier, client, prompts
from app.core.enums import AIProvider, ReplyIntent, StageType

# ------------------------------------------------------------------ reglas


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Sí, me interesa. Cuéntame más.", ReplyIntent.POSITIVE),
        ("¿Cuánto cuesta el servicio?", ReplyIntent.PRICING),
        ("Envíame una cotización por favor", ReplyIntent.PRICING),
        ("¿Cuánto costaría el servicio?", ReplyIntent.PRICING),
        ("Agendemos una llamada el jueves", ReplyIntent.MEETING_REQUEST),
        ("Gracias por escribir, lo reviso y te cuento", ReplyIntent.NEUTRAL),
        ("No nos interesa por ahora, gracias", ReplyIntent.NEGATIVE),
        ("Por favor no me escriban más", ReplyIntent.UNSUBSCRIBE),
        ("Estoy fuera de la oficina hasta el 5 de agosto", ReplyIntent.OUT_OF_OFFICE),
        ("Yo no soy la persona que ve eso, escríbele a Camila", ReplyIntent.WRONG_PERSON),
    ],
)
def test_reglas_clasifican_lo_evidente(texto: str, esperado: ReplyIntent) -> None:
    assert classifier.classify_with_rules("Re: propuesta", texto).intent is esperado


def test_las_tildes_no_cambian_la_clasificacion() -> None:
    """Media Colombia escribe sin tildes en el correo."""
    con = classifier.classify_with_rules("", "¿Cuánto cuesta la cotización?")
    sin = classifier.classify_with_rules("", "Cuanto cuesta la cotizacion?")
    assert con.intent is sin.intent is ReplyIntent.PRICING


def test_baja_gana_a_negativa() -> None:
    """«No me interesa, no me escriban más» es una baja, no un no.

    La diferencia importa: la baja bloquea la dirección para siempre.
    """
    result = classifier.classify_with_rules("", "No me interesa. Por favor no me escriban más.")
    assert result.intent is ReplyIntent.UNSUBSCRIBE


def test_pregunta_sin_palabras_clave_cae_en_question_con_poca_confianza() -> None:
    result = classifier.classify_with_rules("", "¿Y esto cómo funcionaría en mi caso?")
    assert result.intent is ReplyIntent.QUESTION
    assert result.is_confident is False


def test_texto_ambiguo_es_unknown_y_no_sugiere_nada() -> None:
    """Ante la duda, no inventar. Un UNKNOWN honesto es mejor que una
    etiqueta bonita y falsa."""
    result = classifier.classify_with_rules("", "Ok.")
    assert result.intent is ReplyIntent.UNKNOWN
    assert result.suggested_stage is None
    assert result.is_confident is False


def test_cada_intencion_tiene_etapa_sugerida_decidida() -> None:
    """Ninguna intención puede quedarse sin decisión explícita: o sugiere
    etapa o declara que no sugiere ninguna."""
    for intent in ReplyIntent:
        assert intent in classifier.SUGGESTED_STAGE


def test_las_etapas_sugeridas_existen_en_el_embudo() -> None:
    for stage in classifier.SUGGESTED_STAGE.values():
        assert stage is None or isinstance(stage, StageType)


def test_deteccion_de_baja_independiente_de_la_ia() -> None:
    """La supresión es la única acción automática: no puede depender de que
    el modelo conteste."""
    assert classifier.looks_like_unsubscribe("Eliminen mis datos de su lista") is True
    assert classifier.looks_like_unsubscribe("Me interesa, cuéntame más") is False


# ------------------------------------------------------------------ cliente


class _FakeUsage:
    input_tokens = 1500
    output_tokens = 400
    cache_read_input_tokens = 1200


class _FakeBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], stop_reason: str = "end_turn") -> None:
        self.content = [_FakeBlock(json.dumps(payload))]
        self.stop_reason = stop_reason
        self.model = "claude-opus-5"
        self.usage = _FakeUsage()


class _FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.last_kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


def _patch_client(monkeypatch: pytest.MonkeyPatch, messages: _FakeMessages) -> None:
    fake = type("Fake", (), {"beta": type("Beta", (), {"messages": messages})()})()
    monkeypatch.setattr(client, "_client", lambda _api_key: fake)


# Credenciales de Claude para los tests: el camino del SDK oficial.
_CLAUDE = client.AICredentials(
    provider=AIProvider.ANTHROPIC, api_key="sk-test", model=client.DEFAULT_MODEL
)


@pytest.mark.asyncio
async def test_el_bloque_de_sistema_va_marcado_para_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Es el 90% de la entrada y es idéntico entre llamadas: sin caché, la
    factura se dispara (§12.1)."""
    messages = _FakeMessages(_FakeResponse({"intent": "POSITIVE"}))
    _patch_client(monkeypatch, messages)

    await client.complete_json(
        system="reglas", user="datos", schema={"type": "object"}, credentials=_CLAUDE
    )

    system_blocks = messages.last_kwargs["system"]
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}
    # Y lo variable NO viaja en el sistema, que rompería la caché cada vez.
    assert "datos" not in system_blocks[0]["text"]


@pytest.mark.asyncio
async def test_se_pide_salida_estructurada_no_texto_libre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _FakeMessages(_FakeResponse({"ok": True}))
    _patch_client(monkeypatch, messages)

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    await client.complete_json(system="s", user="u", schema=schema, credentials=_CLAUDE)

    output = messages.last_kwargs["output_config"]
    assert output["format"] == {"type": "json_schema", "schema": schema}


@pytest.mark.asyncio
async def test_un_rechazo_del_modelo_no_es_un_error_del_crm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, _FakeMessages(_FakeResponse({}, stop_reason="refusal")))

    with pytest.raises(client.AIUnavailableError) as exc:
        await client.complete_json(system="s", user="u", schema={}, credentials=_CLAUDE)
    assert exc.value.code == "AI_REFUSAL"


@pytest.mark.asyncio
async def test_un_fallo_del_proveedor_se_convierte_en_ai_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una caída de Anthropic no puede tumbar un envío."""
    error = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    _patch_client(monkeypatch, _FakeMessages(error=error))

    with pytest.raises(client.AIUnavailableError):
        await client.complete_json(system="s", user="u", schema={}, credentials=_CLAUDE)


@pytest.mark.asyncio
async def test_la_clasificacion_cae_a_reglas_si_la_ia_falla(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "is_configured", lambda: True)
    _patch_client(
        monkeypatch,
        _FakeMessages(error=anthropic.APIConnectionError(request=None)),  # type: ignore[arg-type]
    )

    result = await classifier.classify(
        subject="Re: idea",
        body="¿Cuánto cuesta?",
        company_name="La Finca",
    )
    assert result.intent is ReplyIntent.PRICING
    assert result.source == "rules"


@pytest.mark.asyncio
async def test_sin_clave_ni_se_intenta_llamar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "is_configured", lambda: False)

    def _explode() -> None:  # pragma: no cover - debe no llamarse
        raise AssertionError("no debería instanciarse el cliente sin clave")

    monkeypatch.setattr(client, "_client", _explode)

    result = await classifier.classify(
        subject="", body="Me interesa, cuéntame más", company_name="X"
    )
    assert result.source == "rules"
    assert result.intent is ReplyIntent.POSITIVE


def test_el_coste_estimado_descuenta_la_cache() -> None:
    caro = client.AIResult(data={}, model="claude-opus-5", input_tokens=1500, output_tokens=400)
    barato = client.AIResult(
        data={},
        model="claude-opus-5",
        input_tokens=300,
        output_tokens=400,
        cached_tokens=1200,
    )
    assert barato.estimated_cost_usd < caro.estimated_cost_usd


# ------------------------------------------------------------------ prompts


def test_el_prompt_prohibe_inventar_datos() -> None:
    """Es la regla que más importa: un detalle inventado destruye la
    credibilidad en la primera frase."""
    assert "inventar" in prompts.PERSONALIZER_SYSTEM.lower()
    assert "120" in prompts.PERSONALIZER_SYSTEM


def test_sin_senales_el_contexto_lo_dice_explicitamente() -> None:
    text = prompts.build_personalizer_input(
        company_name="La Finca",
        category=None,
        city=None,
        website=None,
        rating=None,
        reviews=None,
        signals=[],
        service_name="Web",
        value_proposition=None,
        problems_solved=[],
        contact_name=None,
        job_title=None,
        sender_name="Daniel",
        tone="tu",
    )
    assert "NO inventes" in text


def test_el_esquema_del_clasificador_cubre_todas_las_intenciones() -> None:
    valores = set(prompts.CLASSIFIER_SCHEMA["properties"]["intent"]["enum"])
    assert valores == {i.value for i in ReplyIntent}
