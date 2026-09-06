"""Elegir y guardar el buscador desde Configuración.

Lo que importa aquí es que una clave mal copiada se sepa al guardar y no tres
días después, a mitad de una búsqueda de 100 empresas; y que Brave no exija el
motor que sí necesita Google.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt
from app.services.mail_admin_svc import SettingsService
from tests.integration.test_email_send import _seed_settings

API = "/api/v1"


def _buscador_responde(monkeypatch: pytest.MonkeyPatch, status: int = 200) -> list[str]:
    """Simula la API del buscador y devuelve las URLs que se llamaron."""
    llamadas: list[str] = []

    async def fake_get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        llamadas.append(str(url))
        cuerpo = {"items": [{"title": "x", "link": "https://x.co"}], "web": {"results": []}}
        return httpx.Response(status, json=cuerpo, request=httpx.Request("GET", str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return llamadas


@pytest.mark.asyncio
async def test_brave_se_guarda_sin_motor(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La ventaja práctica de Brave: un dato de configuración en vez de dos."""
    await _seed_settings(db)
    llamadas = _buscador_responde(monkeypatch)

    response = await client.put(
        f"{API}/settings/serp",
        json={"provider": "BRAVE", "api_key": "brave-clave-de-prueba"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["serp_provider"] == "BRAVE"
    assert body["serp_configured"] is True
    assert "api.search.brave.com" in llamadas[0]
    # La clave se guarda cifrada y no vuelve en la respuesta.
    assert "brave-clave-de-prueba" not in response.text
    settings = await SettingsService(db).get()
    await db.refresh(settings)
    assert decrypt(settings.serp_api_key_enc or "") == "brave-clave-de-prueba"


@pytest.mark.asyncio
async def test_google_sin_motor_se_rechaza(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_settings(db)
    _buscador_responde(monkeypatch)

    response = await client.put(
        f"{API}/settings/serp",
        json={"provider": "GOOGLE_CSE", "api_key": "AIza-clave-de-prueba"},
    )

    assert response.status_code == 400
    assert "cx" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_una_clave_que_no_sirve_falla_al_guardar(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Es el punto de la prueba: enterarse ahora, no en la búsqueda de mañana."""
    await _seed_settings(db)
    _buscador_responde(monkeypatch, status=401)

    response = await client.put(
        f"{API}/settings/serp",
        json={"provider": "BRAVE", "api_key": "brave-clave-mala"},
    )

    assert response.status_code == 502
    assert "no es válida" in response.json()["error"]["message"]

    # Y no se guarda nada: la configuración anterior sigue en pie.
    settings = await SettingsService(db).get()
    await db.refresh(settings)
    assert settings.serp_api_key_enc is None


@pytest.mark.asyncio
async def test_cambiar_de_buscador_conserva_la_clave_guardada(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Al reenviar el formulario sin la clave no se puede perder la que había."""
    await _seed_settings(db)
    _buscador_responde(monkeypatch)

    await client.put(
        f"{API}/settings/serp",
        json={"provider": "BRAVE", "api_key": "brave-clave-de-prueba"},
    )
    response = await client.put(f"{API}/settings/serp", json={"provider": "BRAVE"})

    assert response.status_code == 200
    settings = await SettingsService(db).get()
    await db.refresh(settings)
    assert decrypt(settings.serp_api_key_enc or "") == "brave-clave-de-prueba"
