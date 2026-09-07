"""Engine y sesiones de SQLAlchemy 2.x (async).

Una sesión por request, cerrada siempre. El commit lo hace el Service que
posee la transacción, no el dependency: así un caso de uso que toca tres
repositorios lo hace en una única transacción.
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

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        # asyncpg no acepta sslmode en DSN; configurar SSL en connect_args
        connect_args = {
            "server_settings": {"application_name": settings.app_name},
        }
        # Si es producción, forzar SSL
        if settings.is_production:
            connect_args["ssl"] = ssl.create_default_context()
        _engine = create_async_engine(
            settings.sqlalchemy_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=settings.db_pool_pre_ping,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # poder leer el objeto después del commit
            autoflush=False,
        )
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependency de FastAPI: una sesión por request.

    Hace rollback ante cualquier excepción que escape del handler. No hace
    commit — eso es responsabilidad del Service.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Sesión fuera del ciclo de request: workers, CLI, tests.

    Aquí sí hacemos commit al salir sin error, porque no hay un Service
    superior que sea el dueño de la transacción.
    """
    factory = get_session_factory()
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