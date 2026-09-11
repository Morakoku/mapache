"""Engine y sesiones de SQLAlchemy 2.x (async).

Una sesión por request, cerrada siempre. El commit lo hace el Service que
posee la transacción, no el dependency: así un caso de uso que toca tres
repositorios lo hace en una única transacción.

En entornos serverless (Vercel) donde la conexión directa a PostgreSQL falla,
se usa Supabase PostgREST API como fallback.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine | None:
    """Obtiene el engine SQLAlchemy, o None si no hay DB_URL configurada."""
    global _engine
    if _engine is None:
        settings = get_settings()
        if not settings.sqlalchemy_url:
            logger.info("no_database_url_postgrest_mode")
            return None
        # asyncpg no acepta sslmode en DSN; configurar SSL en connect_args
        connect_args = {
            "server_settings": {"application_name": settings.app_name},
            # Compatible con poolers tipo pgbouncer/Supavisor (Supabase): sin
            # cache de sentencias preparadas. Es seguro tambien contra Postgres directo.
            "statement_cache_size": 0,
        }
        # Forzar SSL en producción o contra un pooler gestionado (Supabase).
        _url = settings.sqlalchemy_url or ""
        if settings.is_production or "supabase.com" in _url:
            ctx = ssl.create_default_context()
            if "supabase.com" in _url:
                # Supavisor presenta su propia cadena; ciframos sin verificar CA
                # (equivale a libpq sslmode=require). El trafico va cifrado.
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            connect_args["ssl"] = ctx
        _engine = create_async_engine(
            settings.sqlalchemy_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=settings.db_pool_pre_ping,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """Obtiene el session factory, o None si no hay engine."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        if engine is None:
            return None
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,  # poder leer el objeto después del commit
            autoflush=False,
        )
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession | None]:
    """Dependency de FastAPI: una sesión por request.
    
    Si no hay DB configurada (modo PostgREST), devuelve None.
    El router debe manejar este caso.
    """
    factory = get_session_factory()
    if factory is None:
        # Modo PostgREST - no hay sesión SQLAlchemy
        yield None
        return
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession | None]:
    """Sesión fuera del ciclo de request: workers, CLI, tests.
    
    Si no hay DB configurada (modo PostgREST), devuelve None.
    """
    factory = get_session_factory()
    if factory is None:
        yield None
        return
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Cierra el pool. Se llama en el shutdown de la app y entre tests."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
