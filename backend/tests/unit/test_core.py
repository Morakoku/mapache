"""Tests unitarios del núcleo: cifrado, ofuscación de logs, cola de jobs."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.enums import JobType
from app.core.exceptions import ConflictError, DomainError, NotFoundError
from app.core.jobs import InProcessQueue, JobRegistry
from app.core.logging import mask_email, mask_phone
from app.core.security import decrypt, encrypt, new_tracking_token

# ------------------------------------------------------------------ cifrado


def test_encrypt_decrypt_roundtrip() -> None:
    secreto = "ya29.a0AfH6SMBx-token-de-google"
    cifrado = encrypt(secreto)

    assert cifrado != secreto
    assert decrypt(cifrado) == secreto


def test_encrypt_is_not_deterministic() -> None:
    """Fernet incluye IV y timestamp: dos cifrados del mismo texto difieren.

    Importa: si fuera determinista, un atacante con acceso de lectura podría
    saber qué cuentas comparten contraseña.
    """
    assert encrypt("mismo-valor") != encrypt("mismo-valor")


def test_tracking_tokens_are_unique() -> None:
    tokens = {new_tracking_token() for _ in range(1000)}
    assert len(tokens) == 1000


# ------------------------------------------------------------------ ofuscación


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("juan.perez@empresa.com", "j***@empresa.com"),
        ("contacto a ventas@restaurante.co hoy", "contacto a v***@restaurante.co hoy"),
        ("sin correo aquí", "sin correo aquí"),
    ],
)
def test_mask_email(entrada: str, esperado: str) -> None:
    assert mask_email(entrada) == esperado


def test_mask_phone_keeps_last_two_digits() -> None:
    masked = mask_phone("llamar al +57 300 123 4567")

    assert "4567" not in masked
    assert masked.endswith("67")


# ------------------------------------------------------------------ excepciones


def test_domain_error_serialises_to_api_shape() -> None:
    error = ConflictError("Ya existe un servicio con ese nombre", details={"name": "Web"})

    assert error.to_dict() == {
        "error": {
            "code": "CONFLICT",
            "message": "Ya existe un servicio con ese nombre",
            "details": {"name": "Web"},
        }
    }
    assert error.http_status == 409


def test_not_found_builds_specific_code() -> None:
    error = NotFoundError.for_entity("lead", "abc-123")

    assert error.code == "LEAD_NOT_FOUND"
    assert error.http_status == 404
    assert isinstance(error, DomainError)


# ------------------------------------------------------------------ cola de jobs


async def test_inprocess_queue_runs_handler() -> None:
    registry = JobRegistry()
    ejecutados: list[dict] = []
    done = asyncio.Event()

    async def handler(job_id: uuid.UUID, payload: dict) -> None:
        ejecutados.append({"job_id": job_id, **payload})
        done.set()

    registry.register(JobType.DISCOVERY, handler)
    queue = InProcessQueue(registry, concurrency=1)

    job_id = await queue.enqueue(JobType.DISCOVERY, {"search_id": "s1"})
    await asyncio.wait_for(done.wait(), timeout=2)

    assert ejecutados == [{"job_id": job_id, "search_id": "s1"}]
    await queue.shutdown()


async def test_queue_survives_a_failing_handler() -> None:
    """Un handler que revienta no debe tumbar la cola ni los demás jobs."""
    registry = JobRegistry()
    segundo_ok = asyncio.Event()

    async def revienta(_job_id: uuid.UUID, _payload: dict) -> None:
        raise RuntimeError("el scraper encontró un challenge")

    async def funciona(_job_id: uuid.UUID, _payload: dict) -> None:
        segundo_ok.set()

    registry.register(JobType.DISCOVERY, revienta)
    registry.register(JobType.SCORING, funciona)
    queue = InProcessQueue(registry, concurrency=2)

    await queue.enqueue(JobType.DISCOVERY, {})
    await queue.enqueue(JobType.SCORING, {})

    await asyncio.wait_for(segundo_ok.wait(), timeout=2)
    await queue.shutdown()


async def test_enqueue_unregistered_type_fails_loudly() -> None:
    """Encolar sin handler es un error de programación: falla ya, no en silencio."""
    queue = InProcessQueue(JobRegistry(), concurrency=1)

    with pytest.raises(RuntimeError, match="Sin handler registrado"):
        await queue.enqueue(JobType.ENRICHMENT, {})


def test_registry_rejects_duplicate_handler() -> None:
    registry = JobRegistry()

    async def handler(_job_id: uuid.UUID, _payload: dict) -> None: ...

    registry.register(JobType.DISCOVERY, handler)

    with pytest.raises(RuntimeError, match="Handler duplicado"):
        registry.register(JobType.DISCOVERY, handler)
