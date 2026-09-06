"""fase 8 intencion

Guarda la intención detectada en la respuesta del prospecto, su confianza y la
etapa sugerida. Todo es una **sugerencia** hasta que el usuario la revisa:
`intent_reviewed` marca cuándo dejó de proponerse.

Los tipos ENUM `reply_intent` y `stage_type` ya existen de fases anteriores, así
que se referencian con `create_type=False`: la autogeneración los declaraba con
`sa.Enum(...)`, que intenta crearlos otra vez y falla.

Revision ID: 0b31298f593b
Revises: bf6d7b8ab808
Create Date: 2026-07-28 10:53:55.264450
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0b31298f593b"
down_revision: str | None = "bf6d7b8ab808"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

reply_intent = postgresql.ENUM(name="reply_intent", create_type=False)
stage_type = postgresql.ENUM(name="stage_type", create_type=False)


def upgrade() -> None:
    op.add_column("conversations", sa.Column("reply_intent", reply_intent, nullable=True))
    op.add_column(
        "conversations",
        sa.Column("intent_confidence", sa.Numeric(precision=3, scale=2), nullable=True),
    )
    op.add_column("conversations", sa.Column("intent_summary", sa.Text(), nullable=True))
    op.add_column(
        "conversations", sa.Column("intent_suggested_stage", stage_type, nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("intent_reply_points", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column("conversations", sa.Column("intent_source", sa.String(length=10), nullable=True))
    op.add_column(
        "conversations",
        sa.Column(
            "intent_reviewed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    # Bandeja: "respuestas con intención sin revisar" es la consulta del día.
    op.create_index(
        "ix_conversations_intent_pending",
        "conversations",
        ["reply_intent"],
        postgresql_where=sa.text("reply_intent IS NOT NULL AND intent_reviewed IS FALSE"),
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_intent_pending", table_name="conversations")
    op.drop_column("conversations", "intent_reviewed")
    op.drop_column("conversations", "intent_source")
    op.drop_column("conversations", "intent_reply_points")
    op.drop_column("conversations", "intent_suggested_stage")
    op.drop_column("conversations", "intent_summary")
    op.drop_column("conversations", "intent_confidence")
    op.drop_column("conversations", "reply_intent")
