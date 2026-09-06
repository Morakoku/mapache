"""Fixtures de test.

Los tests corren contra un Postgres real (contenedor `postgres_test`), no
SQLite: el CRM depende de tipos ENUM nativos, JSONB, índices parciales y
`pg_trgm`. Probar contra otro motor daría verde en tests y rojo en producción.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Cargar .env.test ANTES de importar nada de app.* — Settings se lee al importar.
_ENV_TEST = Path(__file__).resolve().parents[1] / ".env.test"
if _ENV_TEST.exists():
    for line in _ENV_TEST.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from app.core.config import get_settings  # noqa: E402
from app.core.container import reset_container  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.rate_limit import reset_default_limiter  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.base import Base  # noqa: E402


@pytest.fixture(scope="session")
def settings():  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
async def engine(settings):  # type: ignore[no-untyped-def]
    """Engine de test. Crea el esquema una vez y lo tira al terminar.

    Se usa `create_all` en vez de `alembic upgrade` para que los tests no
    dependan del historial de migraciones. La coherencia entre modelos y
    migraciones la vigila `test_migrations.py`.
    """
    eng = create_async_engine(settings.sqlalchemy_url, echo=False, poolclass=None)

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:  # type: ignore[no-untyped-def]
    """Sesión aislada por test.

    Todo ocurre dentro de una transacción que se revierte al final, así que
    los tests no se contaminan entre sí y no hace falta limpiar tablas.
    """
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
    session = factory()

    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest.fixture
async def client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app, con la sesión de test inyectada."""
    reset_container()
    reset_default_limiter()
    app = create_app()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    reset_container()
