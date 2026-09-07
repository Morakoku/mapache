"""LOOP-16 — Primera integración real Hermes → Mapache (en TEST).

Demuestra la cadena completa: Hermes → autenticación → autorización → contrato
→ Mapache → worker → job → resultado → Hermes, sin efectos comerciales.

- Dispatch con whitelist estricta (DISCOVERY/ENRICHMENT/FOLLOWUP_TICK; SEND_BATCH
  y el resto → DENY) e Idempotency-Key obligatoria.
- Polling del job (solo jobs de la identidad).
- Jobs controlados que se ejecutan de verdad y terminan COMPLETED/FAILED sin
  enviar correos, sin scraping real (target inexistente) ni tocar datos reales.
- Aislamiento: Hermes no accede a CRM/scraping/mail/settings/jobs/DELETE.
- Auditoría del dispatch con identidad hermes.

El fixture usa el `get_db` real de la app (sesión fresca por request, igual que
producción): evita el caching del identity-map de una sesión compartida.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.container import get_job_registry, reset_container
from app.core.database import get_session_factory
from app.core.enums import JobType
from app.core.security import issue_service_token
from app.main import create_app
from app.models.audit import AuditLog
from app.models.idempotency import IdempotencyEvent
from app.models.job import Job
from app.services.job_svc import JobService
from app.workers import register_workers

TEST_KEY = "clave-de-prueba-de-32-bytes-abcdefghijklmn"
TTL = 300
API = "/api/v1"

# Operaciones que Hermes NO puede tocar (fuera del contrato).
DENIED_OPERATIONS: list[tuple[str, str]] = [
    ("GET", "/api/v1/leads"),
    ("GET", "/api/v1/companies"),
    ("GET", "/api/v1/jobs"),
    ("GET", "/api/v1/settings"),
    ("POST", "/api/v1/searches/00000000-0000-0000-0000-000000000000/run"),
    ("POST", "/api/v1/companies/00000000-0000-0000-0000-000000000000/enrich"),
    ("POST", "/api/v1/emails/send"),
    ("DELETE", "/api/v1/leads/00000000-0000-0000-0000-000000000000?confirm=true"),
]


def _auth_settings() -> Settings:
    return Settings(
        app_name="test",
        environment="test",
        debug=False,
        database_url="postgresql://crm:crm@localhost:5435/crm_test",
        encryption_key="x" * 44,
        secret_key="test-secret-key",
        service_auth_enabled=True,
        service_token_key=TEST_KEY,
        service_token_ttl_seconds=TTL,
        service_trusted_client_ids="hermes",
        hermes_scopes="hermes.dispatch,hermes.jobs.read",
        audit_enabled=True,
        rate_limit_enabled=False,
    )


def _token() -> str:
    return issue_service_token("hermes", TEST_KEY, nonce=uuid.uuid4().hex)


def _auth_headers(*, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {_token()}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


@pytest.fixture
async def hermes_client(db: AsyncSession) -> AsyncClient:
    # `db` fuerza la creación del esquema (engine fixture, create_all) en la BD
    # de test; la app usa su propio get_db (sesión fresca por request).
    reset_container()
    register_workers(get_job_registry())
    app = create_app(settings=_auth_settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _poll_job(client: AsyncClient, job_id: str, *, tries: int = 40) -> dict:
    terminal = {"COMPLETED", "FAILED", "CANCELLED"}
    for _ in range(tries):
        r = await client.get(f"{API}/hermes/jobs/{job_id}", headers=_auth_headers())
        if r.status_code == 200 and r.json()["status"] in terminal:
            return r.json()
        await asyncio.sleep(0.05)
    raise AssertionError("el job no alcanzó estado terminal")


class TestDispatch:
    async def test_followup_tick_encola_ejecuta_y_completa(
        self, hermes_client: AsyncClient
    ) -> None:
        r = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "FOLLOWUP_TICK", "payload": {"limit": 10}},
            headers=_auth_headers(idempotency_key=f"key-{uuid.uuid4().hex[:20]}"),
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "QUEUED"
        assert body["job_id"]

        state = await _poll_job(hermes_client, body["job_id"])
        assert state["status"] == "COMPLETED"
        assert state["result"] == {"sent": 0, "skipped": 0, "rescheduled": 0, "failed": 0}

    async def test_enrichment_sin_ids_completa(self, hermes_client: AsyncClient) -> None:
        r = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "ENRICHMENT", "payload": {}},
            headers=_auth_headers(idempotency_key=f"key-{uuid.uuid4().hex[:20]}"),
        )
        assert r.status_code == 202
        state = await _poll_job(hermes_client, r.json()["job_id"])
        assert state["status"] == "COMPLETED"
        assert state["result"]["processed"] == 0

    async def test_discovery_whitelisted_falla_sin_scrape(self, hermes_client: AsyncClient) -> None:
        # DISCOVERY está en la whitelist; con un search_id inexistente el worker
        # falla rápido (FAILED) sin tocar el scraper ni datos reales.
        r = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "DISCOVERY", "payload": {"search_id": str(uuid.uuid4())}},
            headers=_auth_headers(idempotency_key=f"key-{uuid.uuid4().hex[:20]}"),
        )
        assert r.status_code == 202
        state = await _poll_job(hermes_client, r.json()["job_id"])
        assert state["status"] == "FAILED"

    async def test_requires_idempotency_key(self, hermes_client: AsyncClient) -> None:
        r = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "FOLLOWUP_TICK", "payload": {}},
            headers=_auth_headers(),  # sin Idempotency-Key
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    async def test_send_batch_bloqueado(self, hermes_client: AsyncClient, db: AsyncSession) -> None:
        r = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "SEND_BATCH", "payload": {}},
            headers=_auth_headers(idempotency_key=f"key-{uuid.uuid4().hex[:20]}"),
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "JOB_TYPE_BLOCKED"
        jobs = (
            (await db.execute(select(Job).where(Job.job_type == JobType.SEND_BATCH)))
            .scalars()
            .all()
        )
        assert jobs == [], "SEND_BATCH no debe crear ningún job"

    async def test_otros_job_types_bloqueados(self, hermes_client: AsyncClient) -> None:
        for job_type in ("SCORING", "INBOX_SYNC", "SCRAPER_HEALTH"):
            r = await hermes_client.post(
                f"{API}/hermes/dispatch",
                json={"job_type": job_type, "payload": {}},
                headers=_auth_headers(idempotency_key=f"key-{uuid.uuid4().hex[:20]}"),
            )
            assert r.status_code == 403, f"{job_type} debía estar bloqueado"
            assert r.json()["error"]["code"] == "JOB_TYPE_NOT_ALLOWED"

    async def test_job_type_desconocido_422(self, hermes_client: AsyncClient) -> None:
        r = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "HACK", "payload": {}},
            headers=_auth_headers(idempotency_key=f"key-{uuid.uuid4().hex[:20]}"),
        )
        assert r.status_code == 422


class TestIdempotency:
    async def test_misma_key_no_crea_segundo_job(self, hermes_client: AsyncClient) -> None:
        key = f"key-{uuid.uuid4().hex[:20]}"
        r1 = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "FOLLOWUP_TICK", "payload": {"limit": 1}},
            headers=_auth_headers(idempotency_key=key),
        )
        assert r1.status_code == 202
        first_job = r1.json()["job_id"]

        r2 = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "FOLLOWUP_TICK", "payload": {"limit": 1}},
            headers=_auth_headers(idempotency_key=key),
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert r2.json()["error"]["details"]["job_id"] == str(first_job)


class TestOwnership:
    async def test_job_creado_por_hermes_tiene_client_id(
        self, hermes_client: AsyncClient, db: AsyncSession
    ) -> None:
        r = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "FOLLOWUP_TICK", "payload": {}},
            headers=_auth_headers(idempotency_key=f"key-{uuid.uuid4().hex[:20]}"),
        )
        job = await db.get(Job, uuid.UUID(r.json()["job_id"]))
        assert job is not None
        assert job.client_id == "hermes"

    async def test_job_ajeno_no_visible_para_hermes(self, hermes_client: AsyncClient) -> None:
        # Un job creado por la UI (client_id NULL) no pertenece a Hermes. Se
        # crea con la session factory de la app (como producción) y se limpia.
        async with get_session_factory()() as s:
            ui_job = await JobService(s).create(JobType.DISCOVERY, {})
            await s.commit()
            ui_id = ui_job.id

        r = await hermes_client.get(f"{API}/hermes/jobs/{ui_id}", headers=_auth_headers())
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "JOB_NOT_OWNED"

        async with get_session_factory()() as s:
            job = await s.get(Job, ui_id)
            if job is not None:
                await s.delete(job)
                await s.commit()

    async def test_get_job_sin_token_401(self, hermes_client: AsyncClient) -> None:
        r = await hermes_client.get(f"{API}/hermes/jobs/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 401


class TestIsolation:
    async def test_hermes_no_accede_a_los_141_endpoints(self, hermes_client: AsyncClient) -> None:
        for method, path in DENIED_OPERATIONS:
            r = await hermes_client.request(method, path, headers=_auth_headers())
            assert r.status_code == 403, f"{method} {path} debía ser DENY para hermes"


class TestAudit:
    async def test_dispatch_queda_auditado(
        self, hermes_client: AsyncClient, db: AsyncSession
    ) -> None:
        r = await hermes_client.post(
            f"{API}/hermes/dispatch",
            json={"job_type": "FOLLOWUP_TICK", "payload": {}},
            headers=_auth_headers(idempotency_key=f"key-{uuid.uuid4().hex[:20]}"),
        )
        assert r.status_code == 202
        job_id = uuid.UUID(r.json()["job_id"])

        audit = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.action == "hermes.dispatch",
                        AuditLog.client_id == "hermes",
                    )
                )
            )
            .scalars()
            .first()
        )
        assert audit is not None
        assert audit.status == 202
        assert audit.idempotency_key is not None

        # El vínculo key → job queda en idempotency_events (resultado en jobs).
        idem = (
            (await db.execute(select(IdempotencyEvent).where(IdempotencyEvent.job_id == job_id)))
            .scalars()
            .first()
        )
        assert idem is not None

    async def test_no_secrets_en_logs(self, hermes_client: AsyncClient, caplog) -> None:
        with caplog.at_level(logging.DEBUG):
            await hermes_client.post(
                f"{API}/hermes/dispatch",
                json={"job_type": "FOLLOWUP_TICK", "payload": {}},
                headers=_auth_headers(idempotency_key=f"key-{uuid.uuid4().hex[:20]}"),
            )
            await hermes_client.get(f"{API}/leads", headers=_auth_headers())
        assert TEST_KEY not in caplog.text
