"""jobs: lease de worker para resiliencia de la cola (Fase 2)

Revision ID: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
Create Date: 2026-09-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("locked_by", sa.String(length=128), nullable=True))
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_jobs_running_heartbeat",
        "jobs",
        ["status", "heartbeat_at"],
        unique=False,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    # Los jobs que ya estaban RUNNING no tienen lease: se toma su started_at
    # como referencia para que el reclaim por antigüedad pueda verlos.
    op.execute(
        "UPDATE jobs "
        "SET locked_at = COALESCE(started_at, now()), "
        "    heartbeat_at = COALESCE(started_at, now()) "
        "WHERE status = 'RUNNING'"
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_running_heartbeat", table_name="jobs")
    op.drop_column("jobs", "heartbeat_at")
    op.drop_column("jobs", "locked_by")
    op.drop_column("jobs", "locked_at")
