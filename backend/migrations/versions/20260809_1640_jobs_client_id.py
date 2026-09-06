"""jobs.client_id para el contrato Hermes (LOOP-16)

Revision ID: b2c3d4e5f607
Revises: a1b2c3d4e5f6
Create Date: 2026-08-09 16:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f607"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("client_id", sa.String(length=64), nullable=True))
    op.create_index("ix_jobs_client_id", "jobs", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_client_id", table_name="jobs")
    op.drop_column("jobs", "client_id")
