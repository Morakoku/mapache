"""Cliente de IA (decisión D10).

Un único punto de contacto con el SDK. Todo lo que sale de aquí es JSON válido
contra un esquema: se usa salida estructurada (`output_config.format`) en vez
de pedir "responde en JSON" y luego parsear texto libre, que falla el día que
el modelo antepone una frase amable.

Soporta cuatro proveedores. Claude va por el SDK oficial de Anthropic —que
además da caché de prompt y reserva en servidor—; OpenAI, DeepSeek y Kimi
comparten `app.ai.providers`, porque exponen la misma API.

La clave sale de la configuración (cifrada en base de datos) y, si no hay,
del entorno. Así se puede cambiar de proveedor desde la interfaz sin tocar el
despliegue.

Degradación (§12.4 del diseño): sin clave, o con `ai_enabled` en falso,
`is_configured()` devuelve False y quien llama usa su camino sin IA. Una
excepción del proveedor tampoco puede tumbar un envío — se convierte en
`AIUnavailableError` y el llamante decide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

import anthropic
import httpx
from anthropic.types.beta import (
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlockParam,
)

from app.ai.providers import OPENAI_COMPATIBLE, SPECS, chat_json
from app.core.config import get_settings
from app.core.enums import AIProvider
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.core.security import decrypt

logger = get_logger(__name__)

# Precio por millón de tokens (§12.1). Se usa solo para estimar el coste que se
# le enseña al usuario; la factura real la manda Anthropic.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
DEFAULT_MODEL = "claude-opus-5"


class AIUnavailableError(ExternalServiceError):
    """La IA no pudo responder. Nunca es fatal: siempre hay camino sin ella."""

    code = "AI_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AIResult:
    """Respuesta del modelo, ya parseada."""

    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def estimated_cost_usd(self) -> float:
        price_in, price_out = PRICING.get(self.model, PRICING[DEFAULT_MODEL])
        # Lo leído de caché cuesta ~10% de la entrada normal.
        billable_in = self.input_tokens + self.cached_tokens * 0.1
        return (billable_in * price_in + self.output_tokens * price_out) / 1_000_000


@dataclass(frozen=True, slots=True)
class AICredentials:
    """Con qué proveedor, clave y modelo se llama."""

    provider: AIProvider
    api_key: str
    model: str


def credentials_from(row: Any | None) -> AICredentials | None:
    """Credenciales a partir de la fila de configuración.

    La clave de la base manda sobre la del entorno: es la que el usuario
    acaba de escribir en la pantalla de Configuración, y que un valor de
    despliegue la pisara sería incomprensible desde la interfaz.
    """
    env_key = get_settings().anthropic_api_key

    if row is None:
        return (
            AICredentials(AIProvider.ANTHROPIC, env_key.get_secret_value(), DEFAULT_MODEL)
            if env_key
            else None
        )

    provider = row.ai_provider or AIProvider.ANTHROPIC
    model = row.ai_model or SPECS[provider].suggested_model

    if row.ai_api_key_enc:
        return AICredentials(provider, decrypt(row.ai_api_key_enc), model)

    # Sin clave guardada solo queda la del entorno, que es de Anthropic.
    if provider is AIProvider.ANTHROPIC and env_key:
        return AICredentials(provider, env_key.get_secret_value(), model)
    return None


def is_configured(row: Any | None = None) -> bool:
    return credentials_from(row) is not None


def _client(api_key: str) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        timeout=get_settings().ai_timeout_seconds,
    )


async def complete_json(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    credentials: AICredentials,
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium",
    max_tokens: int | None = None,
) -> AIResult:
    """Pide una respuesta que cumpla `schema` y la devuelve parseada."""
    if credentials.provider in OPENAI_COMPATIBLE:
        return await _complete_openai_compatible(
            credentials=credentials,
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens,
        )
    return await _complete_anthropic(
        credentials=credentials,
        system=system,
        user=user,
        schema=schema,
        effort=effort,
        max_tokens=max_tokens,
    )


async def _complete_anthropic(
    *,
    credentials: AICredentials,
    system: str,
    user: str,
    schema: dict[str, Any],
    effort: Literal["low", "medium", "high", "xhigh", "max"],
    max_tokens: int | None,
) -> AIResult:
    """Camino de Claude, por el SDK oficial.

    El bloque de sistema va marcado para caché: es idéntico entre llamadas
    (guía de estilo + reglas de redacción) y supone el ~90% de la entrada.
    Cachearlo es lo que baja la factura de ~$38 a ~$27 al mes (§12.1).
    """
    settings = get_settings()
    client = _client(credentials.api_key)
    model = credentials.model

    system_blocks: list[BetaTextBlockParam] = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
    ]
    output_config: BetaOutputConfigParam = {
        "effort": effort,
        "format": {"type": "json_schema", "schema": schema},
    }
    messages: list[BetaMessageParam] = [{"role": "user", "content": user}]

    try:
        response = await client.beta.messages.create(
            model=model,
            max_tokens=max_tokens or settings.ai_max_output_tokens,
            # Si los clasificadores rechazan la petición, el servidor la
            # reintenta solo en el modelo de reserva en vez de devolver un
            # borrador vacío.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system_blocks,
            output_config=output_config,
            messages=messages,
        )
    except anthropic.APIStatusError as exc:
        logger.warning("ai_api_error", status=exc.status_code, error=str(exc)[:300])
        # Un 401/403 no es "el proveedor falló": es la clave. Decirlo mal deja
        # al usuario buscando el problema donde no está.
        mensaje = (
            "La clave de IA no es válida o no tiene permiso."
            if exc.status_code in (401, 403)
            else "El proveedor de IA devolvió un error. Se usará el camino sin IA."
        )
        raise AIUnavailableError(mensaje, details={"status": exc.status_code}) from exc
    except anthropic.APIConnectionError as exc:
        logger.warning("ai_connection_error", error=str(exc)[:300])
        raise AIUnavailableError("No se pudo contactar con el proveedor de IA.") from exc

    if response.stop_reason == "refusal":
        # No es un fallo técnico: el modelo declinó. Se trata igual que una
        # caída — el llamante sigue con su plantilla.
        logger.info("ai_refusal", model=response.model)
        raise AIUnavailableError("La IA declinó generar esta respuesta.", code="AI_REFUSAL")

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise AIUnavailableError("La IA devolvió una respuesta vacía.", code="AI_EMPTY")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - el esquema lo impide
        logger.warning("ai_invalid_json", snippet=text[:200])
        raise AIUnavailableError("La IA devolvió un JSON inválido.", code="AI_BAD_JSON") from exc

    usage = response.usage
    result = AIResult(
        data=data,
        model=response.model,
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cached_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
    )
    logger.info(
        "ai_completed",
        model=result.model,
        input_tokens=result.input_tokens,
        cached_tokens=result.cached_tokens,
        output_tokens=result.output_tokens,
        cost_usd=round(result.estimated_cost_usd, 4),
    )
    return result


async def _complete_openai_compatible(
    *,
    credentials: AICredentials,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int | None,
) -> AIResult:
    """Camino de OpenAI, DeepSeek y Kimi.

    Cualquier fallo —de red, de clave, de formato— acaba en
    `AIUnavailableError`, que es lo que hace que el CRM siga funcionando sin
    IA en vez de dejar un correo a medias.
    """
    settings = get_settings()

    try:
        chat = await chat_json(
            provider=credentials.provider,
            api_key=credentials.api_key,
            model=credentials.model,
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens or settings.ai_max_output_tokens,
            timeout=settings.ai_timeout_seconds,
        )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "ai_api_error",
            provider=credentials.provider.value,
            status=exc.response.status_code,
            error=exc.response.text[:300],
        )
        mensaje = (
            "La clave de IA no es válida o no tiene permiso."
            if exc.response.status_code in (401, 403)
            else "El proveedor de IA devolvió un error. Se usará el camino sin IA."
        )
        raise AIUnavailableError(mensaje, details={"status": exc.response.status_code}) from exc
    except httpx.HTTPError as exc:
        logger.warning("ai_connection_error", provider=credentials.provider.value, error=str(exc))
        raise AIUnavailableError("No se pudo contactar con el proveedor de IA.") from exc

    if not chat.text:
        raise AIUnavailableError("La IA devolvió una respuesta vacía.", code="AI_EMPTY")

    try:
        data = json.loads(_strip_code_fence(chat.text))
    except json.JSONDecodeError as exc:
        logger.warning(
            "ai_invalid_json",
            provider=credentials.provider.value,
            snippet=chat.text[:200],
        )
        raise AIUnavailableError("La IA devolvió un JSON inválido.", code="AI_BAD_JSON") from exc

    result = AIResult(
        data=data,
        model=chat.model,
        input_tokens=chat.input_tokens,
        output_tokens=chat.output_tokens,
    )
    logger.info(
        "ai_completed",
        provider=credentials.provider.value,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    return result


def _strip_code_fence(text: str) -> str:
    """Quita el ```json que algunos modelos ponen pese a pedirles que no.

    Es más barato tolerarlo aquí que perder la personalización entera por tres
    caracteres de adorno.
    """
    limpio = text.strip()
    if not limpio.startswith("```"):
        return limpio
    sin_apertura = limpio.split("\n", 1)[-1]
    return sin_apertura.rsplit("```", 1)[0].strip()


# Prompt mínimo para comprobar que una clave funciona. Se pide un JSON trivial
# en vez de texto libre: así se valida de paso que el proveedor sabe devolver
# JSON, que es lo único que este CRM le pide.
_PING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


async def verify_credentials(credentials: AICredentials) -> None:
    """Comprueba que la clave y el modelo funcionan de verdad.

    Lanza `AIUnavailableError` con el motivo si no. Es una llamada real y de
    coste ridículo: vale mucho más que descubrir la clave mal pegada tres días
    después, en el primer correo que salió sin personalizar.
    """
    await complete_json(
        system='Responde únicamente {"ok": true}.',
        user="ping",
        schema=_PING_SCHEMA,
        credentials=credentials,
        effort="low",
        max_tokens=64,
    )
