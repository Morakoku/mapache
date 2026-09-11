"""Entorno de Alembic.

Lee la URL de la base desde Settings (nunca de alembic.ini) y corre las
migraciones sobre un engine async.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings

# Importar el paquete de modelos puebla Base.metadata. Cada modelo nuevo debe
# quedar importado en app/models/__init__.py o autogenerate no lo verá.
import app.models  # noqa: F401
from app.models.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# str(): pydantic puede devolver un tipo Annotated/URL (p.ej. PostgresDsn) que
# configparser ya no auto-convierte a string en Python 3.14+.
config.set_main_option("sqlalchemy.url", str(get_settings().alembic_url))

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to) -> bool:  # type: ignore[no-untyped-def]
    """Ignora objetos que no gestiona Alembic (extensiones, vistas materializadas)."""
    return not (type_ == "table" and name in {"spatial_ref_sys"})


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse (`alembic upgrade head --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
