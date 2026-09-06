"""Tests de integraciÃ³n de la capa de seguridad (LOOP-13).

Verifican el comportamiento real de la app: confirmaciÃ³n destructiva,
escritura de auditorÃ­a e idempotencia por Idempotency-Key.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ConflictError, DomainError
from app.core.idempotency import IdempotencyService, require_idempotency
from app.models.audit import AuditLog
from app.models.idempotency import IdempotencyEvent

API = "/api/v1"


def _attach_domain_handler(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


class TestDestructiveConfirmation:
    async def test_delete_sin_confirm_devuelve_409(self, client: AsyncClient) -> None:
        r = await client.delete(f"{API}/templates/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "REQUIRES_CONFIRMATION"

    async def test_delete_con_confirm_llega_al_handler(self, client: AsyncClient) -> None:
        # Con confirm=true el guardia deja pasar; el 404 es del handler.
        r = await client.delete(
            f"{API}/templates/00000000-0000-0000-0000-000000000000?confirm=true"
        )
        assert r.status_code == 404

    async def test_job_cancel_sin_confirm_devuelve_409(self, client: AsyncClient) -> None:
        r = await client.post(f"{API}/jobs/00000000-0000-0000-0000-000000000000/cancel")
        assert r.status_code == 409


class TestAuditLog:
    async def test_post_registra_fila_de_auditoria(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        payload = {"name": "Plantilla auditorÃ­a", "subject": "Hola", "body_text": "cuerpo"}
        r = await client.post(f"{API}/templates", json=payload)
        assert r.status_code == 201

        result = await db.execute(
            select(AuditLog)
            .where(AuditLog.method == "POST", AuditLog.path == f"{API}/templates")
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
        row = result.scalars().first()
        assert row is not None
        assert row.status == 201
        assert row.action == "write"
        # Nunca se guardan bodies ni secretos: solo cabeceras de control.
        assert row.idempotency_key is None

    async def test_audit_no_rompe_la_peticion(self, client: AsyncClient) -> None:
        r = await client.post(
            f"{API}/templates",
            json={"name": "x", "subject": "y"},
        )
        assert r.status_code in (201, 422)


class TestIdempotencyService:
    async def test_key_duplicada_lanza_conflict(self, db: AsyncSession) -> None:
        svc = IdempotencyService(db)
        await svc.begin("clave-12345678", "POST", f"{API}/emails/send")
        with pytest.raises(ConflictError) as ei:
            await svc.begin("clave-12345678", "POST", f"{API}/emails/send")
        assert ei.value.code == "IDEMPOTENCY_CONFLICT"

    async def test_attach_job_guarda_referencia(self, db: AsyncSession) -> None:
        import uuid

        svc = IdempotencyService(db)
        await svc.begin("clave-87654321", "POST", f"{API}/searches/x/run")
        job_id = uuid.uuid4()
        await svc.attach_job("clave-87654321", "POST", f"{API}/searches/x/run", job_id)
        await db.flush()

        result = await db.execute(
            select(IdempotencyEvent).where(IdempotencyEvent.idempotency_key == "clave-87654321")
        )
        row = result.scalar_one()
        assert row.job_id == job_id


class TestIdempotencyHttp:
    async def test_segunda_llamada_con_misma_key_devuelve_409(self, db: AsyncSession) -> None:
        app = FastAPI()
        router = APIRouter(dependencies=[Depends(require_idempotency)])

        @router.post("/api/v1/test/job")
        async def job() -> dict:
            return {"job_id": "00000000-0000-0000-0000-000000000001"}

        app.include_router(router)

        _attach_domain_handler(app)

        async def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        headers = {"Idempotency-Key": "clave-http-123456"}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            first = await ac.post("/api/v1/test/job", headers=headers)
            second = await ac.post("/api/v1/test/job", headers=headers)

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        app.dependency_overrides.clear()

    async def test_sin_key_la_operacion_sigue_sin_idempotencia(self, db: AsyncSession) -> None:
        app = FastAPI()
        router = APIRouter(dependencies=[Depends(require_idempotency)])

        @router.post("/api/v1/test/job")
        async def job() -> dict:
            return {"job_id": "00000000-0000-0000-0000-000000000002"}

        app.include_router(router)

        _attach_domain_handler(app)

        async def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r1 = await ac.post("/api/v1/test/job")
            r2 = await ac.post("/api/v1/test/job")

        assert r1.status_code == 200
        assert r2.status_code == 200

        app.dependency_overrides.clear()
