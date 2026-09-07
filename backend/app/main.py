"""Punto de entrada de la aplicación."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.audit import AuditMiddleware
from app.core.config import Settings, get_settings
from app.core.container import get_job_queue, get_job_registry
from app.core.database import dispose_engine, session_scope
from app.core.destructive import DestructiveGuardMiddleware
from app.core.enums import JobType
from app.core.exceptions import DomainError
from app.core.headers import SecurityHeadersMiddleware
from app.core.logging import get_logger, setup_logging
from app.core.rate_limit import RateLimitMiddleware
from app.core.scheduler import PeriodicTask, Scheduler
from app.middleware import ServiceAuthMiddleware
from app.routers import api_router, public_router
from app.routers.health import VERSION
from app.services.job_svc import JobService
from app.workers import register_workers

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()
    logger.info(
        "app_starting",
        environment=settings.environment,
        version=VERSION,
        job_queue=settings.job_queue_backend,
    )

    register_workers(get_job_registry())

    scheduler = Scheduler(get_job_queue())
    if settings.scheduler_enabled:
        scheduler.start(
            [
                PeriodicTask(
                    JobType.FOLLOWUP_TICK,
                    every_seconds=settings.followup_tick_minutes * 60,
                    payload={},
                ),
                PeriodicTask(
                    JobType.INBOX_SYNC,
                    every_seconds=settings.inbox_sync_minutes * 60,
                    payload={},
                    initial_delay=60,
                ),
            ]
        )
    else:
        logger.info("scheduler_disabled")

    # Un reinicio deja huérfano lo que estuviera corriendo en la cola en
    # memoria. Se reencola para que la UI no muestre progreso congelado.
    try:
        async with session_scope() as session:
            if session is not None:
                recovered = await JobService(session).recover_stale()
                if recovered:
                    logger.warning("stale_jobs_requeued", count=recovered)
            else:
                logger.info("stale_job_recovery_skipped_no_db")
    except Exception as exc:  # noqa: BLE001 - la app debe arrancar igualmente
        logger.error("stale_job_recovery_failed", error=str(exc))

    yield

    logger.info("app_stopping")
    await scheduler.shutdown()
    await get_job_queue().shutdown()
    await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=VERSION,
        description="CRM de prospección, outbound sales y conversión de clientes.",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(ServiceAuthMiddleware, settings=settings)

    # LOOP-13: capas de seguridad sin tocar la funcionalidad. Orden de registro
    # = inverso al de ejecución: SecurityHeaders queda como capa más externa.
    if settings.destructive_confirm_required:
        app.add_middleware(DestructiveGuardMiddleware)
    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            enabled=True,
            window_seconds=settings.rate_limit_window_seconds,
        )
    if settings.audit_enabled:
        app.add_middleware(AuditMiddleware)
    if settings.security_headers_enabled:
        app.add_middleware(SecurityHeadersMiddleware, hsts=settings.hsts_enabled)

    _register_exception_handlers(app)

    app.include_router(public_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        """Único punto de traducción dominio -> HTTP.

        Los Services levantan DomainError; nadie por debajo del router conoce
        códigos de estado.
        """
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "SCHEMA_VALIDATION_ERROR",
                    "message": "Datos de entrada inválidos.",
                    "details": {"fields": exc.errors()},
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", path=request.url.path, method=request.method)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Error interno del servidor.",
                    "details": {},
                }
            },
        )


app = create_app()
