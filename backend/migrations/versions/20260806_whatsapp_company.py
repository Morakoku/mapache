"""add whatsapp column to companies

Revision ID: c1a2b3d4e5f6
Revises: b41d7e9c2a58
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1a2b3d4e5f6"
down_revision: str | None = "b41d7e9c2a58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # E.164 del número de WhatsApp. Se guarda en E.164 igual que `phone` para
    # poder generar `wa.me/<numero>` y alimentar la integración de Meta.
    op.add_column(
        "companies",
        sa.Column("whatsapp", sa.String(length=20), nullable=True),
    )
    op.create_index("ix_companies_whatsapp", "companies", ["whatsapp"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_companies_whatsapp", table_name="companies")
    op.drop_column("companies", "whatsapp")