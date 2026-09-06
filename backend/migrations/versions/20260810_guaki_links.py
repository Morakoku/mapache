"""guaki_links: vínculo Prospecto (Mapache) ↔ Negocio (Guaki) — LOOP-24.

Revision ID: d3e4f5a6b7c8
Revises: b2c3d4e5f607
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "b2c3d4e5f607"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guaki_links",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lead_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("guaki_business_id", sa.String(length=64), nullable=False),
        sa.Column("match_state", sa.String(length=20), server_default="UNMATCHED", nullable=False),
        sa.Column("confidence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("signals", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
    )
    op.create_index("ix_guaki_links_match_state", "guaki_links", ["match_state"])
    op.create_index("ix_guaki_links_guaki_business_id", "guaki_links", ["guaki_business_id"])


def downgrade() -> None:
    op.drop_index("ix_guaki_links_guaki_business_id", table_name="guaki_links")
    op.drop_index("ix_guaki_links_match_state", table_name="guaki_links")
    op.drop_table("guaki_links")
