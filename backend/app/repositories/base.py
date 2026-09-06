"""Repositorio genérico.

Solo acceso a datos: construye queries y devuelve entidades. Ninguna regla de
negocio aquí, y ningún `commit` — la transacción la controla el Service.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------ lectura

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    async def get_by(self, **filters: Any) -> ModelT | None:
        stmt = select(self.model).filter_by(**filters).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists(self, **filters: Any) -> bool:
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await self.session.execute(stmt)
        return (result.scalar_one() or 0) > 0

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: Any | None = None,
        **filters: Any,
    ) -> Sequence[ModelT]:
        stmt = select(self.model).filter_by(**filters)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await self.session.execute(stmt)
        return result.scalar_one() or 0

    async def paginate(
        self,
        stmt: Select[tuple[ModelT]],
        *,
        page: int = 1,
        size: int = 50,
    ) -> tuple[Sequence[ModelT], int]:
        """Devuelve (items de la página, total sin paginar).

        El total se cuenta sobre la misma query sin ORDER BY: ordenar dentro
        de un COUNT es trabajo tirado y en algunos casos un error de SQL.
        """
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self.session.execute(count_stmt)).scalar_one() or 0

        page = max(page, 1)
        result = await self.session.execute(stmt.limit(size).offset((page - 1) * size))
        return result.scalars().all(), total

    # ------------------------------------------------------------ escritura

    async def add(self, entity: ModelT) -> ModelT:
        """Añade y hace flush para que la PK quede disponible. Sin commit."""
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def add_all(self, entities: Sequence[ModelT]) -> Sequence[ModelT]:
        self.session.add_all(list(entities))
        await self.session.flush()
        return entities

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.flush()
