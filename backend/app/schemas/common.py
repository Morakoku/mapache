"""Schemas compartidos: paginación, errores, respuestas de operaciones async."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class APIModel(BaseModel):
    """Base de todos los schemas.

    `from_attributes` permite construir directamente desde entidades ORM.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(APIModel, Generic[T]):
    items: Sequence[T]
    total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: Sequence[T], total: int, page: int, size: int) -> Page[T]:
        pages = (total + size - 1) // size if size else 0
        return cls(items=items, total=total, page=page, size=size, pages=pages)


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    size: int = Field(default=50, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorOut(BaseModel):
    error: ErrorDetail


class IdOut(APIModel):
    id: uuid.UUID


class JobAcceptedOut(APIModel):
    """Respuesta 202 de toda operación larga.

    La API nunca bloquea en scraping, enriquecimiento o envío masivo: devuelve
    un job y el cliente sigue el progreso por `GET /jobs/{id}`.
    """

    job_id: uuid.UUID
    status: str = "QUEUED"
    message: str | None = None


class HealthOut(BaseModel):
    status: str
    environment: str
    database: str
    version: str
