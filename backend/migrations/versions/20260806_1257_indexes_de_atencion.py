"""indexes de atencion

Revision ID: 57cd6e932d47
Revises: c1a2b3d4e5f6
Create Date: 2026-08-06 12:57:33.918162
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '57cd6e932d47'
down_revision: str | None = 'c1a2b3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # /metrics/attention cuenta conversaciones con status NEEDS_REPLY. El índice
    # existente ix_conversations_status empieza por owner_id, pero este query no
    # filtra por owner: un índice parcial sobre status basta.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversations_attention "
        "ON conversations (status) WHERE status = 'NEEDS_REPLY'"
    )
    # leads calientes: engagement_score >= 40, sin respuesta, OPEN. No existía
    # ninguna vía eficiente (solo stage_score y owner_status_activity).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_leads_hot "
        "ON leads (engagement_score DESC) WHERE status = 'OPEN' AND replied_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_conversations_attention")
    op.execute("DROP INDEX IF EXISTS ix_leads_hot")
