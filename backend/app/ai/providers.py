"""Proveedores de IA compatibles con la API de OpenAI.

OpenAI, DeepSeek y Kimi exponen el mismo `/chat/completions`: lo único que
cambia entre ellos es la URL base y el nombre del modelo. Por eso comparten un
cliente en vez de tener tres.

Se usa `httpx` —ya es dependencia— en vez del SDK de OpenAI: para una sola
llamada JSON, añadir un paquete entero no compensa, y así los tres proveedores
funcionan sin instalar nada más.

Claude no pasa por aquí: va por el SDK oficial de Anthropic, que además da
caché de prompt y reserva en servidor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.enums import AIProvider
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    label: str
    base_url: str
    # Modelo sugerido al elegir el proveedor. Es editable en la interfaz porque
    # los catálogos cambian y un identificador caducado deja la IA muerta.
    suggested_model: str
    api_keys_url: str


# La URL base es lo único que este CRM necesita saber de cada proveedor.
SPECS: dict[AIProvider, ProviderSpec] = {
    AIProvider.ANTHROPIC: ProviderSpec(
        label="Claude (Anthropic)",
        base_url="https://api.anthropic.com",
        suggested_model="claude-opus-5",
        api_keys_url="https://console.anthropic.com/settings/keys",
    ),
    AIProvider.OPENAI: ProviderSpec(
        label="ChatGPT (OpenAI)",
        base_url="https://api.openai.com/v1",
        suggested_model="gpt-4o",
        api_keys_url="https://platform.openai.com/api-keys",
    ),
    AIProvider.DEEPSEEK: ProviderSpec(
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        suggested_model="deepseek-chat",
        api_keys_url="https://platform.deepseek.com/api_keys",
    ),
    AIProvider.KIMI: ProviderSpec(
        label="Kimi (Moonshot)",
        base_url="https://api.moonshot.ai/v1",
        suggested_model="moonshot-v1-8k",
        api_keys_url="https://platform.moonshot.ai/console/api-keys",
    ),
}

OPENAI_COMPATIBLE = frozenset({AIProvider.OPENAI, AIProvider.DEEPSEEK, AIProvider.KIMI})


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


async def chat_json(
    *,
    provider: AIProvider,
    api_key: str,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int,
    timeout: float,
) -> ChatResult:
    """Una respuesta JSON de un proveedor compatible con OpenAI.

    Se pide `json_object` y el esquema va dentro del mensaje de sistema, en vez
    de usar `json_schema` estructurado: DeepSeek y Kimi no lo soportan igual
    que OpenAI, y una petición que un proveedor rechaza deja al usuario sin IA
    sin motivo aparente. El esquema en el prompt funciona en los tres.

    Si aun así devuelve algo que no es JSON, quien llama lo trata como caída y
    sigue por el camino sin IA — nunca se envía un correo a medias.
    """
    spec = SPECS[provider]
    instrucciones = (
        f"{system}\n\n"
        "FORMATO DE SALIDA\n"
        "Responde únicamente con un objeto JSON válido que cumpla este esquema, "
        "sin texto antes ni después y sin bloques de código:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{spec.base_url}/chat/completions",
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": instrucciones},
                    {"role": "user", "content": user},
                ],
            },
        )

    response.raise_for_status()
    body = response.json()

    choice = (body.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    usage = body.get("usage") or {}

    return ChatResult(
        text=text.strip(),
        model=body.get("model") or model,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
    )
