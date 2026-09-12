"""Modelo `jobs`: estado persistente de los procesos en background.

Vive en la Fase 1 aunque los workers lleguen en la Fase 2, porque es
infraestructura: sin él, un reinicio del proceso pierde el rastro de lo que
estaba corriendo y el usuario se queda mirando una barra de progreso que ya
no avanza.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import JobStatus, JobType
from app.models.base import OwnedModel, pg_enum


class Job(OwnedModel):
    __tablename__ = "jobs"

    job_type: Mapped[JobType] = mapped_column(pg_enum(JobType, "job_type"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        nullable=False,
        server_default=JobStatus.QUEUED.value,
    )

    # Identidad de servicio que creó el job (p.ej. "hermes" vía el contrato).
    # NULL = creado por la UI humana. LOOP-16: el contrato solo deja consultar
    # los jobs cuyo `client_id` coincide con la identidad autenticada.
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_message: Mapped[str | None] = mapped_column(String(255), nullable=True)

    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")

    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Lease del worker que tiene tomado el job. Sin esto, un proceso que muere
    # deja el job en RUNNING para siempre y nadie sabe si sigue vivo. El
    # heartbeat avanza mientras se procesa; si se vence, otro worker lo
    # reclama (ver `JobService.recover_stale`).
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Índice parcial: la cola solo consulta lo pendiente, que es una
        # fracción mínima de la tabla una vez lleve meses en marcha.
        Index(
            "ix_jobs_pending",
            "status",
            "scheduled_at",
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
        # Reclaim de huérfanos: solo mira jobs RUNNING por heartbeat vencido.
        Index(
            "ix_jobs_running_heartbeat",
            "status",
            "heartbeat_at",
            postgresql_where=text("status = 'RUNNING'"),
        ),
        Index("ix_jobs_type_created", "job_type", "created_at"),
    )

    @property
    def progress_pct(self) -> int | None:
        if not self.progress_total:
            return None
        return min(100, round(100 * self.progress_current / self.progress_total))

    @property
    def can_retry(self) -> bool:
        return self.status == JobStatus.FAILED and self.attempts < self.max_attempts
